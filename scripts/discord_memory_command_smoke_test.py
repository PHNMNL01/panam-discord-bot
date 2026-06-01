import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def assert_no_bot_import() -> None:
    source = (ROOT / "panam_discord_memory_command.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "bot" not in imported_names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bot"


def main() -> None:
    import panam_discord_memory_command

    assert_no_bot_import()
    assert hasattr(panam_discord_memory_command, "handle_memory_clear_command")

    print("discord memory command smoke test ok")


if __name__ == "__main__":
    main()
