#!/usr/bin/env python3
"""Local-only, one-shot manual dogfood Codex review runner.

Reviews the output of `.tomtit/dogfood/run_task.py` (a Claude Code
implementation run against an external workspace) using Codex as an
independent, read-only reviewer. This is not a product CLI, not Runtime
integration, and not an automatic-repair loop: it makes exactly one Codex
invocation, never modifies the target workspace, and never reinterprets a
failed or ambiguous review as approval.

Owner rerun example:

    python3 .tomtit/dogfood/run_review.py \\
      --workspace /absolute/path/to/target-repository \\
      --task-prompt-file .tomtit/dogfood/tasks/task.md \\
      --implementation-run-directory .tomtit/dogfood/runs/<implementation-run-id> \\
      --baseline-ref HEAD \\
      --allowed-path app/page.tsx \\
      --check-command "npm run lint" \\
      --check-command "npm run build" \\
      --timeout-seconds 900
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tomtit.adapters.claude_code_cli import classify_workspace_changes  # noqa: E402

_MAX_EXCERPT_BYTES = 4000
_HELP_PROBE_TIMEOUT_SECONDS = 15
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_PROCESS_CLEANUP_GRACE_SECONDS = 5.0
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

VALID_VERDICTS = ("APPROVE", "REQUEST_CHANGES", "BLOCKED")
VALID_SEVERITIES = ("HIGH", "MEDIUM", "LOW")

# Status strings a prior "implementation attempt" evidence directory may
# legitimately report. "SUCCEEDED" comes from run_task.py; "REPAIR_ATTEMPT_
# COMPLETED" comes from run_repair.py's deliberately distinct status (see
# load_implementation_evidence() below for why both are accepted here).
_ACCEPTABLE_IMPLEMENTATION_STATUSES = ("SUCCEEDED", "REPAIR_ATTEMPT_COMPLETED")

_REQUIRED_VERDICT_KEYS = {
    "verdict",
    "summary",
    "goal_satisfied",
    "scope_compliant",
    "checks_satisfied",
    "findings",
    "residual_risks",
}
_REQUIRED_FINDING_KEYS = {"id", "severity", "title", "evidence", "required_change"}

# Command substitution/backtick/newline smuggling is rejected regardless of
# quoting position -- these are never legitimate inside a single check-command
# argument. Standalone shell-operator *tokens* (checked after shlex.split, in
# _FORBIDDEN_STANDALONE_TOKENS below) are rejected only when they appear as a
# whole token, not merely as a substring inside a quoted argument -- otherwise
# an entirely ordinary command like `python3 -c "import sys; sys.exit(1)"`
# would be wrongly refused for containing a literal `;` inside its own quoted
# argument, even though shlex.split keeps that argument as one token and
# shell=False means it is never given operator meaning either way.
_RAW_FORBIDDEN_CHECK_COMMAND_SUBSTRINGS = ("$(", "`", "\n")
_FORBIDDEN_STANDALONE_CHECK_COMMAND_TOKENS = {";", "&&", "||", "|", ">", "<", ">>", "<<", "&"}

_GIT_SPECIAL_STATE_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")

# Candidate flag/word tokens searched for in the *actual* installed CLI's own
# --help output. Nothing here is assumed to exist; each capability is only
# considered present if evidence for it is literally found in the probed help
# text. See detect_codex_capabilities()/resolve_approval_flag().
#
# `codex --help` and `codex exec --help` are parsed *separately* and never
# conflated: the real installed CLI exposes `--ask-for-approval`/`-a` only as
# a global flag (documented in `codex --help`), not repeated in
# `codex exec --help`. Requiring evidence in the exec-scoped help text alone
# was the original defect -- a real, capable CLI was rejected as
# UNSUPPORTED_CODEX_CLI_CONTRACT because the flag it actually documents
# globally was never looked for there. `resolve_approval_flag()` now searches
# global help first (preferred, matches the discovered real contract) and
# falls back to exec-scoped help only for CLI versions that document it
# there instead; either location is accepted as evidence, but neither is
# assumed without being found.
#
# `sandbox_flag` (whether an explicit `--sandbox` flag exists at all) is
# deliberately *not* in `_REQUIRED_CAPABILITIES`: `codex exec` may default to
# a read-only sandbox without ever exposing an explicit flag, in which case
# `read_only_mode` (a documented read-only mode or default, found anywhere in
# either help text) is the only evidence required. `sandbox_flag` is still
# recorded and used only to decide whether the invocation should *explicitly*
# request `--sandbox read-only`, per the requirement to keep requesting
# explicit read-only mode whenever the CLI supports being asked for it.
_REQUIRED_CAPABILITIES = (
    "exec_subcommand",
    "read_only_mode",
    "working_directory_flag",
    "isolated_final_message",
    "non_interactive_approval",
)

# Never constructed by this runner under any discovered contract; checked
# defensively in build_codex_argv() so read-only-only stays a hard guarantee,
# not merely an intent.
_FORBIDDEN_ARGV_TOKENS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "danger-full-access",
    "workspace-write",
)


class PreflightFailure(Exception):
    """A configuration or precondition failure discovered before Codex runs.

    Maps to exit code 3. Nothing has been invoked yet when this is raised.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class ReviewFailure(Exception):
    """A failure discovered during or after the Codex invocation itself.

    Maps to exit code 4: process failure, timeout, output-parsing failure, or
    a proven workspace mutation. Never used to smuggle a rejected verdict
    through as a lesser outcome -- these are all "the review did not produce
    a trustworthy result", distinct from a valid REQUEST_CHANGES/BLOCKED.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


# ---------------------------------------------------------------------------
# Small local helpers.
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _excerpt(data: bytes, *, limit: int = _MAX_EXCERPT_BYTES) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) <= limit:
        return text
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "...(truncated)"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Allowed-path validation. Deliberately the same rules as run_task.py: exact,
# relative, POSIX, no traversal, no Git metadata, deterministic dedup.
# ---------------------------------------------------------------------------


def normalize_allowed_paths(raw_paths: list[str], workspace: Path) -> tuple[str, ...]:
    if not raw_paths:
        raise PreflightFailure("NO_ALLOWED_PATHS", "at least one exact allowed path is required")
    normalized: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path or "\\" in raw_path:
            raise PreflightFailure("INVALID_ALLOWED_PATH", "allowed paths must be non-empty POSIX relative paths")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute() or raw_path == "." or ".." in candidate.parts:
            raise PreflightFailure("INVALID_ALLOWED_PATH", "allowed paths must be safe relative paths")
        value = candidate.as_posix()
        if value in ("", ".") or value == ".git" or value.startswith(".git/"):
            raise PreflightFailure("INVALID_ALLOWED_PATH", "allowed paths may not target Git metadata")
        resolved = (workspace / Path(*candidate.parts)).resolve(strict=False)
        if not _is_within(resolved, workspace):
            raise PreflightFailure("INVALID_ALLOWED_PATH", "allowed path resolves outside the workspace")
        normalized.add(value)
    return tuple(sorted(normalized))


# ---------------------------------------------------------------------------
# Git helpers. Every call is a fixed argv list; no shell, no interpolation.
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path, *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def git_rev_parse(cwd: Path, ref: str) -> str | None:
    completed = _run_git(["rev-parse", "--verify", ref], cwd)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_dir(cwd: Path) -> Path | None:
    completed = _run_git(["rev-parse", "--git-dir"], cwd)
    if completed.returncode != 0:
        return None
    raw = Path(completed.stdout.strip())
    return raw if raw.is_absolute() else (cwd / raw).resolve()


def git_special_state(cwd: Path) -> str | None:
    directory = git_dir(cwd)
    if directory is None:
        return None
    for marker in _GIT_SPECIAL_STATE_MARKERS:
        if (directory / marker).exists():
            return marker
    return None


def git_index_clean(cwd: Path) -> bool:
    completed = _run_git(["diff", "--cached", "--quiet"], cwd)
    return completed.returncode == 0


def git_status_porcelain(cwd: Path) -> str:
    completed = _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd)
    return completed.stdout


def _parse_status_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if not line:
            continue
        # Porcelain v1: two status chars, one space, then the path (or
        # "old -> new" for renames, where only the new path currently exists).
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        paths.append(raw_path.strip('"'))
    return paths


def git_changed_paths(cwd: Path, baseline_commit: str) -> list[str]:
    """Union of (a) files differing from the baseline commit and (b) untracked files."""

    tracked = _run_git(["diff", "--name-only", baseline_commit], cwd)
    tracked_paths = [line for line in tracked.stdout.splitlines() if line]
    status_paths = _parse_status_paths(git_status_porcelain(cwd))
    return sorted(set(tracked_paths) | set(status_paths))


def git_tracked_diff(cwd: Path, baseline_commit: str, paths: list[str]) -> str:
    if not paths:
        return ""
    completed = _run_git(["diff", baseline_commit, "--", *paths], cwd, timeout=60.0)
    return completed.stdout


# ---------------------------------------------------------------------------
# Workspace immutability snapshot.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    head: str
    index_clean: bool
    status_porcelain: str
    changed_paths: tuple[str, ...]
    file_hashes: tuple[tuple[str, str | None], ...]  # path -> sha256, or None if absent/deleted


def snapshot_workspace(workspace: Path, baseline_commit: str) -> WorkspaceSnapshot:
    head = git_rev_parse(workspace, "HEAD") or ""
    changed = git_changed_paths(workspace, baseline_commit)
    hashes: list[tuple[str, str | None]] = []
    for rel in changed:
        full = workspace / rel
        try:
            if full.is_file():
                hashes.append((rel, _sha256(full.read_bytes())))
            else:
                hashes.append((rel, None))
        except OSError:
            hashes.append((rel, None))
    return WorkspaceSnapshot(
        head=head,
        index_clean=git_index_clean(workspace),
        status_porcelain=git_status_porcelain(workspace),
        changed_paths=tuple(changed),
        file_hashes=tuple(sorted(hashes)),
    )


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[str]:
    """Paths whose presence/content differs, plus a synthetic "HEAD" marker if HEAD moved."""

    modified: set[str] = set()
    before_map = dict(before.file_hashes)
    after_map = dict(after.file_hashes)
    for path in set(before_map) | set(after_map):
        if before_map.get(path) != after_map.get(path):
            modified.add(path)
    if before.head != after.head:
        modified.add("<HEAD>")
    return sorted(modified)


# ---------------------------------------------------------------------------
# Implementation evidence loading (the contract produced by run_task.py).
# ---------------------------------------------------------------------------


def load_implementation_evidence(
    run_directory: Path,
    workspace: Path,
    expected_implementation_run_id: str | None = None,
    expected_implementation_attempt: int | None = None,
) -> dict[str, Any]:
    if not run_directory.is_dir():
        raise PreflightFailure("MISSING_IMPLEMENTATION_EVIDENCE", "implementation run directory does not exist")

    receipt_path = run_directory / "receipt.json"
    result_path = run_directory / "result.json"
    patch_path = run_directory / "diff.patch"
    for path in (receipt_path, result_path, patch_path):
        if not path.is_file():
            raise PreflightFailure("MISSING_IMPLEMENTATION_EVIDENCE", f"required evidence file missing: {path.name}")

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE", f"evidence file is not readable/valid JSON: {exc.__class__.__name__}"
        ) from None

    try:
        patch_text = patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE", f"diff.patch is not readable: {exc.__class__.__name__}"
        ) from None

    required_receipt_keys = {"status", "success", "unexpected_changed_paths", "changed_paths", "workspace"}
    if not isinstance(receipt, dict) or not required_receipt_keys.issubset(receipt.keys()):
        raise PreflightFailure("INVALID_IMPLEMENTATION_EVIDENCE", "receipt.json is missing required fields")

    # Accepts evidence from either run_task.py ("SUCCEEDED", mirroring the
    # adapter's own ClaudeCodeCLIStatus) or run_repair.py ("REPAIR_ATTEMPT_
    # COMPLETED", which deliberately never claims "SUCCEEDED" so a repair
    # receipt can never be mistaken for an independently-approved result --
    # see REPAIR_RUNNER.md). Both still require success=True and no
    # unexpected changed paths below; only the accepted status *string* is
    # broadened, not the underlying safety check.
    if receipt.get("status") not in _ACCEPTABLE_IMPLEMENTATION_STATUSES or receipt.get("success") is not True:
        raise PreflightFailure(
            "IMPLEMENTATION_NOT_SUCCESSFUL",
            f"implementation receipt reports status={receipt.get('status')!r} success={receipt.get('success')!r}",
        )
    if receipt.get("unexpected_changed_paths"):
        raise PreflightFailure(
            "IMPLEMENTATION_SCOPE_VIOLATION",
            f"implementation receipt reports unexpected changed paths: {receipt.get('unexpected_changed_paths')!r}",
        )

    # Fail closed on the receipt workspace unconditionally -- never gated by
    # truthiness. A missing, empty, null, or malformed-type value must be
    # rejected exactly like a wrong value; none of them may fall through and
    # let authority-bearing fields (e.g. allowed_side_effect_paths) below be
    # trusted without an actual equality proof.
    receipt_workspace = receipt.get("workspace")
    if not isinstance(receipt_workspace, str) or not receipt_workspace.strip():
        raise PreflightFailure(
            "IMPLEMENTATION_WORKSPACE_MISMATCH",
            f"implementation receipt workspace is missing, empty, or not a string: {receipt_workspace!r}",
        )
    try:
        receipt_workspace_path = Path(receipt_workspace).resolve()
    except (OSError, ValueError) as exc:
        raise PreflightFailure(
            "IMPLEMENTATION_WORKSPACE_MISMATCH",
            f"implementation receipt workspace could not be resolved: {exc.__class__.__name__}",
        ) from None
    if receipt_workspace_path != workspace.resolve():
        raise PreflightFailure(
            "IMPLEMENTATION_WORKSPACE_MISMATCH",
            "implementation receipt workspace does not match the review --workspace",
        )

    # Canonical implementation identity binding (run_id + attempt only).
    # `repair_run_id` is compatibility/diagnostic metadata and is never an
    # acceptable substitute for the canonical `run_id` here -- accepting it
    # would let evidence from the wrong implementation run be trusted merely
    # because it happens to carry a useful `allowed_side_effect_paths`.
    # Callers that do not yet have a trusted expected identity to bind
    # against (e.g. run_repair.py loading the previous-attempt evidence it
    # was explicitly pointed at) pass None and skip this check.
    if expected_implementation_run_id is not None:
        receipt_run_id = receipt.get("run_id")
        if (
            not isinstance(receipt_run_id, str)
            or not receipt_run_id
            or receipt_run_id != expected_implementation_run_id
        ):
            raise PreflightFailure(
                "IMPLEMENTATION_RUN_ID_MISMATCH",
                f"implementation receipt run_id {receipt_run_id!r} does not match "
                f"expected implementation run id {expected_implementation_run_id!r}",
            )

    if expected_implementation_attempt is not None:
        receipt_attempt = receipt.get("attempt")
        if (
            not isinstance(receipt_attempt, int)
            or isinstance(receipt_attempt, bool)
            or receipt_attempt != expected_implementation_attempt
        ):
            raise PreflightFailure(
                "IMPLEMENTATION_ATTEMPT_MISMATCH",
                f"implementation receipt attempt {receipt_attempt!r} does not match "
                f"expected implementation attempt {expected_implementation_attempt!r}",
            )

    return {"receipt": receipt, "result": result, "patch_text": patch_text}


# ---------------------------------------------------------------------------
# Deterministic checks.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
    command: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_excerpt: str
    stderr_excerpt: str
    stdout_sha256: str
    stderr_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def validate_check_command_text(text: str) -> list[str]:
    if not text or not text.strip():
        raise PreflightFailure("INVALID_CHECK_COMMAND", "check command must not be empty")
    for substring in _RAW_FORBIDDEN_CHECK_COMMAND_SUBSTRINGS:
        if substring in text:
            raise PreflightFailure(
                "SHELL_OPERATOR_REJECTED", f"check command contains a rejected construct: {substring!r}"
            )
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise PreflightFailure("INVALID_CHECK_COMMAND", f"check command could not be parsed: {exc}") from None
    if not argv:
        raise PreflightFailure("INVALID_CHECK_COMMAND", "check command parsed to an empty argument list")
    for token in argv:
        if token in _FORBIDDEN_STANDALONE_CHECK_COMMAND_TOKENS:
            raise PreflightFailure(
                "SHELL_OPERATOR_REJECTED", f"check command contains a rejected shell operator token: {token!r}"
            )
    return argv


def run_check_command(text: str, cwd: Path, timeout_seconds: int) -> CheckResult:
    argv = validate_check_command_text(text)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            command=text,
            exit_code=completed.returncode,
            timed_out=False,
            duration_ms=duration_ms,
            stdout_excerpt=_excerpt(completed.stdout),
            stderr_excerpt=_excerpt(completed.stderr),
            stdout_sha256=_sha256(completed.stdout),
            stderr_sha256=_sha256(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        out = exc.stdout if isinstance(exc.stdout, (bytes, bytearray)) else b""
        err = exc.stderr if isinstance(exc.stderr, (bytes, bytearray)) else b""
        return CheckResult(
            command=text,
            exit_code=None,
            timed_out=True,
            duration_ms=duration_ms,
            stdout_excerpt=_excerpt(out),
            stderr_excerpt=_excerpt(err),
            stdout_sha256=_sha256(out),
            stderr_sha256=_sha256(err),
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        message = f"{exc.__class__.__name__} errno={getattr(exc, 'errno', None)}"
        return CheckResult(
            command=text,
            exit_code=None,
            timed_out=False,
            duration_ms=duration_ms,
            stdout_excerpt="",
            stderr_excerpt=message,
            stdout_sha256=_EMPTY_SHA256,
            stderr_sha256=_sha256(message.encode("utf-8")),
        )


# ---------------------------------------------------------------------------
# Codex CLI discovery. Every capability claim is verified against the actual
# installed CLI's own --help output at run time -- nothing here is assumed
# from memory. If a required capability cannot be confirmed, the runner fails
# closed with UNSUPPORTED_CODEX_CLI_CONTRACT before any real invocation.
# ---------------------------------------------------------------------------


def discover_codex_executable(explicit_path: str | None) -> str | None:
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
    return shutil.which("codex")


def probe_help_text(executable: str, argv_tail: list[str]) -> str:
    try:
        completed = subprocess.run(
            [executable, *argv_tail],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_HELP_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.decode("utf-8", errors="replace") + "\n" + completed.stderr.decode("utf-8", errors="replace")


def _word_present(text: str, word: str) -> bool:
    import re

    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])", text) is not None


def resolve_approval_flag(top_help_text: str, exec_help_text: str) -> tuple[str, str] | None:
    """Find the confirmed non-interactive-approval flag and where it was documented.

    Returns (flag_token, scope) where scope is "global" (found in `codex
    --help`) or "exec" (found only in `codex exec --help`), or None if no
    evidence was found in either. Global is checked first and preferred,
    matching the real discovered contract; a CLI that documents the flag only
    at the exec level is still accepted via the fallback.
    """

    for haystack, scope in ((top_help_text, "global"), (exec_help_text, "exec")):
        if not _word_present(haystack, "never"):
            continue
        if _word_present(haystack, "--ask-for-approval"):
            return "--ask-for-approval", scope
        if _word_present(haystack, "-a"):
            return "-a", scope
    return None


def detect_codex_capabilities(top_help_text: str, exec_help_text: str) -> dict[str, bool]:
    """Search the *actual* probed help text for each capability's evidence.

    `top_help_text` (`codex --help`) and `exec_help_text` (`codex exec
    --help`) are parsed separately per capability, and a capability counts as
    present if evidence appears in *either* text -- global-only or
    exec-only documentation are both accepted, since real CLI versions are
    not assumed to place every flag at the same scope.
    """

    caps: dict[str, bool] = {}
    caps["exec_subcommand"] = _word_present(top_help_text, "exec")
    caps["sandbox_flag"] = _word_present(top_help_text, "--sandbox") or _word_present(exec_help_text, "--sandbox")
    caps["read_only_mode"] = any(
        token in text for text in (top_help_text, exec_help_text) for token in ("read-only", "read_only")
    )
    caps["working_directory_flag"] = any(
        _word_present(text, token) for text in (top_help_text, exec_help_text) for token in ("--cd", "-C")
    )
    caps["isolated_final_message"] = _word_present(top_help_text, "--output-last-message") or _word_present(
        exec_help_text, "--output-last-message"
    )
    caps["non_interactive_approval"] = resolve_approval_flag(top_help_text, exec_help_text) is not None
    return caps


def missing_required_capabilities(caps: dict[str, bool]) -> list[str]:
    return [name for name in _REQUIRED_CAPABILITIES if not caps.get(name, False)]


# ---------------------------------------------------------------------------
# Process-group execution (bounded subprocess, no shell, no retry, no
# background survivor). Mirrors the process-lifecycle discipline already
# established in src/tomtit/adapters/claude_code_cli.py.
# ---------------------------------------------------------------------------


def _signal_group(pgid: int, sig: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _group_alive(pgid: int) -> bool | None:
    if os.name != "posix":
        return None
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    return True


def _await_group_absence(pgid: int, deadline: float) -> bool | None:
    while True:
        alive = _group_alive(pgid)
        if alive is not True:
            return alive
        if time.monotonic() >= deadline:
            return True
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)


def _ensure_group_quiescent(pgid: int) -> None:
    if os.name != "posix" or pgid <= 1:
        return
    alive = _group_alive(pgid)
    if alive is not True:
        return
    if _signal_group(pgid, signal.SIGTERM):
        alive = _await_group_absence(pgid, time.monotonic() + _PROCESS_CLEANUP_GRACE_SECONDS)
        if alive is False:
            return
    _signal_group(pgid, signal.SIGKILL)
    _await_group_absence(pgid, time.monotonic() + _PROCESS_CLEANUP_GRACE_SECONDS)


@dataclasses.dataclass(frozen=True, slots=True)
class ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    duration_ms: int


def execute_bounded(argv: list[str], *, cwd: Path, timeout_seconds: float) -> ProcessOutcome:
    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pgid = proc.pid
    deadline = start + timeout_seconds
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _ensure_group_quiescent(pgid)
        try:
            stdout, stderr = proc.communicate(timeout=_PROCESS_CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
    else:
        _ensure_group_quiescent(pgid)
    duration_ms = int((time.monotonic() - start) * 1000)
    return ProcessOutcome(
        exit_code=proc.returncode if not timed_out else None,
        timed_out=timed_out,
        stdout=stdout or b"",
        stderr=stderr or b"",
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Review prompt assembly.
# ---------------------------------------------------------------------------

_VERDICT_SCHEMA_TEXT = """Return exactly one JSON object and nothing else -- no prose before or after it,
no markdown code fence. The object must have this exact shape:

