"""Gate 3 capability spike: a bounded Claude Code CLI adapter.

This adapter executes exactly one external `claude` CLI process and returns an
immutable result describing what was observed. It owns no canonical state: it
does not import from `tomtit.runtime` or `tomtit.domain`, appends no event,
persists no decision, and decides nothing about retry, completion, or failure
classification beyond describing the process outcome. Runtime or a future
Coordinator interprets the result; this module only produces it.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

ADAPTER_NAME = "claude-code-cli"
ADAPTER_VERSION = "0.1.0-gate3-spike"

DEFAULT_ENVIRONMENT_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "TERM",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY",
)

_SECRET_NAME_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)

_IGNORED_DIR_NAMES = frozenset({".git", "__pycache__"})
_IGNORED_FILE_NAMES = frozenset({".DS_Store"})
_IGNORED_SUFFIXES = frozenset({".pyc"})

_MAX_SNAPSHOT_FILE_BYTES = 1_000_000
_PROCESS_POLL_INTERVAL_SECONDS = 0.02
_PROCESS_WAIT_GRACE_SECONDS = 5
_PROCESS_CLEANUP_GRACE_SECONDS = 5.0
_READER_JOIN_TIMEOUT_SECONDS = 5.0

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class ClaudeCodeCLIStatus(str, Enum):
    """Closed outcome vocabulary. Only SUCCEEDED means the invocation is trustworthy."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNAVAILABLE = "UNAVAILABLE"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


class _ProcessGroupCleanup(str, Enum):
    """Outcome of bounded worker process-group cleanup.

    Only QUIESCENT and CLEANED permit a successful result: both mean no member
    of the worker group can still mutate the workspace. FAILED means quiescence
    could not be proven, so every snapshot-derived claim is untrustworthy.
    """

    QUIESCENT = "PROCESS_GROUP_QUIESCENT"
    CLEANED = "PROCESS_GROUP_CLEANED"
    FAILED = "PROCESS_GROUP_CLEANUP_FAILED"


