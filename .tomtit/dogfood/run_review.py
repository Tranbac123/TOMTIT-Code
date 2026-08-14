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
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Mapping, Sequence
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    classify_workspace_changes,
    snapshot_workspace_hashes,
    summarize_side_effect_paths,
    workspace_hash,
)

_MAX_EXCERPT_BYTES = 4000
_HELP_PROBE_TIMEOUT_SECONDS = 15
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_PROCESS_CLEANUP_GRACE_SECONDS = 5.0
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# Bounded retry budget for one composite protected-Git-metadata measurement
# (see `protected_git_metadata_snapshot`). Not a general-purpose retry knob.
_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS = 3

# The exact, complete descriptor grammars emitted by `_directory_descriptor`
# and `_regular_file_measurement` (see those two functions). Numeric fields
# allow an optional leading "-" only because Python's plain `{int}`
# formatting would render one for a negative `os.stat_result` field (never
# observed in practice, but the grammar must match exactly what an f-string
# of that field can produce, not a narrower approximation of it). `mode` is
# `stat.S_IMODE(...)` formatted `:04o`, whose value is masked to at most
# 0o7777 -- i.e. always rendered as exactly four octal digits. `sha256` is a
# `hashlib.sha256().hexdigest()`, always exactly 64 lowercase hex digits.
#
# `_strip_volatile_timestamp_fields` requires a `fullmatch` against one of
# these two patterns -- the *entire* input, not merely a prefix -- before
# stripping anything. Any input that is not byte-for-byte one of these two
# canonical shapes (a "path:"/"symlink:" descriptor, an unknown type, a
# truncated or reordered dir:/file: string, or any other caller-controlled
# text that merely begins with "dir:" or "file:") is returned unchanged.
_CANONICAL_DIR_DESCRIPTOR_RE = re.compile(
    r"dir:dev=(?P<dev>-?\d+):ino=(?P<ino>-?\d+):mode=(?P<mode>[0-7]{4}):"
    r"size=(?P<size>-?\d+):nlink=(?P<nlink>-?\d+):"
    r"mtime_ns=-?\d+:ctime_ns=-?\d+"
)
_CANONICAL_FILE_DESCRIPTOR_RE = re.compile(
    r"file:dev=(?P<dev>-?\d+):ino=(?P<ino>-?\d+):mode=(?P<mode>[0-7]{4}):"
    r"size=(?P<size>-?\d+):nlink=(?P<nlink>-?\d+):"
    r"mtime_ns=-?\d+:ctime_ns=-?\d+:sha256=(?P<sha256>[0-9a-f]{64})"
)

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


class EvidenceIntegrityFailure(Exception):
    """A canonical evidence tree cannot be safely identified without following aliases."""


class ProcessQuiescenceFailure(Exception):
    """A controlled subprocess group could not be positively proven absent."""


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


def _read_only_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _strip_volatile_timestamp_fields(descriptor: str) -> str:
    """Remove the `mtime_ns`/`ctime_ns` pair from one identity descriptor.

    Canonical-grammar aware, not merely prefix-aware: the *entire* input
    must `fullmatch` the exact `dir:` or `file:` descriptor grammar emitted
    by `_directory_descriptor`/`_regular_file_measurement` -- every field in
    its exact structural position -- before anything is stripped. This
    rejects not just `path:`/`symlink:` descriptors but also any
    caller-controlled opaque string that merely begins with `dir:` or
    `file:` (e.g. an unrelated extra field, a truncated or reordered
    canonical-looking string, or any future/unrecognized extension of the
    grammar): all such input is returned byte-for-byte unchanged, since the
    grammar cannot be positively confirmed timestamp-only. Reconstruction
    uses only the matched non-timestamp field values, so the result is
    injective with respect to every load-bearing (non-timestamp) field.

    Used only to build a cross-phase-*stable* projection of an identity
    descriptor for equality between two separate measurement calls (see
    `protected_git_metadata_snapshot`). It must never be used inside a
    single measurement's own before/opened/after race checks, which keep
    comparing the full raw `os.stat_result`-derived identity so a genuine
    same-window mutation is still caught.
    """

    match = _CANONICAL_DIR_DESCRIPTOR_RE.fullmatch(descriptor)
    if match is not None:
        fields = match.groupdict()
        return (
            f"dir:dev={fields['dev']}:ino={fields['ino']}:mode={fields['mode']}:"
            f"size={fields['size']}:nlink={fields['nlink']}"
        )
    match = _CANONICAL_FILE_DESCRIPTOR_RE.fullmatch(descriptor)
    if match is not None:
        fields = match.groupdict()
        return (
            f"file:dev={fields['dev']}:ino={fields['ino']}:mode={fields['mode']}:"
            f"size={fields['size']}:nlink={fields['nlink']}:sha256={fields['sha256']}"
        )
    return descriptor


