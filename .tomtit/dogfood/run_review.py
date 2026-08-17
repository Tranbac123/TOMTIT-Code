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

# Bounded observation budget for establishing the *initial* protected-Git-
# metadata baseline (see `establish_protected_git_metadata_baseline`). A
# separate knob from the one above: that one bounds *retries of a single
# already-trusted-anchored measurement*; this one bounds *independent
# from-scratch observations compared against each other*, since bootstrap
# has no external trust to anchor a retry to. Not a general-purpose retry
# knob.
_MAX_INITIAL_BASELINE_OBSERVATIONS = 3

# The exact, complete descriptor grammars emitted by `_directory_descriptor`
# and `_regular_file_measurement` (see those two functions). `mode` is
# `stat.S_IMODE(...)` formatted `:04o`, whose value is masked to at most
# 0o7777 -- i.e. always rendered as exactly four octal digits. `sha256` is a
# `hashlib.sha256().hexdigest()`, always exactly 64 lowercase hex digits.
#
# `dev`/`ino`/`size`/`nlink`/`mtime_ns`/`ctime_ns` are all rendered with
# Python's plain `{int}` (i.e. `str(int)`) formatting of an `os.stat_result`
# field. That formatting is lexically exact: "0", or a non-zero digit
# sequence with no leading zeros, optionally negated for a negative value
# (never observed in practice for these fields, but the grammar must match
# what the formatting *can* produce, not a narrower approximation of it). It
# never emits an alternate spelling of the same value such as "00", "01",
# "-0", or "-01" -- so those must NOT be accepted as canonical, even though
# they look numeric.
_CANONICAL_INT = r"(?:0|-?[1-9][0-9]*)"
#
# `_strip_volatile_timestamp_fields` requires a `fullmatch` against one of
# these two patterns -- the *entire* input, not merely a prefix -- before
# stripping anything. Any input that is not byte-for-byte one of these two
# canonical shapes (a "path:"/"symlink:" descriptor, an unknown type, a
# truncated or reordered dir:/file: string, a field spelled with a
# non-canonical integer lexical form, or any other caller-controlled text
# that merely begins with "dir:" or "file:") is returned unchanged.
_CANONICAL_DIR_DESCRIPTOR_RE = re.compile(
    r"dir:dev=(?P<dev>" + _CANONICAL_INT + r"):ino=(?P<ino>" + _CANONICAL_INT + r"):"
    r"mode=(?P<mode>[0-7]{4}):size=(?P<size>" + _CANONICAL_INT + r"):"
    r"nlink=(?P<nlink>" + _CANONICAL_INT + r"):"
    r"mtime_ns=(?P<mtime_ns>" + _CANONICAL_INT + r"):ctime_ns=(?P<ctime_ns>" + _CANONICAL_INT + r")"
)
_CANONICAL_FILE_DESCRIPTOR_RE = re.compile(
    r"file:dev=(?P<dev>" + _CANONICAL_INT + r"):ino=(?P<ino>" + _CANONICAL_INT + r"):"
    r"mode=(?P<mode>[0-7]{4}):size=(?P<size>" + _CANONICAL_INT + r"):"
    r"nlink=(?P<nlink>" + _CANONICAL_INT + r"):"
    r"mtime_ns=(?P<mtime_ns>" + _CANONICAL_INT + r"):ctime_ns=(?P<ctime_ns>" + _CANONICAL_INT + r"):"
    r"sha256=(?P<sha256>[0-9a-f]{64})"
)
# The *stable cross-phase projection* of a `file:` descriptor -- i.e. what
# `_strip_volatile_timestamp_fields` actually returns, and therefore what a
# trusted_baseline value (the return of a prior `protected_git_metadata_snapshot()`
# call) really looks like: identical to `_CANONICAL_FILE_DESCRIPTOR_RE` but
# with the `mtime_ns=...:ctime_ns=...` segment entirely absent, never merely
# optional -- a projected value can never legitimately carry it.
_STABLE_FILE_DESCRIPTOR_RE = re.compile(
    r"file:dev=(?P<dev>" + _CANONICAL_INT + r"):ino=(?P<ino>" + _CANONICAL_INT + r"):"
    r"mode=(?P<mode>[0-7]{4}):size=(?P<size>" + _CANONICAL_INT + r"):"
    r"nlink=(?P<nlink>" + _CANONICAL_INT + r"):"
    r"sha256=(?P<sha256>[0-9a-f]{64})"
)

