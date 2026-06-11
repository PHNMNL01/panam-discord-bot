import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def assert_no_bot_import() -> None:
    source = (ROOT / "panam_discord_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "bot" not in imported_names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "bot"


def assert_tts_config() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "discord-ext-voice-recv" in requirements
    assert "edge-tts" in requirements
    assert "elevenlabs" in requirements

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "PANAM_TTS_PROVIDER=elevenlabs",
        "PANAM_TTS_VOICE=cs-CZ-VlastaNeural",
        "PANAM_TTS_RATE=+0%",
        "PANAM_TTS_VOLUME=+0%",
        "ELEVENLABS_API_KEY=",
        "ELEVENLABS_VOICE_ID=",
        "ELEVENLABS_MODEL_ID=eleven_multilingual_v2",
        "ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128",
        "ELEVENLABS_STABILITY=0.45",
        "ELEVENLABS_SIMILARITY_BOOST=0.75",
        "ELEVENLABS_STYLE=0.25",
        "ELEVENLABS_USE_SPEAKER_BOOST=true",
    ):
        assert key in env_example


def main() -> None:
    import panam_discord_runner

    assert_no_bot_import()
    assert hasattr(panam_discord_runner, "run_discord_bot")
    assert hasattr(panam_discord_runner, "DiscordAIBot")
    assert hasattr(panam_discord_runner, "bot")
    command_names = {
        command.name
        for command in panam_discord_runner.bot.tree.get_commands()
    }
    assert {
        "voice_join",
        "voice_leave",
        "voice_say",
        "ask_voice",
        "panam_talk_voice",
        "listen_test",
    }.issubset(command_names)
    assert {"listen", "voice_watch", "voice_watch_on"}.isdisjoint(command_names)
    assert_tts_config()

    print("discord runner smoke test ok")


if __name__ == "__main__":
    main()
