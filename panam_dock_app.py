import logging
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import panam_process_manager


BASE_DIR = Path(__file__).resolve().parent

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


def _is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def _is_authorized_post() -> bool:
    if ADMIN_TOKEN:
        return request.headers.get("X-Panam-Dock-Token") == ADMIN_TOKEN
    return _is_local_request()


def _token_warning() -> str | None:
    if ADMIN_TOKEN:
        return None
    return "PANAM_DOCK_ADMIN_TOKEN neni nastaveny. Dock povoluje POST akce jen z 127.0.0.1."


@app.get("/")
def index():
    return render_template(
        "dock.html",
        services=_service_statuses_json(),
        token_required=bool(ADMIN_TOKEN),
        token_warning=_token_warning(),
    )


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
