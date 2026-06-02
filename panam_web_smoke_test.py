from panam_command_router import parse_panam_command


def assert_command(
    raw_text: str,
    expected_intent: str,
    expected_text: str | None = None,
    assume_addressed: bool = False,
) -> None:
    command = parse_panam_command(raw_text, assume_addressed=assume_addressed)
    assert command.intent == expected_intent, (
        raw_text,
        command.intent,
        expected_intent,
    )
    if expected_text is not None:
        assert command.text == expected_text, (raw_text, command.text, expected_text)


def main() -> None:
    assert_command("Panam", "empty")
    assert_command("Panam help", "help")
    assert_command("Panam řekni mi ahoj", "ask", "ahoj")
    assert_command("Panam shrň toto je dlouhý text", "summary")
    assert_command("Panam přidej poznámku koupit SSD", "note_add")
    assert_command("Panam ukaž poznámky", "note_list")
    assert_command("Panam najdi poznámku SSD", "note_search")
    assert_command("Panam přidej todo dodělat web adapter", "todo_add")
    assert_command("Panam ukaž todo", "todo_list")
    assert_command("Jak se máš?", "ask")
    assert_command("help", "help", assume_addressed=True)
    assert_command("ukaž todo", "todo_list", assume_addressed=True)
    assert_command("přidej todo dodělat web", "todo_add", assume_addressed=True)
    assert_command("ukaž poznámky", "note_list", assume_addressed=True)
    assert_command("shrň toto je dlouhý text", "summary", assume_addressed=True)
    assert_command("jak se máš?", "ask", assume_addressed=True)
    assert_command("Panam help", "help", assume_addressed=True)
    assert_command("shrn to", "summary_previous", assume_addressed=True)
    assert_command("shrň toto", "summary_previous", assume_addressed=True)
    assert_command("Panam shrn to", "summary_previous", assume_addressed=True)
    assert_command("shrn toto je dlouhy text", "summary", assume_addressed=True)
    assert_command("vysvetli to", "ask_previous", assume_addressed=True)
    print("panam_web_smoke_test ok")


if __name__ == "__main__":
    main()
