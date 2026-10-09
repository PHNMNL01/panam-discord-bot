"""One local Voice Deck server. Launch never starts audio or a cloud session.

python -B -m panam_companion.web_cli --live-approved --budget-dir <existing-directory>
Only after separate human authorization for microphone/playback and paid calls.
"""
import argparse
import asyncio
import logging
from pathlib import Path
import socket
import sys

from aiohttp import web

from .budget import Budget
from .config import RUNTIME, SafeError, load_config
from .local_owner import LocalOwnership
from .local_runtime import LocalController, finish_operation
from .web_app import HOST, PORT, ORIGIN, create_app
from .web_runtime import WebRuntime


def check_port():
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((HOST, PORT))
    except OSError:
        raise SafeError("Port 8767 is unavailable; no alternate port or process termination.") from None


async def serve(config, budget, *, input_device=None, output_device=None, input_channels=1,
                controller_factory=LocalController, stop_event=None):
    controller = controller_factory(config, budget, input_device=input_device,
                                    output_device=output_device, input_channels=input_channels)
    runtime = WebRuntime(controller)
    quiet = logging.Logger("panam.private.web", level=logging.CRITICAL + 1)
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    runner = None
    try:
        runner = web.AppRunner(create_app(runtime), access_log=None, logger=quiet, handle_signals=False)
        await runner.setup()
        try:
            await web.TCPSite(runner, HOST, PORT, reuse_address=False, reuse_port=False).start()
        except OSError:
            raise SafeError("Port 8767 is unavailable; server not started.") from None
        print(f"Panam Companion / Voice Deck: {ORIGIN}/ — listening and cloud are OFF. Ctrl+C shuts down the Python runtime.", flush=True)
        await (stop_event or asyncio.Event()).wait()
    finally:
        try:
            await runtime.close()
        finally:
            if runner is not None:
                await finish_operation(runner.cleanup())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-approved", action="store_true")
    parser.add_argument("--budget-dir", type=Path)
    parser.add_argument("--input-device", type=int)
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--input-channels", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    try:
        if not args.live_approved:
            raise SafeError("Local audio and paid calls require explicit approval and --live-approved.")
        if sys.platform != "win32":
            raise SafeError("Use the existing Windows audio runtime.")
        directory = args.budget_dir.resolve() if args.budget_dir else None
        if directory is None or not directory.is_relative_to(RUNTIME.resolve()) or not (directory / "budget.json").is_file():
            raise SafeError("Select an existing approved budget directory; no counter initialization.")
        check_port()
        config = load_config()
        with LocalOwnership(directory):
            budget = Budget(directory)
            if budget.data["uncertain"]:
                raise SafeError("Budget finalization uncertain; no automatic recovery.")
            asyncio.run(serve(config, budget, input_device=args.input_device,
                              output_device=args.output_device, input_channels=args.input_channels))
        return 0
    except SafeError as failure:
        print(str(failure), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Web runtime failed; cleanup must be confirmed before restart. Sensitive details suppressed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
