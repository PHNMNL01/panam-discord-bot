import logging
import os
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import panam_log_reader
import panam_process_manager
import panam_test_runner


BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "runtime" / "dock"
HISTORY_FILE = HISTORY_DIR / "test_history.json"
MAX_HISTORY_ITEMS = 50

load_dotenv(dotenv_path=BASE_DIR / ".env")

ADMIN_TOKEN = os.getenv("PANAM_DOCK_ADMIN_TOKEN", "").strip()

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web_admin" / "templates"),
    static_folder=str(BASE_DIR / "web_admin" / "static"),
)
logger = logging.getLogger("panam.dock")


def _service_statuses_json() -> list[dict]:
    return [asdict(status) for status in panam_process_manager.get_all_service_statuses()]


def _test_result_json(result: panam_test_runner.PanamTestResult) -> dict:
    return panam_test_runner.result_to_dict(result)


def _is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def _is_authorized_post() -> bool:
    if ADMIN_TOKEN:
        return request.headers.get("X-Panam-Dock-Token") == ADMIN_TOKEN
    return _is_local_request()


def _is_authorized_admin_request() -> bool:
    return _is_authorized_post()


def _token_warning() -> str | None:
    if ADMIN_TOKEN:
        return None
    return "PANAM_DOCK_ADMIN_TOKEN neni nastaveny. Dock povoluje POST akce jen z 127.0.0.1."


def _page_context(active_page: str) -> dict:
    return {
        "active_page": active_page,
        "token_required": bool(ADMIN_TOKEN),
        "token_warning": _token_warning(),
    }


def _load_test_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []
    return data[-MAX_HISTORY_ITEMS:]


def _save_test_history(history: list[dict]) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history[-MAX_HISTORY_ITEMS:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _append_test_history(entry: dict) -> None:
    history = _load_test_history()
    history.append(entry)
    _save_test_history(history)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_entry_for_result(result: panam_test_runner.PanamTestResult) -> dict:
    return {
        "id": _now_iso(),
        "started_at": _now_iso(),
        "mode": "single",
        "test_name": result.name,
        "test_label": result.label,
        "passed": result.passed,
        "duration_seconds": result.duration_seconds,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "missing": result.missing,
        "error": result.error,
    }


def _history_entry_for_run_all(results: list[panam_test_runner.PanamTestResult]) -> dict:
    passed_count = sum(1 for result in results if result.passed)
    failed_count = len(results) - passed_count
    return {
        "id": _now_iso(),
        "started_at": _now_iso(),
        "mode": "run_all",
        "test_name": "run_all",
        "test_label": "Run all safe tests",
        "passed": failed_count == 0,
        "duration_seconds": sum(result.duration_seconds for result in results),
        "returncode": None,
        "timed_out": any(result.timed_out for result in results),
        "missing": any(result.missing for result in results),
        "error": None,
        "passed_count": passed_count,
        "failed_count": failed_count,
    }


@app.get("/")
def index():
    return render_template(
        "dock.html",
        services=_service_statuses_json(),
        **_page_context("dashboard"),
    )


@app.get("/tests")
def tests_page():
    return render_template("tests.html", **_page_context("tests"))


@app.get("/logs")
def logs_page():
    return render_template("logs.html", **_page_context("logs"))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "panam-dock"})


@app.get("/api/status")
def api_status():
    return jsonify(
        {
            "status": "ok",
            "services": _service_statuses_json(),
            "token_required": bool(ADMIN_TOKEN),
            "token_warning": _token_warning(),
        }
    )


@app.get("/api/tests")
def api_tests():
    return jsonify({"status": "ok", "tests": panam_test_runner.get_available_tests()})


@app.get("/api/tests/history")
def api_test_history():
    return jsonify({"status": "ok", "history": list(reversed(_load_test_history()))})


@app.post("/api/tests/history/clear")
def api_clear_test_history():
    if not _is_authorized_admin_request():
        logger.warning("Dock clear test history denied remote=%s", request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    _save_test_history([])
    logger.info("Dock test history cleared")
    return jsonify({"status": "ok", "history": []})


@app.get("/api/logs")
def api_logs():
    if not _is_authorized_admin_request():
        logger.warning("Dock logs list denied remote=%s", request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({"status": "ok", "logs": panam_log_reader.get_available_logs()})


@app.get("/api/logs/<log_name>")
def api_log_tail(log_name: str):
    if not _is_authorized_admin_request():
        logger.warning("Dock log read denied log=%s remote=%s", log_name, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    try:
        lines = int(request.args.get("lines", "300"))
        log_data = panam_log_reader.read_log_tail(log_name, max_lines=lines)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    logger.info("Dock log read log=%s exists=%s", log_data["name"], log_data["exists"])
    return jsonify({"status": "ok", "log": log_data})


@app.post("/api/service/<service_name>/start")
def api_start_service(service_name: str):
    if not _is_authorized_post():
        logger.warning("Dock start denied for service=%s remote=%s", service_name, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    try:
        status = panam_process_manager.start_service(service_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Dock start failed for service=%s", service_name)
        return jsonify({"error": "Service start failed"}), 500

    logger.info(
        "Dock start service=%s state=%s running=%s pid=%s",
        status.name,
        status.state,
        status.running,
        status.pid,
    )
    return jsonify({"status": "ok", "service": asdict(status)})


@app.post("/api/tests/<test_name>/run")
def api_run_test(test_name: str):
    if not _is_authorized_post():
        logger.warning("Dock test run denied for test=%s remote=%s", test_name, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    try:
        result = panam_test_runner.run_smoke_test(test_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    logger.info(
        "Dock test run test=%s passed=%s duration=%.3f",
        result.name,
        result.passed,
        result.duration_seconds,
    )
    _append_test_history(_history_entry_for_result(result))
    return jsonify({"status": "ok", "result": _test_result_json(result)})


@app.post("/api/tests/run-all")
def api_run_all_tests():
    if not _is_authorized_post():
        logger.warning("Dock run-all tests denied remote=%s", request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    results = panam_test_runner.run_all_smoke_tests()
    passed = all(result.passed for result in results)

    logger.info(
        "Dock run-all tests count=%s passed=%s",
        len(results),
        passed,
    )
    _append_test_history(_history_entry_for_run_all(results))
    return jsonify(
        {
            "status": "ok",
            "passed": passed,
            "results": [_test_result_json(result) for result in results],
        }
    )


@app.post("/api/service/<service_name>/stop")
def api_stop_service(service_name: str):
    if not _is_authorized_post():
        logger.warning("Dock stop denied for service=%s remote=%s", service_name, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    try:
        status = panam_process_manager.stop_service(service_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Dock stop failed for service=%s", service_name)
        return jsonify({"error": "Service stop failed"}), 500

    logger.info(
        "Dock stop service=%s state=%s running=%s pid=%s",
        status.name,
        status.state,
        status.running,
        status.pid,
    )
    return jsonify({"status": "ok", "service": asdict(status)})


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PANAM_DOCK_PORT", "5051")),
        debug=True,
    )
