import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_DIR = PROJECT_ROOT
PID_DIR = PROJECT_ROOT / "runtime" / "pids"
LOG_DIR = PROJECT_ROOT / "logs"
WEB_HEALTH_URL = "http://127.0.0.1:5050/health"
RELOADER_ENV_KEYS = {
    "WERKZEUG_RUN_MAIN",
    "WERKZEUG_SERVER_FD",
    "FLASK_RUN_FROM_CLI",
    "WERKZEUG_DEBUG_PIN",
}


@dataclass(frozen=True)
class PanamServiceDefinition:
    name: str
    label: str
    script: str
    log_file: str


@dataclass
class PanamServiceStatus:
    name: str
    label: str
    script: str
    running: bool
    pid: int | None
    pid_file: str
    state: str
    status_detail: str
    last_error: str | None = None
    log_tail: str | None = None


SERVICES: dict[str, PanamServiceDefinition] = {
    "bot": PanamServiceDefinition(
        name="bot",
        label="Panam Discord Bot",
        script="bot.py",
        log_file="panam_bot_process.log",
    ),
    "web": PanamServiceDefinition(
        name="web",
        label="Panam Web",
        script="panam_web_app.py",
        log_file="panam_web_process.log",
    ),
}


def _get_service_definition(service_name: str) -> PanamServiceDefinition:
    try:
        return SERVICES[service_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(SERVICES))
        raise ValueError(f"Unknown service '{service_name}'. Allowed services: {allowed}") from exc


def _pid_file_for(service_name: str) -> Path:
    return PID_DIR / f"{service_name}.pid"


def _read_pid(pid_file: Path) -> int | None:
    try:
        raw_pid = pid_file.read_text(encoding="utf-8").strip()
        return int(raw_pid)
    except (FileNotFoundError, ValueError):
        return None


def _log_file_for(definition: PanamServiceDefinition) -> Path:
    return LOG_DIR / definition.log_file


def _tail_log(definition: PanamServiceDefinition, max_chars: int = 1200) -> str | None:
    log_path = _log_file_for(definition)
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None

    return text[-max_chars:].strip() or None


def _remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        if os.name == "nt":
            _remove_pid_file_windows(pid_file)
        if pid_file.exists():
            try:
                pid_file.write_text("", encoding="utf-8")
            except OSError:
                pass


def _remove_pid_file_windows(pid_file: Path) -> None:
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "& { param($path) Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }",
                str(pid_file),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False

    if os.name == "nt":
        powershell_result = _is_process_running_powershell(pid)
        if powershell_result is not None:
            return powershell_result

        tasklist_result = _is_process_running_tasklist(pid)
        if tasklist_result is not None:
            return tasklist_result

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

    return False


def _is_process_running_powershell(pid: int) -> bool | None:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _is_process_running_tasklist(pid: int) -> bool | None:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    return f'","{pid}",' in result.stdout


def _check_web_health() -> tuple[bool, str | None]:
    try:
        with urllib.request.urlopen(WEB_HEALTH_URL, timeout=1.0) as response:
            if response.status == 200:
                return True, None
            return False, f"Health returned HTTP {response.status}."
    except urllib.error.URLError as exc:
        return False, f"Health check failed: {exc.reason}"
    except OSError as exc:
        return False, f"Health check failed: {exc}"


def _status_from_definition(definition: PanamServiceDefinition) -> PanamServiceStatus:
    pid_file = _pid_file_for(definition.name)
    pid = _read_pid(pid_file)
    pid_file_exists = pid_file.exists()
    process_alive = bool(pid and _is_process_running(pid))

    if pid_file_exists and pid is None:
        _remove_pid_file(pid_file)
        pid_file_exists = False

    if definition.name == "web":
        return _web_status_from_process_state(
            definition=definition,
            pid=pid,
            pid_file=pid_file,
            pid_file_exists=pid_file_exists,
            process_alive=process_alive,
        )

    if pid and not process_alive:
        _remove_pid_file(pid_file)
        pid = None

    running = bool(pid and process_alive)
    state = "running" if running else "stopped"
    status_detail = "pid_running" if running else "stopped"

    return _build_status(
        definition=definition,
        running=running,
        pid=pid,
        pid_file=pid_file,
        state=state,
        status_detail=status_detail,
    )


def _web_status_from_process_state(
    definition: PanamServiceDefinition,
    pid: int | None,
    pid_file: Path,
    pid_file_exists: bool,
    process_alive: bool,
) -> PanamServiceStatus:
    health_ok, health_error = _check_web_health()

    if pid and not process_alive:
        _remove_pid_file(pid_file)
        pid = None
        pid_file_exists = False

    if health_ok:
        detail = "health_ok" if pid_file_exists else "health_ok_no_managed_pid"
        return _build_status(
            definition=definition,
            running=True,
            pid=pid if process_alive else None,
            pid_file=pid_file,
            state="running",
            status_detail=detail,
        )

    if process_alive:
        return _build_status(
            definition=definition,
            running=False,
            pid=pid,
            pid_file=pid_file,
            state="error",
            status_detail="process_alive_but_health_failed",
            last_error=health_error,
        )

    return _build_status(
        definition=definition,
        running=False,
        pid=None,
        pid_file=pid_file,
        state="stopped",
        status_detail="stopped",
    )


