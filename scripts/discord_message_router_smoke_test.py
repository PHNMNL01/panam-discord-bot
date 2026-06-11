import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def assert_no_bot_import() -> None:
    source = (ROOT / "panam_discord_message_router.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "bot" not in imported_names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bot"


def main() -> None:
    import panam_discord_message_router

    assert_no_bot_import()
    assert hasattr(panam_discord_message_router, "handle_discord_message")
    assert hasattr(panam_discord_message_router, "handle_basic_panam_message")
    assert hasattr(panam_discord_message_router, "handle_natural_voice_reply")
    assert hasattr(panam_discord_message_router, "log_natural_voice_reply_status")

    print("discord message router smoke test passed")


if __name__ == "__main__":
    main()
