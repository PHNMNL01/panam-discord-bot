from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_help
from panam_discord_responses import split_discord_message


def main() -> None:
    module_source = Path(panam_discord_help.__file__).read_text(encoding="utf-8")
    assert "import discord" not in module_source
    assert "from discord" not in module_source
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    help_text = panam_discord_help.get_help_text()
    assert isinstance(help_text, str)
    assert help_text.strip()
    assert len(help_text) > 1900
    assert all(len(chunk) <= 1900 for chunk in split_discord_message(help_text))

    for expected in (
        "/ask",
        "/help",
        "/process_file",
        "/extract_data",
        ".json",
        "Panam přidej poznámku",
    ):
        assert expected in help_text, expected

    print("discord help smoke test ok")


if __name__ == "__main__":
    main()
