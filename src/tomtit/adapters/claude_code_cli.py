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


class ClaudeCodeCLIStatus(str, Enum):
    """Closed outcome vocabulary. Only SUCCEEDED means the invocation is trustworthy."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNAVAILABLE = "UNAVAILABLE"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


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

    An explicit absolute path is honored only if it is an executable file.
    Otherwise discovery uses `shutil.which`, which never invokes a shell.
    """

    if explicit_path:
        candidate = Path(explicit_path)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
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


def _terminate_process_group(proc: subprocess.Popen) -> None:
    if os.name != "posix":
        proc.terminate()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_process_group(proc: subprocess.Popen) -> None:
    if os.name != "posix":
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _execute(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> tuple[int | None, bool, _StreamReader, _StreamReader]:
    """Run one bounded subprocess. No shell, no retry, no background survivor."""

    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # POSIX: proc.pid becomes the new process group id.
    )
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
        _terminate_process_group(proc)
        try:
            proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass

    stdout_reader.join(timeout=_PROCESS_WAIT_GRACE_SECONDS)
    stderr_reader.join(timeout=_PROCESS_WAIT_GRACE_SECONDS)

    return proc.returncode, timed_out, stdout_reader, stderr_reader


def _determine_status(
    *,
    exit_code: int | None,
    timed_out: bool,
    unexpected_changed_paths: Sequence[str],
    expected_changed: bool,
) -> tuple[ClaudeCodeCLIStatus, bool, str | None, str | None]:
    # An unexpected write is a safety-relevant condition, checked first: even a
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

        resolved_executable = executable_path or discover_claude_executable()
        if resolved_executable is None:
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.UNAVAILABLE,
                error_category="CLI_NOT_FOUND",
                error_message="claude executable was not discovered",
            )

        child_env = build_child_environment(request.environment_allowlist)
        secret_values = _collect_secret_values(child_env)
        home_value = child_env.get("HOME")

        before_hashes = snapshot_workspace_hashes(working_directory)
        argv = self._build_argv(resolved_executable, request)

        exit_code, timed_out, stdout_reader, stderr_reader = _execute(
            argv,
            cwd=working_directory,
            env=child_env,
            timeout_seconds=request.timeout_seconds,
            max_stdout_bytes=request.max_stdout_bytes,
            max_stderr_bytes=request.max_stderr_bytes,
        )

        after_hashes = snapshot_workspace_hashes(working_directory)
        changed_paths = _diff_changed_paths(before_hashes, after_hashes)
        allowed = set(request.allowed_changed_paths)
        unexpected_changed_paths = tuple(path for path in changed_paths if path not in allowed)
        expected_changed = bool(set(changed_paths) & allowed)

        status, success, error_category, error_message = _determine_status(
            exit_code=exit_code,
            timed_out=timed_out,
            unexpected_changed_paths=unexpected_changed_paths,
            expected_changed=expected_changed,
        )

        stdout_text = _redact_home(
            _redact(bytes(stdout_reader.buffer).decode("utf-8", errors="replace"), secret_values),
            home_value,
        )
        stderr_text = _redact_home(
            _redact(bytes(stderr_reader.buffer).decode("utf-8", errors="replace"), secret_values),
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
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            stdout_sha256=stdout_reader.digest,
            stderr_sha256=stderr_reader.digest,
            stdout_truncated=stdout_reader.truncated,
            stderr_truncated=stderr_reader.truncated,
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
        empty_digest = hashlib.sha256(b"").hexdigest()
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
