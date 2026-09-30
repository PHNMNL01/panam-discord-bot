"""Closed Windows-local DL-2.6 adapter, with no generic command entry point.

Private DL-2.4 metadata/Job helpers are reused without changing its profile.
All worktree observations are names/stat only; worktree contents are never read.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time

from .git_inspection_models import InspectionReason as Reason
from .git_read_only_adapter import (
    _Job, _Unavailable, _safe_root, _inventory, _config, _read, _environment,
)
from .phase_branch_creation_models import (
    ACTION, OperationRejected, ProcessEvidence, RepositoryObservation,
    _LaunchPermission, TargetObservationUnavailable, canonical, validate_ref,
)

_GIT = r"C:\Program Files\Git\cmd\git.exe"
_PROCESS_SECONDS = 10.0
_CAPTURE_MAX = 2 * 1024 * 1024
_TOTAL_SECONDS = 30.0
_SPAWN_LOCK = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _argv(operation, intent):
    operations = {
        "ROOT": ("rev-parse", "--show-toplevel"),
        "FORMAT": ("rev-parse", "--show-object-format"),
        "HEAD_REF": ("symbolic-ref", "-q", "HEAD"),
        "HEAD": ("rev-parse", "--verify", "HEAD^{commit}"),
        "TYPE": ("cat-file", "-t", intent.source_commit.oid),
        "REFS": ("for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)"),
        "REF_VALID": ("check-ref-format", intent.target_ref),
        ACTION: ("branch", "--no-track", "--", intent.target_ref[len("refs/heads/"):], intent.source_commit.oid),
    }
    controls = ("protocol.allow=never", "core.hooksPath=" + os.devnull,
        "core.fsmonitor=false", "core.untrackedCache=false", "core.attributesFile=" + os.devnull,
        "core.excludesFile=" + os.devnull, "core.preloadIndex=false", "core.splitIndex=false",
        "core.sparseCheckout=false", "credential.helper=", "gc.auto=0", "maintenance.auto=false")
    return (_GIT, "--no-optional-locks", "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs") + tuple(
        item for control in controls for item in ("-c", control)) + operations[operation]


def _line(payload):
    text = payload.decode("utf-8", "strict")
    if not text.endswith("\n") or any(c in text[:-1] for c in "\r\n\0"):
        raise OperationRejected("malformed Git observation")
    return text[:-1]


def _raw_refs(root, inventory, algorithm):
    width = {"sha1": 40, "sha256": 64}[algorithm]
    oid_pattern = "[0-9a-f]{" + str(width) + "}"
    refs = {}
    packed = root / ".git" / "packed-refs"
    if packed.exists():
        for line in _read(packed).decode("ascii", "strict").splitlines():
            if line.startswith("#"):
                continue
            if line.startswith("^") and re.fullmatch(oid_pattern, line[1:]):
                continue
            parts = line.split(" ")
            if len(parts) != 2 or not re.fullmatch(oid_pattern, parts[0]) or parts[1] in refs:
                raise OperationRejected("broken packed refs")
            validate_ref(parts[1])
            refs[parts[1]] = parts[0]
    for relative, size, stamp, inode, attrs in inventory:
        if relative.startswith(".git/") and relative.endswith(".lock"):
            raise OperationRejected("unresolved Git lock")
        if not relative.startswith(".git/refs/") or attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
            continue
        name = relative[len(".git/"):]
        validate_ref(name)
        value = _read(root / relative, 1024).decode("ascii", "strict")
        if not value.endswith("\n") or "\n" in value[:-1]:
            raise OperationRejected("broken loose ref")
        value = value[:-1]
        if value.startswith("ref: "):
            validate_ref(value[5:])
        elif not re.fullmatch(oid_pattern, value):
            raise OperationRejected("broken loose ref")
        refs[name] = value
    resolved = {}
    for name, value in refs.items():
        seen = {name}
        symbolic = value[5:] if value.startswith("ref: ") else ""
        while value.startswith("ref: "):
            target = value[5:]
            if target in seen or target not in refs:
                raise OperationRejected("broken symbolic ref")
            seen.add(target)
            value = refs[target]
        resolved[name] = (value, symbolic)
    return resolved


class GitPhaseBranchAdapter:
    def observe(self, intent):
        try:
            return self._observe(intent)
        except (_Unavailable, OSError, ValueError, KeyError, OperationRejected) as error:
            raise TargetObservationUnavailable(type(error).__name__ + ": " + str(error)[:192]) from error

    def _observe(self, intent):
        intent.__post_init__()
        deadline = time.monotonic() + _TOTAL_SECONDS
        commands = []

        def command(name):
            evidence = _capture(name, intent, deadline)
            commands.append(evidence)
            if not evidence.complete or not evidence.quiescent or evidence.exit_code != 0:
                raise OperationRejected("failed " + name + " observation")
            return evidence.stdout

        root = _safe_root(intent.policy.project_root)
        before = _inventory(root, deadline)
        _config(root)  # Reject implicit helpers/includes before any Git process.
        if os.path.normcase(_line(command("ROOT"))) != os.path.normcase(str(root).replace("\\", "/")):
            raise OperationRejected("root mismatch")
        if _line(command("FORMAT")) != intent.source_commit.algorithm:
            raise OperationRejected("object format mismatch")
        if _line(command("HEAD_REF")) != intent.source_ref:
            raise OperationRejected("detached/unexpected source")
        if _line(command("HEAD")) != intent.source_commit.oid or _line(command("TYPE")) != "commit":
            raise OperationRejected("unresolved/unapproved source commit")
        command("REF_VALID")
        raw = _raw_refs(root, before, intent.source_commit.algorithm)
        parsed = {}
        for line in command("REFS").decode("utf-8", "strict").splitlines():
            parts = line.split("\0")
            if len(parts) != 3 or parts[0] in parsed:
                raise OperationRejected("ambiguous Git refs")
            parsed[parts[0]] = (parts[1], parts[2])
        if parsed != raw or raw.get(intent.source_ref) != (intent.source_commit.oid, ""):
            raise OperationRejected("incomplete/ref-source observation")
        target = intent.target_ref
        state = "TARGET_REF_DEFINITELY_ABSENT"
        folded = target.casefold()
        for name, (oid, symbolic) in raw.items():
            key = name.casefold()
            if name == target:
                state = "TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED" if symbolic else "TARGET_REF_PRESENT"
                break
            if key == folded or key.startswith(folded + "/") or folded.startswith(key + "/"):
                state = "TARGET_REF_COLLISION"
        if state == "TARGET_REF_DEFINITELY_ABSENT" and (
                (root / ".git" / target).exists() or (root / ".git/logs" / target).exists()):
            state = "TARGET_REF_COLLISION"
        controls = []
        for relative, size, stamp, inode, attrs in before:
            if attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
                continue
            if (relative in {".git/HEAD", ".git/index", ".git/config", ".git/packed-refs"}
                    or relative.startswith((".git/refs/", ".git/logs/"))):
                controls.append((relative, hashlib.sha256(_read(root / relative)).hexdigest()))
        if _inventory(root, deadline) != before:
            raise OperationRejected("repository changed during observation")
        return RepositoryObservation(canonical({"root": str(root), "source_ref": intent.source_ref,
            "source_commit": asdict(intent.source_commit), "refs": raw, "inventory": before,
            "controls": controls}), state, tuple(commands))

    def launch(self, intent, permission):
        if type(permission) is not _LaunchPermission:
            raise OperationRejected("no private dispatch permission")
        permission.take(intent)
        return _capture(ACTION, intent, time.monotonic() + _PROCESS_SECONDS)

    def unchanged(self, before, after):
        return before.state_json == after.state_json and before.target_state == after.target_state

    def expected_creation(self, intent, before, after):
        if before.target_state != "TARGET_REF_DEFINITELY_ABSENT" or after.target_state != "TARGET_REF_PRESENT":
            return False
        a, b = json.loads(before.state_json), json.loads(after.state_json)
        expected = dict(a["refs"])
        expected[intent.target_ref] = [intent.source_commit.oid, ""]
        if b["refs"] != expected or any(a[k] != b[k] for k in ("root", "source_ref", "source_commit")):
            return False
        ref_path, log_path = ".git/" + intent.target_ref, ".git/logs/" + intent.target_ref
        allowed_files = {ref_path, log_path}
        parents = set()
        for relative in allowed_files:
            parts = relative.split("/")
            parents.update("/".join(parts[:n]) for n in range(1, len(parts)))
        old_inventory = {r[0]: r[1:] for r in a["inventory"]}
        new_inventory = {r[0]: r[1:] for r in b["inventory"]}
        for name in old_inventory.keys() | new_inventory.keys():
            if name in allowed_files:
                if name in old_inventory:
                    return False
                continue
            if name in parents:
                if name not in new_inventory or not new_inventory[name][-1] & stat.FILE_ATTRIBUTE_DIRECTORY:
                    return False
                if name in old_inventory and old_inventory[name][2:] != new_inventory[name][2:]:
                    return False
                continue
            if old_inventory.get(name) != new_inventory.get(name):
                return False
        old_controls = dict(a["controls"])
        new_controls = {k: v for k, v in b["controls"] if k not in allowed_files}
        if old_controls != new_controls:
            return False
        log = Path(intent.policy.project_root) / log_path
        if log.exists():
            contents = _read(log)
            prefix = ("0" * len(intent.source_commit.oid) + " " + intent.source_commit.oid + " ").encode()
            if not contents.startswith(prefix) or contents.count(b"\n") != 1:
                return False
            if hashlib.sha256(contents).hexdigest() != dict(b["controls"]).get(log_path):
                return False
        return True


def _capture(operation, intent, deadline) -> ProcessEvidence:
    """Private fixed Git operation transport; tests replace selection, not ownership."""
    argv = _argv(operation, intent)
    root = intent.policy.project_root
    may_have_executed = False
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
        may_have_executed = True
        if job.k.ResumeThread(ht) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
        _winapi.CloseHandle(ht)
        ht = None
        from .phase_branch_creation import _boundary
        if operation == ACTION:
            _boundary("C7")
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
    return ProcessEvidence(operation, argv, root, started, _now(), exit_code,
                              bytes(buffers[0]), bytes(buffers[1]), reason is Reason.OBSERVED,
                              cleanup, reason.value, primary_reason.value, may_have_executed)
