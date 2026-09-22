"""Closed Windows-local Git observations; no public command/executable override.

The transport is private to this adapter. Windows Job ownership precedes resume;
all captures are bounded while being read, including on error paths.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from types import MappingProxyType

from .git_inspection_models import (
    ByteIdentity, CommandObservation, GitObjectIdentity, GitObservation,
    IndexEntry, InspectionReason as Reason, PathChange, RemoteBinding,
    TrustedGitInspectionContext,
)


_GIT = r"C:\Program Files\Git\cmd\git.exe"
_PROCESS_SECONDS = 10.0
_CAPTURE_MAX = 2 * 1024 * 1024
_PROCESS_MAX = 32
_TOTAL_SECONDS = 60.0
_METADATA_MAX = 8192
_FILE_MAX = 2 * 1024 * 1024
_SPAWN_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Unavailable(Exception):
    def __init__(self, reason: Reason):
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class _Operation:
    argv: tuple[str, ...]
    parser: str
    exits: tuple[int, ...] = (0,)
    scoped: bool = False


_RAW = ("--raw", "--no-abbrev", "-z", "--no-renames", "--no-ext-diff", "--no-textconv")
_DIFF = ("--binary", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color")
_OPERATIONS = MappingProxyType({
    "ROOT": _Operation(("rev-parse", "--show-toplevel"), "single-line root"),
    "FORMAT": _Operation(("rev-parse", "--show-object-format"), "sha1/sha256"),
    "BRANCH": _Operation(("symbolic-ref", "-q", "HEAD"), "refs/heads/name", (0, 1)),
    "HEAD": _Operation(("rev-parse", "--verify", "HEAD^{commit}"), "commit OID", (0, 128)),
    "TREE": _Operation(("rev-parse", "--verify", "HEAD^{tree}"), "tree OID"),
    "INDEX": _Operation(("ls-files", "--stage", "-z"), "NUL index entries"),
    "STAGED": _Operation(("diff-index", "--cached", *_RAW, "HEAD", "--"), "NUL raw changes"),
    "UNSTAGED": _Operation(("diff-files", *_RAW, "--"), "NUL raw changes", scoped=True),
    "STAGED_DIFF": _Operation(("diff", "--cached", *_DIFF, "--"), "binary diff", scoped=True),
    "UNSTAGED_DIFF": _Operation(("diff", *_DIFF, "--"), "binary diff", scoped=True),
    "UPSTREAM": _Operation(("rev-parse", "--symbolic-full-name", "@{upstream}"), "local ref", (0, 128)),
    "DIVERGENCE": _Operation(("rev-list", "--left-right", "--count", "HEAD...@{upstream}"), "two counts"),
})


def _environment() -> dict[str, str]:
    # No inherited GIT_*, HOME, PATH, credential, SSH, pager or config injections.
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_LITERAL_PATHSPECS": "1", "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_ALLOW_PROTOCOL": "", "LC_ALL": "C", "LANG": "C",
    })
    return env


def _argv(operation: str, scope: tuple[str, ...]) -> tuple[str, ...]:
    op = _OPERATIONS[operation]
    config = (
        "protocol.allow=never", "core.fsmonitor=false", "core.untrackedCache=false",
        "core.hooksPath=" + os.devnull, "core.attributesFile=" + os.devnull,
        "core.excludesFile=" + os.devnull, "core.autocrlf=false", "core.safecrlf=false",
        "core.preloadIndex=false", "core.ignoreStat=false", "core.splitIndex=false",
        "core.sparseCheckout=false", "credential.helper=", "gc.auto=0",
        "maintenance.auto=false", "diff.external=", "diff.renames=false",
    )
    prefix = (_GIT, "--no-optional-locks", "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs")
    return prefix + tuple(item for value in config for item in ("-c", value)) + op.argv + (scope if op.scoped else ())


class _Job:
    """Anonymous non-breakaway Job; no executable runs before assignment."""

    def __init__(self) -> None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k
        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
        k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.ResumeThread.argtypes = (wintypes.HANDLE,)
        k.ResumeThread.restype = wintypes.DWORD

        class Basic(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    def assign(self, process: int) -> None:
        if not self.k.AssignProcessToJobObject(self.handle, process):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")

    def active(self) -> int:
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, ABI-aligned 48 bytes.
        counters = (ctypes.c_ulonglong * 6)()
        if not self.k.QueryInformationJobObject(self.handle, 1, ctypes.byref(counters), ctypes.sizeof(counters), None):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject")
        return ctypes.cast(counters, ctypes.POINTER(wintypes.DWORD))[10]

    def terminate(self) -> None:
        if not self.k.TerminateJobObject(self.handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject")

    def close(self) -> None:
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def _capture(operation: str, scope: tuple[str, ...], root: str, deadline: float) -> CommandObservation:
    """Private fixed Git operation transport; tests replace selection, not ownership."""
    argv = _argv(operation, scope)
    started = _now()
    limit = min(time.monotonic() + _PROCESS_SECONDS, deadline)
    reason, cleanup, exit_code = Reason.OBSERVED, "NOT_STARTED", None
    buffers = [bytearray(), bytearray()]
    overflow, read_error = threading.Event(), threading.Event()
    readers, fds = [], []
    hp = ht = job = None
    assigned = False
    try:
        if os.name != "nt":
            raise _Unavailable(Reason.UNSUPPORTED_PROFILE)
        if time.monotonic() >= limit - 1.0:
            raise _Unavailable(Reason.INSPECTION_DEADLINE)
        import _winapi
        import msvcrt
        job = _Job()
        out_r, out_w = os.pipe()
        fds.extend((out_r, out_w))
        err_r, err_w = os.pipe()
        fds.extend((err_r, err_w))
        null_fd = os.open(os.devnull, os.O_RDONLY)
        fds.append(null_fd)
        handles = [msvcrt.get_osfhandle(fd) for fd in (null_fd, out_w, err_w)]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
        startup.lpAttributeList = {"handle_list": handles}
        with _SPAWN_LOCK:
            try:
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                # Native argv quoting is the same as shell=False subprocess on Windows.
                hp, ht, _pid, _tid = _winapi.CreateProcess(
                    argv[0], subprocess.list2cmdline(argv), None, None, True,
                    0x4 | 0x08000000, _environment(), root, startup,
                )
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
        job.assign(hp)
        assigned = True
        for fd in (out_w, err_w, null_fd):
            os.close(fd)
            fds.remove(fd)

        def read(fd: int, buffer: bytearray) -> None:
            try:
                while block := os.read(fd, 8192):
                    available = _CAPTURE_MAX - len(buffer)
                    buffer.extend(block[:available])
                    if len(block) > available:
                        overflow.set()
                        return
            except OSError:
                read_error.set()

        for fd, buffer in ((out_r, buffers[0]), (err_r, buffers[1])):
            thread = threading.Thread(target=read, args=(fd, buffer), daemon=True)
            thread.start()
            readers.append(thread)
        if job.k.ResumeThread(ht) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
        _winapi.CloseHandle(ht)
        ht = None
        while True:
            if overflow.is_set():
                reason = Reason.OUTPUT_OVERFLOW
                break
            if read_error.is_set():
                reason = Reason.REQUIRED_EVIDENCE_UNAVAILABLE
                break
            if time.monotonic() >= limit - 1.0:
                reason = Reason.COMMAND_TIMEOUT
                break
            if _winapi.WaitForSingleObject(hp, 0) == 0:
                exit_code = _winapi.GetExitCodeProcess(hp)
                # Process signaling precedes Job accounting updates on Windows.
                # Allow bounded quiescence; never mistake that race for a live
                # orphan, or assume parent exit proves the owned tree is empty.
                drain_until = min(time.monotonic() + 0.1, limit - 1.0)
                while job.active() and time.monotonic() < drain_until:
                    time.sleep(0.005)
                if job.active():
                    reason = Reason.REQUIRED_EVIDENCE_UNAVAILABLE
                break
            time.sleep(0.005)
    except _Unavailable as exc:
        reason = exc.reason
    except OSError:
        reason = Reason.GIT_UNAVAILABLE if hp is None else Reason.REQUIRED_EVIDENCE_UNAVAILABLE
    finally:
        if hp is not None:
            import _winapi
            try:
                if assigned:
                    if job.active():
                        job.terminate()
                    while job.active() and time.monotonic() < limit:
                        time.sleep(0.005)
                    cleanup = "OWNED_JOB_EMPTY" if not job.active() else "FAILED"
                else:
                    # Creation remained suspended: no descendant could have run.
                    _winapi.TerminateProcess(hp, 1)
                    cleanup = "SUSPENDED_PROCESS_TERMINATED" if _winapi.WaitForSingleObject(hp, 500) == 0 else "FAILED"
                if _winapi.WaitForSingleObject(hp, 0) == 0:
                    exit_code = _winapi.GetExitCodeProcess(hp)
            except OSError:
                cleanup = "FAILED"
            finally:
                if ht is not None:
                    _winapi.CloseHandle(ht)
                _winapi.CloseHandle(hp)
        if job is not None:
            job.close()
        for reader in readers:
            reader.join(max(0.0, limit - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            cleanup = "FAILED"
        for fd in fds:
            os.close(fd)
    primary_reason = reason
    if cleanup == "FAILED":
        reason = Reason.CLEANUP_FAILURE
    elif overflow.is_set():
        reason = Reason.OUTPUT_OVERFLOW
    elif read_error.is_set():
        reason = Reason.REQUIRED_EVIDENCE_UNAVAILABLE
    return CommandObservation(operation, argv, root, started, _now(), exit_code,
                              bytes(buffers[0]), bytes(buffers[1]), reason is Reason.OBSERVED,
                              cleanup, reason, primary_reason)


def _decode(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise _Unavailable(Reason.MALFORMED_OUTPUT) from exc


def _line(payload: bytes) -> str:
    value = _decode(payload)
    if not value.endswith("\n") or "\n" in value[:-1] or "\r" in value or "\0" in value:
        raise _Unavailable(Reason.MALFORMED_OUTPUT)
    return value[:-1]


def _name(path: str) -> None:
    from .git_inspection_models import _path
    try:
        _path(path)
    except ValueError as exc:
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT) from exc


def _index(payload: bytes, algorithm: str) -> tuple[IndexEntry, ...]:
    length = {"sha1": 40, "sha256": 64}[algorithm]
    if payload and not payload.endswith(b"\0"):
        raise _Unavailable(Reason.MALFORMED_OUTPUT)
    rows = []
    for record in payload.split(b"\0")[:-1]:
        match = re.fullmatch(rb"(100644|100755|120000|160000) ([0-9a-f]{" + str(length).encode() + rb"}) ([0-3])\t(.+)", record, re.DOTALL)
        if match is None:
            raise _Unavailable(Reason.MALFORMED_OUTPUT)
        mode, oid, stage, raw_path = match.groups()
        path = _decode(raw_path)
        _name(path)
        if mode in (b"120000", b"160000"):
            raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
        rows.append(IndexEntry(path, mode.decode(), oid.decode(), int(stage)))
    if len(rows) > _METADATA_MAX or len({(r.path.casefold(), r.stage) for r in rows}) != len(rows):
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    return tuple(rows)


def _changes(payload: bytes, algorithm: str) -> tuple[PathChange, ...]:
    if payload and not payload.endswith(b"\0"):
        raise _Unavailable(Reason.MALFORMED_OUTPUT)
    parts = payload.split(b"\0")[:-1]
    if len(parts) % 2:
        raise _Unavailable(Reason.MALFORMED_OUTPUT)
    rows = []
    n = str({"sha1": 40, "sha256": 64}[algorithm]).encode()
    for header, raw_path in zip(parts[::2], parts[1::2]):
        match = re.fullmatch(rb":[0-7]{6} [0-7]{6} [0-9a-f]{" + n + rb"} [0-9a-f]{" + n + rb"} ([ACDMRTUXB])", header)
        if match is None or match[1] in (b"R", b"C"):
            raise _Unavailable(Reason.MALFORMED_OUTPUT)
        path = _decode(raw_path)
        _name(path)
        rows.append(PathChange(path, match[1].decode()))
    return tuple(rows)


def _same_path(a: str, b: str) -> bool:
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def _safe_root(root: str) -> Path:
    if os.name != "nt" or not re.match(r"^[A-Za-z]:[\\/]", root) or root.startswith(("\\", "/")):
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    if any(part in ("..", ".") for part in root.replace("\\", "/").split("/")[1:]) or ":" in root[2:]:
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    path = Path(root)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    if k.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED only; mapped/network drives fail closed.
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    for parent in (*reversed(path.parents), path):
        st = parent.lstat()
        if st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    if not path.is_dir() or not _same_path(str(path.resolve(strict=True)), root):
        raise _Unavailable(Reason.ROOT_MISMATCH)
    if not (path / ".git").is_dir():
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    for parent in path.parents:
        if (parent / ".git").exists():
            raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    return path


def _inventory(root: Path, deadline: float) -> tuple[tuple[str, int, int, int, int], ...]:
    """Names/stat only: no ignore files, attributes, excluded or untracked contents."""
    rows = []
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    raise _Unavailable(Reason.INSPECTION_DEADLINE)
                path = Path(entry.path)
                # Windows DirEntry.stat has zero st_ino/st_nlink; obtain actual
                # non-following identity before enforcing the hardlink boundary.
                st = path.lstat()
                rel = path.relative_to(root).as_posix()
                if st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                if stat.S_ISREG(st.st_mode) and st.st_nlink != 1:
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                if ":" in rel or (path.name.casefold() == ".git" and rel != ".git"):
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                if path.name.casefold() == ".gitattributes" or rel.casefold() == ".git/info/attributes":
                    raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
                if rel.casefold() in (".git/commondir", ".git/objects/info/alternates", ".git/objects/info/http-alternates", ".git/shallow") or path.suffix.casefold() == ".promisor" or path.name.startswith("sharedindex."):
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                if rel.casefold().startswith((".git/worktrees", ".git/modules", ".git/refs/replace")):
                    raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                rows.append((rel, st.st_size, st.st_mtime_ns, st.st_ino, st.st_file_attributes))
                if len(rows) > _METADATA_MAX:
                    raise _Unavailable(Reason.REQUIRED_EVIDENCE_UNAVAILABLE)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
    return tuple(sorted(rows))


def _read(path: Path, maximum: int = _FILE_MAX) -> bytes:
    # Every caller has authorized this exact control-metadata or tracked-scope path.
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise _Unavailable(Reason.OUTPUT_OVERFLOW)
    return payload


def _config(root: Path) -> tuple[tuple[str, str, str, str], ...]:
    """Conservative closed subset, checked BEFORE Git can interpret local config.

    No includes, escapes, continuation, implicit files or unknown executable keys.
    Unsupported syntax is diagnostic unavailability, never silently reinterpreted.
    """
    payload = _decode(_read(root / ".git" / "config", 65536))
    rows = []
    section = None
    allowed = {
        "core": {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "symlinks", "autocrlf"},
        "user": {"name", "email"}, "remote": {"url", "pushurl", "fetch"},
        "branch": {"remote", "merge"}, "extensions": {"objectformat"},
    }
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        match = re.fullmatch(r'\[([a-zA-Z]+)(?: "([A-Za-z0-9_./-]+)")?\]', line)
        if match:
            section = (match[1].lower(), match[2] or "")
            if section[0] not in allowed or bool(section[1]) != (section[0] in ("branch", "remote")):
                raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
            continue
        match = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9]*)\s*=\s*([^\\\x00-\x1f#;]+)', line)
        if match is None or section is None or match[1].lower() not in allowed[section[0]]:
            raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
        value = match[2].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if not value or '"' in value:
            raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
        row = (*section, match[1].lower(), value)
        if any(old[:3] == row[:3] for old in rows) and row[0] != "remote":
            raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
        rows.append(row)
    values = {(s, k): v for s, sub, k, v in rows if not sub}
    if values.get(("core", "bare"), "false").lower() != "false" or values.get(("core", "repositoryformatversion"), "0") not in ("0", "1"):
        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
    return tuple(rows)


def _remotes(config: tuple) -> tuple[RemoteBinding, ...]:
    names = sorted({sub for section, sub, key, value in config if section == "remote"})
    result = []
    for name in names:
        urls = tuple(v for s, sub, k, v in config if s == "remote" and sub == name and k == "url")
        pushes = tuple(v for s, sub, k, v in config if s == "remote" and sub == name and k == "pushurl")
        try:
            result.append(RemoteBinding(name, urls, pushes))
        except ValueError as exc:
            raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION) from exc
    return tuple(result)


class _Session:
    def __init__(self, root: str, context: TrustedGitInspectionContext):
        self.root, self.context = root, context
        self.deadline = time.monotonic() + _TOTAL_SECONDS
        self.commands: list[CommandObservation] = []

    def command(self, name: str) -> CommandObservation:
        if len(self.commands) >= _PROCESS_MAX:
            raise _Unavailable(Reason.PROCESS_LIMIT)
        if time.monotonic() >= self.deadline:
            raise _Unavailable(Reason.INSPECTION_DEADLINE)
        if _OPERATIONS[name].scoped and not self.context.approved_read_path_scope:
            raise _Unavailable(Reason.SCOPE_MISMATCH)  # Never turn an empty scope into all paths.
        result = _capture(name, self.context.approved_read_path_scope, self.root, self.deadline)
        self.commands.append(result)
        if result.reason is not Reason.OBSERVED:
            raise _Unavailable(result.reason)
        if result.exit_code not in _OPERATIONS[name].exits:
            raise _Unavailable(Reason.REQUIRED_EVIDENCE_UNAVAILABLE)
        return result

    def snapshot(self) -> tuple[GitObservation, Reason]:
        root = _safe_root(self.root)
        inventory = _inventory(root, self.deadline)
        config = _config(root)
        ctx = self.context
        if not _same_path(str(root / ".git"), ctx.expected_repository_identity.git_directory_binding):
            raise _Unavailable(Reason.REPOSITORY_MISMATCH)
        actual_root = _line(self.command("ROOT").stdout)
        if not _same_path(actual_root, self.root):
            raise _Unavailable(Reason.ROOT_MISMATCH)
        algorithm = _line(self.command("FORMAT").stdout)
        if algorithm not in ("sha1", "sha256"):
            raise _Unavailable(Reason.MALFORMED_OUTPUT)
        index = _index(self.command("INDEX").stdout, algorithm)
        # Even absent worktree attributes can be read implicitly from index blobs.
        if any(Path(row.path).name.casefold() == ".gitattributes" for row in index):
            raise _Unavailable(Reason.UNSUPPORTED_CONFIGURATION)
        tracked = {row.path for row in index}
        scope = ctx.approved_read_path_scope
        if any(path not in tracked for path in scope):
            raise _Unavailable(Reason.SCOPE_MISMATCH)  # Never read untracked content.
        branch_result = self.command("BRANCH")
        branch = _line(branch_result.stdout) if branch_result.exit_code == 0 else None
        if branch is not None:
            if not branch.startswith("refs/heads/"):
                raise _Unavailable(Reason.MALFORMED_OUTPUT)
            branch = branch[len("refs/heads/"):]
        head_result = self.command("HEAD")
        head = tree = None
        condition = Reason.OBSERVED
        if head_result.exit_code == 0:
            try:
                head = GitObjectIdentity(algorithm, "commit", _line(head_result.stdout))
                tree = GitObjectIdentity(algorithm, "tree", _line(self.command("TREE").stdout))
            except ValueError as exc:
                raise _Unavailable(Reason.MALFORMED_OUTPUT) from exc
        else:
            condition = Reason.UNBORN
        if branch is None:
            condition = Reason.DETACHED
        staged = _changes(self.command("STAGED").stdout, algorithm) if head else ()
        unstaged = ()
        staged_diff = unstaged_diff = b""
        identities = []
        if scope and head:
            # Recheck path topology immediately before any content-bearing Git read.
            _safe_root(self.root)
            _inventory(root, self.deadline)
            unstaged = _changes(self.command("UNSTAGED").stdout, algorithm)
            staged_diff = self.command("STAGED_DIFF").stdout
            unstaged_diff = self.command("UNSTAGED_DIFF").stdout
            for relative in scope:
                target = root / relative
                if target.exists():
                    if not target.is_file() or target.stat().st_nlink != 1:
                        raise _Unavailable(Reason.UNSUPPORTED_LAYOUT)
                    identities.append(ByteIdentity.of("worktree-raw", relative, _read(target)))
                else:
                    identities.append(ByteIdentity.of("worktree-absent", relative, b""))
        upstream = None
        divergence = None
        if branch and head:
            tracking = self.command("UPSTREAM")
            if tracking.exit_code == 0:
                upstream = _line(tracking.stdout)
                if not upstream.startswith("refs/"):
                    raise _Unavailable(Reason.MALFORMED_OUTPUT)
                counts = _line(self.command("DIVERGENCE").stdout)
                if not re.fullmatch(r"[0-9]+\s+[0-9]+", counts):
                    raise _Unavailable(Reason.MALFORMED_OUTPUT)
                divergence = tuple(int(value) for value in counts.split())
            elif any(section == "branch" and sub == branch for section, sub, key, value in config):
                raise _Unavailable(Reason.REQUIRED_EVIDENCE_UNAVAILABLE)
        content_unassessed = tuple(sorted(tracked.difference(scope)))
        untracked = tuple(rel for rel, size, stamp, ino, attrs in inventory
                          if not (attrs & stat.FILE_ATTRIBUTE_DIRECTORY)
                          and not rel.startswith(".git/") and rel not in tracked)
        # Stat inventory plus exact control payloads binds config, HEAD and index bytes.
        controls = [("inventory", inventory), ("config", config)]
        for relative in (".git/HEAD", ".git/index", ".git/packed-refs"):
            target = root / relative
            if target.exists():
                controls.append((relative, hashlib.sha256(_read(target)).hexdigest()))
        metadata = ByteIdentity.of("git-control-metadata-v1", self.root,
                                   json.dumps(controls, ensure_ascii=True, separators=(",", ":")).encode())
        observation = GitObservation(
            actual_root, algorithm, branch, head, tree, index, staged, unstaged, untracked,
            tuple(sorted({row.path for row in index if row.stage})), _remotes(config),
            tuple((key, value) for section, sub, key, value in config if section == "branch" and sub == branch),
            upstream, divergence, tuple(identities), staged_diff, unstaged_diff, metadata,
            content_unassessed,
        )
        return observation, condition


class GitReadOnlyAdapter:
    """Infrastructure port used by GitInspector after Registry/trust validation.

    Root is the validated Policy value, not part of a public inspection request.
    No operation/argv/environment/executable/resource options are exposed.
    """

    def observe(self, policy, context: TrustedGitInspectionContext):
        session = _Session(policy.project_root, context)
        observations = []
        try:
            first, condition = session.snapshot()
            observations.append(first)
            if condition is not Reason.OBSERVED:
                return tuple(observations), tuple(session.commands), condition
            second, condition = session.snapshot()
            observations.append(second)
            if first != second or condition is not Reason.OBSERVED:
                return tuple(observations), tuple(session.commands), Reason.STATE_DRIFT
            ctx = context
            if first.object_format != ctx.expected_repository_identity.object_format:
                reason = Reason.REPOSITORY_MISMATCH
            elif first.branch != ctx.expected_branch_constraint:
                reason = Reason.BRANCH_MISMATCH
            elif ctx.expected_baseline_constraint.mode == "REQUIRED" and first.head != ctx.expected_baseline_constraint.head:
                reason = Reason.BASELINE_MISMATCH
            else:
                expected = {r.name: r for r in ctx.expected_configured_remote_bindings}
                actual = {r.name: r for r in first.remotes}
                reason = Reason.OBSERVED if all(actual.get(k) == v for k, v in expected.items()) and (ctx.additional_remotes == "REPORT_ONLY" or actual == expected) else Reason.REMOTE_MISMATCH
            return tuple(observations), tuple(session.commands), reason
        except _Unavailable as exc:
            return tuple(observations), tuple(session.commands), exc.reason
        except (OSError, ValueError, UnicodeError):
            return tuple(observations), tuple(session.commands), Reason.REQUIRED_EVIDENCE_UNAVAILABLE
