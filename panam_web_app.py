import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import panam_web_adapter


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(dotenv_path=BASE_DIR / ".env")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web" / "templates"),
    static_folder=str(BASE_DIR / "web" / "static"),
)
logger = logging.getLogger("panam.web")


def _env_bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "panam-web"})


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Zpráva je prázdná."}), 400

    try:
        answer = asyncio.run(
            panam_web_adapter.handle_web_message(OPENAI_MODEL, message)
        )
    except Exception:
        logger.exception("Web chat request failed")
        return jsonify({"error": "Panam web odpověď se nepodařila vytvořit."}), 500

    return jsonify({"answer": answer})


@app.post("/api/clear")
def clear():
    message = panam_web_adapter.clear_web_chat_memory()
    return jsonify({"status": "ok", "message": message})


if __name__ == "__main__":
    host = os.getenv("PANAM_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("PANAM_WEB_PORT", "5050"))
    debug = _env_bool("PANAM_WEB_DEBUG", "1")
    use_reloader = _env_bool("PANAM_WEB_USE_RELOADER", "1")
    started_by_dock = _env_bool("PANAM_STARTED_BY_DOCK", "0")

    if started_by_dock:
        debug = False
        use_reloader = False

    app.run(
        host=host,
        port=port,
        debug=debug,
        use_reloader=use_reloader,
    )
