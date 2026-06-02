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


@app.get("/")
def index():
    return render_template("index.html")


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