def _stable_cross_phase_projection(identity: Mapping[str, str]) -> dict[str, str]:
    """Project one identity snapshot onto its cross-phase-stable form.

    Path identity, entry type, mode, device/inode, size, link count, and
    content hashes remain fully load-bearing; only `mtime_ns`/`ctime_ns` are
    excluded, since benign OS-level metadata churn (e.g. an asynchronously
    attached extended attribute) can change them without any semantic
    mutation of the measured object.
    """

    return {key: _strip_volatile_timestamp_fields(value) for key, value in identity.items()}


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    """Load-bearing identity for one filesystem object observation."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_descriptor(metadata: os.stat_result) -> str:
    return (
        f"dir:dev={metadata.st_dev}:ino={metadata.st_ino}:"
        f"mode={stat.S_IMODE(metadata.st_mode):04o}:size={metadata.st_size}:"
        f"nlink={metadata.st_nlink}:mtime_ns={metadata.st_mtime_ns}:"
        f"ctime_ns={metadata.st_ctime_ns}"
    )


def _regular_file_measurement(
    path: Path,
    metadata: os.stat_result,
    *,
    reject_hardlinks: bool,
    capture_bytes: bool,
) -> tuple[str, bytes | None]:
    if reject_hardlinks and metadata.st_nlink != 1:
        raise EvidenceIntegrityFailure(
            f"regular file has unsafe link count {metadata.st_nlink}: {path.name}"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceIntegrityFailure(
            f"regular file cannot be opened safely: {path.name}: {exc.__class__.__name__}"
        ) from None
    try:
        opened = os.fstat(descriptor)
        expected_identity = _stat_identity(metadata)
        opened_identity = _stat_identity(opened)
        if expected_identity != opened_identity or not stat.S_ISREG(opened.st_mode):
            raise EvidenceIntegrityFailure(f"regular file identity changed while opening: {path.name}")
        digest = hashlib.sha256()
        captured = bytearray() if capture_bytes else None
        bytes_read = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            digest.update(chunk)
            if captured is not None:
                captured.extend(chunk)
        closed_over = os.fstat(descriptor)
        if _stat_identity(closed_over) != opened_identity:
            raise EvidenceIntegrityFailure(f"regular file identity changed while hashing: {path.name}")
        if bytes_read != opened.st_size:
            raise EvidenceIntegrityFailure(
                f"regular file byte count changed while hashing: {path.name}"
            )
        try:
            pathname_after = path.lstat()
        except OSError as exc:
            raise EvidenceIntegrityFailure(
                f"regular file pathname changed after reading: {path.name}: {exc.__class__.__name__}"
            ) from None
        if (
            not stat.S_ISREG(pathname_after.st_mode)
            or _stat_identity(pathname_after) != opened_identity
        ):
            raise EvidenceIntegrityFailure(
                f"regular file pathname no longer names the opened object: {path.name}"
            )
    except OSError as exc:
        raise EvidenceIntegrityFailure(
            f"regular file cannot be read safely: {path.name}: {exc.__class__.__name__}"
        ) from None
    finally:
        os.close(descriptor)
    return (
        f"file:dev={opened.st_dev}:ino={opened.st_ino}:"
        f"mode={stat.S_IMODE(opened.st_mode):04o}:size={opened.st_size}:"
        f"nlink={opened.st_nlink}:mtime_ns={opened.st_mtime_ns}:"
        f"ctime_ns={opened.st_ctime_ns}:sha256={digest.hexdigest()}",
        bytes(captured) if captured is not None else None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CanonicalEvidenceSnapshot:
    """One consistent canonical-tree measurement and its validated file bytes."""

    identity: dict[str, str]
    file_bytes: dict[str, bytes]


def _measure_type_aware_tree(
    root: Path,
    *,
    reject_symlinks: bool,
    reject_hardlinks: bool,
    capture_files: frozenset[str] = frozenset(),
) -> CanonicalEvidenceSnapshot:
    """Measure a tree without following aliases, revalidating every live path."""

    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise EvidenceIntegrityFailure(
            f"identity root is unavailable: {exc.__class__.__name__}"
        ) from None
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise EvidenceIntegrityFailure("identity root must be a real directory, not an alias")

    identity: dict[str, str] = {".": _directory_descriptor(root_metadata)}
    captured: dict[str, bytes] = {}

    def visit(
        directory: Path,
        relative: PurePosixPath,
        expected_directory: os.stat_result,
    ) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise EvidenceIntegrityFailure(
                f"identity directory is unreadable: {relative.as_posix()}: {exc.__class__.__name__}"
            ) from None
        for entry in entries:
            entry_relative = relative / entry.name
            key = entry_relative.as_posix()
            entry_path = directory / entry.name
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise EvidenceIntegrityFailure(
                    f"identity member is unreadable: {key}: {exc.__class__.__name__}"
                ) from None
            if stat.S_ISLNK(metadata.st_mode):
                if reject_symlinks:
                    raise EvidenceIntegrityFailure(f"symlink identity member is forbidden: {key}")
                try:
                    target = os.readlink(entry_path)
                except OSError as exc:
                    raise EvidenceIntegrityFailure(
                        f"symlink identity member is unreadable: {key}: {exc.__class__.__name__}"
                    ) from None
                identity[key] = (
                    f"symlink:mode={stat.S_IMODE(metadata.st_mode):04o}:target={target}"
                )
            elif stat.S_ISDIR(metadata.st_mode):
                identity[key] = _directory_descriptor(metadata)
                visit(entry_path, entry_relative, metadata)
            elif stat.S_ISREG(metadata.st_mode):
                descriptor, content = _regular_file_measurement(
                    entry_path,
                    metadata,
                    reject_hardlinks=reject_hardlinks,
                    capture_bytes=key in capture_files,
                )
                identity[key] = descriptor
                if content is not None:
                    captured[key] = content
            else:
                raise EvidenceIntegrityFailure(f"special identity member is forbidden: {key}")

        try:
            directory_after = directory.lstat()
        except OSError as exc:
            raise EvidenceIntegrityFailure(
                f"identity directory changed during measurement: {relative.as_posix()}: {exc.__class__.__name__}"
            ) from None
        if (
            not stat.S_ISDIR(directory_after.st_mode)
            or _stat_identity(directory_after) != _stat_identity(expected_directory)
        ):
            raise EvidenceIntegrityFailure(
                f"identity directory changed during measurement: {relative.as_posix()}"
            )

    visit(root, PurePosixPath(), root_metadata)
    return CanonicalEvidenceSnapshot(identity=identity, file_bytes=captured)


def snapshot_type_aware_tree(
    root: Path,
    *,
    reject_symlinks: bool,
    reject_hardlinks: bool,
) -> dict[str, str]:
    """Return an lstat-based, non-following identity for one exact filesystem tree."""

    return _measure_type_aware_tree(
        root,
        reject_symlinks=reject_symlinks,
        reject_hardlinks=reject_hardlinks,
    ).identity


def snapshot_evidence_tree(root: Path) -> dict[str, str]:
    """Strict canonical evidence identity: no aliases, special files, or hard links."""

    return snapshot_type_aware_tree(
        root,
        reject_symlinks=True,
        reject_hardlinks=True,
    )


def snapshot_canonical_evidence(
    root: Path,
    required_files: Sequence[str],
) -> dict[str, str]:
    """Validate a real evidence directory and every required regular member."""

    return read_canonical_evidence(root, required_files).identity


def read_canonical_evidence(
    root: Path,
    required_files: Sequence[str],
) -> CanonicalEvidenceSnapshot:
    """Return exact bytes from the same safe handles used to validate identity."""

    required = frozenset(PurePosixPath(name).as_posix() for name in required_files)
    measured = _measure_type_aware_tree(
        root,
        reject_symlinks=True,
        reject_hardlinks=True,
        capture_files=required,
    )
    for name in required_files:
        key = PurePosixPath(name).as_posix()
        descriptor = measured.identity.get(key)
        if (
            descriptor is None
            or not descriptor.startswith("file:")
            or key not in measured.file_bytes
        ):
            raise EvidenceIntegrityFailure(f"canonical evidence file is missing or unsafe: {name}")
    return measured


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
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
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


def git_common_dir(cwd: Path) -> Path | None:
    completed = _run_git(["rev-parse", "--git-common-dir"], cwd)
    if completed.returncode != 0:
        return None
    raw = Path(completed.stdout.strip())
    return raw.resolve() if raw.is_absolute() else (cwd / raw).resolve()


_WORKSPACE_GIT_ENTRY_PATH_KEY = "@workspace_git_entry_path"
_WORKSPACE_GIT_ENTRY_KEY = "@workspace_git_entry"


def _workspace_git_entry_descriptor(cwd: Path) -> tuple[str, str]:
    """Measure the worktree-root .git entry without following aliases."""

    entry = cwd.resolve() / ".git"
    try:
        metadata = entry.lstat()
    except OSError as exc:
        raise EvidenceIntegrityFailure(
            f"workspace .git entry is unavailable: {exc.__class__.__name__}"
        ) from None
    if stat.S_ISDIR(metadata.st_mode):
        descriptor = _directory_descriptor(metadata)
    elif stat.S_ISREG(metadata.st_mode):
        descriptor, _ = _regular_file_measurement(
            entry,
            metadata,
            reject_hardlinks=False,
            capture_bytes=False,
        )
    else:
        raise EvidenceIntegrityFailure(
            "workspace .git entry must be a real directory or regular indirection file"
        )
    return (f"path:{entry}", descriptor)


def workspace_git_entry_hash_from_snapshot(snapshot: Mapping[str, str]) -> str:
    """Hash the explicit worktree-root .git component of a repository snapshot."""

    try:
        entry_path = snapshot[_WORKSPACE_GIT_ENTRY_PATH_KEY]
        entry = snapshot[_WORKSPACE_GIT_ENTRY_KEY]
    except KeyError as exc:
        raise EvidenceIntegrityFailure(
            "protected Git metadata snapshot has no workspace .git entry identity"
        ) from exc
    return workspace_hash(
        {
            _WORKSPACE_GIT_ENTRY_PATH_KEY: entry_path,
            _WORKSPACE_GIT_ENTRY_KEY: entry,
        }
    )


def protected_git_metadata_snapshot(cwd: Path) -> dict[str, str]:
    """Cross-phase-stable snapshot of protected Git metadata.

    Snapshots the root `.git` entry plus the actual per-worktree/common
    metadata trees. The returned identity is a *stable cross-phase
    projection*: path identity, entry type, mode, device/inode, size, link
    count, and content hashes remain fully load-bearing, but `mtime_ns`/
    `ctime_ns` are excluded (see `_stable_cross_phase_projection`). Benign
    OS-level metadata churn between two separate calls to this function --
    for example an extended attribute the OS attaches to a file some time
    after it was written -- can change those two fields without any
    semantic mutation of the measured object, and must not register as one.

    Internally, one composite measurement pass still requires the root
    `.git` entry and the resolved git-dir/git-common-dir to be unchanged
    from just before the tree walk to just after it -- a same-window
    self-consistency check *within a single call*, not a cross-phase one.
    `git_dir`/`git_common_dir` are pure path strings with no timestamp
    component, so any difference there is always semantic and fails closed
    immediately. If the root `.git` entry differs only in its timestamp
    fields, the whole composite pass is retried, bounded by
    `_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS`; a non-timestamp
    difference, or persistent instability across every attempt, fails
    closed.
    """

    last_error: EvidenceIntegrityFailure | None = None
    for _ in range(_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS):
        git_entry_before = _workspace_git_entry_descriptor(cwd)
        directory = git_dir(cwd)
        common_directory = git_common_dir(cwd)
        if directory is None or common_directory is None:
            raise EvidenceIntegrityFailure("Git metadata locations could not be resolved")
        roots = (
            ("git_dir", directory.resolve()),
            ("git_common_dir", common_directory.resolve()),
        )
        identity: dict[str, str] = {
            "@git_dir": f"path:{roots[0][1]}",
            "@git_common_dir": f"path:{roots[1][1]}",
            _WORKSPACE_GIT_ENTRY_PATH_KEY: git_entry_before[0],
            _WORKSPACE_GIT_ENTRY_KEY: git_entry_before[1],
        }
        for role, root in roots:
            tree = snapshot_type_aware_tree(
                root,
                reject_symlinks=True,
                reject_hardlinks=False,
            )
            for relative, descriptor in tree.items():
                identity[f"{role}/{relative}"] = descriptor
        git_entry_after = _workspace_git_entry_descriptor(cwd)
        entry_changed = git_entry_after != git_entry_before
        roots_changed = (
            git_dir(cwd) != roots[0][1] or git_common_dir(cwd) != roots[1][1]
        )
        if not entry_changed and not roots_changed:
            return _stable_cross_phase_projection(identity)
        if roots_changed:
            raise EvidenceIntegrityFailure(
                "resolved Git metadata locations changed during protected metadata measurement"
            )
        timestamp_only = (
            git_entry_after[0] == git_entry_before[0]
            and _strip_volatile_timestamp_fields(git_entry_after[1])
            == _strip_volatile_timestamp_fields(git_entry_before[1])
        )
        if not timestamp_only:
            raise EvidenceIntegrityFailure(
                "workspace .git entry changed during protected metadata measurement"
            )
        last_error = EvidenceIntegrityFailure(
            "workspace .git entry timestamp remained unstable across the bounded "
            "coherent-measurement retry limit"
        )
        # Timestamp-only same-window instability: bounded-retry the whole
        # composite measurement from scratch.

    assert last_error is not None
    raise last_error


def protected_git_metadata_hash(cwd: Path) -> str:
    return workspace_hash(protected_git_metadata_snapshot(cwd))


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


def git_index_identity(cwd: Path) -> str | None:
    """Hash index entries, stages, modes, blob ids, paths, and semantic flags."""

    completed = _run_git(["ls-files", "--stage", "-v", "-z"], cwd)
    if completed.returncode != 0:
        return None
    return _sha256(completed.stdout.encode("utf-8", errors="surrogateescape"))


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


@dataclasses.dataclass(frozen=True, slots=True)
class ReviewWorkspacePreservation:
    """Canonical classification of filesystem drift caused during one review."""

    status: str
    workspace_preserved: bool
    product_state_preserved: bool
    authorized_side_effect_drift: bool
    changed_paths: tuple[str, ...]
    source_changed_paths: tuple[str, ...]
    authorized_side_effect_changed_paths: tuple[str, ...]
    unexpected_changed_paths: tuple[str, ...]


def diff_workspace_hashes(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    """Return every whole-workspace entry whose presence or identity changed."""

    paths = set(before) | set(after)
    return tuple(sorted(path for path in paths if before.get(path) != after.get(path)))


def reviewed_workspace_integrity_hash(
    file_hashes: Mapping[str, str],
    allowed_source_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str],
) -> str:
    """Hash product and unexpected state while excluding authorized side effects.

    The adapter's full snapshot still observes every non-runtime-cache entry.
    Only regular entries beneath an explicit Owner-authorized side-effect root
    are removed from this identity. Source paths win over an overlapping root,
    and symlinks remain protected so lexical authorization cannot hide a
    retargeted alias.
    """

    product, _, unexpected = partition_reviewed_workspace_state(
        file_hashes,
        allowed_source_paths,
        allowed_side_effect_paths,
    )
    protected = {**product, **unexpected}
    return workspace_hash(protected)


def partition_reviewed_workspace_state(
    file_hashes: Mapping[str, str],
    allowed_source_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Partition one snapshot into product, authorized-side-effect, and other state."""

    source_paths, side_effect_paths, unexpected_paths = classify_workspace_changes(
        tuple(sorted(file_hashes)),
        allowed_source_paths,
        allowed_side_effect_paths,
    )
    source = {path: file_hashes[path] for path in source_paths}
    side_effects: dict[str, str] = {}
    unexpected = {path: file_hashes[path] for path in unexpected_paths}
    for path in side_effect_paths:
        digest = str(file_hashes[path])
        if digest.startswith("symlink:") and not digest.startswith("symlink:internal:"):
            unexpected[path] = file_hashes[path]
        else:
            side_effects[path] = file_hashes[path]
    return source, side_effects, unexpected


