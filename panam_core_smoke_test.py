from pathlib import Path

import panam_core


def main() -> None:
    assert hasattr(panam_core, "PanamResponse")
    assert hasattr(panam_core, "handle_chat")
    assert hasattr(panam_core, "handle_talk")
    assert hasattr(panam_core, "handle_summary")

    source = Path("panam_core.py").read_text(encoding="utf-8")
    assert "import discord" not in source
    assert "from discord" not in source

    print("panam_core smoke test ok")


if __name__ == "__main__":
    main()