@dataclasses.dataclass(frozen=True, slots=True)
class ClaudeCodeCLIRequest:
    """A bounded, immutable invocation request.

    Carries no Runtime-authority field and no full canonical record: only the
    identifiers needed to correlate a result with its caller, and the bounded
    execution parameters the adapter needs to run one process safely.
    """

    task_id: str
    run_id: str
    attempt: int
    goal: str
    prompt: str
    working_directory: Path
    timeout_seconds: int
    allowed_changed_paths: tuple[str, ...]
    environment_allowlist: tuple[str, ...] = DEFAULT_ENVIRONMENT_ALLOWLIST
    max_stdout_bytes: int = 65536
    max_stderr_bytes: int = 65536
    model: str | None = None
    permission_mode: str = "acceptEdits"

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if not self.goal:
            raise ValueError("goal must be non-empty")
        if not self.prompt:
            raise ValueError("prompt must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_stdout_bytes <= 0:
            raise ValueError("max_stdout_bytes must be positive")
        if self.max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")
        object.__setattr__(self, "working_directory", Path(self.working_directory))
        object.__setattr__(self, "allowed_changed_paths", tuple(self.allowed_changed_paths))
        object.__setattr__(self, "environment_allowlist", tuple(self.environment_allowlist))


@dataclasses.dataclass(frozen=True, slots=True)
class ClaudeCodeCLIResult:
    """An immutable observation of one Claude Code CLI invocation.

    This is data only. Nothing about it writes canonical state; the caller
    decides what the result means.
    """

    adapter_name: str
    adapter_version: str
    task_id: str
    run_id: str
    attempt: int
    status: ClaudeCodeCLIStatus
    success: bool
    executable_path: str | None
    sanitized_argv: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    exit_code: int | None
    timed_out: bool
    stdout_excerpt: str
    stderr_excerpt: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    workspace_before_hash: str | None
    workspace_after_hash: str | None
    changed_paths: tuple[str, ...]
    unexpected_changed_paths: tuple[str, ...]
    patch_path: str | None
    evidence_manifest_path: str | None
    error_category: str | None
    error_message: str | None


def discover_claude_executable(explicit_path: str | None = None) -> str | None:
    """Locate the `claude` executable without shell interpolation.

    This is the single validation boundary for executables: an explicit
    override is honored only if it resolves to an executable regular file, so
    a caller-supplied path can never reach `subprocess.Popen` unchecked. A
    relative override is resolved explicitly rather than left ambiguous.
    Otherwise discovery uses `shutil.which`, which never invokes a shell.
    """

    if explicit_path:
        candidate = Path(explicit_path)
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            return None
        return None
    return shutil.which("claude")


def build_child_environment(allowlist: Sequence[str]) -> dict[str, str]:
    """Build a child environment containing only allowlisted, present variables."""

    return {name: os.environ[name] for name in allowlist if name in os.environ}


def _collect_secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    values = {value for name, value in env.items() if value and _SECRET_NAME_PATTERN.search(name)}
    # Longest first, so a short secret cannot mask part of a longer one during replacement.
    return tuple(sorted(values, key=len, reverse=True))


def _redact(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for value in secret_values:
        redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _redact_home(text: str, home: str | None) -> str:
    if not home or len(home) < 4:
        return text
    return text.replace(home, "$HOME")


def _iter_workspace_relative_paths(root: Path) -> Iterator[str]:
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if path.name in _IGNORED_FILE_NAMES:
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        yield rel.as_posix()


def snapshot_workspace_hashes(root: Path) -> dict[str, str]:
    """Hash every tracked-relevant file, for change detection and scope proof."""

    hashes: dict[str, str] = {}
    for rel in _iter_workspace_relative_paths(root):
        try:
            hashes[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        except OSError:
            hashes[rel] = "unreadable"
    return hashes


def workspace_hash(file_hashes: Mapping[str, str]) -> str:
    """A single deterministic hash summarizing an entire workspace snapshot."""

    canonical = json.dumps(dict(sorted(file_hashes.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_workspace_contents(
    root: Path,
    *,
    max_file_bytes: int = _MAX_SNAPSHOT_FILE_BYTES,
) -> dict[str, bytes | None]:
    """Read file contents for later patch rendering. `None` marks an omitted file."""

    contents: dict[str, bytes | None] = {}
    for rel in _iter_workspace_relative_paths(root):
        path = root / rel
        try:
            if path.stat().st_size > max_file_bytes:
                contents[rel] = None
            else:
                contents[rel] = path.read_bytes()
        except OSError:
            contents[rel] = None
    return contents


def _diff_changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    keys = set(before) | set(after)
    return tuple(sorted(path for path in keys if before.get(path) != after.get(path)))


def _decode_lines(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def render_unified_diff(
    before_contents: Mapping[str, bytes | None],
    after_contents: Mapping[str, bytes | None],
    changed_paths: Sequence[str],
) -> str:
    """A bounded unified diff over exactly the changed paths. Git-independent."""

    sections: list[str] = []
    for rel_path in changed_paths:
        before_present = rel_path in before_contents
        after_present = rel_path in after_contents
        before_bytes = before_contents.get(rel_path)
        after_bytes = after_contents.get(rel_path)

        omitted = (before_present and before_bytes is None) or (after_present and after_bytes is None)
        if omitted:
            sections.append(
                f"--- a/{rel_path}\n+++ b/{rel_path}\n"
                "@@ content omitted: file exceeds the diff size bound or was unreadable @@\n"
            )
            continue

        before_lines = _decode_lines(before_bytes) if before_present and before_bytes is not None else []
        after_lines = _decode_lines(after_bytes) if after_present and after_bytes is not None else []
        from_label = f"a/{rel_path}" if before_present else "/dev/null"
        to_label = f"b/{rel_path}" if after_present else "/dev/null"
        diff = list(
            difflib.unified_diff(before_lines, after_lines, fromfile=from_label, tofile=to_label, lineterm="")
        )
        if diff:
            sections.append("\n".join(diff) + "\n")
    return "".join(sections)


def build_evidence_manifest(result: ClaudeCodeCLIResult) -> dict[str, object]:
    """The plain-dict form of a result, ready for deterministic JSON rendering."""

    return {
        "adapter_name": result.adapter_name,
        "adapter_version": result.adapter_version,
        "task_id": result.task_id,
        "run_id": result.run_id,
        "attempt": result.attempt,
        "status": result.status.value,
        "success": result.success,
        "executable_path": result.executable_path,
        "sanitized_argv": list(result.sanitized_argv),
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stdout_excerpt": result.stdout_excerpt,
        "stderr_excerpt": result.stderr_excerpt,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "workspace_before_hash": result.workspace_before_hash,
        "workspace_after_hash": result.workspace_after_hash,
        "changed_paths": list(result.changed_paths),
        "unexpected_changed_paths": list(result.unexpected_changed_paths),
        "patch_path": result.patch_path,
        "evidence_manifest_path": result.evidence_manifest_path,
        "error_category": result.error_category,
        "error_message": result.error_message,
    }


def render_evidence_manifest_json(result: ClaudeCodeCLIResult) -> str:
    """Stable, sorted-key JSON so the same result always renders identically."""

    manifest = build_evidence_manifest(result)
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_evidence(
    result: ClaudeCodeCLIResult,
    *,
    before_contents: Mapping[str, bytes | None],
    after_contents: Mapping[str, bytes | None],
    output_dir: Path,
) -> ClaudeCodeCLIResult:
    """Persist a sanitized patch and manifest, returning the result with their paths filled in."""

    output_dir.mkdir(parents=True, exist_ok=True)
    patch_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_DIFF.patch"
    patch_path.write_text(
        render_unified_diff(before_contents, after_contents, result.changed_paths),
        encoding="utf-8",
    )

    manifest_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_RESULT.json"
    finalized = dataclasses.replace(
        result,
        patch_path=str(patch_path),
        evidence_manifest_path=str(manifest_path),
    )
    manifest_path.write_text(render_evidence_manifest_json(finalized), encoding="utf-8")
    return finalized


class _StreamReader(threading.Thread):
    """Drains a pipe into a bounded buffer while hashing the full stream.

    The hash covers everything read, even past the excerpt bound, so evidence
    proves what was actually produced rather than just what was retained.
    """

    def __init__(self, stream: object, max_bytes: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._max_bytes = max_bytes
        self._hasher = hashlib.sha256()
        self.buffer = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(4096)  # type: ignore[attr-defined]
                if not chunk:
                    break
                self._hasher.update(chunk)
                if len(self.buffer) < self._max_bytes:
                    remaining = self._max_bytes - len(self.buffer)
                    self.buffer.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.truncated = True
                else:
                    self.truncated = True
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()  # type: ignore[attr-defined]
            except OSError:
                pass

    @property
    def digest(self) -> str:
        return self._hasher.hexdigest()


def _signal_process_group(pgid: int, sig: int) -> bool:
    """Signal an entire worker process group.

    Returns True when the signal was delivered or the group was already gone,
    and False when the group exists but cannot be safely controlled. Only the
    group created by this adapter's own `start_new_session=True` child is ever
    signalled; no unrelated system process is scanned or targeted.
    """

    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _process_group_alive(pgid: int) -> bool | None:
    """Probe group membership: True alive, False absent, None indeterminate.

    A POSIX process group exists exactly while it still has a member, so a
    signal-0 probe answers "can anything in this invocation still run?".
    `PermissionError` means the group exists but is not ours to control, which
    is treated as indeterminate rather than quiescent.
    """

    if os.name != "posix":
        return None
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return None
    return True


def _await_process_group_absence(pgid: int, *, deadline: float) -> bool | None:
    """Poll until the group is absent or the bounded monotonic deadline passes."""

    while True:
        alive = _process_group_alive(pgid)
        if alive is not True:
            return alive
        if time.monotonic() >= deadline:
            return True
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)


def ensure_process_group_quiescent(
    pgid: int,
    *,
    grace_seconds: float | None = None,
) -> _ProcessGroupCleanup:
    """Prove the worker group is finished, terminating survivors if necessary.

    Escalates SIGTERM, bounded grace, then SIGKILL, and verifies absence after
    each step. Every wait is bounded by a monotonic deadline. Anything that
    cannot be positively verified — an indeterminate probe, a group that
    outlives SIGKILL, or a platform without process-group verification — fails
    closed as FAILED rather than being reported as quiescent.
    """

    if grace_seconds is None:
        grace_seconds = _PROCESS_CLEANUP_GRACE_SECONDS

    if os.name != "posix":
        return _ProcessGroupCleanup.FAILED
    if pgid <= 1:
        return _ProcessGroupCleanup.FAILED
    try:
        if pgid == os.getpgrp():
            # Never signal TOMTIT's own process group.
            return _ProcessGroupCleanup.FAILED
    except OSError:
        return _ProcessGroupCleanup.FAILED

    alive = _process_group_alive(pgid)
    if alive is False:
        return _ProcessGroupCleanup.QUIESCENT
    if alive is None:
        return _ProcessGroupCleanup.FAILED

    if not _signal_process_group(pgid, signal.SIGTERM):
        return _ProcessGroupCleanup.FAILED
    alive = _await_process_group_absence(pgid, deadline=time.monotonic() + grace_seconds)
    if alive is False:
        return _ProcessGroupCleanup.CLEANED
    if alive is None:
        return _ProcessGroupCleanup.FAILED

    if not _signal_process_group(pgid, signal.SIGKILL):
        return _ProcessGroupCleanup.FAILED
    alive = _await_process_group_absence(pgid, deadline=time.monotonic() + grace_seconds)
    if alive is False:
        return _ProcessGroupCleanup.CLEANED
    return _ProcessGroupCleanup.FAILED


@dataclasses.dataclass(frozen=True, slots=True)
class _ExecutionOutcome:
    """Frozen observation of one subprocess, captured after group quiescence.

    Stream evidence is copied out of the reader buffers only once the readers
    have finished, so no live thread can mutate what the result reports.
    """

    exit_code: int | None
    timed_out: bool
    stdout_bytes: bytes
    stderr_bytes: bytes
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    cleanup: _ProcessGroupCleanup
    readers_alive: bool
    start_failure: tuple[ClaudeCodeCLIStatus, str, str] | None = None


def _start_failure_outcome(
    status: ClaudeCodeCLIStatus,
    error_category: str,
    error_message: str,
) -> _ExecutionOutcome:
    """A normalized outcome for a process that never started. Nothing to clean up."""

    return _ExecutionOutcome(
        exit_code=None,
        timed_out=False,
        stdout_bytes=b"",
        stderr_bytes=b"",
        stdout_sha256=_EMPTY_SHA256,
        stderr_sha256=_EMPTY_SHA256,
        stdout_truncated=False,
        stderr_truncated=False,
        cleanup=_ProcessGroupCleanup.QUIESCENT,
        readers_alive=False,
        start_failure=(status, error_category, error_message),
    )


def _execute(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> _ExecutionOutcome:
    """Run one bounded subprocess. No shell, no retry, no background survivor.

    Returns only after the entire worker process group is proven quiescent (or
    cleanup is reported as failed) and both readers have finished, so a caller
    that snapshots the workspace afterwards cannot be raced by a descendant.
    """

    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # POSIX: proc.pid becomes the new process group id.
        )
    except FileNotFoundError as exc:
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.UNAVAILABLE,
            "EXECUTABLE_UNAVAILABLE",
            f"executable could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )
    except PermissionError as exc:
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.UNAVAILABLE,
            "EXECUTABLE_NOT_EXECUTABLE",
            f"executable could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )
    except OSError as exc:
        # Deliberately excludes str(exc): the message stays a fixed shape so no
        # environment-derived text can reach persisted evidence.
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.FAILED,
            "PROCESS_START_FAILED",
            f"process could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )

    pgid = proc.pid
    stdout_reader = _StreamReader(proc.stdout, max_stdout_bytes)
    stderr_reader = _StreamReader(proc.stderr, max_stderr_bytes)
    stdout_reader.start()
    stderr_reader.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)

    if timed_out:
        # Stop the group first, then reap the direct parent so its zombie
        # cannot keep the group alive for the quiescence probe below.
        if not _signal_process_group(pgid, signal.SIGTERM):
            proc.terminate()
        try:
            proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            if not _signal_process_group(pgid, signal.SIGKILL):
                proc.kill()
            try:
                proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass

    # Every return path, normal exit included: a descendant that outlives its
    # parent still belongs to this invocation and must not be able to write
    # after the result is formed.
    cleanup = ensure_process_group_quiescent(pgid)

    # Only now can the pipes reach EOF, because the group holding their write
    # ends is gone. Joining before cleanup is what let readers survive.
    join_timeout = _READER_JOIN_TIMEOUT_SECONDS
    stdout_reader.join(timeout=join_timeout)
    stderr_reader.join(timeout=join_timeout)
    readers_alive = stdout_reader.is_alive() or stderr_reader.is_alive()

    return _ExecutionOutcome(
        exit_code=proc.returncode,
        timed_out=timed_out,
        stdout_bytes=bytes(stdout_reader.buffer),
        stderr_bytes=bytes(stderr_reader.buffer),
        stdout_sha256=stdout_reader.digest,
        stderr_sha256=stderr_reader.digest,
        stdout_truncated=stdout_reader.truncated,
        stderr_truncated=stderr_reader.truncated,
        cleanup=cleanup,
        readers_alive=readers_alive,
    )


def _determine_status(
    *,
    exit_code: int | None,
    timed_out: bool,
    unexpected_changed_paths: Sequence[str],
    expected_changed: bool,
    cleanup: _ProcessGroupCleanup = _ProcessGroupCleanup.QUIESCENT,
    readers_alive: bool = False,
) -> tuple[ClaudeCodeCLIStatus, bool, str | None, str | None]:
    # Fail closed before anything else. If the worker group or its readers
    # cannot be proven finished, the workspace snapshot behind every check
    # below may already be stale, so no other classification can be reported
    # as though it were reliable. The observed paths are still returned on the
    # result, so this ordering hides no data — only the trust claim changes.
    if cleanup is _ProcessGroupCleanup.FAILED:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "PROCESS_CLEANUP_FAILED",
            "worker process group could not be proven quiescent before evidence capture",
        )
    if readers_alive:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "PROCESS_CLEANUP_FAILED",
            "a worker output reader was still running after process cleanup",
        )
    # An unexpected write is a safety-relevant condition, checked next: even a
    # timed-out or nonzero-exit process may already have written outside scope.
    if unexpected_changed_paths:
        return (
            ClaudeCodeCLIStatus.SCOPE_VIOLATION,
            False,
            "UNEXPECTED_CHANGED_PATHS",
            f"changed paths outside the allowed set: {list(unexpected_changed_paths)}",
        )
    if timed_out:
        return (
            ClaudeCodeCLIStatus.TIMED_OUT,
            False,
            "TIMEOUT",
            "process exceeded the configured timeout and was terminated",
        )
    if exit_code != 0:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "NONZERO_EXIT",
            f"process exited with code {exit_code}",
        )
    if not expected_changed:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "NO_EXPECTED_CHANGE",
            "process exited 0 but no allowed path was changed",
        )
    return ClaudeCodeCLIStatus.SUCCEEDED, True, None, None


class ClaudeCodeCLIAdapter:
    """Executes one Claude Code CLI process and returns an immutable result.

    This class owns no canonical state. It does not import Runtime or domain
    modules, and `run()` never mutates anything outside the child process's own
    workspace and the value it returns.
    """

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def run(
        self,
        request: ClaudeCodeCLIRequest,
        *,
        executable_path: str | None = None,
    ) -> ClaudeCodeCLIResult:
        started_at = datetime.now(timezone.utc)
        start_monotonic = time.monotonic()

        working_directory = request.working_directory
        if not working_directory.is_absolute():
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category="INVALID_WORKSPACE",
                error_message="working_directory must be an absolute path",
            )
        if not working_directory.exists() or not working_directory.is_dir():
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category="INVALID_WORKSPACE",
                error_message="working_directory does not exist or is not a directory",
            )

        # Explicit overrides go through the same validation boundary as
        # discovery; an unchecked caller path must never reach Popen.
        resolved_executable = discover_claude_executable(executable_path)
        if resolved_executable is None:
            explicit = bool(executable_path)
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.UNAVAILABLE,
                error_category="EXECUTABLE_UNAVAILABLE" if explicit else "CLI_NOT_FOUND",
                error_message=(
                    "explicit executable_path is not an executable regular file"
                    if explicit
                    else "claude executable was not discovered"
                ),
            )

        child_env = build_child_environment(request.environment_allowlist)
        secret_values = _collect_secret_values(child_env)
        home_value = child_env.get("HOME")

        before_hashes = snapshot_workspace_hashes(working_directory)
        argv = self._build_argv(resolved_executable, request)

        outcome = _execute(
            argv,
            cwd=working_directory,
            env=child_env,
            timeout_seconds=request.timeout_seconds,
            max_stdout_bytes=request.max_stdout_bytes,
            max_stderr_bytes=request.max_stderr_bytes,
        )

        # Ordering invariant: `_execute` has already proven the worker group
        # quiescent and joined both readers, so this snapshot cannot be raced
        # by a descendant of the invocation it is meant to describe.
        after_hashes = snapshot_workspace_hashes(working_directory)
        changed_paths = _diff_changed_paths(before_hashes, after_hashes)
        allowed = set(request.allowed_changed_paths)
        unexpected_changed_paths = tuple(path for path in changed_paths if path not in allowed)
        expected_changed = bool(set(changed_paths) & allowed)

        status: ClaudeCodeCLIStatus
        success: bool
        error_category: str | None
        error_message: str | None
        if outcome.start_failure is not None:
            status, error_category, error_message = outcome.start_failure
            success = False
        else:
            status, success, error_category, error_message = _determine_status(
                exit_code=outcome.exit_code,
                timed_out=outcome.timed_out,
                unexpected_changed_paths=unexpected_changed_paths,
                expected_changed=expected_changed,
                cleanup=outcome.cleanup,
                readers_alive=outcome.readers_alive,
            )

        stdout_text = _redact_home(
            _redact(outcome.stdout_bytes.decode("utf-8", errors="replace"), secret_values),
            home_value,
        )
        stderr_text = _redact_home(
            _redact(outcome.stderr_bytes.decode("utf-8", errors="replace"), secret_values),
            home_value,
        )
        sanitized_argv = tuple(_redact_home(_redact(arg, secret_values), home_value) for arg in argv)

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)

        return ClaudeCodeCLIResult(
            adapter_name=self.name,
            adapter_version=self.version,
            task_id=request.task_id,
            run_id=request.run_id,
            attempt=request.attempt,
            status=status,
            success=success,
            executable_path=resolved_executable,
            sanitized_argv=sanitized_argv,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            stdout_sha256=outcome.stdout_sha256,
            stderr_sha256=outcome.stderr_sha256,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            workspace_before_hash=workspace_hash(before_hashes),
            workspace_after_hash=workspace_hash(after_hashes),
            changed_paths=changed_paths,
            unexpected_changed_paths=unexpected_changed_paths,
            patch_path=None,
            evidence_manifest_path=None,
            error_category=error_category,
            error_message=error_message,
        )

    def _build_argv(self, executable: str, request: ClaudeCodeCLIRequest) -> list[str]:
        argv = [
            executable,
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            request.permission_mode,
        ]
        if request.model:
            argv.extend(["--model", request.model])
        argv.append(request.prompt)
        return argv

    def _early_result(
        self,
        request: ClaudeCodeCLIRequest,
        started_at: datetime,
        start_monotonic: float,
        *,
        status: ClaudeCodeCLIStatus,
        error_category: str,
        error_message: str,
        executable_path: str | None = None,
    ) -> ClaudeCodeCLIResult:
        completed_at = datetime.now(timezone.utc)
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        empty_digest = _EMPTY_SHA256
        return ClaudeCodeCLIResult(
            adapter_name=self.name,
            adapter_version=self.version,
            task_id=request.task_id,
            run_id=request.run_id,
            attempt=request.attempt,
            status=status,
            success=False,
            executable_path=executable_path,
            sanitized_argv=(),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            exit_code=None,
            timed_out=False,
            stdout_excerpt="",
            stderr_excerpt="",
            stdout_sha256=empty_digest,
            stderr_sha256=empty_digest,
            stdout_truncated=False,
            stderr_truncated=False,
            workspace_before_hash=None,
            workspace_after_hash=None,
            changed_paths=(),
            unexpected_changed_paths=(),
            patch_path=None,
            evidence_manifest_path=None,
            error_category=error_category,
            error_message=error_message,
        )