def classify_review_workspace_preservation(
    before: Mapping[str, str],
    after: Mapping[str, str],
    *,
    allowed_source_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str],
    workspace: Path,
) -> ReviewWorkspacePreservation:
    """Classify review-time drift with the adapter's canonical scope semantics."""

    changed_paths = diff_workspace_hashes(before, after)
    source, side_effects, unexpected = classify_workspace_changes(
        changed_paths,
        allowed_source_paths,
        allowed_side_effect_paths,
        workspace_root=workspace,
    )

    # A removed or retargeted symlink may no longer be inspectable through the
    # live workspace. Keep all symlink identity drift in the protected class,
    # even when its lexical path sits under an authorized side-effect root.
    unsafe_symlink_drift = {
        path
        for path in side_effects
        if (
            str(before.get(path, "")).startswith("symlink:")
            and not str(before.get(path, "")).startswith("symlink:internal:")
        )
        or (
            str(after.get(path, "")).startswith("symlink:")
            and not str(after.get(path, "")).startswith("symlink:internal:")
        )
    }
    if unsafe_symlink_drift:
        side_effects = tuple(path for path in side_effects if path not in unsafe_symlink_drift)
        unexpected = tuple(sorted((*unexpected, *unsafe_symlink_drift)))

    product_state_preserved = not source
    workspace_preserved = product_state_preserved and not unexpected
    if unexpected:
        status = "UNEXPECTED_DRIFT"
    elif source:
        status = "PRODUCT_STATE_DRIFT"
    elif side_effects:
        status = "AUTHORIZED_SIDE_EFFECT_DRIFT"
    else:
        status = "PRODUCT_STATE_PRESERVED"
    return ReviewWorkspacePreservation(
        status=status,
        workspace_preserved=workspace_preserved,
        product_state_preserved=product_state_preserved,
        authorized_side_effect_drift=bool(side_effects),
        changed_paths=changed_paths,
        source_changed_paths=source,
        authorized_side_effect_changed_paths=side_effects,
        unexpected_changed_paths=unexpected,
    )


