import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_LIMIT = 8000
RELOADER_ENV_KEYS = {
    "WERKZEUG_RUN_MAIN",
    "WERKZEUG_SERVER_FD",
    "FLASK_RUN_FROM_CLI",
    "WERKZEUG_DEBUG_PIN",
}


@dataclass(frozen=True)
class PanamSmokeTest:
    name: str
    label: str
    path: str
    safe: bool = True


@dataclass
class PanamTestResult:
    name: str
    label: str
    path: str
    command: list[str]
    returncode: int | None
    passed: bool
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool = False
    missing: bool = False
    error: str | None = None


TESTS: dict[str, PanamSmokeTest] = {
    "web_router": PanamSmokeTest(
        name="web_router",
        label="Panam Web Router",
        path="panam_web_smoke_test.py",
    ),
    "web_flask": PanamSmokeTest(
        name="web_flask",
        label="Panam Web Flask",
        path="panam_web_flask_smoke_test.py",
    ),
    "dock": PanamSmokeTest(
        name="dock",
        label="Panam Dock",
        path="panam_dock_smoke_test.py",
    ),
    "router": PanamSmokeTest(
        name="router",
        label="Panam Router",
        path="scripts/router_smoke_test.py",
    ),
    "docx": PanamSmokeTest(
        name="docx",
        label="DOCX",
        path="scripts/docx_smoke_test.py",
    ),
    "docx_transform": PanamSmokeTest(
        name="docx_transform",
        label="DOCX Transform",
        path="scripts/docx_transform_smoke_test.py",
    ),
    "spreadsheet_transform": PanamSmokeTest(
        name="spreadsheet_transform",
        label="Spreadsheet Transform",
        path="scripts/spreadsheet_transform_smoke_test.py",
    ),
}


def _get_test(test_name: str) -> PanamSmokeTest:
    try:
        return TESTS[test_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(TESTS))
        raise ValueError(f"Unknown smoke test '{test_name}'. Allowed tests: {allowed}") from exc


def _tail_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-OUTPUT_LIMIT:]


def build_test_env() -> dict[str, str]:
    env = os.environ.copy()

    for key in RELOADER_ENV_KEYS:
        env.pop(key, None)

    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def get_available_tests() -> list[dict]:
    tests = []
    for test in TESTS.values():
        tests.append(
            {
                "name": test.name,
                "label": test.label,
                "path": test.path,
                "safe": test.safe,
                "exists": (PROJECT_ROOT / test.path).is_file(),
            }
        )
    return tests


def _result_to_dict(result: PanamTestResult) -> dict:
    return asdict(result)


def run_smoke_test(test_name: str, timeout_seconds: int = 60) -> PanamTestResult:
    test = _get_test(test_name)
    test_path = PROJECT_ROOT / test.path
    command = [sys.executable, test.path]
    start_time = time.perf_counter()

    if not test_path.is_file():
        duration = time.perf_counter() - start_time
        return PanamTestResult(
            name=test.name,
            label=test.label,
            path=test.path,
            command=command,
            returncode=None,
            passed=False,
            duration_seconds=duration,
            stdout="",
            stderr=f"Smoke test file not found: {test_path}",
            missing=True,
            error=f"Smoke test file not found: {test.path}",
        )

    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=build_test_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start_time
        return PanamTestResult(
            name=test.name,
            label=test.label,
            path=test.path,
            command=command,
            returncode=None,
            passed=False,
            duration_seconds=duration,
            stdout=_tail_output(exc.stdout),
            stderr=_tail_output(exc.stderr) or f"Smoke test timed out after {timeout_seconds} seconds.",
            timed_out=True,
            error=f"Smoke test timed out after {timeout_seconds} seconds.",
        )
    except OSError as exc:
        duration = time.perf_counter() - start_time
        return PanamTestResult(
            name=test.name,
            label=test.label,
            path=test.path,
            command=command,
            returncode=None,
            passed=False,
            duration_seconds=duration,
            stdout="",
            stderr=str(exc),
            error=str(exc),
        )

    duration = time.perf_counter() - start_time
    return PanamTestResult(
        name=test.name,
        label=test.label,
        path=test.path,
        command=command,
        returncode=result.returncode,
        passed=result.returncode == 0,
        duration_seconds=duration,
        stdout=_tail_output(result.stdout),
        stderr=_tail_output(result.stderr),
    )


def run_all_smoke_tests(timeout_seconds: int = 60) -> list[PanamTestResult]:
    return [
        run_smoke_test(test.name, timeout_seconds=timeout_seconds)
        for test in TESTS.values()
        if test.safe
    ]


def result_to_dict(result: PanamTestResult) -> dict:
    return _result_to_dict(result)
