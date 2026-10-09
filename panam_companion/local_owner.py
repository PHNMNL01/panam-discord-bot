"""Windows local-controller ownership, independent of the selected budget.

No I/O on import. Never unlink lock files: the OS byte lock represents ownership.
"""
from contextlib import ExitStack
from pathlib import Path

from .budget import ProcessLock
from .config import RUNTIME, SafeError

OWNER_PATH = RUNTIME / "companion-owner.lock"


class OwnerLock:
    def __init__(self, path=OWNER_PATH):
        self.path = Path(path).resolve()
        self.file = None

    def __enter__(self):
        import msvcrt
        if self.file is not None:
            raise SafeError("Local runtime ownership already acquired.")
        try:
            # The launcher verifies the existing runtime directory; do not create
            # arbitrary parent directories or delete another owner's marker.
            file = self.path.open("a+b")
            try:
                file.seek(0, 2)
                if file.tell() == 0:
                    file.write(b"0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except BaseException:
                file.close()
                raise
            self.file = file
            return self
        except Exception:
            raise SafeError("Local Companion runtime already owned or ownership unavailable.") from None

    def __exit__(self, *_args):
        import msvcrt
        if self.file is not None:
            file, self.file = self.file, None
            try:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                file.close()


class LocalOwnership:
    """Both launchers acquire owner first, then the unchanged budget lock."""
    def __init__(self, budget_directory, *, owner_path=OWNER_PATH):
        self.owner = OwnerLock(owner_path)
        self.budget_directory = Path(budget_directory).resolve()
        self.stack = None

    def __enter__(self):
        if self.stack is not None:
            raise SafeError("Local runtime ownership already acquired.")
        budget_path = self.budget_directory / "instance.lock"
        if budget_path.resolve() == self.owner.path:
            raise SafeError("Owner and budget locks must be distinct.")
        stack = ExitStack()
        try:
            stack.enter_context(self.owner)
            stack.enter_context(ProcessLock(self.budget_directory))
        except BaseException:
            stack.close()
            raise
        self.stack = stack
        return self

    def __exit__(self, *args):
        if self.stack is not None:
            stack, self.stack = self.stack, None
            return stack.__exit__(*args)
