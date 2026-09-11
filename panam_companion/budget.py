"""Crash-conservative counters. These are local reservations, NOT a billing cap."""
from contextlib import AbstractContextManager
import json
import math
import os

from .config import SafeError

TOTAL_SECONDS = 600
SESSION_SECONDS = 300
CLOSE_MARGIN = 15
MAX_CALLS = 40
CALL_RESERVE = 0.01
VOICE_PER_SECOND = 0.05 / 60
TOTAL_DOLLARS = 1.00


class ProcessLock(AbstractContextManager):
    def __init__(self, directory):
        self.directory = directory
        self.file = None

    def __enter__(self):
        import msvcrt
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = (self.directory / "instance.lock").open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise SafeError("Jiná instance Companion již běží.") from None
        return self

    def __exit__(self, *args):
        import msvcrt
        if self.file and not self.file.closed:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


class Budget:
    def __init__(self, directory):
        self.path = directory / "budget.json"
        self.active = 0
        self.data = dict(version=1, seconds=0, backend_calls=0,
                         backend_reserved_usd=0.0, input_tokens=0, output_tokens=0,
                         observed_voice_seconds=0.0, uncertain=False)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if set(loaded) != set(self.data) or loaded["version"] != 1:
                    raise ValueError
                if type(loaded["uncertain"]) is not bool:
                    raise ValueError
                for key in set(loaded) - {"uncertain"}:
                    value = loaded[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError
                self.data = loaded
            except Exception:
                raise SafeError("Poškozené počítadlo rozpočtu; nutná kontrola, žádný automatický reset.") from None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(".tmp")
        with pending.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(self.data, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, self.path)

    @property
    def reserved_dollars(self):
        return self.data["seconds"] * VOICE_PER_SECOND + self.data["backend_reserved_usd"]

    def reserve_session(self):
        if self.active or self.data["uncertain"]:
            raise SafeError("Aktivní nebo nepotvrzeně uzavřená relace; nový start je blokován.")
        seconds = min(SESSION_SECONDS, int(TOTAL_SECONDS - self.data["seconds"]))
        if seconds <= CLOSE_MARGIN + 5 or self.reserved_dollars + seconds * VOICE_PER_SECOND > TOTAL_DOLLARS:
            raise SafeError("Časový nebo finanční limit dávky byl vyčerpán.")
        self.active = seconds
        self.data["seconds"] += seconds
        # A crash / lost final event locks subsequent starts, even after restart.
        self.data["uncertain"] = True
        self.save()
        return seconds

    def finish_session(self, elapsed, provider_seconds, finalized):
        if not self.active:
            return
        valid = (isinstance(provider_seconds, (int, float)) and
                 math.isfinite(provider_seconds) and provider_seconds >= 0)
        if finalized and valid:
            charged = math.ceil(max(elapsed, provider_seconds))
            self.data["seconds"] += charged - self.active
            self.data["observed_voice_seconds"] += provider_seconds
            self.data["uncertain"] = charged > self.active
        self.active = 0
        self.save()

    def reserve_backend(self):
        if not self.active or self.data["backend_calls"] >= MAX_CALLS:
            raise SafeError("Limit požadavků backendu byl vyčerpán.")
        if self.reserved_dollars + CALL_RESERVE > TOTAL_DOLLARS - 0.09:
            raise SafeError("Bezpečnostní rezerva rozpočtu další požadavek nepovoluje.")
        self.data["backend_calls"] += 1
        self.data["backend_reserved_usd"] = round(self.data["backend_reserved_usd"] + CALL_RESERVE, 4)
        self.save()

    def observe_backend(self, usage):
        for source, key in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
            value = usage.get(source)
            if type(value) is int and value >= 0:
                self.data[key] += value
        self.save()