def _build_status(
    definition: PanamServiceDefinition,
    running: bool,
    pid: int | None,
    pid_file: Path,
    state: str,
    status_detail: str,
    last_error: str | None = None,
    log_tail: str | None = None,
) -> PanamServiceStatus:
    return PanamServiceStatus(
        name=definition.name,
        label=definition.label,
        script=definition.script,
        running=running,
        pid=pid,
        pid_file=str(pid_file),
        state=state,
        status_detail=status_detail,
        last_error=last_error,
        log_tail=log_tail,
    )


def get_service_status(service_name: str) -> PanamServiceStatus:
    definition = _get_service_definition(service_name)
    return _status_from_definition(definition)


def get_all_service_statuses() -> list[PanamServiceStatus]:
    return [_status_from_definition(definition) for definition in SERVICES.values()]


def build_child_process_env(service_name: str) -> dict[str, str]:
    _get_service_definition(service_name)
    env = os.environ.copy()

    for key in RELOADER_ENV_KEYS:
        env.pop(key, None)

    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    if service_name == "web":
        env.update(
            {
                "PANAM_WEB_DEBUG": "0",
                "PANAM_WEB_USE_RELOADER": "0",
                "PANAM_WEB_HOST": "127.0.0.1",
                "PANAM_WEB_PORT": "5050",
                "PANAM_STARTED_BY_DOCK": "1",
            }
        )

    return env


def start_service(service_name: str) -> PanamServiceStatus:
    definition = _get_service_definition(service_name)
    current_status = _status_from_definition(definition)
    if current_status.running:
        return current_status
    if current_status.pid and current_status.state == "error":
        current_status.log_tail = _tail_log(definition)
        return current_status

    script_path = BASE_DIR / definition.script
    if not script_path.is_file():
        raise FileNotFoundError(f"Service script not found: {script_path}")

    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_path = _log_file_for(definition)
    log_handle = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    env = build_child_process_env(service_name)

    try:
        log_handle.write(f"starting service={service_name}\n")
        log_handle.write(f"python={sys.executable}\n")
        log_handle.write(f"script={script_path}\n")
        log_handle.write(f"cwd={PROJECT_ROOT}\n")
        log_handle.write("removed_werkzeug_env=true\n")
        log_handle.flush()
        process = subprocess.Popen(
            [sys.executable, "-u", str(script_path)],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            env=env,
        )
    finally:
        log_handle.close()

    _pid_file_for(definition.name).write_text(str(process.pid), encoding="utf-8")
    status = _wait_for_started_status(definition)
    if not status.running:
        status.log_tail = _tail_log(definition)
        if status.last_error is None:
            status.last_error = "Service did not become healthy after start."
    return status


def stop_service(service_name: str) -> PanamServiceStatus:
    definition = _get_service_definition(service_name)
    current_status = _status_from_definition(definition)
    if current_status.pid is None:
        return current_status

    pid = current_status.pid
    if not _is_process_running(pid):
        _remove_pid_file(_pid_file_for(definition.name))
        return _status_from_definition(definition)

    stop_error = None
    if os.name == "nt":
        stop_error = _stop_windows_process(pid)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            stop_error = str(exc)

    for _ in range(40):
        if not _is_process_running(pid):
            _remove_pid_file(_pid_file_for(definition.name))
            break
        time.sleep(0.1)

    status = _status_from_definition(definition)
    if status.pid and _is_process_running(status.pid):
        status.state = "error"
        status.status_detail = "stop_failed"
        status.last_error = stop_error or "Process is still running after stop."
        status.log_tail = _tail_log(definition)
    return status


def _stop_windows_process(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "taskkill failed or timed out."

    if result.returncode == 0:
        return None

    message = (result.stderr or result.stdout or "").strip()
    try:
        os.kill(pid, signal.SIGTERM)
        return None
    except OSError as exc:
        taskkill_message = message or f"taskkill returned {result.returncode}."
        return f"{taskkill_message} Fallback SIGTERM failed: {exc}"


def _wait_for_started_status(definition: PanamServiceDefinition) -> PanamServiceStatus:
    attempts = 20 if definition.name == "web" else 15
    status = _status_from_definition(definition)

    for _ in range(attempts):
        if status.running:
            return status
        if status.pid is None:
            return status
        time.sleep(0.1)
        status = _status_from_definition(definition)

    return status
