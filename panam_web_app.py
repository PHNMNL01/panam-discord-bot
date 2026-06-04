import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from panam_discord_attachments import MAX_DOCUMENT_SIZE_BYTES, SUPPORTED_DOCUMENT_EXTENSIONS
import panam_web_adapter


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
WEB_UPLOADS_DIR = RUNTIME_DIR / "web_uploads"
WEB_OUTPUTS_DIR = RUNTIME_DIR / "web_outputs"

load_dotenv(dotenv_path=BASE_DIR / ".env")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web" / "templates"),
    static_folder=str(BASE_DIR / "web" / "static"),
)
logger = logging.getLogger("panam.web")

DOWNLOADS: dict[str, dict[str, str]] = {}


def _env_bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _safe_extension(filename: str) -> str:
    return Path(str(filename or "")).suffix.lower()


def _get_upload_size(file_storage) -> int:
    stream = file_storage.stream
    current_position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current_position)
    return size


def _resolve_web_file_mode(
    instruction: str,
    output_format: str,
    extension: str,
) -> tuple[str, str | None, str]:
    from panam_router import decide_file_response_mode

    normalized_format = (output_format or "auto").lower().strip().strip(".")
    if normalized_format in {"", "auto"}:
        decision = decide_file_response_mode(
            instruction,
            extension=extension,
            has_xlsx_context=extension == ".xlsx",
            has_docx_context=extension == ".docx",
        )
        mode = str(decision.get("mode") or "chat_answer")
        decided_format = decision.get("output_format")
        if not isinstance(decided_format, str):
            decided_format = None

        if mode.startswith("unsupported_"):
            return mode, None, str(decision.get("instruction") or instruction)
        return mode, decided_format, str(decision.get("instruction") or instruction)

    if normalized_format == "chat":
        return "chat_answer", None, instruction

    if extension == ".xlsx":
        decision = decide_file_response_mode(
            instruction,
            extension=extension,
            has_xlsx_context=True,
            has_docx_context=False,
        )
        if decision.get("mode") == "spreadsheet_transform":
            return "spreadsheet_transform", "xlsx", str(
                decision.get("instruction") or instruction
            )

    if normalized_format in {"md", "txt", "docx"}:
        return "human_document", normalized_format, instruction

    if normalized_format in {"json", "csv", "xlsx"}:
        return "structured_data", normalized_format, instruction

    return "unsupported_output_format", None, instruction


def _persist_web_output(output_path: Path, output_filename: str | None) -> tuple[str, str]:
    WEB_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    download_id = uuid.uuid4().hex
    safe_name = secure_filename(output_filename or output_path.name) or "panam_output"
    stored_path = WEB_OUTPUTS_DIR / f"{download_id}_{safe_name}"
    shutil.copy2(output_path, stored_path)
    DOWNLOADS[download_id] = {
        "path": str(stored_path),
        "filename": safe_name,
    }
    return download_id, safe_name


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


@app.post("/api/files/process")
def process_file():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Chybi soubor k nahrani."}), 400

    original_filename = Path(uploaded.filename).name
    extension = _safe_extension(original_filename)
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        return jsonify({"error": "Tenhle typ souboru zatim web file pipeline nepodporuje."}), 400

    upload_size = _get_upload_size(uploaded)
    if upload_size > MAX_DOCUMENT_SIZE_BYTES:
        return jsonify({"error": "Ten soubor je moc velky. Zatim beru max 20 MB."}), 413

    instruction = str(request.form.get("instruction") or "").strip()
    output_format = str(request.form.get("output_format") or "auto").strip()
    if not instruction:
        instruction = "Analyzuj tuto prilohu a strucne popis, co obsahuje."

    mode, resolved_output_format, resolved_instruction = _resolve_web_file_mode(
        instruction,
        output_format,
        extension,
    )
    if mode == "unsupported_output_format":
        return jsonify({"error": "Nepodporovany vystupni format."}), 400
    if mode.startswith("unsupported_"):
        return jsonify({"error": "Tenhle typ upravy web file pipeline zatim bezpecne nepodporuje."}), 400

    upload_id = uuid.uuid4().hex
    safe_upload_name = secure_filename(original_filename) or f"upload{extension}"
    upload_path = WEB_UPLOADS_DIR / f"{upload_id}_{safe_upload_name}"
    WEB_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    result = None
    try:
        from panam_file_service import cleanup_file_result

        uploaded.save(upload_path)
        logger.info(
            "web_file_process status=started filename=%s extension=%s size_bytes=%s mode=%s output_format=%s",
            original_filename,
            extension,
            upload_size,
            mode,
            resolved_output_format,
        )
        result = asyncio.run(
            panam_web_adapter.handle_web_file_request(
                OPENAI_MODEL,
                upload_path,
                original_filename,
                resolved_instruction,
                mode,
                resolved_output_format,
            )
        )

        logger.info(
            "web_file_process status=success filename=%s extension=%s size_bytes=%s result_kind=%s mode=%s output_format=%s",
            original_filename,
            extension,
            upload_size,
            result.kind,
            result.mode,
            result.output_format,
        )

        if result.kind == "chat_answer":
            return jsonify(
                {
                    "kind": "chat_answer",
                    "answer": result.message,
                    "message": result.message,
                }
            )

        if result.output_path is None:
            return jsonify({"error": "Souborovy vystup se nepodarilo vytvorit."}), 500

        download_id, output_filename = _persist_web_output(
            result.output_path,
            result.output_filename,
        )
        return jsonify(
            {
                "kind": "file",
                "message": result.message,
                "output_filename": output_filename,
                "download_url": f"/api/files/download/{download_id}",
            }
        )

    except Exception as error:
        logger.exception(
            "web_file_process status=error filename=%s extension=%s size_bytes=%s mode=%s output_format=%s",
            original_filename,
            extension,
            upload_size,
            mode,
            resolved_output_format,
        )
        if error.__class__.__name__ in {
            "PanamFileServiceUserError",
            "TextExtractionUserError",
            "SpreadsheetTransformUserError",
            "DocxTransformUserError",
        }:
            return jsonify({"error": str(error)}), 400
        return jsonify({"error": "Soubor se nepodarilo zpracovat."}), 500

    finally:
        if result is not None:
            from panam_file_service import cleanup_file_result

            try:
                cleanup_file_result(result)
            except OSError:
                logger.warning("web_file_process cleanup warning=job_cleanup_failed")
        try:
            upload_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "web_file_process cleanup warning=upload_cleanup_failed filename=%s",
                upload_path.name,
            )


@app.get("/api/files/download/<download_id>")
def download_file(download_id: str):
    if not download_id.isalnum():
        return jsonify({"error": "Neplatny download odkaz."}), 404

    item = DOWNLOADS.get(download_id)
    if item is None:
        return jsonify({"error": "Soubor neni k dispozici."}), 404

    output_path = Path(item["path"])
    try:
        output_path.resolve().relative_to(WEB_OUTPUTS_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Neplatny download odkaz."}), 404

    if not output_path.exists():
        return jsonify({"error": "Soubor neni k dispozici."}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=item["filename"],
    )


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
