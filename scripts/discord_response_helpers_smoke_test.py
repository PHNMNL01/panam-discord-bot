from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_discord_responses


def main() -> None:
    assert panam_discord_responses is not None

    short_chunks = panam_discord_responses.split_discord_message("kratky text")
    assert short_chunks == ["kratky text"], short_chunks

    limit = 25
    long_text = "slovo " * 20
    long_chunks = panam_discord_responses.split_discord_message(long_text, limit=limit)
    assert len(long_chunks) > 1, long_chunks
    assert all(len(chunk) <= limit for chunk in long_chunks), long_chunks

    module_source = Path(panam_discord_responses.__file__).read_text(encoding="utf-8")
    assert "import bot" not in module_source
    assert "from bot" not in module_source

    print("discord response helpers smoke test ok")


if __name__ == "__main__":
    main()