# Canonical effective-index identity (see `canonical_index_projection`,
# `git_index_identity`). Byte-safe: built from `-z`-terminated, byte-mode
# subprocess output (`_run_git_bytes`), never text-mode, so no path byte
# sequence is lost or collapsed through a lossy Unicode round-trip. Every
# parsed field must fullmatch these grammars or the whole observation is
# unclassified.
_HEX_DIGITS_RE = re.compile(rb"[0-9a-f]+")
# Every Git object-storage format this Runtime recognizes, and the exact
# hex-digest width a full object id must have under that format (see
# `_repository_object_format`). This is POSITIVELY determined once per
# complete projection attempt via a read-only Git plumbing query -- never
# inferred per-record from a presented OID's own length, which would let a
# SHA-256-width id become "valid" inside a SHA-1 repository or vice versa.
_REPOSITORY_OBJECT_FORMAT_HEX_LENGTH = {"sha1": 40, "sha256": 64}
# The well-known empty-blob object id for each Git object format this
# runner recognizes -- `sha1("blob 0\0")` / `sha256("blob 0\0")`. Used only
# for the intent-to-add cross-check (Section 13/15): the *only* thing that
# makes an `add -N` entry visible to `ls-files --stage` distinguishable from
# a genuinely empty tracked file. Selected by the repository's own
# positively-determined object format, never by a presented OID's length.
_EMPTY_BLOB_OBJECT_ID_BY_FORMAT = {
    "sha1": b"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "sha256": b"473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813",
}
# Exact canonical stage spelling: a single digit, never "00", "+0", etc.
_INDEX_STAGE_RE = re.compile(rb"[0-3]")
_INDEX_RESOLVE_UNDO_STAGE_RE = re.compile(rb"[1-3]")
# `git ls-files --stage -t -v -z` tag letters for a plain `--stage` (cached)
# listing: H = ordinary cached entry, S = skip-worktree, M = unmerged.
# R/C/K/?/U only appear when -d/-m/-o/-k/--resolve-undo are also requested,
# which this query never does -- any other letter is unclassified.
_INDEX_STAGE_BASE_TAGS = frozenset({b"H", b"S", b"M"})
# The exact set of Git modes a real cached index entry (a stage record or a
# resolve-undo record) can ever have: regular file, executable file,
# symlink, gitlink/submodule. `git ls-files` without `--sparse` never emits
# a tree-mode (040000) entry (sparse directories are always expanded to
# their contained files), and no *cached* entry is ever mode 000000 -- that
# value only ever means "absent" in porcelain-v2's HEAD/index/worktree mode
# triplet (see `_STATUS_VALID_MODES`). A lexically six-digit octal string
# such as "777777" matches a bare `[0-7]{6}` pattern but is not a real Git
# mode and must never be accepted merely for looking octal-shaped.
_INDEX_ENTRY_VALID_MODES = frozenset({b"100644", b"100755", b"120000", b"160000"})
_STATUS_MODE_ABSENT = b"000000"
_STATUS_VALID_MODES = _INDEX_ENTRY_VALID_MODES | {_STATUS_MODE_ABSENT}
# Porcelain-v2 XY alphabets, empirically confirmed against real
# `git status --porcelain=v2` output for every state exercised by this
# Runtime's own regression matrix (ordinary/staged/worktree modify, add,
# delete, typechange, rename, copy, intent-to-add -- including the
# `add -N` case, which emits Y="A" (worktree "added" relative to the
# index's empty placeholder), confirmed by direct experiment -- and every
# unmerged combination). Type "1" (ordinary changed) entries: X (index vs
# HEAD) is never R/C (those are always emitted as type "2" instead) and
# never U (unmerged entries are always emitted as type "u" instead). Type
# "2" (renamed/copied) entries: X is always exactly R or C. Y (worktree vs
# index) uses the same alphabet as X for both record types.
# NOTE: `frozenset(b".MTAD")` would iterate the bytes object into its
# integer byte codes, not single-byte `bytes` objects -- never a match
# against a `xy[0:1]`-style single-byte slice. Every alphabet below is
# built from explicit single-byte `bytes` literals instead.
_STATUS_TYPE1_X_CHARS = frozenset({b".", b"M", b"T", b"A", b"D"})
_STATUS_TYPE1_Y_CHARS = frozenset({b".", b"M", b"T", b"A", b"D"})
_STATUS_TYPE2_X_CHARS = frozenset({b"R", b"C"})
# A type "1" (ordinary changed) record can never legitimately carry
# ".." (nothing changed -- git never emits a changed-path record for an
# unchanged path) or any of the seven documented unmerged XY pairs (those
# states are always emitted as type "u", never type "1"). Type "2"'s X is
# always drawn from `_STATUS_TYPE2_X_CHARS` above, which never overlaps
# this set, so the check is a no-op there but applied uniformly for clarity
# (F04-R2/Section 9, Codex's exact reproduced "XY=.." and "XY=AA" on a
# type-1 record).
_STATUS_TYPE1_TYPE2_FORBIDDEN_XY = frozenset({b".."}) | frozenset(
    {b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"}
)
# For each of the seven documented unmerged ("u") XY pairs: whether stage 1
# (common ancestor), stage 2 (ours/HEAD), and stage 3 (theirs/MERGE_HEAD)
# is PRESENT in that state (F04-R2/Section 11). Empirically confirmed by
# direct real-conflict experiment for UU (all three present -- both sides
# modified), AA (stage 1 absent -- both sides independently added the same
# new path, so no common-ancestor entry exists), DU (stage 2 absent --
# deleted by us, real `modify/delete` conflict), and UD (stage 3 absent --
# deleted by them, the same conflict from the other branch's perspective).
# DD/AU/UA are derived from the single consistent positional rule those
# four confirmations establish: "A" at a position means no common-ancestor
# entry exists (stage 1 absent); "D" at a position means that side's own
# stage is absent; "U" at a position means that side's own stage is
# present. This is not an independent per-character alphabet check (unlike
# type 1/2's XY) -- only these exact seven pairs, each with its own fixed
# stage-presence pattern, are real Git states.
_UNMERGED_STAGE_PRESENCE: dict[bytes, tuple[bool, bool, bool]] = {
    b"DD": (True, False, False),
    b"AU": (False, True, True),
    b"UD": (True, True, False),
    b"UA": (False, True, True),
    b"DU": (True, False, True),
    b"AA": (False, True, True),
    b"UU": (True, True, True),
}
# `<sub>` submodule-state field grammar: "N..." for a non-submodule path, or
# "S<c><m><u>" for a submodule, where each of c/m/u is either its own
# documented letter or "." (empirically confirmed: a non-submodule change
# shows exactly "N...", a submodule with no state change shows "S...").
_STATUS_SUBMODULE_NOT_A_SUBMODULE = b"N..."
_STATUS_SUBMODULE_RE = re.compile(rb"S[C.][M.][U.]")
# `<X><score>` rename/copy score grammar for a type "2" entry: the same
# single letter as the record's own XY.X value, followed by a similarity
# PERCENTAGE (0-100 inclusive; range enforced separately in
# `_valid_type2_score`, since a regex alone cannot bound a variable-length
# decimal) with no leading zero (empirically confirmed via `git mv`:
# "R100"). Codex's reproduced malformed examples "Xbad" (rejected by this
# pattern: wrong letter, non-digit score) and "R101"/"R999" (lexically
# match this pattern but are out of the valid percentage range, rejected by
# `_valid_type2_score`) are both covered.
_STATUS_TYPE2_SCORE_RE = re.compile(rb"[RC](0|[1-9][0-9]*)")
# Bounded observation budget for establishing ONE coherent canonical index
# projection (see `canonical_index_projection`). No single Git command can
# produce the complete canonical index state atomically -- the projection
# is assembled from three separate queries -- so this bounds independent,
# from-scratch, whole-projection rebuilds compared consecutively against
# each other, exactly mirroring `_MAX_INITIAL_BASELINE_OBSERVATIONS`'s
# already-approved 2-of-3-consecutive-match algorithm. Not a general-purpose
# retry knob, and never nested inside another retry loop.
_MAX_CANONICAL_INDEX_OBSERVATIONS = 3

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


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedRegularFileEvidence:
    """Trusted per-path nonvolatile identity + content evidence for one
    regular file, sourced *only* from a previously successful, fully
    coherent `protected_git_metadata_snapshot()` call -- never from an
    unstable reading observed during the *current* call. `mtime_ns`/
    `ctime_ns` are deliberately absent: they are exactly the two fields a
    timestamp-only instability is allowed to differ on.

    This is the fix for the reproduced proof gap where an unstable
    attempt's own SHA was (incorrectly) trusted as a baseline for a later
    retry within the *same* call: a same-size, same-inode, same-mode
    content mutation occurring immediately before the first read would
    then pass as "coherent," because both readings agreed with each
    other -- while neither was ever checked against genuinely
    pre-mutation content. Comparison must be against evidence from
    *outside* the current call.
    """

    dev: int
    ino: int
    mode: int
    size: int
    nlink: int
    sha256: str

    def matches(
        self,
        *,
        dev: int,
        ino: int,
        mode: int,
        size: int,
        nlink: int,
        sha256: str,
    ) -> bool:
        """True only if every load-bearing (non-timestamp) field agrees."""

        return (
            self.dev == dev
            and self.ino == ino
            and self.mode == mode
            and self.size == size
            and self.nlink == nlink
            and self.sha256 == sha256
        )


def _parse_trusted_file_descriptor(descriptor: str) -> TrustedRegularFileEvidence | None:
    """Safely extract trusted per-file evidence from a stable-projected
    identity descriptor string -- i.e. a value out of a `trusted_baseline`
    mapping, which is always the *return value* of a prior
    `protected_git_metadata_snapshot()` call and therefore already has its
    `mtime_ns`/`ctime_ns` fields stripped (see `_STABLE_FILE_DESCRIPTOR_RE`).
    Uses the same approved, exact `fullmatch()`-anchored parsing style
    `_strip_volatile_timestamp_fields` relies on -- never an ad-hoc or
    partial parse. Returns `None` for anything that is not byte-for-byte a
    stable-projected `file:` descriptor (a `dir:`/`path:`/`symlink:` entry,
    or any non-canonical text): there is no content evidence to trust for
    those, by construction.
    """

    match = _STABLE_FILE_DESCRIPTOR_RE.fullmatch(descriptor)
    if match is None:
        return None
    fields = match.groupdict()
    return TrustedRegularFileEvidence(
        dev=int(fields["dev"]),
        ino=int(fields["ino"]),
        mode=int(fields["mode"], 8),
        size=int(fields["size"]),
        nlink=int(fields["nlink"]),
        sha256=fields["sha256"],
    )


class TimestampOnlyRegularFileInstability(Exception):
    """A regular-file measurement observed a before/opened/closing/final stat
    mismatch confined *exactly* to `mtime_ns`/`ctime_ns`, where the actually
    -read content and every other nonvolatile field were verified to match
    *trusted* evidence from a prior coherent snapshot (see
    `TrustedRegularFileEvidence`). This is never returned as a successful
    measurement -- it is a diagnostic, retry-triggering signal only. The
    caller (`protected_git_metadata_snapshot`) retries the whole composite
    snapshot from scratch; this exception carries no authority of its own
    and is never itself used as evidence for a later attempt.

    Only raised when a caller explicitly opts in via
    `tolerate_timestamp_only_instability=True` *and* supplies matching
    `trusted_evidence`; every other caller of `_regular_file_measurement`
    (in particular canonical evidence reading, and any timestamp-only
    instability with no trusted evidence available) is completely
    unaffected and keeps failing closed on any mismatch.
    """

    def __init__(
        self,
        *,
        path: str,
        dev: int,
        ino: int,
        mode: int,
        size: int,
        nlink: int,
        sha256: str,
        changed_fields: tuple[str, ...] = ("mtime_ns", "ctime_ns"),
    ) -> None:
        super().__init__(
            f"regular file measurement observed a timestamp-only stat difference: {path}"
        )
        self.path = path
        self.dev = dev
        self.ino = ino
        self.mode = mode
        self.size = size
        self.nlink = nlink
        self.sha256 = sha256
        self.changed_fields = changed_fields


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


# Bounded sample size for `describe_evidence_identity_diff` -- diagnostic
# only, never authoritative, and never an unbounded dump of a mismatched
# evidence tree (see TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REPORT.md).
_EVIDENCE_DIFF_BOUNDED_SAMPLE_SIZE = 10


def _descriptor_type(descriptor: str) -> str:
    prefix, _, _ = descriptor.partition(":")
    return prefix


def _parse_evidence_descriptor_fields(descriptor: str) -> dict[str, str] | None:
    """Parse one canonical `dir:`/`file:` descriptor into its named fields,
    for bounded field-level diagnostic comparison
    (`describe_evidence_identity_diff`) only -- never used to decide
    authority. Returns `None` for a `symlink:` descriptor or anything else
    that does not `fullmatch` the canonical grammar; such values are
    compared only as an opaque whole by the caller.
    """

    match = _CANONICAL_DIR_DESCRIPTOR_RE.fullmatch(descriptor)
    if match is None:
        match = _CANONICAL_FILE_DESCRIPTOR_RE.fullmatch(descriptor)
    if match is None:
        return None
    fields = match.groupdict()
    parsed = {
        "device": fields["dev"],
        "inode": fields["ino"],
        "mode": fields["mode"],
        "size": fields["size"],
        "nlink": fields["nlink"],
        "mtime_ns": fields["mtime_ns"],
        "ctime_ns": fields["ctime_ns"],
    }
    if "sha256" in fields:
        parsed["content_sha256"] = fields["sha256"]
    return parsed


def _describe_descriptor_field_diff(before_value: str, after_value: str) -> tuple[str, ...]:
    """Bounded field-level diagnostic only: which named fields differ
    between two raw canonical descriptors for the SAME path -- never used
    to decide authority. Returns `("type",)` if the two descriptors are not
    the same kind (`dir:`/`file:`/`symlink:`) or cannot both be parsed into
    named fields; otherwise the sorted names of every field whose value
    differs (from `device`, `inode`, `mode`, `size`, `nlink`, `mtime_ns`,
    `ctime_ns`, `content_sha256`).
    """

    if _descriptor_type(before_value) != _descriptor_type(after_value):
        return ("type",)
    before_fields = _parse_evidence_descriptor_fields(before_value)
    after_fields = _parse_evidence_descriptor_fields(after_value)
    if before_fields is None or after_fields is None:
        return () if before_value == after_value else ("type",)
    return tuple(
        sorted(name for name in before_fields if before_fields[name] != after_fields.get(name))
    )


def describe_evidence_identity_diff(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, Any]:
    """Bounded, field-level description of what changed between two raw
    canonical evidence-tree identity snapshots of the SAME root, for
    diagnostic persistence only -- never authoritative for any preservation
    decision (F-RUN005/Section 9). Reports the total changed-path count and
    a bounded sample of changed paths; for each sampled path, whether it
    appeared or disappeared, or else exactly which named fields differ
    (`device`, `inode`, `mode`, `size`, `nlink`, `mtime_ns`, `ctime_ns`,
    `content_sha256`, or `type`). Never an unbounded dump of a mismatched
    evidence tree.
    """

    changed_paths = diff_workspace_hashes(before, after)
    sample: list[dict[str, Any]] = []
    for path in changed_paths[:_EVIDENCE_DIFF_BOUNDED_SAMPLE_SIZE]:
        before_value = before.get(path)
        after_value = after.get(path)
        if before_value is None:
            sample.append({"path": path, "appeared": True, "disappeared": False, "changed_fields": []})
            continue
        if after_value is None:
            sample.append({"path": path, "appeared": False, "disappeared": True, "changed_fields": []})
            continue
        sample.append(
            {
                "path": path,
                "appeared": False,
                "disappeared": False,
                "changed_fields": list(_describe_descriptor_field_diff(before_value, after_value)),
                "before_descriptor": before_value,
                "after_descriptor": after_value,
            }
        )
    return {"changed_path_count": len(changed_paths), "changed_paths_sample": sample}


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
    tolerate_timestamp_only_instability: bool = False,
    trusted_evidence: TrustedRegularFileEvidence | None = None,
    bootstrap_unconditional_timestamp_tolerance: bool = False,
    churn_sink: list[str] | None = None,
) -> tuple[str, bytes | None]:
    """Measure one regular file's identity and content across a single pass.

    By default (both tolerance parameters `False`), behavior is
    byte-for-byte what it has always been: any before/opened/closing/final
    stat mismatch -- for any reason, including a timestamp-only one --
    fails closed with `EvidenceIntegrityFailure`. This is what canonical
    evidence reading (`read_canonical_evidence` and friends) always gets;
    it is not affected by anything below. `bootstrap_unconditional_timestamp_tolerance`
    and `tolerate_timestamp_only_instability` are mutually exclusive modes;
    no caller sets both.

    **Strict validation mode** (`tolerate_timestamp_only_instability=True`,
    used by `protected_git_metadata_snapshot` once a trusted baseline
    exists): a mismatch confined *exactly* to `mtime_ns`/`ctime_ns` --
    device, inode, mode, size, nlink, and type all agree at every
    observation point, and the full content was read and hashed without a
    byte-count discrepancy -- is *provisionally* eligible for tolerance.
    It is never accepted on that basis alone:

    - if `trusted_evidence` is `None` (no previously successful *coherent*
      snapshot exists to compare against), tolerance is refused and this
      still fails closed. There is no trusted pre-instability content to
      prove the unstable reading against, so an unstable reading can never
      establish its own baseline -- this is the fix for the reproduced
      counterexample where a same-size content mutation immediately before
      the read (A -> B) would otherwise pass simply because the unstable
      reading and a later retry both happened to agree *with each other*.
    - if `trusted_evidence` is given, the bytes actually read this attempt
      (device, inode, mode, size, nlink, SHA-256) are compared against it.
      Any mismatch -- including, decisively, a different SHA-256 -- fails
      closed immediately. Only when every field matches does this raise
      `TimestampOnlyRegularFileInstability`, a diagnostic retry-eligible
      signal (never treated as evidence of anything itself).

    **Bootstrap observation mode** (`bootstrap_unconditional_timestamp_tolerance=True`,
    used only while building one *provisional* observation for
    `establish_protected_git_metadata_baseline` -- see there): a
    timestamp-only-confined mismatch is tolerated unconditionally, with no
    trusted evidence needed and no exception raised at all -- the normal
    descriptor is returned as if the reading had been coherent, and the
    path is appended to `churn_sink` (if given) purely as diagnostic
    record-keeping. This is safe *only* because one bootstrap observation
    is never authoritative by itself: the caller must independently
    rebuild a second, separate observation and compare their complete
    stable projections before either is trusted (see
    `establish_protected_git_metadata_baseline`).

    Any non-timestamp-only mismatch (a real semantic change) still fails
    closed exactly as in the default case regardless of either tolerance
    parameter; neither one ever removes a check, only narrows what --
    given either genuinely trusted external proof or the caller's own
    independent-rebuild-and-compare obligation -- does not have to be a
    hard failure in isolation.
    """

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
        if not stat.S_ISREG(opened.st_mode):
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
        if not stat.S_ISREG(pathname_after.st_mode):
            raise EvidenceIntegrityFailure(
                f"regular file pathname no longer names the opened object: {path.name}"
            )
    except OSError as exc:
        raise EvidenceIntegrityFailure(
            f"regular file cannot be read safely: {path.name}: {exc.__class__.__name__}"
        ) from None
    finally:
        os.close(descriptor)

    expected_identity = _stat_identity(metadata)
    opened_identity = _stat_identity(opened)
    closed_identity = _stat_identity(closed_over)
    final_identity = _stat_identity(pathname_after)
    sha256_hex = digest.hexdigest()

    def _differs_only_in_timestamps(a: tuple, b: tuple) -> bool:
        return a == b or a[:5] == b[:5]

    intra_measurement_coherent = (
        expected_identity == opened_identity
        and closed_identity == opened_identity
        and final_identity == opened_identity
    )

    if not intra_measurement_coherent:
        confined_to_timestamps = (
            _differs_only_in_timestamps(expected_identity, opened_identity)
            and _differs_only_in_timestamps(closed_identity, opened_identity)
            and _differs_only_in_timestamps(final_identity, opened_identity)
        )
        if not confined_to_timestamps or not (
            tolerate_timestamp_only_instability or bootstrap_unconditional_timestamp_tolerance
        ):
            raise EvidenceIntegrityFailure(
                f"regular file identity changed during measurement: {path.name}"
            )
        if bootstrap_unconditional_timestamp_tolerance:
            # One provisional bootstrap observation: tolerated unconditionally
            # -- no trusted evidence exists yet to prove it against, and none
            # is needed, because this single reading is never authoritative
            # by itself (see establish_protected_git_metadata_baseline). Fall
            # through to the normal return below, using the bytes actually
            # read.
            if churn_sink is not None:
                churn_sink.append(str(path))
        else:
            # Strict validation mode. Provisionally timestamp-only. Never
            # accepted on that basis alone: it must be proven against
            # evidence from outside this call.
            if trusted_evidence is None:
                raise EvidenceIntegrityFailure(
                    "regular file timestamp-only instability has no trusted prior "
                    f"coherent evidence to validate against: {path.name}"
                )
            if not trusted_evidence.matches(
                dev=opened.st_dev,
                ino=opened.st_ino,
                mode=stat.S_IMODE(opened.st_mode),
                size=opened.st_size,
                nlink=opened.st_nlink,
                sha256=sha256_hex,
            ):
                raise EvidenceIntegrityFailure(
                    "regular file content or identity does not match trusted prior "
                    f"coherent evidence: {path.name}"
                )
            if churn_sink is not None:
                churn_sink.append(str(path))
            raise TimestampOnlyRegularFileInstability(
                path=str(path),
                dev=opened.st_dev,
                ino=opened.st_ino,
                mode=opened.st_mode,
                size=opened.st_size,
                nlink=opened.st_nlink,
                sha256=sha256_hex,
            )

    return (
        f"file:dev={opened.st_dev}:ino={opened.st_ino}:"
        f"mode={stat.S_IMODE(opened.st_mode):04o}:size={opened.st_size}:"
        f"nlink={opened.st_nlink}:mtime_ns={opened.st_mtime_ns}:"
        f"ctime_ns={opened.st_ctime_ns}:sha256={sha256_hex}",
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
    tolerate_regular_file_timestamp_only_instability: bool = False,
    trusted_regular_file_baseline: Mapping[str, str] | None = None,
    bootstrap_unconditional_timestamp_tolerance: bool = False,
    churn_sink: list[str] | None = None,
) -> CanonicalEvidenceSnapshot:
    """Measure a tree without following aliases, revalidating every live path.

    Directory measurement is never relaxed here: a directory's own stat
    identity must match exactly across the walk regardless of the tolerance
    parameters below, which apply only to regular-file members (see
    `_regular_file_measurement`). `bootstrap_unconditional_timestamp_tolerance`
    and `tolerate_regular_file_timestamp_only_instability` are mutually
    exclusive modes; no caller sets both.

    `trusted_regular_file_baseline`, when given, maps each relative member
    key (the same key used in the returned identity dict, e.g. `"HEAD"` or
    `"refs/heads/main"`) to that path's canonical `file:` descriptor string
    from a previously successful, fully coherent tree measurement -- never
    from the current call. It is parsed with the same approved, exact
    canonical-grammar `fullmatch()` parser used elsewhere
    (`_parse_trusted_file_descriptor`), never with an ad-hoc partial parse.
    """

    trusted_baseline = trusted_regular_file_baseline or {}

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
                trusted_descriptor = trusted_baseline.get(key)
                descriptor, content = _regular_file_measurement(
                    entry_path,
                    metadata,
                    reject_hardlinks=reject_hardlinks,
                    capture_bytes=key in capture_files,
                    tolerate_timestamp_only_instability=tolerate_regular_file_timestamp_only_instability,
                    trusted_evidence=(
                        _parse_trusted_file_descriptor(trusted_descriptor)
                        if trusted_descriptor is not None
                        else None
                    ),
                    bootstrap_unconditional_timestamp_tolerance=bootstrap_unconditional_timestamp_tolerance,
                    churn_sink=churn_sink,
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
    tolerate_regular_file_timestamp_only_instability: bool = False,
    trusted_regular_file_baseline: Mapping[str, str] | None = None,
    bootstrap_unconditional_timestamp_tolerance: bool = False,
    churn_sink: list[str] | None = None,
) -> dict[str, str]:
    """Return an lstat-based, non-following identity for one exact filesystem tree."""

    return _measure_type_aware_tree(
        root,
        reject_symlinks=reject_symlinks,
        reject_hardlinks=reject_hardlinks,
        tolerate_regular_file_timestamp_only_instability=tolerate_regular_file_timestamp_only_instability,
        trusted_regular_file_baseline=trusted_regular_file_baseline,
        bootstrap_unconditional_timestamp_tolerance=bootstrap_unconditional_timestamp_tolerance,
        churn_sink=churn_sink,
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


def _run_git_bytes(args: list[str], cwd: Path, *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Byte-oriented variant of `_run_git` for output that may contain
    arbitrary path bytes (e.g. `ls-files -z`). Text-mode subprocess capture
    round-trips through the process's locale encoding, which is lossy for
    path bytes that are not valid text in that encoding; canonical index
    identity must not depend on that round-trip. Narrow and index-specific
    -- `_run_git` itself is unchanged and remains the right choice for
    output that is genuinely text (revisions, refs, porcelain summaries).
    """

    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
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


def _workspace_git_entry_descriptor(
    cwd: Path,
    *,
    tolerate_timestamp_only_instability: bool = False,
    trusted_evidence: TrustedRegularFileEvidence | None = None,
    bootstrap_unconditional_timestamp_tolerance: bool = False,
    churn_sink: list[str] | None = None,
) -> tuple[str, str]:
    """Measure the worktree-root .git entry without following aliases.

    A directory-typed root entry is never subject to timestamp tolerance
    (see `_measure_type_aware_tree`'s docstring); only the linked-worktree
    regular-indirection-file case can raise `TimestampOnlyRegularFileInstability`,
    and only when the caller opts in and supplies matching `trusted_evidence`
    from a previously successful, fully coherent snapshot -- or, in bootstrap
    observation mode, tolerates a timestamp-only mismatch unconditionally
    (see `_regular_file_measurement`).
    """

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
            tolerate_timestamp_only_instability=tolerate_timestamp_only_instability,
            trusted_evidence=trusted_evidence,
            bootstrap_unconditional_timestamp_tolerance=bootstrap_unconditional_timestamp_tolerance,
            churn_sink=churn_sink,
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


def _build_provisional_protected_git_metadata_observation(
    cwd: Path,
    *,
    churn_sink: list[str] | None = None,
) -> dict[str, str]:
    """Build exactly one complete, independently-rebuilt protected Git
    metadata observation, for bootstrap comparison purposes only (see
    `establish_protected_git_metadata_baseline`).

    Every non-timestamp check -- unreadable/incomplete files, byte-count
    inconsistency, path replacement, device/inode/mode/type/symlink
    mismatches, malformed protected paths, incomplete tree traversal -- is
    unconditional here and fails closed exactly as in strict validation
    mode; nothing in bootstrap mode ever weakens any of them. Only a
    same-window mismatch confined *exactly* to `mtime_ns`/`ctime_ns` (the
    root `.git` entry, the resolved git-dir/git-common-dir themselves never
    have a timestamp component and so any difference there is always
    semantic, or any regular-file member) is unconditionally tolerated,
    without needing external trusted evidence -- because this ONE
    observation is never accepted as authoritative by itself. It is always
    returned as the stable cross-phase projection (`mtime_ns`/`ctime_ns`
    excluded), ready for direct comparison against another observation
    built the same way.

    Raises `EvidenceIntegrityFailure` immediately or a filesystem-level
    exception is not caught. A caller wanting a bounded number of
    independent attempts is responsible for that bound (see
    `establish_protected_git_metadata_baseline`); this function never
    retries internally.
    """

    git_entry_before = _workspace_git_entry_descriptor(
        cwd,
        bootstrap_unconditional_timestamp_tolerance=True,
        churn_sink=churn_sink,
    )
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
            bootstrap_unconditional_timestamp_tolerance=True,
            churn_sink=churn_sink,
        )
        for relative, descriptor in tree.items():
            identity[f"{role}/{relative}"] = descriptor
    git_entry_after = _workspace_git_entry_descriptor(
        cwd,
        bootstrap_unconditional_timestamp_tolerance=True,
        churn_sink=churn_sink,
    )
    if git_dir(cwd) != roots[0][1] or git_common_dir(cwd) != roots[1][1]:
        raise EvidenceIntegrityFailure(
            "resolved Git metadata locations changed during baseline observation"
        )
    if git_entry_after != git_entry_before:
        timestamp_only = (
            git_entry_after[0] == git_entry_before[0]
            and _strip_volatile_timestamp_fields(git_entry_after[1])
            == _strip_volatile_timestamp_fields(git_entry_before[1])
        )
        if not timestamp_only:
            raise EvidenceIntegrityFailure(
                "workspace .git entry changed during baseline observation"
            )
        if churn_sink is not None:
            churn_sink.append(_WORKSPACE_GIT_ENTRY_KEY)
        identity[_WORKSPACE_GIT_ENTRY_PATH_KEY] = git_entry_after[0]
        identity[_WORKSPACE_GIT_ENTRY_KEY] = git_entry_after[1]

    return _stable_cross_phase_projection(identity)


@dataclasses.dataclass(frozen=True, slots=True)
class ProtectedGitMetadataBaseline:
    """The result of `establish_protected_git_metadata_baseline`."""

    stable_projection: dict[str, str]
    observations_used: int
    timestamp_churn_observed: bool
    required_third_observation: bool


def establish_protected_git_metadata_baseline(
    cwd: Path,
) -> ProtectedGitMetadataBaseline:
    """Explicitly, boundedly establish the *initial* coherent
    protected-repository starting state.

    This is the only place Runtime is ever permitted to trust a protected
    Git metadata observation without external trusted evidence -- and it
    never does so from a single reading. Runtime cannot prove content that
    existed before its first content observation, so bootstrap instead
    requires *two independently rebuilt, complete observations whose
    stable semantic/nonvolatile projections agree*:

        observation_1 = complete provisional observation
        observation_2 = independently rebuilt complete provisional observation
        if stable(observation_1) == stable(observation_2):
            establish observation_2 as the baseline
        else:
            continue with observation_3, comparing it only against
            observation_2 (never observation_1 -- a prior disagreement is
            never silently bridged), until either two *consecutive*
            observations agree or `_MAX_INITIAL_BASELINE_OBSERVATIONS` is
            exhausted.

    With the bound at 3: `S1 == S2` establishes `S2`; `S1 != S2` but
    `S2 == S3` establishes `S3`; `S1 != S2` and `S2 != S3` fails closed.
    This is intentionally a flat, non-nested bound -- one observation
    attempt per loop iteration, never a retry loop nested inside another.

    If content changes from A to B before the very first read of a
    bootstrap sequence (`S1` observes B because the mutation already
    happened; `S2` independently observes B again), `B` legitimately
    becomes the baseline: Runtime never claimed to have observed A, and
    never needs to reconstruct or prove it. This is deliberately different
    from later (post-baseline) validation, where a mismatch against
    already-*trusted* evidence must fail closed -- see
    `protected_git_metadata_snapshot`'s `trusted_baseline` parameter and
    `TrustedRegularFileEvidence`; that contract is unchanged and is not
    reopened by this function.

    Each observation independently rebuilds the *complete* protected
    repository state from scratch (root `.git` entry, plus the full
    `git_dir`/`git_common_dir` trees) -- no subtree reuse, no descriptor
    reuse, no partial-observation reuse, no per-file nested retry. Only a
    same-window timestamp-only mismatch (never any other kind of
    difference) is tolerated *within* one observation, and only because
    that single observation is never trusted by itself.

    Raises `EvidenceIntegrityFailure` if no two consecutive observations
    ever agree within the bound, or if any single observation hits a
    genuine (non-timestamp) semantic problem -- unreadable/incomplete
    files, byte-count inconsistency, path replacement, device/inode/mode/
    type/symlink mismatches, malformed protected paths, or incomplete
    tree traversal -- which aborts establishment immediately rather than
    silently moving on to another attempt.
    """

    previous: dict[str, str] | None = None
    timestamp_churn_observed = False

    for attempt in range(1, _MAX_INITIAL_BASELINE_OBSERVATIONS + 1):
        churn: list[str] = []
        current = _build_provisional_protected_git_metadata_observation(cwd, churn_sink=churn)
        if churn:
            timestamp_churn_observed = True
        if previous is not None and current == previous:
            return ProtectedGitMetadataBaseline(
                stable_projection=current,
                observations_used=attempt,
                timestamp_churn_observed=timestamp_churn_observed,
                required_third_observation=attempt >= 3,
            )
        previous = current

    raise EvidenceIntegrityFailure(
        "could not establish a coherent protected Git metadata baseline within "
        f"{_MAX_INITIAL_BASELINE_OBSERVATIONS} independently rebuilt observations"
    )


def protected_git_metadata_snapshot(
    cwd: Path,
    *,
    trusted_baseline: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Cross-phase-stable **validation** of protected Git metadata against
    `trusted_baseline` -- this function never establishes a new baseline,
    with or without one. Omitting `trusted_baseline` (or a path having no
    entry in it) does not fall back to any bootstrap behavior: it simply
    means there is nothing to validate a timestamp-only instability
    against, so that measurement fails closed exactly as any other
    unproven mismatch would. To establish the very first trusted baseline
    for a run, call the separate, explicit
    `establish_protected_git_metadata_baseline()` instead -- never this
    function with `trusted_baseline=None`.

    Snapshots the root `.git` entry plus the actual per-worktree/common
    metadata trees. The returned identity is a *stable cross-phase
    projection*: path identity, entry type, mode, device/inode, size, link
    count, and content hashes remain fully load-bearing, but `mtime_ns`/
    `ctime_ns` are excluded (see `_stable_cross_phase_projection`). Benign
    OS-level metadata churn between two separate calls to this function --
    for example an extended attribute the OS attaches to a file some time
    after it was written -- can change those two fields without any
    semantic mutation of the measured object, and must not register as one.

    Internally, one composite measurement pass requires:

    - the root `.git` entry and the resolved git-dir/git-common-dir to be
      unchanged from just before the tree walk to just after it (a
      same-window self-consistency check *within a single call*, not a
      cross-phase one); and
    - every regular-file member visited during that same tree walk (the
      root `.git` entry itself when it is a linked-worktree indirection
      file, or any file within the git-dir/git-common-dir trees -- HEAD, a
      ref, a loose object, the index, ...) to be internally coherent across
      its own open/read/close/final-pathname observation points.

    `git_dir`/`git_common_dir` are pure path strings with no timestamp
    component, so any difference there is always semantic and fails closed
    immediately. Any *non-timestamp* difference at the root entry or in any
    member file -- a different path, device, inode, mode, size, nlink, or
    content -- also fails closed immediately, exactly as before.

    A difference confined *exactly* to `mtime_ns`/`ctime_ns` in a member
    file is never accepted on its own -- an unstable reading can never
    prove its own past. It is validated against `trusted_baseline`: the
    *stable-projected return value of a previously successful, fully
    coherent call to this same function* (e.g. the "before" snapshot of a
    before/probe/after sequence), never anything produced during the
    current call. If `trusted_baseline` is `None`, or has no entry for the
    affected path, there is no trusted pre-instability content to prove
    the reading against, so the measurement fails closed immediately --
    this is what keeps the very first ("initial") snapshot in any sequence
    honestly fail-closed rather than silently self-baselining. If
    `trusted_baseline` does have an entry for that path, every load-bearing
    field of the unstable reading -- including, decisively, its SHA-256 --
    must match it exactly; any mismatch fails closed immediately (this is
    what closes a same-size, same-inode, same-mode content mutation
    occurring immediately before the read: the unstable reading's SHA no
    longer matches the trusted evidence, even though a later retry might
    coherently reproduce the *mutated* content). Only when the trusted
    evidence matches does the whole composite measurement bounded-retry
    from scratch -- never a partial tree or a previously successful
    subtree -- capped at `_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS`
    *total* attempts shared across every source of instability, root or
    member, however many distinct paths are involved; a different path
    becoming unstable on a later attempt is validated against its own
    entry in the same constant `trusted_baseline`, independent of any
    other path. Persistent timestamp-only instability across every attempt
    still fails closed once the bound is exhausted, exactly as before.

    The root `.git` entry's own before/after comparison (only meaningful
    when it is a linked-worktree regular-indirection file) is unaffected
    architecturally: it still compares two readings taken within this same
    call, each of which independently goes through the same trusted-baseline
    proof above before being tolerated as timestamp-only.

    Directory measurement is never relaxed by any of this (see
    `_measure_type_aware_tree`).
    """

    root_trusted_evidence = (
        _parse_trusted_file_descriptor(trusted_baseline[_WORKSPACE_GIT_ENTRY_KEY])
        if trusted_baseline is not None and _WORKSPACE_GIT_ENTRY_KEY in trusted_baseline
        else None
    )
    tree_trusted_baselines: dict[str, dict[str, str]] = {}
    if trusted_baseline is not None:
        for role in ("git_dir", "git_common_dir"):
            prefix = f"{role}/"
            tree_trusted_baselines[role] = {
                key[len(prefix) :]: value
                for key, value in trusted_baseline.items()
                if key.startswith(prefix)
            }

    last_error: EvidenceIntegrityFailure | None = None

    for _ in range(_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS):
        try:
            git_entry_before = _workspace_git_entry_descriptor(
                cwd,
                tolerate_timestamp_only_instability=True,
                trusted_evidence=root_trusted_evidence,
            )
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
                    tolerate_regular_file_timestamp_only_instability=True,
                    trusted_regular_file_baseline=tree_trusted_baselines.get(role),
                )
                for relative, descriptor in tree.items():
                    identity[f"{role}/{relative}"] = descriptor
            git_entry_after = _workspace_git_entry_descriptor(
                cwd,
                tolerate_timestamp_only_instability=True,
                trusted_evidence=root_trusted_evidence,
            )
        except TimestampOnlyRegularFileInstability as exc:
            last_error = EvidenceIntegrityFailure(
                "protected Git metadata member timestamp remained unstable across the "
                f"bounded coherent-measurement retry limit: {exc.path}"
            )
            # Timestamp-only same-window instability in a member file,
            # already proven against trusted_baseline: bounded-retry the
            # whole composite measurement from scratch.
            continue

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
        # Timestamp-only same-window instability at the root .git entry
        # level: bounded-retry the whole composite measurement from scratch.

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


def _split_nul_terminated_records(data: bytes) -> list[bytes] | None:
    """Split `-z` NUL-terminated Git command output into its records.

    Requires the output to be empty or end with the terminating NUL --
    anything else (a truncated/incomplete write) returns `None`. This is
    the framing check every canonical-index source (`ls-files --stage`,
    `status --porcelain=v2`, `ls-files --resolve-undo`) must pass before any
    record within it is trusted.
    """

    if not data:
        return []
    if not data.endswith(b"\x00"):
        return None
    records = data.split(b"\x00")
    assert records[-1] == b""
    return records[:-1]


def _repository_object_format(cwd: Path) -> tuple[str, int] | None:
    """Positively determine this repository's Git object storage format via
    a read-only Git plumbing query (`git rev-parse --show-object-format`,
    supported by the installed Git version), never inferred per-record from
    a presented object id's own length.

    Returns `(format_name, hex_id_length)` for a recognized format
    (`_REPOSITORY_OBJECT_FORMAT_HEX_LENGTH`), or `None` if the command
    fails (including on an older Git that does not support the flag) or
    reports anything unrecognized -- callers must treat that identically to
    every other "canonical projection unavailable" case, never fall back to
    guessing a width from the data itself.
    """

    completed = _run_git(["rev-parse", "--show-object-format"], cwd)
    if completed.returncode != 0:
        return None
    format_name = completed.stdout.strip()
    hex_length = _REPOSITORY_OBJECT_FORMAT_HEX_LENGTH.get(format_name)
    if hex_length is None:
        return None
    return (format_name, hex_length)


def _valid_object_id_shape(object_id: bytes, expected_hex_length: int) -> bool:
    """Whether `object_id` is a syntactically well-formed object id of
    EXACTLY `expected_hex_length` hex digits -- the repository's own
    positively-determined object-format width, never merely "40 or 64".
    This is shape only: it says nothing about whether the id names a real
    object or the documented all-zero absence placeholder -- see
    `_valid_real_object_id`/`_valid_zero_placeholder_object_id`, the two
    field-context-aware predicates every caller must actually use
    (F04-R2/Section 7/14). Never called directly by a record parser.
    """

    return len(object_id) == expected_hex_length and _HEX_DIGITS_RE.fullmatch(object_id) is not None


def _valid_real_object_id(object_id: bytes, expected_hex_length: int) -> bool:
    """Whether `object_id` is a REAL object id: correct repository-format
    width, hexadecimal, and NOT all-zero (F04-R2/Section 7). A cached index
    entry (`ls-files --stage`), a resolve-undo (REUC) record, and a
    porcelain-v2 mode/OID pair whose mode indicates the state is PRESENT
    must always carry a real object id -- the all-zero value is never a
    legitimate object id, only a documented absence placeholder in the
    specific fields where Git defines one (see
    `_valid_zero_placeholder_object_id`)."""

    if not _valid_object_id_shape(object_id, expected_hex_length):
        return False
    return object_id != b"0" * expected_hex_length


def _valid_zero_placeholder_object_id(object_id: bytes, expected_hex_length: int) -> bool:
    """Whether `object_id` is EXACTLY the documented all-zero absence
    placeholder, at the repository's own object-format width (F04-R2/
    Section 7/8) -- used only for porcelain-v2 fields whose associated mode
    indicates the corresponding HEAD/index/stage entry is genuinely
    absent."""

    return object_id == b"0" * expected_hex_length


def _valid_status_mode_oid_pair(mode: bytes, object_id: bytes, expected_oid_length: int) -> bool:
    """Whether one porcelain-v2 HEAD/index mode+OID pair (or one type-u
    per-stage mode+OID pair) represents a coherent presence/absence state
    (F04-R2/Section 8): a PRESENT state requires a real Git mode
    (`_INDEX_ENTRY_VALID_MODES`) paired with a real, nonzero object id of
    the repository's own width; an ABSENT state requires the documented
    `000000` mode paired with the documented all-zero placeholder of that
    same width. A present mode with a zero OID, or an absent mode with a
    nonzero OID, is never a real Git state -- Codex's exact reproduced
    "present HEAD/index modes but all-zero hH/hI object IDs" -- and is
    rejected here, not merely bound unchecked.
    """

    if mode == _STATUS_MODE_ABSENT:
        return _valid_zero_placeholder_object_id(object_id, expected_oid_length)
    if mode not in _INDEX_ENTRY_VALID_MODES:
        return False
    return _valid_real_object_id(object_id, expected_oid_length)


def _valid_type2_score(score_field: bytes, expected_x: bytes) -> bool:
    """Whether a type-"2" rename/copy `<X><score>` token is valid
    (F04-R2/Section 10): the same letter as the record's own XY.X value,
    followed by a similarity PERCENTAGE strictly between 0 and 100
    inclusive -- the lexical grammar alone (`_STATUS_TYPE2_SCORE_RE`)
    cannot bound a variable-length decimal, so the numeric value is checked
    separately. Codex's exact reproduced `R101` and `R999` (lexically valid,
    semantically impossible) are rejected here; real Git-produced `R100`
    remains valid.
    """

    match = _STATUS_TYPE2_SCORE_RE.fullmatch(score_field)
    if match is None:
        return False
    if score_field[0:1] != expected_x:
        return False
    return 0 <= int(match.group(1)) <= 100


def _valid_stage_tag_combination(base_tag: bytes, assume_unchanged: bool, stage: int) -> bool:
    """Whether this exact (tag, assume-unchanged, stage) combination is one
    Git can actually emit from `git ls-files --stage -t -v -z` (Section 9),
    validated as ONE semantic relationship rather than three independently
    valid fields cross-producted together.

    Real, Git-producible combinations: an ordinary cached entry (`H`/`h`)
    or a skip-worktree entry (`S`/`s`) is always stage 0, with the
    assume-unchanged bit free to be set or not; an unmerged entry (`M`) is
    always stage 1, 2, or 3, and never carries the assume-unchanged bit
    (Codex's reproduced "m ... stage 1" is exactly this impossible
    combination). Anything else -- `H`/`S` at a nonzero stage, `M` at stage
    0, or `M` with assume-unchanged set -- is rejected.
    """

    if base_tag == b"M":
        return not assume_unchanged and stage in (1, 2, 3)
    if base_tag in (b"H", b"S"):
        return stage == 0
    return False


def _parse_canonical_index_stage_record(
    record: bytes, expected_oid_length: int
) -> tuple[bytes, int, bytes, bytes, bool, bool] | None:
    """Parse one `git ls-files --stage -t -v -z` NUL-delimited record.

    Returns `(path_bytes, stage, mode, object_id, skip_worktree,
    assume_unchanged)`, or `None` if the record is not a fully recognized
    cached-index entry -- an unsupported/unclassified tag letter, an
    invalid Git mode, an object id not matching the repository's own
    positively-determined object format width, a malformed stage (including
    a non-canonical spelling like `"00"`), an impossible tag/stage
    combination, an empty path, or an unexpected field count are all
    treated identically: cannot classify, caller must fail closed.
    """

    try:
        prefix, path_bytes = record.split(b"\t", 1)
    except ValueError:
        return None
    if not path_bytes:
        return None
    fields = prefix.split(b" ")
    if len(fields) != 4:
        return None
    tag, mode, object_id, stage_bytes = fields
    if len(tag) != 1 or not tag.isalpha():
        return None
    assume_unchanged = tag.islower()
    base_tag = tag.upper()
    if base_tag not in _INDEX_STAGE_BASE_TAGS:
        return None
    if mode not in _INDEX_ENTRY_VALID_MODES:
        return None
    # A cached index entry always carries a REAL object id -- never the
    # all-zero absence placeholder, which only ever means anything in
    # specific porcelain-v2 fields (F04-R2/Section 7).
    if not _valid_real_object_id(object_id, expected_oid_length):
        return None
    if not _INDEX_STAGE_RE.fullmatch(stage_bytes):
        return None
    stage = int(stage_bytes)
    if not _valid_stage_tag_combination(base_tag, assume_unchanged, stage):
        return None
    return (path_bytes, stage, mode, object_id, base_tag == b"S", assume_unchanged)


def _parse_canonical_resolve_undo_record(
    record: bytes, expected_oid_length: int
) -> tuple[bytes, int, bytes, bytes] | None:
    """Parse one `git ls-files --resolve-undo -z` NUL-delimited record."""

    try:
        prefix, path_bytes = record.split(b"\t", 1)
    except ValueError:
        return None
    if not path_bytes:
        return None
    fields = prefix.split(b" ")
    if len(fields) != 3:
        return None
    mode, object_id, stage_bytes = fields
    if mode not in _INDEX_ENTRY_VALID_MODES:
        return None
    # A resolve-undo (REUC) record always carries a REAL conflicted blob
    # object id -- never the all-zero absence placeholder (F04-R2/
    # Section 13).
    if not _valid_real_object_id(object_id, expected_oid_length):
        return None
    if not _INDEX_RESOLVE_UNDO_STAGE_RE.fullmatch(stage_bytes):
        return None
    stage = int(stage_bytes)
    return (path_bytes, stage, mode, object_id)


def _valid_status_type1_or_type2_fixed_fields(
    xy: bytes,
    sub: bytes,
    mode_head: bytes,
    mode_index: bytes,
    mode_worktree: bytes,
    hash_head: bytes,
    hash_index: bytes,
    *,
    expected_oid_length: int,
    x_alphabet: frozenset[bytes],
) -> bool:
    """Shared fixed-field grammar for a porcelain-v2 type "1" or type "2"
    record (Section 10/11/12/14, F04-R2/Section 9): XY drawn from the
    record-specific X alphabet and the shared Y alphabet, and never one of
    the forbidden combinations no real type-1/type-2 record can carry
    (`".."`, or any of the seven unmerged-only pairs -- Codex's exact
    reproduced "XY=.." and "XY=AA"); `<sub>` a valid submodule-state field;
    `mW` any valid status mode; and each of the HEAD/index mode+OID PAIRS
    validated as one coherent presence/absence relationship
    (`_valid_status_mode_oid_pair`), not as independently-shaped fields --
    Codex's exact reproduced "present HEAD/index modes but all-zero
    hH/hI object IDs" is rejected here.
    """

    if len(xy) != 2:
        return False
    if xy in _STATUS_TYPE1_TYPE2_FORBIDDEN_XY:
        return False
    x_char, y_char = xy[0:1], xy[1:2]
    if x_char not in x_alphabet or y_char not in _STATUS_TYPE1_Y_CHARS:
        return False
    if sub != _STATUS_SUBMODULE_NOT_A_SUBMODULE and not _STATUS_SUBMODULE_RE.fullmatch(sub):
        return False
    if mode_worktree not in _STATUS_VALID_MODES:
        return False
    return _valid_status_mode_oid_pair(
        mode_head, hash_head, expected_oid_length
    ) and _valid_status_mode_oid_pair(mode_index, hash_index, expected_oid_length)


def _intent_to_add_paths_from_status_v2(stdout: bytes, expected_oid_length: int) -> set[bytes] | None:
    """Parse `git status --porcelain=v2 --untracked-files=no -z` output for
    intent-to-add paths: an ordinary changed entry (`1 ...`) whose index
    mode (`mI`) and HEAD mode (`mH`) are both `000000` is a path staged only
    via `git add -N` -- a real cached-index entry exists for it (see
    `_build_canonical_index_projection_once`, which cross-checks this
    against that entry), but with no real staged content yet. Porcelain v2
    is git's own documented, stable, script-oriented plumbing format -- not
    the explicitly unstable `--debug` output.

    Every record type porcelain v2 can emit under this exact command
    configuration is either FULLY validated against its documented fixed
    grammar (`1` ordinary-changed, `2` renamed/copied -- consuming its
    extra NUL-delimited origPath continuation and its own rename/copy score
    grammar, `u` unmerged, validated against its documented XY pairs) or,
    for anything else, treated as unsupported for this canonicalization.
    Returns `None` -- fails closed -- on incomplete NUL framing, any
    unrecognized/malformed field (XY, submodule state, mode, OID, or
    rename/copy score), or an unsupported record type; never silently
    skipped.
    """

    records = _split_nul_terminated_records(stdout)
    if records is None:
        return None

    paths: set[bytes] = set()
    i = 0
    while i < len(records):
        record = records[i]
        if record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                return None
            _marker, xy, sub, mode_head, mode_index, mode_worktree, h_head, h_index, path_bytes = fields
            if not path_bytes:
                return None
            if not _valid_status_type1_or_type2_fixed_fields(
                xy,
                sub,
                mode_head,
                mode_index,
                mode_worktree,
                h_head,
                h_index,
                expected_oid_length=expected_oid_length,
                x_alphabet=_STATUS_TYPE1_X_CHARS,
            ):
                return None
            if mode_index == _STATUS_MODE_ABSENT and mode_head == _STATUS_MODE_ABSENT:
                paths.add(path_bytes)
            i += 1
        elif record.startswith(b"2 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                return None
            _marker, xy, sub, mode_head, mode_index, mode_worktree, h_head, h_index, rest = fields
            if not _valid_status_type1_or_type2_fixed_fields(
                xy,
                sub,
                mode_head,
                mode_index,
                mode_worktree,
                h_head,
                h_index,
                expected_oid_length=expected_oid_length,
                x_alphabet=_STATUS_TYPE2_X_CHARS,
            ):
                return None
            # rest = "<X><score> <path>"; the origPath is the *next*
            # NUL-delimited record, per porcelain v2's documented framing
            # for rename/copy entries -- never folded into this one.
            if b" " not in rest:
                return None
            score_field, path_bytes = rest.split(b" ", 1)
            if not path_bytes:
                return None
            if not _valid_type2_score(score_field, xy[0:1]):
                return None
            if i + 1 >= len(records) or not records[i + 1]:
                return None
            # a renamed/copied entry is never intent-to-add; nothing to record.
            i += 2
        elif record.startswith(b"u "):
            fields = record.split(b" ", 10)
            if len(fields) != 11:
                return None
            _marker, xy, sub, m1, m2, m3, mw, h1, h2, h3, path_bytes = fields
            if not path_bytes:
                return None
            # Only the seven documented pairs are real unmerged states, each
            # with its OWN fixed per-stage presence pattern (F04-R2/
            # Section 11) -- not independently-valid XY characters.
            stage_presence = _UNMERGED_STAGE_PRESENCE.get(xy)
            if stage_presence is None:
                return None
            if sub != _STATUS_SUBMODULE_NOT_A_SUBMODULE and not _STATUS_SUBMODULE_RE.fullmatch(sub):
                return None
            if mw not in _STATUS_VALID_MODES:
                return None
            for stage_present, stage_mode, stage_oid in (
                (stage_presence[0], m1, h1),
                (stage_presence[1], m2, h2),
                (stage_presence[2], m3, h3),
            ):
                if stage_present:
                    if stage_mode not in _INDEX_ENTRY_VALID_MODES or not _valid_real_object_id(
                        stage_oid, expected_oid_length
                    ):
                        return None
                elif stage_mode != _STATUS_MODE_ABSENT or not _valid_zero_placeholder_object_id(
                    stage_oid, expected_oid_length
                ):
                    return None
            # an unmerged entry is never intent-to-add; nothing to record.
            i += 1
        else:
            # Unsupported/unclassified record type for this
            # canonicalization (e.g. "?"/"!", never expected under
            # `--untracked-files=no` with no `--ignored`, or anything
            # future/unrecognized) -- never silently skipped.
            return None
    return paths


def _build_canonical_index_projection_once(cwd: Path) -> dict[str, str] | None:
    """Build exactly ONE complete, independently rebuilt canonical
    effective-index projection, for bounded coherence comparison purposes
    only (see `canonical_index_projection`). Never retries internally.

    For every index entry: its exact byte-safe path, stage, Git mode, full
    object id, and assume-unchanged/skip-worktree/intent-to-add state. For
    every resolve-undo record: its path, stage, mode, and object id. This
    represents the repository-relevant *meaning* of the index, not a
    particular physical on-disk encoding -- an ordinary index, a
    split-index layout, and a sparse-index physical compaction that resolve
    to the same effective per-path state produce the same projection here,
    because `git ls-files` itself already resolves all of those into one
    logical listing (no `--sparse` flag is passed, so sparse directories
    are always expanded to their contained files rather than left
    compacted).

    Returns `None` -- fails closed -- on any git command failure, an
    unavailable/unrecognized repository object format, incomplete NUL
    framing, any unparseable/unclassified record (bound to the repository's
    own positively-determined object-format width, never inferred per
    record), a duplicate (path, stage) pair, or an intent-to-add path whose
    status classification disagrees with its own effective stage-0 entry
    (neither present, nor holding the exact empty-blob placeholder object
    id for the repository's object format) -- query outputs that
    correspond to inconsistent logical states must never be combined into
    one projection. Never guesses at an unrecognized state.

    The repository's object format is determined exactly once per attempt,
    before any of the three queries run, and that SAME validated width and
    empty-blob id are used for every OID this attempt parses from any of
    the three sources -- never inferred separately per record.

    The three underlying git invocations (`ls-files --stage`, `status
    --porcelain=v2`, `ls-files --resolve-undo`) run back-to-back with no
    intervening work, which is as coherent an observation boundary as a
    single CLI-based query round can practically offer; the caller
    (`canonical_index_projection`) is responsible for the bounded,
    consecutive-match coherence proof across independently rebuilt
    attempts of this whole function.
    """

    object_format = _repository_object_format(cwd)
    if object_format is None:
        return None
    format_name, expected_oid_length = object_format
    empty_blob_object_id = _EMPTY_BLOB_OBJECT_ID_BY_FORMAT[format_name]

    stage_completed = _run_git_bytes(["ls-files", "--stage", "-t", "-v", "-z"], cwd)
    if stage_completed.returncode != 0:
        return None
    status_completed = _run_git_bytes(
        ["status", "--porcelain=v2", "--untracked-files=no", "-z"], cwd
    )
    if status_completed.returncode != 0:
        return None
    undo_completed = _run_git_bytes(["ls-files", "--resolve-undo", "-z"], cwd)
    if undo_completed.returncode != 0:
        return None

    stage_records = _split_nul_terminated_records(stage_completed.stdout)
    if stage_records is None:
        return None
    undo_records = _split_nul_terminated_records(undo_completed.stdout)
    if undo_records is None:
        return None
    intent_to_add_paths = _intent_to_add_paths_from_status_v2(
        status_completed.stdout, expected_oid_length
    )
    if intent_to_add_paths is None:
        return None

    stage_entries: dict[tuple[bytes, int], tuple[bytes, bytes, bool, bool]] = {}
    for record in stage_records:
        parsed = _parse_canonical_index_stage_record(record, expected_oid_length)
        if parsed is None:
            return None
        path_bytes, stage, mode, object_id, skip_worktree, assume_unchanged = parsed
        entry_key = (path_bytes, stage)
        if entry_key in stage_entries:
            return None
        stage_entries[entry_key] = (mode, object_id, skip_worktree, assume_unchanged)

    # Intent-to-add cross-check (F04/Section 13/15): every path status
    # classifies as intent-to-add must have a matching effective stage-0
    # entry whose object id is *exactly* the empty-blob placeholder for
    # THIS repository's own positively-determined object format -- never
    # accepted on status's say-so alone, nor on stage's say-so alone, and
    # never selected by the presented object id's own length. This is
    # precisely what rejects the "impossible hybrid" case: a non-empty
    # staged object (or one from a different object format) at a path
    # status also calls intent-to-add can never be combined into one
    # coherent projection.
    for intent_path in intent_to_add_paths:
        stage_zero = stage_entries.get((intent_path, 0))
        if stage_zero is None:
            return None
        mode, object_id, _skip_worktree, _assume_unchanged = stage_zero
        if object_id != empty_blob_object_id:
            return None

    projection: dict[str, str] = {"@object_format": format_name}
    for (path_bytes, stage), (mode, object_id, skip_worktree, assume_unchanged) in stage_entries.items():
        intent_to_add = stage == 0 and path_bytes in intent_to_add_paths
        key = f"entry:{path_bytes.hex()}:{stage}"
        projection[key] = (
            f"mode={mode.decode('ascii')}:object={object_id.decode('ascii')}:"
            f"skip_worktree={int(skip_worktree)}:assume_unchanged={int(assume_unchanged)}:"
            f"intent_to_add={int(intent_to_add)}"
        )

    for record in undo_records:
        parsed_undo = _parse_canonical_resolve_undo_record(record, expected_oid_length)
        if parsed_undo is None:
            return None
        undo_path_bytes, undo_stage, undo_mode, undo_object_id = parsed_undo
        key = f"resolve_undo:{undo_path_bytes.hex()}:{undo_stage}"
        if key in projection:
            return None
        projection[key] = f"mode={undo_mode.decode('ascii')}:object={undo_object_id.decode('ascii')}"

    return projection


def canonical_index_projection(cwd: Path) -> dict[str, str] | None:
    """Establish ONE coherent canonical effective-index projection.

    No single Git command can produce the complete canonical index state
    atomically (it is assembled from three separate queries in
    `_build_canonical_index_projection_once`), so a single provisional
    observation is never authoritative by itself -- it may, by chance,
    combine query outputs that correspond to different underlying moments.
    This function independently rebuilds the *complete* projection from
    scratch, up to `_MAX_CANONICAL_INDEX_OBSERVATIONS` times, and accepts
    only two *consecutive* identical complete projections -- exactly the
    same bounded 2-of-3-consecutive-match algorithm already approved for
    `establish_protected_git_metadata_baseline()`, applied here to the
    index's own multi-query observation instead of the whole protected
    tree. With the bound at 3: `P1 == P2` establishes `P2`; `P1 != P2` but
    `P2 == P3` establishes `P3`; `P1 != P2` and `P2 != P3` -- or any
    complete-projection attempt that itself returns `None` and is
    consequently never compared as a "previous" -- leaves the canonical
    index state unavailable. Flat and non-nested: one whole-projection
    attempt per loop iteration, never a retry loop nested inside another.

    Returns `None` -- fails closed, "unavailable" -- if no two consecutive
    observations ever agree within the bound. Callers must never substitute
    an empty hash, a default, or any other equivalent value for this.
    """

    previous: dict[str, str] | None = None
    for _ in range(_MAX_CANONICAL_INDEX_OBSERVATIONS):
        current = _build_canonical_index_projection_once(cwd)
        if current is not None and previous is not None and current == previous:
            return current
        previous = current
    return None


def git_index_identity(cwd: Path) -> str | None:
    """Canonical effective-index identity hash (see
    `canonical_index_projection`): exact byte-safe path, stage, mode, full
    object id, assume-unchanged, skip-worktree, intent-to-add, and
    resolve-undo state for every index entry, established via a bounded
    coherent multi-observation proof. Equivalent effective index states
    (ordinary/split/sparse index layouts) hash identically; any parse
    failure, unrecognized/unclassified index state, or exhausted coherence
    bound returns `None`, which fails closed at every comparison site that
    requires equality against a real hash -- never treated as equal to
    another `None`.
    """

    projection = canonical_index_projection(cwd)
    if projection is None:
        return None
    return workspace_hash(projection)


# The physical `.git/index` file is always measured at "index", directly
# under `git_dir` (see `snapshot_type_aware_tree`'s `f"{role}/{relative}"`
# key convention). Whether it may ALSO alias `git_common_dir/index` depends
# on whether the two roles resolve to the same directory -- see
# `_index_metadata_exception_keys`, which is role-aware precisely because
# they do not, in a linked worktree.
_GIT_DIR_INDEX_KEY = "git_dir/index"
_GIT_COMMON_DIR_INDEX_KEY = "git_common_dir/index"
# Both possible index keys, for DIAGNOSTIC purposes only (e.g. selecting
# which key's raw representation to describe -- see
# `describe_index_representation_diff`'s callers). Never used to decide
# AUTHORITY: that role-aware decision is `_index_metadata_exception_keys`.
_GIT_INDEX_METADATA_KEYS = frozenset({_GIT_DIR_INDEX_KEY, _GIT_COMMON_DIR_INDEX_KEY})
_PATH_DESCRIPTOR_PREFIX = "path:"


def _canonical_absolute_path_descriptor(path_descriptor: str) -> Path | None:
    """Validate a `"path:"` role descriptor as eligible to participate in
    role-relationship reasoning at all (F03-R2/Section 1): it must have the
    exact expected `path:` prefix, and the path portion must be absolute
    with no unresolved `".."` traversal segment -- i.e. it must already be
    in the same normalized, resolved form
    `_build_provisional_protected_git_metadata_observation`'s real snapshot
    producer writes (`directory.resolve()`/`common_directory.resolve()`).
    Returns the parsed `Path` when eligible, `None` otherwise. Callers must
    treat `None` as "cannot participate in role reasoning at all" -- never
    fall back to lexical/string comparison on the raw descriptor.

    A `"."` segment is not checked for separately: unlike `".."`, it is
    never context-dependent or ambiguous under symlinks -- `pathlib.Path`
    itself unconditionally drops it while parsing (`Path("/a/./b").parts ==
    ("/", "a", "b")`, with no literal `"."` ever appearing), so it
    normalizes away transparently rather than needing to be rejected.
    """

    if not path_descriptor.startswith(_PATH_DESCRIPTOR_PREFIX):
        return None
    raw = path_descriptor[len(_PATH_DESCRIPTOR_PREFIX):]
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    if ".." in candidate.parts:
        return None
    return candidate


def _index_metadata_exception_keys(metadata: Mapping[str, str]) -> frozenset[str]:
    """Which protected-metadata keys may participate in the canonical
    index representation exception, given ONE coherent projection's own
    `@git_dir`/`@git_common_dir` role paths (Section 8/F03).

    `git_dir/index` is always the CURRENT worktree's own effective index
    and may always use the canonical exception. `git_common_dir/index`
    aliases that exact same physical file only when `git_dir` and
    `git_common_dir` resolve to the same directory (an ordinary,
    non-linked-worktree repository); in a linked worktree, a distinct
    `git_common_dir/index` belongs to a *different* worktree's index and
    must remain fully strict protected metadata -- never eligible for the
    exception.

    In a linked worktree, `git_dir` is itself physically nested inside
    `git_common_dir` (Git's own `<common>/worktrees/<name>` layout), so the
    independent `git_common_dir` tree walk in `protected_git_metadata_snapshot`
    re-discovers the CURRENT worktree's own index a second time, under a
    common-dir-relative key such as `git_common_dir/worktrees/<name>/index`.
    That second key is still exactly the same physical file as
    `git_dir/index` -- never a different worktree's -- and is exactly as
    eligible for the exception.

    Required order (F03-R2/Section 2): parse the two role descriptors ->
    validate BOTH as canonical, absolute, already-resolved paths via
    `_canonical_absolute_path_descriptor` -> only then compare them (for the
    ordinary equal-path case) or prove a strict-descendant nesting (for the
    linked-worktree case) -> only then derive exception keys. If EITHER
    descriptor is missing, not a `path:` descriptor, relative, or contains
    an unresolved `.`/`..` traversal segment, no exception key is added at
    all -- not even `git_dir/index` alone -- regardless of what the raw
    strings happen to look like lexically (fails closed, not open). This
    also naturally rejects a misleading textual prefix with no real path
    containment, since containment is proven via `Path.relative_to` on the
    canonicalized operands, never string-prefix matching.
    """

    git_dir_path = metadata.get("@git_dir")
    git_common_dir_path = metadata.get("@git_common_dir")
    if not git_dir_path or not git_common_dir_path:
        return frozenset()
    canonical_git_dir = _canonical_absolute_path_descriptor(git_dir_path)
    canonical_git_common_dir = _canonical_absolute_path_descriptor(git_common_dir_path)
    if canonical_git_dir is None or canonical_git_common_dir is None:
        return frozenset()
    if canonical_git_dir == canonical_git_common_dir:
        return frozenset({_GIT_DIR_INDEX_KEY, _GIT_COMMON_DIR_INDEX_KEY})
    try:
        nested_relative = canonical_git_dir.relative_to(canonical_git_common_dir).as_posix()
    except ValueError:
        return frozenset()
    return frozenset({_GIT_DIR_INDEX_KEY, f"git_common_dir/{nested_relative}/index"})


def protected_git_metadata_preserved(
    before: Mapping[str, str],
    after: Mapping[str, str],
    *,
    canonical_index_identity_before: str | None,
    canonical_index_identity_after: str | None,
) -> bool:
    """Whether protected Git metadata is preserved between two coherent
    `protected_git_metadata_snapshot()`-shaped observations.

    The canonical effective index identity must be established (non-`None`)
    on BOTH sides and equal before ANY success path -- including when the
    raw protected-metadata projections are already byte-for-byte identical.
    An unavailable canonical index identity is never treated as equal to
    another unavailable one, and raw equality never substitutes for it.

    Only once that is proven: a raw `.git/index` descriptor difference
    confined *exactly* to the index key(s) eligible for the exception in
    THIS observation's own worktree role (`_index_metadata_exception_keys`
    -- the current worktree's `git_dir/index` always; `git_common_dir/index`
    only when it aliases the same physical file) -- with every other
    protected key byte-for-byte unchanged -- does not by itself indicate
    real repository drift: Git may rewrite the index's physical bytes as a
    routine, harmless stat-cache refresh (the first `git` command that
    inspects a tree whose file mtimes changed since the index was last
    written) with no semantic effect at all.

    Any OTHER protected key changing still fails this closed exactly as
    before -- this never weakens protection for HEAD, refs, config,
    gitdir/common-dir role identity, a linked worktree's separate
    common-dir index, or any other protected path; see
    TARGET04_CANONICAL_INDEX_AUTHORITY_CORRECTION_REPORT.md.
    """

    if not before or not after:
        return False
    if (
        canonical_index_identity_before is None
        or canonical_index_identity_after is None
        or canonical_index_identity_before != canonical_index_identity_after
    ):
        return False
    changed = diff_workspace_hashes(before, after)
    if not changed:
        return True
    exception_keys = _index_metadata_exception_keys(before)
    return all(key in exception_keys for key in changed)


def describe_index_representation_diff(
    before_descriptor: str | None,
    after_descriptor: str | None,
) -> dict[str, bool] | None:
    """Bounded diagnostic only: when a raw `.git/index` descriptor differs
    but is being tolerated because the canonical effective index identity
    is unchanged, report which specific stable-projected fields actually
    differed -- never an unbounded dump. Returns `None` if either
    descriptor is missing or does not match the exact stable `file:`
    descriptor grammar (see `_STABLE_FILE_DESCRIPTOR_RE`).
    """

    if before_descriptor is None or after_descriptor is None:
        return None
    before_match = _STABLE_FILE_DESCRIPTOR_RE.fullmatch(before_descriptor)
    after_match = _STABLE_FILE_DESCRIPTOR_RE.fullmatch(after_descriptor)
    if before_match is None or after_match is None:
        return None
    before_fields = before_match.groupdict()
    after_fields = after_match.groupdict()
    return {
        f"{field}_changed": before_fields[field] != after_fields[field]
        for field in ("dev", "ino", "mode", "size", "nlink", "sha256")
    }


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
    expected_index_identity: str | None,
    expected_git_metadata: Mapping[str, str],
    actual_git_metadata: Mapping[str, str],
    actual_index_identity: str | None,
) -> bool:
    """Require the complete repository identity used by review/repair preflight.

    `expected_git_metadata`/`actual_git_metadata` are full protected Git
    metadata projections (not pre-hashed): a raw `.git/index` descriptor
    difference confined to that one protected key can be tolerated when the
    canonical effective index identity (`expected_index_identity` vs
    `actual_index_identity`) is itself unchanged -- see
    `protected_git_metadata_preserved`. Every other protected key, and the
    canonical index identity itself, remain exactly as strict as before.

    `actual_index_identity` is a REQUIRED parameter, not measured internally
    here: the canonical index projection is now a bounded, multi-query
    coherence proof (`canonical_index_projection`), and the caller has
    already measured it once for this exact observation moment (reused for
    its own semantic evidence fields too) -- this function must never
    re-measure it independently, which would both waste that bounded proof
    and risk observing a subtly different moment (Section 3).

    Both `expected_index_identity` and `actual_index_identity` are
    `str | None` because the canonical index identity can genuinely be
    unavailable (a bounded coherence proof that never resolved). An
    unavailable expected OR actual identity is never treated as preserved
    -- including when they happen to both be `None` -- so this single
    shared helper enforces `CANONICAL_IDENTITY_NON_NULL_REQUIRED` for every
    one of its callers without each needing its own local rule.
    """

    if expected_index_identity is None or actual_index_identity is None:
        return False
    return (
        (git_rev_parse(workspace, "HEAD") or "") == expected_head
        and git_index_clean(workspace)
        and actual_index_identity == expected_index_identity
        and git_special_state(workspace) is None
        and git_rev_parse(workspace, baseline_ref) == baseline_commit
        and protected_git_metadata_preserved(
            expected_git_metadata,
            actual_git_metadata,
            canonical_index_identity_before=expected_index_identity,
            canonical_index_identity_after=actual_index_identity,
        )
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
    # or repository state at all. This is the explicit, bounded coherent-
    # bootstrap establishment (L1) -- not a single untrusted snapshot -- so
    # a genuine but transient timestamp-only instability cannot by itself
    # block a review before any untrusted subprocess has even run.
    review_entry_hashes = snapshot_workspace_hashes(workspace)
    review_entry_head = git_rev_parse(workspace, "HEAD") or ""
    # The run-level index identity must never become authoritative as
    # `None`: block here, before the first untrusted subprocess, rather
    # than let an unavailable canonical index identity silently flow
    # downstream (see review_repository_identity_preserved's own None
    # safety net, which exists as defense in depth, not as the primary
    # gate).
    review_entry_index_identity = git_index_identity(workspace)
    if review_entry_index_identity is None:
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=PREFLIGHT_FAILURE:GIT_INDEX_IDENTITY_UNAVAILABLE")
        print("MESSAGE=canonical effective Git index identity could not be established")
        return 3
    try:
        review_entry_baseline = establish_protected_git_metadata_baseline(workspace)
    except EvidenceIntegrityFailure as exc:
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=PREFLIGHT_FAILURE:GIT_METADATA_IDENTITY_UNAVAILABLE")
        print(f"MESSAGE={exc}")
        return 3
    review_entry_git_metadata = review_entry_baseline.stable_projection
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
        capability_probe_git_metadata = protected_git_metadata_snapshot(
            workspace, trusted_baseline=review_entry_git_metadata
        )
    except EvidenceIntegrityFailure:
        capability_probe_git_metadata = {}
    capability_probe_git_entry_hash = (
        workspace_git_entry_hash_from_snapshot(capability_probe_git_metadata)
        if capability_probe_git_metadata
        else ""
    )
    # Measured once here, reused for both the authority decision below and
    # the semantic receipt field further down -- never recomputed at a
    # nearby but different point (Section 3).
    capability_probe_index_identity = git_index_identity(workspace)
    capability_probe_repository_identity_preserved = (
        review_repository_identity_preserved(
            workspace,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            expected_head=review_entry_head,
            expected_index_identity=review_entry_index_identity,
            expected_git_metadata=review_entry_git_metadata,
            actual_git_metadata=capability_probe_git_metadata,
            actual_index_identity=capability_probe_index_identity,
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
    # Not coalesced to "" -- an unavailable canonical index identity here
    # must flow through as None (never an equivalent placeholder value) so
    # the reviewer-boundary check below correctly fails closed on it,
    # rather than risk two independently unavailable identities ever
    # comparing equal.
    post_check_index_identity = git_index_identity(workspace)
    try:
        post_check_git_metadata = protected_git_metadata_snapshot(
            workspace, trusted_baseline=capability_probe_git_metadata
        )
    except EvidenceIntegrityFailure:
        post_check_git_metadata = {}
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
        expected_git_metadata=review_entry_git_metadata,
        actual_git_metadata=post_check_git_metadata,
        actual_index_identity=post_check_index_identity,
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
            review_exit_git_metadata = protected_git_metadata_snapshot(
                workspace, trusted_baseline=post_check_git_metadata
            )
        except EvidenceIntegrityFailure:
            review_exit_git_metadata = {}
        review_exit_git_metadata_hash = workspace_hash(review_exit_git_metadata)
        review_exit_git_entry_hash = (
            workspace_git_entry_hash_from_snapshot(review_exit_git_metadata)
            if review_exit_git_metadata
            else ""
        )
        # Measured once here, reused for both authority decisions below
        # (reviewer AND overall both compare AGAINST this same review-exit
        # reading) and the semantic receipt fields further down.
        review_exit_index_identity = git_index_identity(workspace)
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
            expected_git_metadata=post_check_git_metadata,
            actual_git_metadata=review_exit_git_metadata,
            actual_index_identity=review_exit_index_identity,
        )
        repository_identity_preserved = review_repository_identity_preserved(
            workspace,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            expected_head=review_entry_head,
            expected_index_identity=review_entry_index_identity,
            expected_git_metadata=review_entry_git_metadata,
            actual_git_metadata=review_exit_git_metadata,
            actual_index_identity=review_exit_index_identity,
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
        # Semantic preservation per phase (Section 16/F05): the SAME
        # decision `review_repository_identity_preserved()` already used
        # for authority above -- never re-derived from `not changed_paths`
        # alone, so persisted evidence can never silently diverge from what
        # Runtime authority actually decided. Raw representation identity
        # (byte/descriptor equality) is kept separately, never conflated.
        capability_probe_metadata_semantically_preserved = protected_git_metadata_preserved(
            review_entry_git_metadata,
            capability_probe_git_metadata,
            canonical_index_identity_before=review_entry_index_identity,
            canonical_index_identity_after=capability_probe_index_identity,
        )
        deterministic_check_metadata_semantically_preserved = protected_git_metadata_preserved(
            review_entry_git_metadata,
            post_check_git_metadata,
            canonical_index_identity_before=review_entry_index_identity,
            canonical_index_identity_after=post_check_index_identity,
        )
        reviewer_metadata_semantically_preserved = protected_git_metadata_preserved(
            post_check_git_metadata,
            review_exit_git_metadata,
            canonical_index_identity_before=post_check_index_identity,
            canonical_index_identity_after=review_exit_index_identity,
        )
        overall_metadata_semantically_preserved = protected_git_metadata_preserved(
            review_entry_git_metadata,
            review_exit_git_metadata,
            canonical_index_identity_before=review_entry_index_identity,
            canonical_index_identity_after=review_exit_index_identity,
        )
        overall_index_representation_diagnostic = (
            describe_index_representation_diff(
                review_entry_git_metadata.get(_GIT_DIR_INDEX_KEY),
                review_exit_git_metadata.get(_GIT_DIR_INDEX_KEY),
            )
            if overall_metadata_semantically_preserved
            and any(key in _GIT_INDEX_METADATA_KEYS for key in overall_git_metadata_changed_paths)
            else None
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
        # Semantic authority decision -- the same one Runtime actually used
        # to accept/reject this review (Section 16/F05). Never merely
        # `not changed_paths`: an accepted index-representation-only
        # difference must report `preserved=true` here even though its raw
        # projection differs, exactly matching authority.
        "protected_git_metadata_preserved": overall_metadata_semantically_preserved,
        # Raw byte/descriptor identity, kept strictly separate: `false`
        # whenever ANY protected key's raw descriptor differs, even one
        # that was semantically tolerated above.
        "protected_git_metadata_raw_representation_preserved": (
            not overall_git_metadata_changed_paths
        ),
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
        # Bounded diagnostic only (never authoritative): which raw stat
        # fields differed, present only when an index-only representation
        # difference was actually tolerated above.
        "index_representation_diagnostic": overall_index_representation_diagnostic,
        "capability_probe_protected_git_metadata_preserved": (
            capability_probe_metadata_semantically_preserved
        ),
        "capability_probe_protected_git_metadata_raw_representation_preserved": (
            not capability_probe_git_metadata_changed_paths
        ),
        "capability_probe_workspace_git_entry_preserved": (
            bool(capability_probe_git_entry_hash)
            and capability_probe_git_entry_hash == review_entry_git_entry_hash
        ),
        "deterministic_check_protected_git_metadata_preserved": (
            deterministic_check_metadata_semantically_preserved
        ),
        "deterministic_check_protected_git_metadata_raw_representation_preserved": (
            not deterministic_check_git_metadata_changed_paths
        ),
        "deterministic_check_workspace_git_entry_preserved": (
            bool(post_check_git_entry_hash)
            and post_check_git_entry_hash == review_entry_git_entry_hash
        ),
        "reviewer_protected_git_metadata_preserved": (
            reviewer_metadata_semantically_preserved
        ),
        "reviewer_protected_git_metadata_raw_representation_preserved": (
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
        "reviewed_index_identity": review_exit_index_identity,
        "reviewed_protected_git_metadata_hash": review_exit_git_metadata_hash,
        # The exact complete stable protected-Git-metadata projection Review
        # measured at exit -- not merely its hash. Repair must never validate
        # its own later reading against anything but this exact projection
        # (never a run-start baseline, never a fresh coherent-bootstrap
        # re-establishment): see `run_repair.prove_reviewed_state()`. Bound
        # to `reviewed_protected_git_metadata_hash` above by construction
        # (both are derived from the same `review_exit_git_metadata` value)
        # and additionally participates in the same canonical review
        # evidence identity check as every other `review-receipt.json`
        # field (see `read_canonical_evidence` in `run_repair.load_review_evidence`).
        "reviewed_protected_git_metadata_projection": review_exit_git_metadata,
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