def review_repository_identity_preserved(
    workspace: Path,
    *,
    baseline_ref: str,
    baseline_commit: str,
    expected_head: str,
    expected_index_identity: str,
    expected_git_metadata_hash: str,
    actual_git_metadata_hash: str | None = None,
) -> bool:
    """Require the complete repository identity used by review/repair preflight."""

    if actual_git_metadata_hash is None:
        try:
            actual_git_metadata_hash = protected_git_metadata_hash(workspace)
        except EvidenceIntegrityFailure:
            return False
    return (
        (git_rev_parse(workspace, "HEAD") or "") == expected_head
        and git_index_clean(workspace)
        and git_index_identity(workspace) == expected_index_identity
        and git_special_state(workspace) is None
        and git_rev_parse(workspace, baseline_ref) == baseline_commit
        and actual_git_metadata_hash == expected_git_metadata_hash
    )


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
    try:
        run_directory.lstat()
    except OSError:
        raise PreflightFailure(
            "MISSING_IMPLEMENTATION_EVIDENCE",
            "implementation run directory does not exist",
        ) from None
    try:
        evidence_snapshot = read_canonical_evidence(
            run_directory,
            ("receipt.json", "result.json", "diff.patch"),
        )
    except EvidenceIntegrityFailure as exc:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE",
            f"implementation evidence identity is unsafe: {exc}",
        ) from None

    try:
        receipt = json.loads(evidence_snapshot.file_bytes["receipt.json"].decode("utf-8"))
        result = json.loads(evidence_snapshot.file_bytes["result.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE", f"evidence file is not readable/valid JSON: {exc.__class__.__name__}"
        ) from None

    try:
        patch_text = evidence_snapshot.file_bytes["diff.patch"].decode("utf-8")
    except UnicodeDecodeError as exc:
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
        completed = execute_bounded(
            argv,
            cwd=str(cwd),
            timeout_seconds=float(timeout_seconds),
            env=_read_only_subprocess_env(),
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            command=text,
            exit_code=completed.exit_code,
            timed_out=completed.timed_out,
            duration_ms=duration_ms,
            stdout_excerpt=_excerpt(completed.stdout),
            stderr_excerpt=_excerpt(completed.stderr),
            stdout_sha256=_sha256(completed.stdout),
            stderr_sha256=_sha256(completed.stderr),
        )
    except ProcessQuiescenceFailure as exc:
        raise PreflightFailure(
            "SUBPROCESS_QUIESCENCE_UNVERIFIED",
            f"deterministic check process group was not proven quiescent: {exc}",
        ) from None
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
        completed = execute_bounded(
            [executable, *argv_tail],
            cwd=Path.cwd(),
            timeout_seconds=_HELP_PROBE_TIMEOUT_SECONDS,
            env=_read_only_subprocess_env(),
        )
    except ProcessQuiescenceFailure as exc:
        raise PreflightFailure(
            "SUBPROCESS_QUIESCENCE_UNVERIFIED",
            f"Codex capability probe process group was not proven quiescent: {exc}",
        ) from None
    except OSError:
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
        raise ProcessQuiescenceFailure("process-group verification is unavailable")
    try:
        if pgid == os.getpgrp():
            raise ProcessQuiescenceFailure("refusing to inspect TOMTIT's own process group")
    except OSError as exc:
        raise ProcessQuiescenceFailure(
            f"process-group identity is unavailable: {exc.__class__.__name__}"
        ) from None
    alive = _group_alive(pgid)
    if alive is False:
        return
    if alive is None:
        raise ProcessQuiescenceFailure("process group state is indeterminate")
    if not _signal_group(pgid, signal.SIGTERM):
        if _group_alive(pgid) is False:
            return
        raise ProcessQuiescenceFailure("process group could not be controlled with SIGTERM")
    alive = _await_group_absence(
        pgid,
        time.monotonic() + _PROCESS_CLEANUP_GRACE_SECONDS,
    )
    if alive is False:
        return
    if alive is None:
        raise ProcessQuiescenceFailure("process group state became indeterminate")
    if not _signal_group(pgid, signal.SIGKILL):
        if _group_alive(pgid) is False:
            return
        raise ProcessQuiescenceFailure("process group could not be controlled with SIGKILL")
    alive = _await_group_absence(
        pgid,
        time.monotonic() + _PROCESS_CLEANUP_GRACE_SECONDS,
    )
    if alive is not False:
        raise ProcessQuiescenceFailure("process group remained present after SIGKILL")


@dataclasses.dataclass(frozen=True, slots=True)
class ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    duration_ms: int


def execute_bounded(
    argv: list[str],
    *,
    cwd: Path | str,
    timeout_seconds: float,
    provider_observer: Callable[[bool | None], None] | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessOutcome:
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        if provider_observer is not None:
            provider_observer(False)
        raise
    if provider_observer is not None:
        provider_observer(True)
    pgid = proc.pid
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _signal_group(pgid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=_PROCESS_CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(pgid, signal.SIGKILL)
            try:
                stdout, stderr = proc.communicate(
                    timeout=_PROCESS_CLEANUP_GRACE_SECONDS
                )
            except subprocess.TimeoutExpired:
                stdout, stderr = b"", b""
    except OSError:
        _signal_group(pgid, signal.SIGTERM)
        wait = getattr(proc, "wait", None)
        if callable(wait):
            try:
                wait(timeout=_PROCESS_CLEANUP_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                _signal_group(pgid, signal.SIGKILL)
        _ensure_group_quiescent(pgid)
        raise
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


def resolve_evidence_directory(
    evidence_directory_arg: str | None,
    review_run_id: str,
    workspace: Path,
) -> Path:
    root = Path(evidence_directory_arg).expanduser() if evidence_directory_arg else (REPO_ROOT / ".tomtit" / "dogfood" / "reviews")
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve(strict=False)
    else:
        root = root.resolve(strict=False)
    directory = root / review_run_id
    if _is_within(directory, workspace):
        raise PreflightFailure(
            "EVIDENCE_DIRECTORY_INSIDE_WORKSPACE",
            "review evidence directory must be outside the target workspace",
        )
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
    raw_implementation_allowed_paths = receipt_data.get("allowed_changed_paths")
    if not isinstance(raw_implementation_allowed_paths, list) or not all(
        isinstance(path, str) for path in raw_implementation_allowed_paths
    ):
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE",
            "implementation receipt allowed_changed_paths is malformed",
        )
    try:
        implementation_allowed_paths = normalize_allowed_paths(
            raw_implementation_allowed_paths,
            workspace,
        )
    except PreflightFailure as exc:
        raise PreflightFailure(
            "INVALID_IMPLEMENTATION_EVIDENCE",
            f"implementation receipt allowed_changed_paths is invalid: {exc.category}",
        ) from None
    if implementation_allowed_paths != allowed_paths:
        raise PreflightFailure(
            "ALLOWED_PATH_CONTRACT_MISMATCH",
            "review allowed paths do not exactly match the bound implementation contract",
        )
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

    evidence_directory = resolve_evidence_directory(
        args.evidence_directory,
        review_run_id,
        workspace,
    )

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
        "allowed_side_effect_paths": allowed_side_effect_paths,
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


def main(
    argv: list[str] | None = None,
    *,
    provider_observer: Callable[[bool | None], None] | None = None,
) -> int:
    args = parse_args(argv)

    try:
        pre = run_preflight(args)
    except PreflightFailure as exc:
        if provider_observer is not None:
            provider_observer(False)
        print(f"STATUS=PREFLIGHT_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    workspace: Path = pre["workspace"]
    baseline_commit: str = pre["baseline_commit"]
    allowed_paths: tuple[str, ...] = pre["allowed_paths"]
    allowed_side_effect_paths: tuple[str, ...] = pre["allowed_side_effect_paths"]
    evidence: dict[str, Any] = pre["implementation_evidence"]
    task_prompt: str = pre["task_prompt"]
    review_run_id: str = pre["review_run_id"]
    evidence_directory: Path = pre["evidence_directory"]

    # Establish the reviewed workspace identity before even the Codex
    # capability/help probes. Those probes are subprocesses of the configured
    # executable and therefore must be treated as untrusted: unlike
    # Owner-authorized deterministic checks, they may not change any workspace
    # or repository state at all.
    review_entry_hashes = snapshot_workspace_hashes(workspace)
    review_entry_head = git_rev_parse(workspace, "HEAD") or ""
    review_entry_index_identity = git_index_identity(workspace) or ""
    try:
        review_entry_git_metadata = protected_git_metadata_snapshot(workspace)
    except EvidenceIntegrityFailure as exc:
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=PREFLIGHT_FAILURE:GIT_METADATA_IDENTITY_UNAVAILABLE")
        print(f"MESSAGE={exc}")
        return 3
    review_entry_git_metadata_hash = workspace_hash(review_entry_git_metadata)
    review_entry_git_entry_hash = workspace_git_entry_hash_from_snapshot(
        review_entry_git_metadata
    )

    resolved_codex_contract: tuple[
        str,
        dict[str, bool],
        tuple[str, str],
    ] | None = None
    codex_contract_failure: PreflightFailure | None = None
    try:
        resolved_codex_contract = resolve_codex_contract(args.codex_executable)
    except PreflightFailure as exc:
        # Defer this return until the untrusted probe interval has been
        # measured. A probe that both mutates the workspace and advertises an
        # invalid contract is still first and foremost an immutability breach.
        codex_contract_failure = exc

    capability_probe_hashes = snapshot_workspace_hashes(workspace)
    try:
        capability_probe_git_metadata = protected_git_metadata_snapshot(workspace)
    except EvidenceIntegrityFailure:
        capability_probe_git_metadata = {}
    capability_probe_git_metadata_hash = workspace_hash(capability_probe_git_metadata)
    capability_probe_git_entry_hash = (
        workspace_git_entry_hash_from_snapshot(capability_probe_git_metadata)
        if capability_probe_git_metadata
        else ""
    )
    capability_probe_repository_identity_preserved = (
        review_repository_identity_preserved(
            workspace,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            expected_head=review_entry_head,
            expected_index_identity=review_entry_index_identity,
            expected_git_metadata_hash=review_entry_git_metadata_hash,
            actual_git_metadata_hash=capability_probe_git_metadata_hash,
        )
    )
    if (
        capability_probe_hashes != review_entry_hashes
        or not capability_probe_repository_identity_preserved
    ):
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=PREFLIGHT_FAILURE:IMMUTABILITY_VIOLATION")
        print("MESSAGE=Codex capability probes changed workspace or repository state")
        return 3
    if codex_contract_failure is not None:
        if provider_observer is not None:
            provider_observer(False)
        print(f"STATUS=PREFLIGHT_FAILURE:{codex_contract_failure.category}")
        print(f"MESSAGE={codex_contract_failure.message}")
        return 3
    assert resolved_codex_contract is not None
    codex_executable, codex_caps, codex_approval = resolved_codex_contract

    try:
        check_results = run_deterministic_checks(args.check_command, workspace, args.timeout_seconds)
    except PreflightFailure as exc:
        if provider_observer is not None:
            provider_observer(False)
        print(f"STATUS=PREFLIGHT_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3
    post_check_hashes = snapshot_workspace_hashes(workspace)
    post_check_head = git_rev_parse(workspace, "HEAD") or ""
    post_check_index_identity = git_index_identity(workspace) or ""
    try:
        post_check_git_metadata = protected_git_metadata_snapshot(workspace)
    except EvidenceIntegrityFailure:
        post_check_git_metadata = {}
    post_check_git_metadata_hash = workspace_hash(post_check_git_metadata)
    post_check_git_entry_hash = (
        workspace_git_entry_hash_from_snapshot(post_check_git_metadata)
        if post_check_git_metadata
        else ""
    )
    check_preservation = classify_review_workspace_preservation(
        review_entry_hashes,
        post_check_hashes,
        allowed_source_paths=allowed_paths,
        allowed_side_effect_paths=allowed_side_effect_paths,
        workspace=workspace,
    )
    check_repository_identity_preserved = review_repository_identity_preserved(
        workspace,
        baseline_ref=args.baseline_ref,
        baseline_commit=baseline_commit,
        expected_head=review_entry_head,
        expected_index_identity=review_entry_index_identity,
        expected_git_metadata_hash=review_entry_git_metadata_hash,
        actual_git_metadata_hash=post_check_git_metadata_hash,
    )
    if not check_repository_identity_preserved or not check_preservation.workspace_preserved:
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=REVIEW_FAILURE:IMMUTABILITY_VIOLATION")
        print("MESSAGE=deterministic checks changed product, unexpected, or repository state")
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
        try:
            outcome = execute_bounded(
                argv,
                cwd=Path(tmp_dir),
                timeout_seconds=float(args.timeout_seconds),
                provider_observer=provider_observer,
                env=_read_only_subprocess_env(),
            )
        except (OSError, ProcessQuiescenceFailure):
            print("STATUS=PREFLIGHT_FAILURE:CODEX_PROCESS_EXECUTION_FAILED")
            return 3
        codex_exit_code = outcome.exit_code
        timed_out = outcome.timed_out
        duration_ms = outcome.duration_ms

        after_snapshot = snapshot_workspace(workspace, baseline_commit)
        modified_paths = diff_snapshots(before_snapshot, after_snapshot)
        review_exit_hashes = snapshot_workspace_hashes(workspace)
        review_exit_head = git_rev_parse(workspace, "HEAD") or ""
        try:
            review_exit_git_metadata = protected_git_metadata_snapshot(workspace)
        except EvidenceIntegrityFailure:
            review_exit_git_metadata = {}
        review_exit_git_metadata_hash = workspace_hash(review_exit_git_metadata)
        review_exit_git_entry_hash = (
            workspace_git_entry_hash_from_snapshot(review_exit_git_metadata)
            if review_exit_git_metadata
            else ""
        )
        reviewer_modified_paths = diff_workspace_hashes(
            post_check_hashes,
            review_exit_hashes,
        )
        preservation = classify_review_workspace_preservation(
            review_entry_hashes,
            review_exit_hashes,
            allowed_source_paths=allowed_paths,
            allowed_side_effect_paths=allowed_side_effect_paths,
            workspace=workspace,
        )
        reviewer_repository_identity_preserved = review_repository_identity_preserved(
            workspace,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            expected_head=post_check_head,
            expected_index_identity=post_check_index_identity,
            expected_git_metadata_hash=post_check_git_metadata_hash,
            actual_git_metadata_hash=review_exit_git_metadata_hash,
        )
        repository_identity_preserved = review_repository_identity_preserved(
            workspace,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            expected_head=review_entry_head,
            expected_index_identity=review_entry_index_identity,
            expected_git_metadata_hash=review_entry_git_metadata_hash,
            actual_git_metadata_hash=review_exit_git_metadata_hash,
        )
        workspace_unchanged = (
            preservation.workspace_preserved
            and repository_identity_preserved
            and not reviewer_modified_paths
            and reviewer_repository_identity_preserved
        )
        preservation_status = (
            preservation.status
            if (
                repository_identity_preserved
                and not reviewer_modified_paths
                and reviewer_repository_identity_preserved
            )
            else "UNEXPECTED_DRIFT"
        )
        reviewed_integrity_hash = reviewed_workspace_integrity_hash(
            review_exit_hashes,
            allowed_paths,
            allowed_side_effect_paths,
        )
        reviewed_product_state, reviewed_side_effect_state, reviewed_unexpected_state = (
            partition_reviewed_workspace_state(
                review_exit_hashes,
                allowed_paths,
                allowed_side_effect_paths,
            )
        )
        side_effect_counts, side_effect_sample = summarize_side_effect_paths(
            preservation.authorized_side_effect_changed_paths,
            allowed_side_effect_paths,
        )
        capability_probe_git_metadata_changed_paths = diff_workspace_hashes(
            review_entry_git_metadata,
            capability_probe_git_metadata,
        )
        deterministic_check_git_metadata_changed_paths = diff_workspace_hashes(
            review_entry_git_metadata,
            post_check_git_metadata,
        )
        reviewer_git_metadata_changed_paths = diff_workspace_hashes(
            post_check_git_metadata,
            review_exit_git_metadata,
        )
        overall_git_metadata_changed_paths = diff_workspace_hashes(
            review_entry_git_metadata,
            review_exit_git_metadata,
        )

        try:
            if reviewer_modified_paths or not reviewer_repository_identity_preserved:
                raise ReviewFailure(
                    "IMMUTABILITY_VIOLATION",
                    "Codex changed workspace or repository state during read-only review",
                )
            if modified_paths:
                raise ReviewFailure("IMMUTABILITY_VIOLATION", f"Codex modified workspace paths: {modified_paths}")
            if not repository_identity_preserved:
                raise ReviewFailure(
                    "IMMUTABILITY_VIOLATION",
                    "review checks or reviewer changed the workspace repository identity",
                )
            if not preservation.workspace_preserved:
                changed = tuple(
                    sorted(
                        (*preservation.source_changed_paths, *preservation.unexpected_changed_paths)
                    )
                )
                raise ReviewFailure(
                    "IMMUTABILITY_VIOLATION",
                    f"review changed product or unexpected workspace paths: {list(changed)}",
                )
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
        "provider_invoked": invocation_count > 0,
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
        "reviewer_workspace_unchanged": (
            not reviewer_modified_paths and reviewer_repository_identity_preserved
        ),
        "reviewer_modified_paths": list(reviewer_modified_paths),
        "reviewer_repository_identity_preserved": (
            reviewer_repository_identity_preserved
        ),
        "workspace_preservation_status": preservation_status,
        "product_state_preserved": preservation.product_state_preserved,
        "repository_identity_preserved": repository_identity_preserved,
        "protected_git_metadata_preserved": not overall_git_metadata_changed_paths,
        "workspace_git_entry_preserved": (
            bool(review_exit_git_entry_hash)
            and review_exit_git_entry_hash == review_entry_git_entry_hash
        ),
        "protected_git_metadata_changed_path_count": len(
            overall_git_metadata_changed_paths
        ),
        "protected_git_metadata_changed_paths_sample": list(
            overall_git_metadata_changed_paths[:20]
        ),
        "capability_probe_protected_git_metadata_preserved": (
            not capability_probe_git_metadata_changed_paths
        ),
        "capability_probe_workspace_git_entry_preserved": (
            bool(capability_probe_git_entry_hash)
            and capability_probe_git_entry_hash == review_entry_git_entry_hash
        ),
        "deterministic_check_protected_git_metadata_preserved": (
            not deterministic_check_git_metadata_changed_paths
        ),
        "deterministic_check_workspace_git_entry_preserved": (
            bool(post_check_git_entry_hash)
            and post_check_git_entry_hash == review_entry_git_entry_hash
        ),
        "reviewer_protected_git_metadata_preserved": (
            not reviewer_git_metadata_changed_paths
        ),
        "reviewer_workspace_git_entry_preserved": (
            bool(review_exit_git_entry_hash)
            and review_exit_git_entry_hash == post_check_git_entry_hash
        ),
        "authorized_side_effect_drift": preservation.authorized_side_effect_drift,
        "review_source_changed_paths": list(preservation.source_changed_paths),
        "review_unexpected_changed_paths": list(preservation.unexpected_changed_paths),
        "review_authorized_side_effect_changed_path_count": len(
            preservation.authorized_side_effect_changed_paths
        ),
        "review_authorized_side_effect_changed_counts_by_root": {
            root: count for root, count in side_effect_counts
        },
        "review_authorized_side_effect_changed_paths_sample": list(side_effect_sample),
        "deterministic_check_workspace_preservation_status": (
            check_preservation.status
            if check_repository_identity_preserved
            else "UNEXPECTED_DRIFT"
        ),
        "deterministic_check_product_state_preserved": (
            check_preservation.product_state_preserved
        ),
        "deterministic_check_repository_identity_preserved": (
            check_repository_identity_preserved
        ),
        "deterministic_check_authorized_side_effect_drift": (
            check_preservation.authorized_side_effect_drift
        ),
        "deterministic_check_source_changed_paths": list(
            check_preservation.source_changed_paths
        ),
        "deterministic_check_unexpected_changed_paths": list(
            check_preservation.unexpected_changed_paths
        ),
        "reviewed_head": review_exit_head,
        "reviewed_index_clean": git_index_clean(workspace),
        "reviewed_index_identity": git_index_identity(workspace),
        "reviewed_protected_git_metadata_hash": review_exit_git_metadata_hash,
        "reviewed_workspace_git_entry_hash": review_exit_git_entry_hash,
        "reviewed_git_special_state": git_special_state(workspace),
        "reviewed_baseline_commit": git_rev_parse(workspace, args.baseline_ref),
        "reviewed_allowed_source_paths": list(allowed_paths),
        "reviewed_workspace_integrity_hash": reviewed_integrity_hash,
        "reviewed_product_state_hash": workspace_hash(reviewed_product_state),
        "reviewed_authorized_side_effect_state_hash": workspace_hash(
            reviewed_side_effect_state
        ),
        "reviewed_unexpected_state_hash": workspace_hash(reviewed_unexpected_state),
        "reviewed_workspace_full_hash": workspace_hash(review_exit_hashes),
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
