from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"


@dataclass(frozen=True)
class PanamLogDefinition:
    name: str
    label: str
    path: str


LOGS: dict[str, PanamLogDefinition] = {
    "main": PanamLogDefinition(
        name="main",
        label="Panam Main",
        path="logs/panam.log",
    ),
    "bot_process": PanamLogDefinition(
        name="bot_process",
        label="Panam Bot Process",
        path="logs/panam_bot_process.log",
    ),
    "web_process": PanamLogDefinition(
        name="web_process",
        label="Panam Web Process",
        path="logs/panam_web_process.log",
    ),
    "dock_process": PanamLogDefinition(
        name="dock_process",
        label="Panam Dock Process",
        path="logs/panam_dock_process.log",
    ),
}


def _get_log_definition(log_name: str) -> PanamLogDefinition:
    try:
        return LOGS[log_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(LOGS))
        raise ValueError(f"Unknown log '{log_name}'. Allowed logs: {allowed}") from exc


def _log_path(definition: PanamLogDefinition) -> Path:
    return PROJECT_ROOT / definition.path


def get_available_logs() -> list[dict]:
    logs = []
    for definition in LOGS.values():
        path = _log_path(definition)
        exists = path.is_file()
        logs.append(
            {
                "name": definition.name,
                "label": definition.label,
                "path": definition.path,
                "exists": exists,
                "size": path.stat().st_size if exists else None,
            }
        )
    return logs


def read_log_tail(
    log_name: str,
    max_lines: int = 300,
    max_chars: int = 60000,
) -> dict:
    definition = _get_log_definition(log_name)
    path = _log_path(definition)
    max_lines = max(1, min(int(max_lines), 1000))
    max_chars = max(1000, min(int(max_chars), 120000))

    if not path.is_file():
        return {
            "name": definition.name,
            "label": definition.label,
            "path": definition.path,
            "exists": False,
            "size": None,
            "content": "",
            "message": "Log file does not exist.",
            "max_lines": max_lines,
            "max_chars": max_chars,
        }

    size = path.stat().st_size
    read_size = min(size, max_chars)

    with path.open("rb") as handle:
        handle.seek(max(0, size - read_size))
        raw = handle.read(read_size)

    content = raw.decode("utf-8", errors="replace")
    lines = content.splitlines()
    if len(lines) > max_lines:
        content = "\n".join(lines[-max_lines:])

    if len(content) > max_chars:
        content = content[-max_chars:]

    return {
        "name": definition.name,
        "label": definition.label,
        "path": definition.path,
        "exists": True,
        "size": size,
        "content": content,
        "message": "",
        "max_lines": max_lines,
        "max_chars": max_chars,
    }