{
  "verdict": "APPROVE | REQUEST_CHANGES | BLOCKED",
  "summary": "string",
  "goal_satisfied": true,
  "scope_compliant": true,
  "checks_satisfied": true,
  "findings": [
    {
      "id": "string",
      "severity": "HIGH | MEDIUM | LOW",
      "title": "string",
      "evidence": "string",
      "required_change": "string"
    }
  ],
  "residual_risks": ["string"]
}

Rules:
- APPROVE requires goal_satisfied=true, scope_compliant=true, checks_satisfied=true,
  and no HIGH or MEDIUM finding.
- REQUEST_CHANGES requires at least one actionable finding.
- Use BLOCKED only when the evidence provided is insufficient to complete review.
- You may inspect the workspace directly in read-only mode, but you must not
  modify any file, stage anything, or run any command that changes repository
  state. You are a reviewer, not an implementer."""


def build_review_prompt(
    *,
    task_prompt: str,
    baseline_ref: str,
    baseline_commit: str,
    allowed_paths: tuple[str, ...],
    status_porcelain: str,
    tracked_diff: str,
    untracked_files: list[str],
    receipt: dict[str, Any],
    result_fields: dict[str, Any],
    check_results: list[CheckResult],
) -> str:
    sections: list[str] = []
    sections.append("# Original implementation task\n\n" + task_prompt.strip())
    sections.append(f"# Baseline\n\nbaseline_ref: {baseline_ref}\nbaseline_commit: {baseline_commit}")
    sections.append("# Allowed paths\n\n" + "\n".join(f"- {p}" for p in allowed_paths))
    sections.append("# git status --porcelain\n\n```\n" + (status_porcelain.strip() or "(clean)") + "\n```")
    sections.append("# Tracked diff from baseline (allowed paths only)\n\n```diff\n" + tracked_diff + "\n```")
    sections.append("# Untracked files\n\n" + ("\n".join(f"- {p}" for p in untracked_files) or "(none)"))
    sections.append("# Implementation receipt\n\n```json\n" + json.dumps(receipt, indent=2, sort_keys=True) + "\n```")
    sections.append(
        "# Relevant adapter-result fields\n\n```json\n" + json.dumps(result_fields, indent=2, sort_keys=True) + "\n```"
    )
    checks_payload = [c.to_dict() for c in check_results]
    sections.append(
        "# Deterministic check results\n\n```json\n" + json.dumps(checks_payload, indent=2, sort_keys=True) + "\n```"
    )
    sections.append("# Review instructions and verdict schema\n\n" + _VERDICT_SCHEMA_TEXT)
    return "\n\n".join(sections) + "\n"


_ADAPTER_RESULT_PROMPT_FIELDS = (
    "status",
    "success",
    "exit_code",
    "timed_out",
    "duration_ms",
    "changed_paths",
    "unexpected_changed_paths",
    "error_category",
    "error_message",
    "stdout_excerpt",
)


def extract_adapter_result_fields(result: dict[str, Any]) -> dict[str, Any]:
    fields = {k: result.get(k) for k in _ADAPTER_RESULT_PROMPT_FIELDS if k in result}
    excerpt = fields.get("stdout_excerpt")
    if isinstance(excerpt, str) and len(excerpt) > 2000:
        fields["stdout_excerpt"] = excerpt[:2000] + "...(truncated for review prompt)"
    return fields


# ---------------------------------------------------------------------------
# Verdict parsing and validation.
# ---------------------------------------------------------------------------


def parse_verdict_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ReviewFailure("NO_VERDICT_OUTPUT", "codex produced no final message content")
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ReviewFailure("INVALID_JSON", f"final message is not valid JSON: {exc}") from None
    return obj


def validate_verdict_schema(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", "verdict is not a JSON object")
    missing = _REQUIRED_VERDICT_KEYS - obj.keys()
    if missing:
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", f"missing required keys: {sorted(missing)}")
    if obj["verdict"] not in VALID_VERDICTS:
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", f"invalid verdict value: {obj['verdict']!r}")
    if not isinstance(obj["summary"], str) or not obj["summary"].strip():
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", "summary must be a non-empty string")
    for key in ("goal_satisfied", "scope_compliant", "checks_satisfied"):
        if not isinstance(obj[key], bool):
            raise ReviewFailure("INVALID_VERDICT_SCHEMA", f"{key} must be a boolean")
    if not isinstance(obj["findings"], list):
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", "findings must be a list")
    for finding in obj["findings"]:
        if not isinstance(finding, dict) or (_REQUIRED_FINDING_KEYS - finding.keys()):
            raise ReviewFailure("INVALID_VERDICT_SCHEMA", "malformed finding entry")
        if finding["severity"] not in VALID_SEVERITIES:
            raise ReviewFailure("INVALID_VERDICT_SCHEMA", f"invalid finding severity: {finding['severity']!r}")
        for key in ("id", "title", "evidence", "required_change"):
            if not isinstance(finding[key], str):
                raise ReviewFailure("INVALID_VERDICT_SCHEMA", f"finding.{key} must be a string")
    if not isinstance(obj["residual_risks"], list) or not all(isinstance(x, str) for x in obj["residual_risks"]):
        raise ReviewFailure("INVALID_VERDICT_SCHEMA", "residual_risks must be a list of strings")
    return obj


def enforce_verdict_rules(obj: dict[str, Any]) -> None:
    verdict = obj["verdict"]
    findings = obj["findings"]
    high = sum(1 for f in findings if f["severity"] == "HIGH")
    medium = sum(1 for f in findings if f["severity"] == "MEDIUM")
    if verdict == "APPROVE":
        if not (obj["goal_satisfied"] and obj["scope_compliant"] and obj["checks_satisfied"]):
            raise ReviewFailure(
                "INVALID_VERDICT_LOGIC",
                "APPROVE requires goal_satisfied, scope_compliant, and checks_satisfied to all be true",
            )
        if high or medium:
            raise ReviewFailure(
                "INVALID_VERDICT_LOGIC", f"APPROVE is not valid with {high} HIGH and {medium} MEDIUM finding(s)"
            )
    elif verdict == "REQUEST_CHANGES":
        if not findings:
            raise ReviewFailure("INVALID_VERDICT_LOGIC", "REQUEST_CHANGES requires at least one finding")


def finding_counts(obj: dict[str, Any]) -> dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for finding in obj.get("findings", []):
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


# ---------------------------------------------------------------------------
# Evidence directory + persistence.
# ---------------------------------------------------------------------------


def resolve_evidence_directory(evidence_directory_arg: str | None, review_run_id: str) -> Path:
    root = Path(evidence_directory_arg).expanduser() if evidence_directory_arg else (REPO_ROOT / ".tomtit" / "dogfood" / "reviews")
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve(strict=False)
    else:
        root = root.resolve(strict=False)
    directory = root / review_run_id
    if directory.exists():
        if not directory.is_dir():
            raise PreflightFailure("EVIDENCE_DIRECTORY_COLLISION", "review evidence directory path is not a directory")
        if any(directory.iterdir()):
            raise PreflightFailure(
                "EVIDENCE_DIRECTORY_COLLISION", "review evidence directory already contains another run"
            )
    return directory


def write_review_evidence(
    directory: Path,
    *,
    prompt_text: str,
    verdict_obj: dict[str, Any] | None,
    receipt: dict[str, Any],
    check_results: list[CheckResult],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "review-prompt.txt").write_text(prompt_text, encoding="utf-8")
    (directory / "review-result.json").write_text(
        json.dumps(verdict_obj, indent=2, sort_keys=True) + "\n" if verdict_obj is not None else "null\n",
        encoding="utf-8",
    )
    (directory / "checks.json").write_text(
        json.dumps([c.to_dict() for c in check_results], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (directory / "review-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI argument parsing.
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded local Codex review of an implementation run.")
    parser.add_argument("--workspace", required=True, metavar="PATH")
    parser.add_argument("--task-prompt-file", required=True, metavar="PATH")
    parser.add_argument("--implementation-run-directory", required=True, metavar="PATH")
    parser.add_argument("--baseline-ref", required=True, metavar="REF")
    parser.add_argument("--allowed-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--check-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--timeout-seconds", type=int, required=True, metavar="INTEGER")
    parser.add_argument("--codex-executable", metavar="PATH")
    parser.add_argument("--model", metavar="TEXT")
    parser.add_argument("--review-run-id", metavar="TEXT")
    parser.add_argument("--evidence-directory", metavar="PATH")
    parser.add_argument("--implementation-run-id", required=True, metavar="TEXT")
    parser.add_argument("--implementation-attempt", type=int, required=True, metavar="INTEGER")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main orchestration.
# ---------------------------------------------------------------------------


def _validate_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser().resolve(strict=False)
    if not workspace.exists() or not workspace.is_dir():
        raise PreflightFailure("INVALID_WORKSPACE", "workspace must exist and be a directory")
    if workspace == REPO_ROOT or _is_within(workspace, REPO_ROOT):
        raise PreflightFailure("WORKSPACE_INSIDE_COORDINATOR", "the TOMTIT Coordinator repository may not be a target")
    completed = _run_git(["rev-parse", "--is-inside-work-tree"], workspace)
    if completed.returncode != 0 or completed.stdout.strip() != "true":
        raise PreflightFailure("NOT_A_GIT_REPOSITORY", "workspace must be a Git repository")
    return workspace


def _read_task_prompt(path_arg: str) -> str:
    try:
        text = Path(path_arg).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightFailure("MISSING_TASK_PROMPT", f"task prompt file could not be read: {exc.__class__.__name__}") from None
    if not text.strip():
        raise PreflightFailure("EMPTY_TASK_PROMPT", "task prompt must not be empty")
    return text


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    if args.timeout_seconds <= 0:
        raise PreflightFailure("INVALID_TIMEOUT", "timeout must be positive")

    workspace = _validate_workspace(args.workspace)

    baseline_commit = git_rev_parse(workspace, args.baseline_ref)
    if baseline_commit is None:
        raise PreflightFailure("BASELINE_REF_UNRESOLVED", "baseline ref does not resolve in the workspace")

    if not git_index_clean(workspace):
        raise PreflightFailure("DIRTY_INDEX", "workspace Git index must be clean before review")

    special_state = git_special_state(workspace)
    if special_state is not None:
        raise PreflightFailure("ACTIVE_GIT_OPERATION", f"workspace has an active Git operation: {special_state}")

    allowed_paths = normalize_allowed_paths(args.allowed_path, workspace)

    changed_paths = git_changed_paths(workspace, baseline_commit)
    if not changed_paths:
        raise PreflightFailure("NO_IMPLEMENTATION_CHANGES", "workspace contains no implementation changes to review")

    implementation_run_id = args.implementation_run_id
    if not isinstance(implementation_run_id, str) or not implementation_run_id.strip():
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_RUN_ID", "--implementation-run-id must be a non-empty string"
        )

    implementation_attempt = args.implementation_attempt
    if not isinstance(implementation_attempt, int) or isinstance(implementation_attempt, bool) or implementation_attempt <= 0:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_ATTEMPT", "--implementation-attempt must be a positive integer"
        )

    run_directory = Path(args.implementation_run_directory).expanduser()
    if not run_directory.is_absolute():
        run_directory = (Path.cwd() / run_directory).resolve(strict=False)

    # Evidence must be fully loaded and bound to the exact expected
    # (workspace, run_id, attempt) BEFORE any of its fields -- including
    # `allowed_side_effect_paths` -- can be trusted to authorize anything
    # below. Reordering this would let unbound/wrong-run evidence grant
    # side-effect scope it never earned.
    evidence = load_implementation_evidence(
        run_directory, workspace, implementation_run_id, implementation_attempt
    )

    receipt_data = evidence["receipt"]
    raw_side_effect_paths = receipt_data.get("allowed_side_effect_paths")
    if raw_side_effect_paths is None:
        # Missing legacy field -- no side-effect authorization, fail closed.
        allowed_side_effect_paths: tuple[str, ...] = ()
    elif isinstance(raw_side_effect_paths, list) and all(isinstance(p, str) for p in raw_side_effect_paths):
        allowed_side_effect_paths = tuple(raw_side_effect_paths)
    else:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE", "implementation receipt allowed_side_effect_paths is malformed"
        )

    # Canonical containment classifier shared with the adapter itself --
    # never reimplemented here (no startswith()).
    source_changed_paths, authorized_side_effect_changed_paths, unexpected_changed_paths = classify_workspace_changes(
        changed_paths, allowed_paths, allowed_side_effect_paths, workspace_root=workspace
    )

    if unexpected_changed_paths:
        raise PreflightFailure(
            "CHANGED_PATH_OUT_OF_SCOPE",
            f"changed paths outside the allowed set: {sorted(unexpected_changed_paths)}",
        )

    task_prompt = _read_task_prompt(args.task_prompt_file)

    review_run_id = args.review_run_id or str(uuid.uuid4())
    if not review_run_id or "/" in review_run_id or "\\" in review_run_id or review_run_id in (".", ".."):
        raise PreflightFailure("INVALID_REVIEW_RUN_ID", "review run id must be a simple non-empty identifier")

    evidence_directory = resolve_evidence_directory(args.evidence_directory, review_run_id)

    return {
        "workspace": workspace,
        "baseline_commit": baseline_commit,
        "allowed_paths": allowed_paths,
        # SOURCE-only, so the downstream review prompt/patch never includes
        # authorized-but-not-source-relevant generated side effects.
        "changed_paths": source_changed_paths,
        "source_changed_paths": source_changed_paths,
        "authorized_side_effect_changed_paths": authorized_side_effect_changed_paths,
        "unexpected_changed_paths": unexpected_changed_paths,
        "implementation_evidence": evidence,
        "task_prompt": task_prompt,
        "review_run_id": review_run_id,
        "evidence_directory": evidence_directory,
    }


def run_deterministic_checks(commands: list[str], workspace: Path, timeout_seconds: int) -> list[CheckResult]:
    return [run_check_command(command, workspace, timeout_seconds) for command in commands]


def resolve_codex_contract(explicit_executable: str | None) -> tuple[str, dict[str, bool], tuple[str, str]]:
    executable = discover_codex_executable(explicit_executable)
    if executable is None:
        raise PreflightFailure("CODEX_NOT_FOUND", "codex executable was not discovered")
    top_help = probe_help_text(executable, ["--help"])
    exec_help = probe_help_text(executable, ["exec", "--help"])
    caps = detect_codex_capabilities(top_help, exec_help)
    missing = missing_required_capabilities(caps)
    if missing:
        raise PreflightFailure(
            "UNSUPPORTED_CODEX_CLI_CONTRACT",
            f"installed codex CLI does not confirm required capabilities: {missing}",
        )
    approval = resolve_approval_flag(top_help, exec_help)
    assert approval is not None  # guaranteed by the required-capability check above
    return executable, caps, approval


def build_codex_argv(
    executable: str,
    *,
    workspace: Path,
    model: str | None,
    last_message_path: Path,
    prompt: str,
    caps: dict[str, bool],
    approval: tuple[str, str],
) -> list[str]:
    """Build the real invocation from the *discovered* CLI contract.

    A global approval flag is placed before `exec`; one documented only at
    the exec level is placed after it, alongside the rest of the exec
    arguments. `--sandbox read-only` is requested explicitly whenever the CLI
    documents a `--sandbox` flag at all; if it does not, the runner relies on
    `codex exec`'s documented read-only default rather than passing a flag
    that was never confirmed to exist.
    """

    approval_flag, approval_scope = approval
    global_args: list[str] = []
    exec_args: list[str] = []

    if approval_scope == "global":
        global_args.extend([approval_flag, "never"])
    else:
        exec_args.extend([approval_flag, "never"])

    if caps.get("sandbox_flag"):
        exec_args.extend(["--sandbox", "read-only"])

    exec_args.extend(["--cd", str(workspace), "--output-last-message", str(last_message_path)])
    if model:
        exec_args.extend(["--model", model])
    exec_args.append(prompt)

    argv = [executable, *global_args, "exec", *exec_args]
    for token in argv[:-1]:  # the prompt itself (last element) is free-form user/task text
        if token in _FORBIDDEN_ARGV_TOKENS:
            raise ReviewFailure("UNSAFE_ARGV_CONSTRUCTED", f"refusing to invoke Codex with unsafe token: {token!r}")
    return argv


def _print_summary(
    *,
    review_run_id: str,
    status: str,
    verdict: str | None,
    goal_satisfied: bool | None,
    scope_compliant: bool | None,
    checks_satisfied: bool | None,
    counts: dict[str, int],
    codex_exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
    workspace_unchanged: bool | None,
    evidence_directory: Path,
) -> None:
    print(f"REVIEW_RUN_ID={review_run_id}")
    print(f"STATUS={status}")
    print(f"VERDICT={verdict or ''}")
    print(f"GOAL_SATISFIED={str(goal_satisfied).lower() if goal_satisfied is not None else ''}")
    print(f"SCOPE_COMPLIANT={str(scope_compliant).lower() if scope_compliant is not None else ''}")
    print(f"CHECKS_SATISFIED={str(checks_satisfied).lower() if checks_satisfied is not None else ''}")
    print(f"HIGH_FINDINGS={counts.get('HIGH', 0)}")
    print(f"MEDIUM_FINDINGS={counts.get('MEDIUM', 0)}")
    print(f"LOW_FINDINGS={counts.get('LOW', 0)}")
    print(f"CODEX_EXIT_CODE={codex_exit_code if codex_exit_code is not None else ''}")
    print(f"TIMED_OUT={str(timed_out).lower()}")
    print(f"DURATION_MS={duration_ms}")
    print(f"WORKSPACE_UNCHANGED={str(workspace_unchanged).lower() if workspace_unchanged is not None else ''}")
    print(f"EVIDENCE_DIRECTORY={evidence_directory}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        pre = run_preflight(args)
    except PreflightFailure as exc:
        print(f"STATUS=PREFLIGHT_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    workspace: Path = pre["workspace"]
    baseline_commit: str = pre["baseline_commit"]
    allowed_paths: tuple[str, ...] = pre["allowed_paths"]
    evidence: dict[str, Any] = pre["implementation_evidence"]
    task_prompt: str = pre["task_prompt"]
    review_run_id: str = pre["review_run_id"]
    evidence_directory: Path = pre["evidence_directory"]

    try:
        codex_executable, codex_caps, codex_approval = resolve_codex_contract(args.codex_executable)
    except PreflightFailure as exc:
        print(f"STATUS=PREFLIGHT_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    try:
        check_results = run_deterministic_checks(args.check_command, workspace, args.timeout_seconds)
    except PreflightFailure as exc:
        print(f"STATUS=PREFLIGHT_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    changed_paths_for_prompt = list(pre["changed_paths"])
    status_porcelain = git_status_porcelain(workspace)
    untracked_files = [p for p in _parse_status_paths(status_porcelain) if p in changed_paths_for_prompt]
    tracked_diff = git_tracked_diff(workspace, baseline_commit, changed_paths_for_prompt)
    receipt_data = evidence["receipt"]
    adapter_fields = extract_adapter_result_fields(evidence["result"])

    prompt_text = build_review_prompt(
        task_prompt=task_prompt,
        baseline_ref=args.baseline_ref,
        baseline_commit=baseline_commit,
        allowed_paths=allowed_paths,
        status_porcelain=status_porcelain,
        tracked_diff=tracked_diff,
        untracked_files=untracked_files,
        receipt=receipt_data,
        result_fields=adapter_fields,
        check_results=check_results,
    )

    before_snapshot = snapshot_workspace(workspace, baseline_commit)

    error_category: str | None = None
    error_message: str | None = None
    verdict_obj: dict[str, Any] | None = None
    codex_exit_code: int | None = None
    timed_out = False
    duration_ms = 0
    modified_paths: list[str] = []
    invocation_count = 0

    with tempfile.TemporaryDirectory(prefix="tomtit-codex-review-") as tmp_dir:
        last_message_path = Path(tmp_dir) / "last-message.txt"
        argv = build_codex_argv(
            codex_executable,
            workspace=workspace,
            model=args.model,
            last_message_path=last_message_path,
            prompt=prompt_text,
            caps=codex_caps,
            approval=codex_approval,
        )
        invocation_count += 1
        outcome = execute_bounded(argv, cwd=Path(tmp_dir), timeout_seconds=float(args.timeout_seconds))
        codex_exit_code = outcome.exit_code
        timed_out = outcome.timed_out
        duration_ms = outcome.duration_ms

        after_snapshot = snapshot_workspace(workspace, baseline_commit)
        modified_paths = diff_snapshots(before_snapshot, after_snapshot)
        workspace_unchanged = not modified_paths

        try:
            if modified_paths:
                raise ReviewFailure("IMMUTABILITY_VIOLATION", f"Codex modified workspace paths: {modified_paths}")
            if timed_out:
                raise ReviewFailure("TIMEOUT", "codex process exceeded the configured timeout")
            if codex_exit_code != 0:
                raise ReviewFailure("CODEX_PROCESS_NONZERO_EXIT", f"codex process exited with code {codex_exit_code}")
            try:
                final_message = last_message_path.read_text(encoding="utf-8")
            except OSError:
                final_message = ""
            parsed = parse_verdict_text(final_message)
            validated = validate_verdict_schema(parsed)
            enforce_verdict_rules(validated)
            verdict_obj = validated
        except ReviewFailure as exc:
            error_category = exc.category
            error_message = exc.message

    counts = finding_counts(verdict_obj) if verdict_obj is not None else {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    receipt = {
        "review_run_id": review_run_id,
        "implementation_run_id": receipt_data.get("run_id"),
        "implementation_attempt": receipt_data.get("attempt"),
        "workspace": str(workspace),
        "baseline_ref": args.baseline_ref,
        "baseline_commit": baseline_commit,
        "codex_executable": codex_executable,
        "codex_capabilities": codex_caps,
        "codex_approval_flag": codex_approval[0],
        "codex_approval_scope": codex_approval[1],
        "invocation_count": invocation_count,
        "process_exit_code": codex_exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "verdict": verdict_obj.get("verdict") if verdict_obj else None,
        "goal_satisfied": verdict_obj.get("goal_satisfied") if verdict_obj else None,
        "scope_compliant": verdict_obj.get("scope_compliant") if verdict_obj else None,
        "checks_satisfied": verdict_obj.get("checks_satisfied") if verdict_obj else None,
        "finding_counts": counts,
        "deterministic_checks": [c.to_dict() for c in check_results],
        "workspace_unchanged": workspace_unchanged,
        "modified_paths": modified_paths,
        "evidence_paths": {
            "review_prompt": str(evidence_directory / "review-prompt.txt"),
            "review_result": str(evidence_directory / "review-result.json"),
            "review_receipt": str(evidence_directory / "review-receipt.json"),
            "checks": str(evidence_directory / "checks.json"),
        },
        "error_category": error_category,
        "error_message": error_message,
        "recorded_at": _utc_now_iso(),
    }

    write_review_evidence(
        evidence_directory,
        prompt_text=prompt_text,
        verdict_obj=verdict_obj,
        receipt=receipt,
        check_results=check_results,
    )

    if error_category is not None:
        _print_summary(
            review_run_id=review_run_id,
            status=f"REVIEW_FAILURE:{error_category}",
            verdict=None,
            goal_satisfied=None,
            scope_compliant=None,
            checks_satisfied=None,
            counts=counts,
            codex_exit_code=codex_exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            workspace_unchanged=workspace_unchanged,
            evidence_directory=evidence_directory,
        )
        return 4

    assert verdict_obj is not None
    verdict = verdict_obj["verdict"]
    _print_summary(
        review_run_id=review_run_id,
        status="COMPLETE",
        verdict=verdict,
        goal_satisfied=verdict_obj["goal_satisfied"],
        scope_compliant=verdict_obj["scope_compliant"],
        checks_satisfied=verdict_obj["checks_satisfied"],
        counts=counts,
        codex_exit_code=codex_exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
        workspace_unchanged=workspace_unchanged,
        evidence_directory=evidence_directory,
    )
    if verdict == "APPROVE":
        return 0
    if verdict == "REQUEST_CHANGES":
        return 1
    return 2  # BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
