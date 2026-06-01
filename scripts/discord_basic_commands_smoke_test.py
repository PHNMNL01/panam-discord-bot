import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXPECTED_HANDLERS = (
    "handle_ping_command",
    "handle_help_command",
)


def assert_no_bot_import() -> None:
    source = (ROOT / "panam_discord_basic_commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "bot" not in imported_names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bot"


def main() -> None:
    import panam_discord_basic_commands

    assert_no_bot_import()
    for handler_name in EXPECTED_HANDLERS:
        assert hasattr(panam_discord_basic_commands, handler_name), handler_name

    print("discord basic commands smoke test ok")


if __name__ == "__main__":
    main()
