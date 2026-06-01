from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import panam_notes


def main() -> None:
    source = Path("panam_notes.py").read_text(encoding="utf-8")
    assert "import discord" not in source
    assert "from discord" not in source

    with tempfile.TemporaryDirectory() as temp_dir:
        panam_notes.NOTES_FILE = Path(temp_dir) / "notes.json"

        panam_notes.create_note(
            "Koupit chleba",
            author_id=123,
            author_name="Tester",
            channel_id=456,
        )

        notes = panam_notes.load_notes()
        assert len(notes) == 1, notes
        assert notes[0]["text"] == "Koupit chleba", notes
        assert notes[0]["author_id"] == 123, notes
        assert notes[0]["author_name"] == "Tester", notes
        assert notes[0]["channel_id"] == 456, notes
        assert "created_at" in notes[0], notes

        list_response = panam_notes.format_note_list_response()
        assert "Poslední poznámky:" in list_response, list_response
        assert "Koupit chleba" in list_response, list_response

        search_response = panam_notes.format_note_search_response("chleba")
        assert "Nalezené poznámky:" in search_response, search_response
        assert "Koupit chleba" in search_response, search_response

        empty_search_response = panam_notes.format_note_search_response("mléko")
        assert empty_search_response == "Nic jsem nenašla.", empty_search_response

    print("notes smoke test ok")


if __name__ == "__main__":
    main()
