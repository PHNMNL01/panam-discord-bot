import json
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TODOS_FILE = BASE_DIR / "todos.json"


def shorten_text(text: str, limit: int = 1900) -> str:
    answer = text.strip()
    if len(answer) <= limit:
        return answer

    suffix = "\n\n…odpověď byla zkrácena."
    return answer[: max(limit - len(suffix), 0)] + suffix


def load_todos() -> list[dict]:
    if not TODOS_FILE.exists():
        return []

    with TODOS_FILE.open("r", encoding="utf-8") as todos_file:
        todos = json.load(todos_file)

    if not isinstance(todos, list):
        return []

    return todos


def save_todos(todos: list[dict]) -> None:
    with TODOS_FILE.open("w", encoding="utf-8") as todos_file:
        json.dump(todos, todos_file, ensure_ascii=False, indent=2)


def create_todo(
    text: str,
    author_id: int,
    author_name: str,
    channel_id: int,
) -> int:
    todos = load_todos()
    next_id = max(
        (todo.get("id", 0) for todo in todos if isinstance(todo.get("id"), int)),
        default=0,
    ) + 1

    todos.append(
        {
            "id": next_id,
            "text": text,
            "done": False,
            "author_id": author_id,
            "author_name": author_name,
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
    )
    save_todos(todos)

    return next_id


def format_todo_list_response() -> str:
    todos = load_todos()
    active_todos = [todo for todo in todos if not todo.get("done")]
    if not active_todos:
        return "Nemáš žádné aktivní úkoly."

    lines = ["Aktivní úkoly:"]
    for todo in active_todos[:15]:
        todo_id = todo.get("id", "?")
        text = todo.get("text", "")
        author_name = todo.get("author_name", "neznámý autor")
        lines.append(f"#{todo_id} - {text} ({author_name})")

    return shorten_text("\n".join(lines))


def complete_todo(todo_id: int) -> bool:
    todos = load_todos()
    todo = next((item for item in todos if item.get("id") == todo_id), None)
    if todo is None:
        return False

    todo["done"] = True
    todo["completed_at"] = datetime.now(timezone.utc).isoformat()
    save_todos(todos)
    return True
