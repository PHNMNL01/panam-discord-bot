import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import discord


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
JOBS_DIR = RUNTIME_DIR / "jobs"


@dataclass
class FileJob:
    job_id: str
    base_dir: Path
    input_dir: Path
    work_dir: Path
    output_dir: Path
    metadata_path: Path
    user_id: int
    channel_id: int
    action: str
    status: str
    created_at: str
    finished_at: str | None = None
    input_files: list[dict] = field(default_factory=list)
    output_files: list[dict] = field(default_factory=list)


def generate_job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{timestamp}_{suffix}"


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name:
        return "file"

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = name.strip("._")
    return name or "file"


def build_panam_output_filename(
    source_filename: str,
    output_extension: str,
    suffix: str = "_by_Panam",
) -> str:
    source_stem = Path(str(source_filename or "")).stem.strip()
    if not source_stem:
        safe_stem = "panam_output"
    else:
        safe_stem = safe_filename(source_stem)
        if safe_stem == "file" and source_stem.lower() != "file":
            safe_stem = "panam_output"

    extension = str(output_extension or "").strip()
    if extension.startswith("."):
        extension = extension[1:]
    extension = re.sub(r"[^A-Za-z0-9]+", "", extension)

    filename = f"{safe_stem}{suffix}"
    if extension:
        filename = f"{filename}.{extension}"

    return safe_filename(filename)


def ensure_within_job(path: Path, job: FileJob) -> Path:
    resolved_path = path.resolve()
    resolved_base = job.base_dir.resolve()
    if resolved_path != resolved_base and resolved_base not in resolved_path.parents:
        raise ValueError("Path is outside the file job directory.")
    return resolved_path


def create_file_job(user_id: int, channel_id: int, action: str) -> FileJob:
    job_id = generate_job_id()
    base_dir = JOBS_DIR / job_id
    input_dir = base_dir / "input"
    work_dir = base_dir / "work"
    output_dir = base_dir / "output"
    metadata_path = base_dir / "job.json"

    input_dir.mkdir(parents=True, exist_ok=False)
    work_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)

    job = FileJob(
        job_id=job_id,
        base_dir=base_dir,
        input_dir=input_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        metadata_path=metadata_path,
        user_id=user_id,
        channel_id=channel_id,
        action=action,
        status="created",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_job_metadata(job)
    return job


async def save_attachment_to_job(file: discord.Attachment, job: FileJob) -> Path:
    filename = safe_filename(file.filename)
    output_path = ensure_within_job(job.input_dir / filename, job)

    data = await file.read()
    output_path.write_bytes(data)

    job.input_files.append(
        {
            "filename": filename,
            "extension": output_path.suffix.lower(),
            "size_bytes": output_path.stat().st_size,
        }
    )
    write_job_metadata(job)
    return output_path


def write_job_metadata(job: FileJob) -> None:
    metadata = {
        "job_id": job.job_id,
        "user_id": job.user_id,
        "channel_id": job.channel_id,
        "action": job.action,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "input_files": job.input_files,
        "output_files": job.output_files,
    }
    job.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    job.metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_work_text(job: FileJob, filename: str, content: str) -> Path:
    output_path = ensure_within_job(job.work_dir / safe_filename(filename), job)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def write_output_text(job: FileJob, filename: str, content: str) -> Path:
    output_path = ensure_within_job(job.output_dir / safe_filename(filename), job)
    output_path.write_text(content, encoding="utf-8")
    job.output_files.append(
        {
            "filename": output_path.name,
            "extension": output_path.suffix.lower(),
            "size_bytes": output_path.stat().st_size,
        }
    )
    write_job_metadata(job)
    return output_path


def cleanup_job(job: FileJob) -> None:
    shutil.rmtree(job.base_dir, ignore_errors=True)
