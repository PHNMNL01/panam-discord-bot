from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_analyze_command


def main() -> None:
    module_source = (PROJECT_ROOT / "panam_discord_analyze_command.py").read_text(
        encoding="utf-8"
    )
    assert "import bot" not in module_source
    assert "from bot" not in module_source
    assert hasattr(panam_discord_analyze_command, "handle_analyze_command")

    print("discord analyze command smoke test ok")


if __name__ == "__main__":
    main()
