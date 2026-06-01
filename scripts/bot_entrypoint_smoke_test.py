import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    import bot

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert hasattr(bot, "run_discord_bot")
    assert "discord.Client" not in source
    assert "@bot.tree.command" not in source

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert not (
                node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "bot"
            )

    print("bot entrypoint smoke test ok")


if __name__ == "__main__":
    main()
