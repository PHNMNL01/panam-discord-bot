from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_todos


def main() -> None:
    source = Path("panam_todos.py").read_text(encoding="utf-8")
    assert "import discord" not in source
    assert "from discord" not in source

    with tempfile.TemporaryDirectory() as temp_dir:
        panam_todos.TODOS_FILE = Path(temp_dir) / "todos.json"

        todo_id = panam_todos.create_todo(
            "Dopsat test",
            author_id=123,
            author_name="Tester",
            channel_id=456,
        )
        assert todo_id == 1, todo_id

        todos = panam_todos.load_todos()
        assert len(todos) == 1, todos
        assert todos[0]["id"] == 1, todos
        assert todos[0]["text"] == "Dopsat test", todos
        assert todos[0]["done"] is False, todos
        assert todos[0]["author_id"] == 123, todos
        assert todos[0]["author_name"] == "Tester", todos
        assert todos[0]["channel_id"] == 456, todos
        assert "created_at" in todos[0], todos
        assert todos[0]["completed_at"] is None, todos

        list_response = panam_todos.format_todo_list_response()
        assert "Aktivní úkoly:" in list_response, list_response
        assert "#1 - Dopsat test (Tester)" in list_response, list_response

        assert panam_todos.complete_todo(1) is True

        todos = panam_todos.load_todos()
        assert todos[0]["done"] is True, todos
        assert todos[0]["completed_at"] is not None, todos

        empty_list_response = panam_todos.format_todo_list_response()
        assert empty_list_response == "Nemáš žádné aktivní úkoly.", empty_list_response

        assert panam_todos.complete_todo(999) is False

    print("todo smoke test ok")


if __name__ == "__main__":
    main()
