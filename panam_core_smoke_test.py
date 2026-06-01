from pathlib import Path

import panam_core


def main() -> None:
    assert hasattr(panam_core, "PanamResponse")

    source = Path("panam_core.py").read_text(encoding="utf-8")
    assert "import discord" not in source
    assert "from discord" not in source

    print("panam_core smoke test ok")


if __name__ == "__main__":
    main()
