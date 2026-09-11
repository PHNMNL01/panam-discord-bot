"""Explicit standalone entrypoint. --check is offline and secret-safe."""
import argparse
import asyncio
import importlib.metadata
import sys

from .config import RUNTIME, SafeError, load_config, safe_identity


def check_versions():
    expected = {"discord.py": "2.7.1", "discord-ext-voice-recv": "0.5.2a179",
                "davey": "0.1.6", "audioop-lts": "0.2.2", "websockets": "17.1",
                "aiohttp": "3.14.3", "python-dotenv": "1.2.3", "PyNaCl": "1.5.0"}
    if sys.version_info[:2] != (3, 14):
        raise SafeError("Tento experiment je ověřen offline s Pythonem 3.14; použijte jeho .venv.")
    for package, version in expected.items():
        if importlib.metadata.version(package) != version:
            raise SafeError("Verze prostředí neodpovídají experimentálnímu záznamu.")


async def run(config, budget, captions, metadata):
    from .discord_adapter import make_client, silence_library_logs
    silence_library_logs()
    client = make_client(config, budget, captions, metadata)
    try:
        await client.start(config.bot_token, reconnect=False)
    finally:
        await client.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Izolovaný Panam GPT-Live test")
    parser.add_argument("--check", action="store_true", help="Pouze místní kontrola, bez připojení")
    parser.add_argument("--live-approved", action="store_true", help="Až po dohodnutém GO a schválení $1 dávky")
    parser.add_argument("--captions", action="store_true", help="Zobrazit přepisy v místním terminálu; nic neukládat")
    parser.add_argument("--metadata", action="store_true", help="Po Stop vypsat pouze bezpečné časové značky")
    args = parser.parse_args()
    try:
        check_versions()
        config = load_config()
        print("Konfigurace je vyplněna. " + safe_identity(config))
        if args.check:
            print("OFFLINE: přístup k API, token bota, oprávnění a zvuk nebyly ověřeny.")
            return 0
        if not args.live_approved:
            raise SafeError("Nejprve je nutný společný živý checkpoint a GO pro limit $1 / 10 minut.")
        from .budget import Budget, ProcessLock
        with ProcessLock(RUNTIME):
            budget = Budget(RUNTIME)
            if budget.data["uncertain"]:
                raise SafeError("Předchozí ukončení není potvrzené. Rozpočet zůstává zablokován.")
            print("GPT-Live-1 / Marin + gpt-5.6-luna, bez nástrojů. Limit 5 min/relaci, 10 min/dávku, $1.")
            print("Připojení do Discord voice odesílá mikrofon do Discordu už před Startem Panam.")
            print("Start povolí příjem testovacího uživatele a odesílání audia do OpenAI.")
            print("Mute zastaví předávání do OpenAI; vlastní mikrofon v Discordu ztlumte také v Discordu.")
            print("Stop odpojí voice a uzavře Live; Quit/Ctrl+C ukončí i bota. Přepisy se na disk neukládají.")
            if input("Pro přihlášení testovacího bota napište CONNECT: ").strip() != "CONNECT":
                print("Bez připojení.")
                return 0
            asyncio.run(run(config, budget, args.captions, args.metadata))
        return 0
    except SafeError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("Ukončeno.")
        return 0
    except Exception:
        print("Experiment selhal. Citlivé podrobnosti byly potlačeny; ověřte stav rozpočtu a konfiguraci.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
