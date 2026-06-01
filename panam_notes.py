import json
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
NOTES_FILE = BASE_DIR / "notes.json"


def shorten_for_discord(text: str, limit: int = 1900) -> str:
    answer = text.strip()
    if len(answer) <= limit:
        return answer

    suffix = "\n\n…odpověď byla zkrácena."
    return answer[: max(limit - len(suffix), 0)] + suffix


def load_notes() -> list[dict]:
    if not NOTES_FILE.exists():
        return []

    with NOTES_FILE.open("r", encoding="utf-8") as notes_file:
        notes = json.load(notes_file)

    if not isinstance(notes, list):
        return []

    return notes


def save_notes(notes: list[dict]) -> None:
    with NOTES_FILE.open("w", encoding="utf-8") as notes_file:
        json.dump(notes, notes_file, ensure_ascii=False, indent=2)


def create_note(
    text: str,
    author_id: int,
    author_name: str,
    channel_id: int,
) -> None:
    notes = load_notes()
    notes.append(
        {
            "text": text,
            "author_id": author_id,
            "author_name": author_name,
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_notes(notes)


def format_note_list_response() -> str:
    notes = load_notes()
    if not notes:
        return "Zatím nemám žádné poznámky."

    lines = ["Poslední poznámky:"]
    for index, note in enumerate(reversed(notes[-10:]), start=1):
        author_name = note.get("author_name", "neznámý autor")
        created_at = note.get("created_at", "neznámý čas")
        note_text = note.get("text", "")
        lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

    return shorten_for_discord("\n".join(lines))


def format_note_search_response(query: str) -> str:
    notes = load_notes()
    if not notes:
        return "Zatím nemám žádné poznámky."

    query_lower = query.lower()
    matches = [
        note
        for note in notes
        if query_lower in str(note.get("text", "")).lower()
    ][-10:]

    if not matches:
        return "Nic jsem nenašla."

    lines = ["Nalezené poznámky:"]
    for index, note in enumerate(reversed(matches), start=1):
        author_name = note.get("author_name", "neznámý autor")
        created_at = note.get("created_at", "neznámý čas")
        note_text = note.get("text", "")
        lines.append(f"{index}. [{created_at}] {author_name}: {note_text}")

    return shorten_for_discord("\n".join(lines))
