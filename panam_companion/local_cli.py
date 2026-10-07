"""Manual Windows CLI; devices is passive, all other runtime use needs approval.

python -B -m panam_companion.local_cli --devices
python -B -m panam_companion.local_cli --live-approved --budget-dir <existing-directory>
Optional: --input-device INDEX --output-device INDEX --input-channels {1,2}.
This retains the experiment .env schema (including Discord IDs/token) and Budget.
No budget initialization/recovery or credential discovery is provided here.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from .budget import Budget, ProcessLock
from .config import RUNTIME, SafeError, load_config
from .local_audio import devices
from .local_runtime import LocalController, audio_operation


async def read_command():
    """Poll Windows console input without blocking the session monitor or owning a reader thread."""
    import msvcrt
    print("> ", end="", flush=True)
    chars = []
    extended = False
    while True:
        if not msvcrt.kbhit():
            await asyncio.sleep(.05)
            continue
        char = msvcrt.getwch()
        if extended:
            extended = False
        elif char in ("\x00", "\xe0"):
            extended = True
        elif char in ("\x03", "\x1a"):
            raise EOFError
        elif char == "\r":
            print()
            return "".join(chars).strip().lower()
        elif char == "\b" and chars:
            chars.pop()
            print("\b \b", end="", flush=True)
        elif char.isprintable() and len(chars) < 80:
            chars.append(char)
            print(char, end="", flush=True)


async def run(controller, reader=read_command):
    actions = {"listen": controller.start_listening, "start": controller.start_call,
               "mute": controller.mute, "unmute": controller.unmute,
               "end": controller.end_call, "stop-listening": controller.stop_listening}
    print("devices | listen | start | mute | unmute | end | stop-listening | status | quit")
    try:
        while True:
            try:
                command = await reader()
            except EOFError:
                return
            try:
                if command == "quit":
                    return
                if command == "devices":
                    inventory = await audio_operation(lambda: devices(controller.backend))
                    print(json.dumps(inventory, ensure_ascii=False))
                elif command in actions:
                    await actions[command]()
                elif command != "status":
                    print("Unknown command.")
                print(json.dumps(controller.status()))
            except SafeError as error:
                print(str(error))
                print(json.dumps(controller.status()))
            except Exception:
                print("Local command failed; closing owned resources.")
                return
    finally:
        await controller.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", action="store_true", help="Passive enumeration; no stream/config/API access")
    parser.add_argument("--live-approved", action="store_true", help="Only after explicit audio and paid-session approval")
    parser.add_argument("--budget-dir", type=Path, help="Existing approved Budget directory under Companion runtime")
    parser.add_argument("--input-device", type=int)
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--input-channels", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    try:
        if args.devices:
            print(json.dumps(devices(), ensure_ascii=False, indent=2))
            return 0
        if not args.live_approved:
            raise SafeError("Local microphone/playback and paid Live calls require explicit approval and --live-approved.")
        if sys.platform != "win32" or not sys.stdin.isatty():
            raise SafeError("Use an interactive Windows console.")
        directory = args.budget_dir.resolve() if args.budget_dir else None
        if directory is None or not directory.is_relative_to(RUNTIME.resolve()) or not (directory / "budget.json").is_file():
            raise SafeError("Select an existing approved budget directory; no new counter will be initialized.")
        config = load_config()
        with ProcessLock(directory):
            budget = Budget(directory)
            if budget.data["uncertain"]:
                raise SafeError("Budget finalization is uncertain; no automatic recovery.")
            controller = LocalController(config, budget, input_device=args.input_device,
                                         output_device=args.output_device, input_channels=args.input_channels)
            asyncio.run(run(controller))
        return 0
    except SafeError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        return 0
    except Exception:
        print("Local runtime failed; sensitive details suppressed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
