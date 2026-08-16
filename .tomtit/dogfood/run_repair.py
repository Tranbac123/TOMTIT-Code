#!/usr/bin/env python3
"""Local-only, one-shot manual dogfood repair runner.

Closes the loop:

    Original task
    -> Claude implementation Attempt 1        (run_task.py)
    -> Codex REQUEST_CHANGES                  (run_review.py)
    -> TOMTIT creates a bounded repair prompt (this tool)
    -> Claude implementation Attempt 2        (this tool, exactly one invocation)
    -> new immutable evidence

This is not an automatic repair loop: exactly one Claude invocation, no
retry, no automatic re-review, capped at one repair round total. The Owner
must run `run_review.py` again, manually, to get an independent verdict on
the repair. This tool never claims a Codex finding is resolved -- only the
next independent review may determine that.

Owner rerun example:

    python3 .tomtit/dogfood/run_repair.py \\
      --workspace /absolute/path/to/target-repository \\
      --original-task-prompt-file .tomtit/dogfood/tasks/task.md \\
      --previous-implementation-run-directory .tomtit/dogfood/runs/<impl-run-id> \\
      --review-run-directory .tomtit/dogfood/reviews/<review-run-id> \\
      --expected-review-run-id <review-run-id> \\
      --allowed-path app/page.tsx \\
      --check-command "npm run lint" \\
      --check-command "npm run build" \\
      --timeout-seconds 900
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    AllowedSideEffectPathError,
    AuthorizedCommandError,
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    classify_workspace_changes,
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    normalize_allowed_side_effect_paths,
    normalize_authorized_commands,
    render_evidence_manifest_json,
    snapshot_workspace_contents,
    snapshot_workspace_hashes,
    workspace_hash,
    write_evidence,
)

# Sibling-module reuse (never duplicated): Git helpers, allowed-path rules,
# verdict schema/logic validation, and the check-command runner are all the
# exact same code paths run_review.py already established and tested.
import run_review  # noqa: E402

REPAIR_ATTEMPT_NUMBER = 2
MAX_REPAIR_ROUNDS = 1
REPAIR_EVIDENCE_FILES = ("result.json", "receipt.json", "diff.patch")
REVIEW_EVIDENCE_FILES = ("review-result.json", "review-receipt.json", "review-prompt.txt", "checks.json")


class PreflightFailure(Exception):
    """A configuration or basic precondition failure discovered before Claude runs.

    Maps to exit code 2. Nothing has been invoked yet when this is raised.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class IntegrityFailure(Exception):
    """An evidence-parsing, schema, or reviewed-state binding-proof failure.

    Maps to exit code 3: the inputs exist and are superficially well-formed
    as files, but their *content* does not prove what this tool requires --
    e.g. malformed JSON, a verdict/finding schema violation, or the current
    workspace no longer matching the state Codex actually reviewed.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


# ---------------------------------------------------------------------------
# Evidence loading.
# ---------------------------------------------------------------------------


def _parse_json_bytes(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityFailure(
            "EVIDENCE_PARSE_FAILURE", f"{name} is not readable/valid JSON: {exc.__class__.__name__}"
        ) from None


def load_previous_implementation_evidence(
    run_directory: Path,
    workspace: Path,
    expected_implementation_run_id: str,
    expected_implementation_attempt: int,
) -> dict[str, Any]:
    """Reuses run_review's own loader: same success/scope requirements apply here.

    Its `run_review.PreflightFailure` is normalized into this module's own
    `PreflightFailure` so every preflight-stage error the caller sees, from
    either module, maps to the same exit code (2).

    The repair is proving the state reviewed from Attempt 1, so the expected
    (run_id, attempt) identity is required here and bound BEFORE anything in
    the returned receipt -- including `allowed_side_effect_paths` -- can be
    trusted (see `run_preflight()` below).
    """

    try:
        return run_review.load_implementation_evidence(
            run_directory, workspace, expected_implementation_run_id, expected_implementation_attempt
        )
    except run_review.PreflightFailure as exc:
        raise PreflightFailure(exc.category, exc.message) from None


def load_review_evidence(review_directory: Path) -> dict[str, Any]:
    try:
        review_directory.lstat()
    except OSError:
        raise PreflightFailure(
            "MISSING_REVIEW_EVIDENCE",
            "review run directory does not exist",
        ) from None
    try:
        evidence_snapshot = run_review.read_canonical_evidence(
            review_directory,
            REVIEW_EVIDENCE_FILES,
        )
    except run_review.EvidenceIntegrityFailure as exc:
        raise IntegrityFailure(
            "UNSAFE_REVIEW_EVIDENCE_IDENTITY",
            f"review evidence identity is unsafe: {exc}",
        ) from None

    result = _parse_json_bytes(
        evidence_snapshot.file_bytes["review-result.json"],
        "review-result.json",
    )
    receipt = _parse_json_bytes(
        evidence_snapshot.file_bytes["review-receipt.json"],
        "review-receipt.json",
    )
    checks = _parse_json_bytes(
        evidence_snapshot.file_bytes["checks.json"],
        "checks.json",
    )
    try:
        prompt_text = evidence_snapshot.file_bytes["review-prompt.txt"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityFailure(
            "EVIDENCE_PARSE_FAILURE", f"review-prompt.txt is not readable: {exc.__class__.__name__}"
        ) from None

    try:
        validated = run_review.validate_verdict_schema(result)
    except run_review.ReviewFailure as exc:
        raise IntegrityFailure("INVALID_REVIEW_RESULT_SCHEMA", exc.message) from None

    return {"result": validated, "receipt": receipt, "checks": checks, "prompt_text": prompt_text}


# ---------------------------------------------------------------------------
# Verdict + finding validation. REQUEST_CHANGES only; findings preserved
# verbatim, never invented or discarded.
# ---------------------------------------------------------------------------


def require_request_changes_verdict(result: dict[str, Any]) -> None:
    verdict = result["verdict"]
    if verdict != "REQUEST_CHANGES":
        raise PreflightFailure(
            "REVIEW_VERDICT_NOT_REQUEST_CHANGES", f"repair requires a REQUEST_CHANGES review verdict, got {verdict!r}"
        )
    try:
        run_review.enforce_verdict_rules(result)
    except run_review.ReviewFailure as exc:
        raise IntegrityFailure("INVALID_REVIEW_RESULT_LOGIC", exc.message) from None


def validate_findings_content(findings: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for finding in findings:
        finding_id = str(finding["id"]).strip()
        if not finding_id:
            raise IntegrityFailure("INVALID_FINDING", "a finding id must be non-empty")
        if finding_id in seen:
            raise IntegrityFailure("DUPLICATE_FINDING_ID", f"duplicate finding id: {finding_id!r}")
        seen.add(finding_id)
        ordered_ids.append(finding_id)
        if not str(finding["required_change"]).strip():
            raise IntegrityFailure("INVALID_FINDING", f"finding {finding_id!r} has an empty required_change")
    return tuple(ordered_ids)


# ---------------------------------------------------------------------------
# Reviewed-state binding. Never trusts workspace_unchanged alone: the
# strongest proof is an independent recomputation of the same whole-workspace
# hash the adapter itself produced right after the implementation run,
# compared byte-for-byte against what is already persisted in result.json.
# ---------------------------------------------------------------------------


def _extract_original_task_section(review_prompt_text: str) -> str:
    marker = "# Original implementation task\n\n"
    start_idx = review_prompt_text.find(marker)
    if start_idx == -1:
        raise IntegrityFailure(
            "REVIEW_PROMPT_UNRECOGNIZED", "review prompt does not contain the expected original-task section"
        )
    start = start_idx + len(marker)
    next_heading = review_prompt_text.find("\n\n# ", start)
    end = next_heading if next_heading != -1 else len(review_prompt_text)
    return review_prompt_text[start:end].strip()


def _commit_object_exists(workspace: Path, commit: str) -> bool:
    """`git cat-file -e <sha>^{commit}` -- unlike `rev-parse --verify`, this
    rejects the all-zero null OID and any other syntactically SHA-shaped but
    nonexistent object, which `rev-parse --verify` alone does not.
    """

    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=str(workspace),
        env=run_review._read_only_subprocess_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    return completed.returncode == 0


def bind_review_to_expected_review_run(
    review_receipt: dict[str, Any],
    expected_review_run_id: str,
) -> None:
    """The parent run loop, never this tool, selects exactly which Review 1
    evidence authorizes a repair -- `expected_review_run_id` is threaded in
    explicitly from that selection (see `run_loop.invoke_repair()`), never
    inferred from a directory name, the receipt's own contents, the
    implementation identity, or filesystem ordering.

    Must run before ANYTHING in `review_receipt` is trusted -- verdict,
    findings, the reviewed protected projection, reviewed state hashes, or
    repair authorization state -- because a receipt bound to the exact same
    implementation run/attempt/workspace/baseline can still be a *different,
    self-consistent* review (e.g. a foreign or superseded Review 2) that
    must never substitute for the Review 1 the parent loop actually
    selected.
    """

    actual_review_run_id = review_receipt.get("review_run_id")
    if not isinstance(actual_review_run_id, str) or not actual_review_run_id:
        raise IntegrityFailure(
            "REVIEW_RUN_ID_MISSING",
            "review evidence has no review_run_id",
        )
    if actual_review_run_id != expected_review_run_id:
        raise IntegrityFailure(
            "REVIEW_RUN_ID_MISMATCH",
            "review evidence review_run_id does not match the expected Review 1 run id",
        )


def bind_review_to_implementation(
    *,
    review_receipt: dict[str, Any],
    previous_receipt: dict[str, Any],
    workspace: Path,
) -> str:
    """Cross-checks workspace/implementation-run binding; returns the baseline commit."""

    review_workspace = review_receipt.get("workspace")
    if not review_workspace or Path(str(review_workspace)).resolve() != workspace.resolve():
        raise IntegrityFailure("REVIEW_WORKSPACE_MISMATCH", "review evidence is bound to a different workspace")

    review_impl_id = review_receipt.get("implementation_run_id")
    if review_impl_id != previous_receipt.get("run_id"):
        raise IntegrityFailure(
            "REVIEW_IMPLEMENTATION_MISMATCH",
            "review evidence is bound to a different implementation run",
        )

    # The review must also state exactly the same canonical implementation
    # attempt as the bound implementation receipt -- never inferred from a
    # directory name, run_id suffix, review_run_id, workspace, Git state, or
    # a missing-field default. `previous_receipt["attempt"]` is already
    # identity-bound by this point (run_preflight() binds implementation
    # evidence to the expected --implementation-attempt before calling
    # here), so it is the trustworthy value to compare against.
    review_implementation_attempt = review_receipt.get("implementation_attempt")
    bound_implementation_attempt = previous_receipt.get("attempt")
    if (
        not isinstance(review_implementation_attempt, int)
        or isinstance(review_implementation_attempt, bool)
        or review_implementation_attempt <= 0
        or review_implementation_attempt != bound_implementation_attempt
    ):
        raise IntegrityFailure(
            "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH",
            f"review evidence implementation_attempt {review_implementation_attempt!r} does not match "
            f"the bound implementation receipt's attempt {bound_implementation_attempt!r}",
        )

    baseline_commit = review_receipt.get("baseline_commit")
    if not baseline_commit:
        raise IntegrityFailure("REVIEW_BASELINE_MISSING", "review evidence does not record a baseline commit")
    if not _commit_object_exists(workspace, str(baseline_commit)):
        raise IntegrityFailure(
            "REVIEW_BASELINE_MISMATCH", "review evidence baseline commit does not exist in this workspace's history"
        )
    baseline_ref = review_receipt.get("baseline_ref")
    if not isinstance(baseline_ref, str) or not baseline_ref:
        raise IntegrityFailure("REVIEW_BASELINE_MISSING", "review evidence does not record a baseline ref")
    live_baseline_commit = run_review.git_rev_parse(workspace, baseline_ref)
    if live_baseline_commit != baseline_commit:
        raise IntegrityFailure(
            "REVIEW_BASELINE_MISMATCH",
            "the review baseline ref no longer resolves to the reviewed baseline commit",
        )
    return str(baseline_commit)


def bind_review_to_original_task(review_prompt_text: str, original_task_prompt: str) -> None:
    embedded = _extract_original_task_section(review_prompt_text)
    if embedded != original_task_prompt.strip():
        raise IntegrityFailure(
            "REVIEW_TASK_PROMPT_MISMATCH",
            "the original task prompt does not match the task embedded in the reviewed prompt",
        )


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceFingerprint:
    workspace: str
    baseline_commit: str
    changed_paths: tuple[str, ...]
    source_changed_paths: tuple[str, ...]
    authorized_side_effect_changed_paths: tuple[str, ...]
    unexpected_changed_paths: tuple[str, ...]
    index_clean: bool
    status_porcelain: str
    untracked_files: tuple[tuple[str, str], ...]
    tracked_diff_sha256: str
    workspace_after_hash_expected: str
    workspace_after_hash_actual: str
    reviewed_head_expected: str | None
    reviewed_head_actual: str
    reviewed_index_identity_expected: str | None
    reviewed_index_identity_actual: str
    reviewed_protected_git_metadata_hash_expected: str | None
    reviewed_protected_git_metadata_hash_actual: str
    reviewed_workspace_git_entry_hash_expected: str | None
    reviewed_workspace_git_entry_hash_actual: str
    reviewed_workspace_integrity_hash_expected: str | None
    reviewed_workspace_integrity_hash_actual: str
    reviewed_product_state_hash_expected: str | None
    reviewed_product_state_hash_actual: str
    reviewed_authorized_side_effect_state_hash_expected: str | None
    reviewed_authorized_side_effect_state_hash_actual: str
    reviewed_unexpected_state_hash_expected: str | None
    reviewed_unexpected_state_hash_actual: str
    reviewed_workspace_full_hash_expected: str | None
    reviewed_workspace_full_hash_actual: str
    authorized_side_effect_drift: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "baseline_commit": self.baseline_commit,
            "changed_paths": list(self.changed_paths),
            "source_changed_paths": list(self.source_changed_paths),
            "authorized_side_effect_changed_paths": list(self.authorized_side_effect_changed_paths),
            "unexpected_changed_paths": list(self.unexpected_changed_paths),
            "index_clean": self.index_clean,
            "status_porcelain": self.status_porcelain,
            "untracked_files": [{"path": p, "sha256": h} for p, h in self.untracked_files],
            "tracked_diff_sha256": self.tracked_diff_sha256,
            "workspace_after_hash_expected": self.workspace_after_hash_expected,
            "workspace_after_hash_actual": self.workspace_after_hash_actual,
            "reviewed_head_expected": self.reviewed_head_expected,
            "reviewed_head_actual": self.reviewed_head_actual,
            "reviewed_index_identity_expected": self.reviewed_index_identity_expected,
            "reviewed_index_identity_actual": self.reviewed_index_identity_actual,
            "reviewed_protected_git_metadata_hash_expected": (
                self.reviewed_protected_git_metadata_hash_expected
            ),
            "reviewed_protected_git_metadata_hash_actual": (
                self.reviewed_protected_git_metadata_hash_actual
            ),
            "reviewed_workspace_git_entry_hash_expected": (
                self.reviewed_workspace_git_entry_hash_expected
            ),
            "reviewed_workspace_git_entry_hash_actual": (
                self.reviewed_workspace_git_entry_hash_actual
            ),
            "reviewed_workspace_integrity_hash_expected": (
                self.reviewed_workspace_integrity_hash_expected
            ),
            "reviewed_workspace_integrity_hash_actual": (
                self.reviewed_workspace_integrity_hash_actual
            ),
            "reviewed_product_state_hash_expected": self.reviewed_product_state_hash_expected,
            "reviewed_product_state_hash_actual": self.reviewed_product_state_hash_actual,
            "reviewed_authorized_side_effect_state_hash_expected": (
                self.reviewed_authorized_side_effect_state_hash_expected
            ),
            "reviewed_authorized_side_effect_state_hash_actual": (
                self.reviewed_authorized_side_effect_state_hash_actual
            ),
            "reviewed_unexpected_state_hash_expected": (
                self.reviewed_unexpected_state_hash_expected
            ),
            "reviewed_unexpected_state_hash_actual": self.reviewed_unexpected_state_hash_actual,
            "reviewed_workspace_full_hash_expected": (
                self.reviewed_workspace_full_hash_expected
            ),
            "reviewed_workspace_full_hash_actual": (
                self.reviewed_workspace_full_hash_actual
            ),
            "authorized_side_effect_drift": self.authorized_side_effect_drift,
        }


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _legacy_workspace_hash_with_root_gitfile(
    workspace: Path,
    current_hashes: dict[str, str],
) -> str:
    """Reproduce the historical linked-worktree hash for strict legacy evidence."""

    git_entry = workspace / ".git"
    try:
        metadata = git_entry.lstat()
    except OSError:
        return workspace_hash(current_hashes)
    if not stat.S_ISREG(metadata.st_mode):
        return workspace_hash(current_hashes)
    _, content = run_review._regular_file_measurement(
        git_entry,
        metadata,
        reject_hardlinks=False,
        capture_bytes=True,
    )
    if content is None:
        raise IntegrityFailure(
            "WORKSPACE_STATE_MISMATCH",
            "legacy linked-worktree .git identity could not be measured",
        )
    historical_hashes = dict(current_hashes)
    historical_hashes[".git"] = run_review._sha256(content)
    return workspace_hash(historical_hashes)


def _is_stable_projection_mapping(value: object) -> bool:
    """Shallow schema check for a stable protected-Git-metadata projection:
    a non-empty mapping of string path/role keys to string descriptor
    values. Shared between modern-evidence classification (which must
    reject an incomplete/malformed projection as malformed modern evidence,
    never silently fall back to legacy) and `_load_trusted_review_projection()`'s
    deeper hash-bound verification, so the two can never disagree about
    what "well-formed" means.
    """

    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    )


def _load_trusted_review_projection(
    review_receipt: dict[str, Any],
    *,
    expected_hash: str,
) -> dict[str, str]:
    """Load and verify the exact Review1 protected Git metadata projection.

    This is the only `trusted_baseline` repair may ever pass to
    `run_review.protected_git_metadata_snapshot()`: never a run-start
    baseline, never a fresh `establish_protected_git_metadata_baseline()`
    re-establishment, never an unstable current reading accepted on its
    own. The projection is read from the same `review_receipt` dict whose
    `workspace` / `implementation_run_id` / `implementation_attempt` /
    `baseline_commit` fields `bind_review_to_implementation()` already
    verified before this is ever called, so it inherits that same binding
    for free -- but a receipt can only be trusted at all once its own
    canonical evidence identity is intact (`load_review_evidence()`, via
    `read_canonical_evidence()`, already guarantees that before any of this
    module ever reads `review_receipt`).

    Additionally bound here to the receipt's own already-`_is_sha256`-
    validated `reviewed_protected_git_metadata_hash`: a projection that does
    not hash to that exact value cannot be the one Review1 actually
    measured, regardless of how it got here.
    """

    projection = review_receipt.get("reviewed_protected_git_metadata_projection")
    if projection is None:
        raise IntegrityFailure(
            "REVIEWED_PROTECTED_PROJECTION_MISSING",
            "review evidence has no reviewed protected Git metadata projection",
        )
    if not _is_stable_projection_mapping(projection):
        raise IntegrityFailure(
            "REVIEWED_PROTECTED_PROJECTION_MALFORMED",
            "review evidence protected Git metadata projection is not a well-formed string mapping",
        )
    if workspace_hash(projection) != expected_hash:
        raise IntegrityFailure(
            "REVIEWED_PROTECTED_PROJECTION_HASH_MISMATCH",
            "review evidence protected Git metadata projection does not match its own bound hash",
        )
    return projection


def prove_reviewed_state(
    *,
    workspace: Path,
    baseline_commit: str,
    previous_result: dict[str, Any],
    previous_receipt: dict[str, Any],
    review_receipt: dict[str, Any],
    allowed_source_paths: tuple[str, ...],
    allowed_side_effect_paths: tuple[str, ...],
) -> WorkspaceFingerprint:
    """The core reviewed-state proof.

    Two independent proofs, both required:

    1. New review evidence binds separate product, authorized-side-effect,
       and protected-other identities. Product and protected-other identity
       must match; authorized side effects are measured but may drift. Legacy
       review receipts without those identities retain the previous strict
       whole-workspace-hash fallback.

    2. The live Git-visible changed paths, split via the canonical
       `classify_workspace_changes()` into SOURCE / AUTHORIZED_SIDE_EFFECT /
       UNEXPECTED, must contain no UNEXPECTED path, and the SOURCE portion
       must exactly match the reviewed implementation's own (already
       source-only) `changed_paths`. Git may legitimately also show an
       Owner-authorized generated side effect (e.g. dist/bundle.js under
       root "dist") that was never part of the reviewed source diff -- this
       is the fix for the raw-tuple-comparison defect that used to reject
       that valid state.
    """

    expected_hash = previous_result.get("workspace_after_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise IntegrityFailure(
            "PREVIOUS_EVIDENCE_INCOMPLETE", "previous implementation result.json has no workspace_after_hash"
        )
    current_hashes = snapshot_workspace_hashes(workspace)
    actual_hash = workspace_hash(current_hashes)
    current_product, current_side_effects, current_unexpected = (
        run_review.partition_reviewed_workspace_state(
            current_hashes,
            allowed_source_paths,
            allowed_side_effect_paths,
        )
    )
    actual_product_hash = workspace_hash(current_product)
    actual_side_effect_hash = workspace_hash(current_side_effects)
    actual_unexpected_hash = workspace_hash(current_unexpected)
    actual_integrity_hash = run_review.reviewed_workspace_integrity_hash(
        current_hashes,
        allowed_source_paths,
        allowed_side_effect_paths,
    )
    expected_head = review_receipt.get("reviewed_head")
    actual_head = run_review.git_rev_parse(workspace, "HEAD") or ""
    expected_index_clean = review_receipt.get("reviewed_index_clean")
    expected_index_identity = review_receipt.get("reviewed_index_identity")
    actual_index_identity = run_review.git_index_identity(workspace) or ""
    expected_git_metadata_hash = review_receipt.get(
        "reviewed_protected_git_metadata_hash"
    )
    expected_git_entry_hash = review_receipt.get(
        "reviewed_workspace_git_entry_hash"
    )
    expected_git_special_state = review_receipt.get("reviewed_git_special_state")
    expected_baseline_commit = review_receipt.get("reviewed_baseline_commit")
    expected_allowed_source_paths = review_receipt.get(
        "reviewed_allowed_source_paths"
    )
    expected_integrity_hash = review_receipt.get("reviewed_workspace_integrity_hash")
    expected_product_hash = review_receipt.get("reviewed_product_state_hash")
    expected_side_effect_hash = review_receipt.get(
        "reviewed_authorized_side_effect_state_hash"
    )
    expected_unexpected_hash = review_receipt.get("reviewed_unexpected_state_hash")
    expected_full_hash = review_receipt.get("reviewed_workspace_full_hash")
    expected_git_metadata_projection = review_receipt.get(
        "reviewed_protected_git_metadata_projection"
    )
    # `reviewed_protected_git_metadata_projection` is a MODERN-EVIDENCE
    # MARKER field, exactly like the other 12 partitioned-identity fields
    # below: if it -- or any one of them -- is present at all, this receipt
    # is modern evidence and the *complete* modern schema is required. There
    # is no partial-modern state: a receipt can never keep the projection
    # while silently losing enough of the rest to be treated as legacy (or
    # vice versa). `_is_stable_projection_mapping` here is the same shallow
    # shape check `_load_trusted_review_projection()` uses later for its own
    # deeper hash-bound verification -- the two can never disagree.
    partitioned_identity_fields = (
        "reviewed_head",
        "reviewed_index_clean",
        "reviewed_index_identity",
        "reviewed_protected_git_metadata_hash",
        "reviewed_protected_git_metadata_projection",
        "reviewed_workspace_git_entry_hash",
        "reviewed_git_special_state",
        "reviewed_baseline_commit",
        "reviewed_allowed_source_paths",
        "reviewed_workspace_integrity_hash",
        "reviewed_product_state_hash",
        "reviewed_authorized_side_effect_state_hash",
        "reviewed_unexpected_state_hash",
        "reviewed_workspace_full_hash",
    )
    partitioned_identity_present = any(
        key in review_receipt for key in partitioned_identity_fields
    )
    partitioned_identity_available = (
        all(key in review_receipt for key in partitioned_identity_fields)
        and isinstance(expected_head, str)
        and bool(expected_head)
        and expected_index_clean is True
        and _is_sha256(expected_index_identity)
        and _is_sha256(expected_git_metadata_hash)
        and _is_stable_projection_mapping(expected_git_metadata_projection)
        and _is_sha256(expected_git_entry_hash)
        and expected_git_special_state is None
        and isinstance(expected_baseline_commit, str)
        and expected_baseline_commit == baseline_commit
        and isinstance(expected_allowed_source_paths, list)
        and all(isinstance(path, str) for path in expected_allowed_source_paths)
        and tuple(expected_allowed_source_paths) == allowed_source_paths
        and all(
            _is_sha256(value)
            for value in (
                expected_integrity_hash,
                expected_product_hash,
                expected_side_effect_hash,
                expected_unexpected_hash,
                expected_full_hash,
            )
        )
    )
    if partitioned_identity_present and not partitioned_identity_available:
        raise IntegrityFailure(
            "PREVIOUS_EVIDENCE_INCOMPLETE",
            "review evidence contains an incomplete or malformed partitioned workspace identity",
        )

    # The trusted projection Review1 actually measured is the ONLY basis
    # repair may ever use to tolerate a benign timestamp-only instability
    # here -- never `establish_protected_git_metadata_baseline()` (repair
    # must never bootstrap its own authority), never the run-start baseline,
    # never an unstable current reading accepted on its own. Legacy review
    # evidence (no partitioned identity at all) never had this projection
    # and is unaffected: its equality check below does not gate on
    # `actual_git_metadata_hash`, so it keeps the pre-existing strict
    # whole-workspace-hash-only fallback unchanged.
    try:
        if partitioned_identity_available:
            reviewed_projection = _load_trusted_review_projection(
                review_receipt, expected_hash=str(expected_git_metadata_hash)
            )
            actual_git_metadata = run_review.protected_git_metadata_snapshot(
                workspace, trusted_baseline=reviewed_projection
            )
        else:
            actual_git_metadata = run_review.protected_git_metadata_snapshot(workspace)
        actual_git_metadata_hash = workspace_hash(actual_git_metadata)
        actual_git_entry_hash = run_review.workspace_git_entry_hash_from_snapshot(
            actual_git_metadata
        )
    except run_review.EvidenceIntegrityFailure:
        actual_git_metadata_hash = ""
        actual_git_entry_hash = ""

    if partitioned_identity_available:
        if (
            actual_head != expected_head
            or actual_index_identity != expected_index_identity
            or actual_git_metadata_hash != expected_git_metadata_hash
            or actual_git_entry_hash != expected_git_entry_hash
        ):
            raise IntegrityFailure(
                "WORKSPACE_STATE_MISMATCH",
                "the current repository identity differs from the reviewed repository state",
            )
        if (
            actual_integrity_hash != expected_integrity_hash
            or actual_product_hash != expected_product_hash
            or actual_unexpected_hash != expected_unexpected_hash
        ):
            raise IntegrityFailure(
                "WORKSPACE_STATE_MISMATCH",
                "the current product or unexpected workspace state differs from the reviewed state",
            )
    elif actual_hash != expected_hash:
        try:
            legacy_actual_hash = _legacy_workspace_hash_with_root_gitfile(
                workspace,
                current_hashes,
            )
        except run_review.EvidenceIntegrityFailure:
            legacy_actual_hash = ""
        if legacy_actual_hash != expected_hash:
            raise IntegrityFailure(
                "WORKSPACE_STATE_MISMATCH",
                "legacy review evidence requires an exact whole-workspace content hash match",
            )

    live_changed_paths = tuple(run_review.git_changed_paths(workspace, baseline_commit))
    source_changed_paths, authorized_side_effect_changed_paths, unexpected_changed_paths = classify_workspace_changes(
        live_changed_paths, allowed_source_paths, allowed_side_effect_paths, workspace_root=workspace
    )
    if unexpected_changed_paths:
        raise IntegrityFailure(
            "WORKSPACE_STATE_MISMATCH",
            f"current workspace has unexpected changed paths not reviewed and not an authorized "
            f"generated side effect: {list(unexpected_changed_paths)}",
        )

    expected_source_changed_paths = tuple(sorted(previous_receipt.get("changed_paths", [])))
    if source_changed_paths != expected_source_changed_paths:
        raise IntegrityFailure(
            "WORKSPACE_STATE_MISMATCH",
            f"current source changed paths {list(source_changed_paths)} differ from the "
            f"reviewed implementation's source changed paths {list(expected_source_changed_paths)}",
        )

    if not run_review.git_index_clean(workspace):
        raise PreflightFailure("DIRTY_INDEX", "workspace Git index must be clean")

    status_porcelain = run_review.git_status_porcelain(workspace)
    untracked = tuple(
        sorted(
            (p, run_review._sha256((workspace / p).read_bytes()))
            for p in run_review._parse_status_paths(status_porcelain)
            if (workspace / p).is_file()
        )
    )
    tracked_diff = run_review.git_tracked_diff(workspace, baseline_commit, list(live_changed_paths))

    return WorkspaceFingerprint(
        workspace=str(workspace),
        baseline_commit=baseline_commit,
        changed_paths=live_changed_paths,
        source_changed_paths=source_changed_paths,
        authorized_side_effect_changed_paths=authorized_side_effect_changed_paths,
        unexpected_changed_paths=unexpected_changed_paths,
        index_clean=True,
        status_porcelain=status_porcelain,
        untracked_files=untracked,
        tracked_diff_sha256=run_review._sha256(tracked_diff.encode("utf-8")),
        workspace_after_hash_expected=str(expected_hash),
        workspace_after_hash_actual=actual_hash,
        reviewed_head_expected=(str(expected_head) if isinstance(expected_head, str) else None),
        reviewed_head_actual=actual_head,
        reviewed_index_identity_expected=(
            str(expected_index_identity)
            if isinstance(expected_index_identity, str)
            else None
        ),
        reviewed_index_identity_actual=actual_index_identity,
        reviewed_protected_git_metadata_hash_expected=(
            str(expected_git_metadata_hash)
            if isinstance(expected_git_metadata_hash, str)
            else None
        ),
        reviewed_protected_git_metadata_hash_actual=actual_git_metadata_hash,
        reviewed_workspace_git_entry_hash_expected=(
            str(expected_git_entry_hash)
            if isinstance(expected_git_entry_hash, str)
            else None
        ),
        reviewed_workspace_git_entry_hash_actual=actual_git_entry_hash,
        reviewed_workspace_integrity_hash_expected=(
            str(expected_integrity_hash)
            if isinstance(expected_integrity_hash, str)
            else None
        ),
        reviewed_workspace_integrity_hash_actual=actual_integrity_hash,
        reviewed_product_state_hash_expected=(
            str(expected_product_hash) if isinstance(expected_product_hash, str) else None
        ),
        reviewed_product_state_hash_actual=actual_product_hash,
        reviewed_authorized_side_effect_state_hash_expected=(
            str(expected_side_effect_hash)
            if isinstance(expected_side_effect_hash, str)
            else None
        ),
        reviewed_authorized_side_effect_state_hash_actual=actual_side_effect_hash,
        reviewed_unexpected_state_hash_expected=(
            str(expected_unexpected_hash)
            if isinstance(expected_unexpected_hash, str)
            else None
        ),
        reviewed_unexpected_state_hash_actual=actual_unexpected_hash,
        reviewed_workspace_full_hash_expected=(
            str(expected_full_hash) if isinstance(expected_full_hash, str) else None
        ),
        reviewed_workspace_full_hash_actual=actual_hash,
        authorized_side_effect_drift=(
            isinstance(expected_side_effect_hash, str)
            and actual_side_effect_hash != expected_side_effect_hash
        ),
    )


# ---------------------------------------------------------------------------
# Repair-round cap: this run directory must be an original (attempt 1) run,
# never a prior repair's own output. There is no `--attempt` flag: the value
# is fixed, and pointing this tool at a repair's own evidence is refused.
# ---------------------------------------------------------------------------


def require_first_attempt(previous_receipt: dict[str, Any]) -> None:
    attempt = previous_receipt.get("attempt", 1)
    if attempt != 1:
        raise PreflightFailure(
            "SECOND_REPAIR_ROUND_REJECTED",
            f"the referenced implementation run is already attempt {attempt}; "
            f"at most {MAX_REPAIR_ROUNDS} repair round is authorized",
        )


# ---------------------------------------------------------------------------
# Repair prompt construction.
# ---------------------------------------------------------------------------


def build_repair_prompt(
    *,
    original_task_prompt: str,
    baseline_commit: str,
    allowed_paths: tuple[str, ...],
    previous_receipt: dict[str, Any],
    previous_patch_text: str,
    review_result: dict[str, Any],
    review_checks: list[dict[str, Any]],
    finding_ids: tuple[str, ...],
    repair_check_commands: list[str],
) -> str:
    sections: list[str] = []
    sections.append("# Original task and goal\n\n" + original_task_prompt.strip())
    sections.append(
        "# Original non-goals and constraints\n\n"
        "All non-goals, constraints, and scope limits stated in the original task above "
        "remain fully in force during this repair. Do not relax or reinterpret them."
    )
    sections.append(f"# Baseline\n\nbaseline_commit: {baseline_commit}")
    sections.append("# Attempt number\n\nprevious attempt = 1\ncurrent repair attempt = 2 (final; no further repair round is authorized)")
    sections.append("# Allowed changed paths (exact, no others permitted)\n\n" + "\n".join(f"- {p}" for p in allowed_paths))
    bounded_patch = previous_patch_text if len(previous_patch_text) <= 20000 else previous_patch_text[:20000] + "\n...(truncated)"
    sections.append(
        "# Current implementation state (Attempt 1)\n\n"
        f"changed_paths: {json.dumps(sorted(previous_receipt.get('changed_paths', [])))}\n\n"
        "Attempt 1 diff:\n```diff\n" + bounded_patch + "\n```"
    )
    sections.append("# Codex verdict\n\nverdict: REQUEST_CHANGES\nsummary: " + str(review_result.get("summary", "")))
    finding_blocks = []
    for finding in review_result.get("findings", []):
        finding_blocks.append(
            f"- id: {finding['id']}\n"
            f"  severity: {finding['severity']}\n"
            f"  title: {finding['title']}\n"
            f"  evidence: {finding['evidence']}\n"
            f"  required_change: {finding['required_change']}"
        )
    sections.append("# Codex findings (fix exactly these, preserve every id verbatim)\n\n" + "\n".join(finding_blocks))
    sections.append(
        "# Deterministic check results Codex observed\n\n```json\n"
        + json.dumps(review_checks, indent=2, sort_keys=True)
        + "\n```"
    )
    sections.append(
        "# Required repair behavior\n\n"
        "- Fix only the Codex findings listed above (finding ids: "
        f"{', '.join(finding_ids)}).\n"
        "- Preserve already-correct behavior; do not regress anything that was not flagged.\n"
        "- Do not redesign unrelated areas.\n"
        "- Do not broaden scope beyond the listed findings.\n"
        "- Do not create a Git commit.\n"
        "- Do not modify dependencies or configuration unless explicitly required by a listed finding.\n"
        "- Run only the checks supplied for this repair; do not invent additional checks.\n"
        "- Report which finding ids were addressed and how.\n"
        "- Do not claim a finding is resolved without pointing to the specific change that addresses it."
    )
    sections.append(
        "# Stop conditions\n\n"
        "If fixing a finding would require modifying a file outside the allowed paths, "
        "or would require a dependency/configuration change not explicitly authorized, "
        "stop and explain why instead of making that change."
    )
    checks_text = "\n".join(f"- {c}" for c in repair_check_commands) if repair_check_commands else "(none supplied)"
    sections.append("# Acceptance checks for this repair\n\n" + checks_text)
    sections.append(
        "# Reminder\n\n"
        "This REQUEST_CHANGES verdict is a required fix, not optional advice. "
        "Do not ask to re-review yourself; the Owner will run an independent Codex "
        "review after this attempt."
    )
    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Allowed-path validation (delegated to run_review's exact rules).
# ---------------------------------------------------------------------------


def normalize_allowed_paths(raw_paths: list[str], workspace: Path) -> tuple[str, ...]:
    try:
        return run_review.normalize_allowed_paths(raw_paths, workspace)
    except run_review.PreflightFailure as exc:
        raise PreflightFailure(exc.category, exc.message) from None


# ---------------------------------------------------------------------------
# Evidence directory resolution (mirrors run_task.py's rules, under runs/).
# ---------------------------------------------------------------------------


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_repair_evidence_directory(evidence_directory_arg: str | None, repair_run_id: str, workspace: Path) -> Path:
    raw = evidence_directory_arg
    directory = Path(raw).expanduser() if raw else REPO_ROOT / ".tomtit" / "dogfood" / "runs" / repair_run_id
    if not directory.is_absolute():
        directory = (Path.cwd() / directory).resolve(strict=False)
    else:
        directory = directory.resolve(strict=False)
    if _is_within(directory, workspace):
        raise PreflightFailure("EVIDENCE_DIRECTORY_INSIDE_WORKSPACE", "evidence directory must be outside the target workspace")
    if directory.exists():
        if not directory.is_dir():
            raise PreflightFailure("EVIDENCE_DIRECTORY_COLLISION", "repair evidence directory path is not a directory")
        if any(directory.iterdir()):
            raise PreflightFailure("EVIDENCE_DIRECTORY_COLLISION", "repair evidence directory already exists and is non-empty")
    return directory


# ---------------------------------------------------------------------------
# CLI argument parsing.
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded local Claude repair attempt for a REQUEST_CHANGES review.")
    parser.add_argument("--workspace", required=True, metavar="PATH")
    parser.add_argument("--original-task-prompt-file", required=True, metavar="PATH")
    parser.add_argument("--previous-implementation-run-directory", required=True, metavar="PATH")
    parser.add_argument("--review-run-directory", required=True, metavar="PATH")
    parser.add_argument("--allowed-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--check-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--authorized-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--allowed-side-effect-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--timeout-seconds", type=int, required=True, metavar="INTEGER")
    parser.add_argument("--model", metavar="TEXT")
    parser.add_argument("--permission-mode", default="acceptEdits", metavar="TEXT")
    parser.add_argument("--claude-executable", metavar="PATH")
    parser.add_argument("--repair-run-id", metavar="TEXT")
    parser.add_argument("--evidence-directory", metavar="PATH")
    parser.add_argument("--implementation-run-id", required=True, metavar="TEXT")
    parser.add_argument("--implementation-attempt", type=int, required=True, metavar="INTEGER")
    parser.add_argument("--expected-review-run-id", required=True, metavar="TEXT")
    return parser.parse_args(argv)


def _read_original_task_prompt(path_arg: str) -> str:
    try:
        text = Path(path_arg).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightFailure("MISSING_TASK_PROMPT", f"task prompt file could not be read: {exc.__class__.__name__}") from None
    if not text.strip():
        raise PreflightFailure("EMPTY_TASK_PROMPT", "task prompt must not be empty")
    return text


def _hash_evidence_files(directory: Path, filenames: tuple[str, ...]) -> dict[str, str]:
    return run_review.snapshot_canonical_evidence(directory, filenames)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_summary(
    *,
    repair_run_id: str,
    status: str,
    success: bool | None,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
    previous_implementation_run_id: str | None,
    review_run_id: str | None,
    finding_ids: tuple[str, ...],
    changed_paths: tuple[str, ...],
    unexpected_changed_paths: tuple[str, ...],
    checks_passed: bool | None,
    workspace_bound: bool | None,
    previous_evidence_unchanged: bool | None,
    evidence_directory: Path | None,
) -> None:
    print(f"REPAIR_RUN_ID={repair_run_id}")
    print(f"STATUS={status}")
    print(f"ATTEMPT={REPAIR_ATTEMPT_NUMBER}")
    print(f"SUCCESS={str(success).lower() if success is not None else ''}")
    print(f"EXIT_CODE={exit_code if exit_code is not None else ''}")
    print(f"TIMED_OUT={str(timed_out).lower()}")
    print(f"DURATION_MS={duration_ms}")
    print(f"PREVIOUS_IMPLEMENTATION_RUN_ID={previous_implementation_run_id or ''}")
    print(f"REVIEW_RUN_ID={review_run_id or ''}")
    print(f"FINDING_IDS={json.dumps(list(finding_ids))}")
    print(f"CHANGED_PATHS={json.dumps(list(changed_paths))}")
    print(f"UNEXPECTED_CHANGED_PATHS={json.dumps(list(unexpected_changed_paths))}")
    print(f"CHECKS_PASSED={str(checks_passed).lower() if checks_passed is not None else ''}")
    print(f"WORKSPACE_BOUND_TO_REVIEWED_STATE={str(workspace_bound).lower() if workspace_bound is not None else ''}")
    print(f"PREVIOUS_EVIDENCE_UNCHANGED={str(previous_evidence_unchanged).lower() if previous_evidence_unchanged is not None else ''}")
    print(f"EVIDENCE_DIRECTORY={evidence_directory or ''}")
    print("NEXT_ACTION=RUN_INDEPENDENT_CODEX_REVIEW")


# ---------------------------------------------------------------------------
# Main orchestration.
# ---------------------------------------------------------------------------


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    if args.timeout_seconds <= 0:
        raise PreflightFailure("INVALID_TIMEOUT", "timeout must be positive")

    try:
        workspace = run_review._validate_workspace(args.workspace)
    except run_review.PreflightFailure as exc:
        raise PreflightFailure(exc.category, exc.message) from None

    if not run_review.git_index_clean(workspace):
        raise PreflightFailure("DIRTY_INDEX", "workspace Git index must be clean before repair")
    special_state = run_review.git_special_state(workspace)
    if special_state is not None:
        raise PreflightFailure("ACTIVE_GIT_OPERATION", f"workspace has an active Git operation: {special_state}")

    original_task_prompt = _read_original_task_prompt(args.original_task_prompt_file)

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

    expected_review_run_id = args.expected_review_run_id
    if not isinstance(expected_review_run_id, str) or not expected_review_run_id.strip():
        raise PreflightFailure(
            "INVALID_EXPECTED_REVIEW_RUN_ID", "--expected-review-run-id must be a non-empty string"
        )

    previous_run_directory = Path(args.previous_implementation_run_directory).expanduser()
    if not previous_run_directory.is_absolute():
        previous_run_directory = (Path.cwd() / previous_run_directory).resolve(strict=False)

    # Evidence must be bound to the exact expected (workspace, run_id,
    # attempt) BEFORE anything in it -- including `allowed_side_effect_paths`
    # -- can be trusted below. The repair is proving the state reviewed from
    # Attempt 1, not an arbitrary directory an attacker could point at.
    previous_evidence = load_previous_implementation_evidence(
        previous_run_directory, workspace, implementation_run_id, implementation_attempt
    )
    require_first_attempt(previous_evidence["receipt"])

    review_directory = Path(args.review_run_directory).expanduser()
    if not review_directory.is_absolute():
        review_directory = (Path.cwd() / review_directory).resolve(strict=False)
    review_evidence = load_review_evidence(review_directory)

    # Which review authorizes this repair is decided by the parent run
    # loop's own Review 1 selection, never by anything in this receipt
    # itself -- this must be verified before any of the receipt's content
    # (verdict, findings, reviewed projection, reviewed state) is trusted.
    bind_review_to_expected_review_run(review_evidence["receipt"], expected_review_run_id)

    require_request_changes_verdict(review_evidence["result"])
    finding_ids = validate_findings_content(review_evidence["result"]["findings"])

    baseline_commit = bind_review_to_implementation(
        review_receipt=review_evidence["receipt"], previous_receipt=previous_evidence["receipt"], workspace=workspace
    )
    bind_review_to_original_task(review_evidence["prompt_text"], original_task_prompt)

    allowed_paths = normalize_allowed_paths(args.allowed_path, workspace)
    raw_original_allowed_paths = previous_evidence["receipt"].get(
        "allowed_changed_paths"
    )
    if not isinstance(raw_original_allowed_paths, list) or not all(
        isinstance(path, str) for path in raw_original_allowed_paths
    ):
        raise IntegrityFailure(
            "ALLOWED_PATH_EVIDENCE_INVALID",
            "previous implementation evidence allowed_changed_paths is malformed",
        )
    try:
        original_allowed_paths = normalize_allowed_paths(
            raw_original_allowed_paths,
            workspace,
        )
    except PreflightFailure as exc:
        raise IntegrityFailure(
            "ALLOWED_PATH_EVIDENCE_INVALID",
            f"previous implementation evidence allowed_changed_paths is invalid: {exc.category}",
        ) from None
    if allowed_paths != original_allowed_paths:
        raise IntegrityFailure(
            "ALLOWED_PATH_CONTRACT_MISMATCH",
            "repair allowed paths do not exactly match the bound implementation contract",
        )
    reviewed_allowed_paths = review_evidence["receipt"].get(
        "reviewed_allowed_source_paths"
    )
    if reviewed_allowed_paths is not None and (
        not isinstance(reviewed_allowed_paths, list)
        or not all(isinstance(path, str) for path in reviewed_allowed_paths)
        or tuple(reviewed_allowed_paths) != allowed_paths
    ):
        raise IntegrityFailure(
            "ALLOWED_PATH_CONTRACT_MISMATCH",
            "repair allowed paths do not match the review identity classification contract",
        )
    allowed_set = set(allowed_paths)
    previous_changed = set(previous_evidence["receipt"].get("changed_paths", []))
    if not previous_changed.issubset(allowed_set):
        raise PreflightFailure(
            "CHANGED_PATH_OUT_OF_SCOPE",
            f"previously changed paths outside the allowed set: {sorted(previous_changed - allowed_set)}",
        )

    # Read the reviewed implementation's own authorized side-effect roots
    # directly from the now-identity-bound receipt -- never from CLI input,
    # a directory name, reviewer output, or Git state -- so classification
    # below can only ever grant authority Attempt 1 actually held.
    raw_original_side_effect_paths = previous_evidence["receipt"].get("allowed_side_effect_paths")
    if raw_original_side_effect_paths is None:
        original_allowed_side_effect_paths: tuple[str, ...] = ()
    elif isinstance(raw_original_side_effect_paths, list) and all(
        isinstance(p, str) for p in raw_original_side_effect_paths
    ):
        original_allowed_side_effect_paths = tuple(raw_original_side_effect_paths)
    else:
        raise IntegrityFailure(
            "SIDE_EFFECT_PATH_EVIDENCE_INVALID",
            "previous implementation evidence allowed_side_effect_paths is malformed",
        )

    fingerprint = prove_reviewed_state(
        workspace=workspace,
        baseline_commit=baseline_commit,
        previous_result=previous_evidence["result"],
        previous_receipt=previous_evidence["receipt"],
        review_receipt=review_evidence["receipt"],
        allowed_source_paths=allowed_paths,
        allowed_side_effect_paths=original_allowed_side_effect_paths,
    )
    # No further "changed paths within allowed scope" check is needed here:
    # `prove_reviewed_state()` already fails closed on any UNEXPECTED path
    # and its SOURCE portion is, by construction of `classify_workspace_
    # changes()`, always a subset of `allowed_paths`.

    for command in args.check_command:
        try:
            run_review.validate_check_command_text(command)
        except run_review.PreflightFailure as exc:
            raise PreflightFailure(exc.category, exc.message) from None

    try:
        authorized_commands = normalize_authorized_commands(args.authorized_command)
    except AuthorizedCommandError as exc:
        raise PreflightFailure(exc.category, exc.message) from None

    # The repair must inherit the *exact same* Bash authorization the original
    # implementation had -- never broader, never narrower, never substituted,
    # never reordered. Proven by ordered-fingerprint equality only: the
    # original implementation's evidence never carries raw authorized-command
    # values to compare against (Codex corrective review, HIGH finding 2), so
    # this repair also never reconstructs them from receipt data -- it only
    # recomputes its own fingerprint and compares it to the persisted one. A
    # REQUEST_CHANGES finding is never grounds to introduce new Bash
    # authorization: it can only ask Claude to fix code within the paths and
    # commands already approved for Attempt 1.
    original_receipt = previous_evidence["receipt"]
    current_set_fingerprint = (
        compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
    )
    if "authorized_command_set_fingerprint" not in original_receipt:
        # Evidence predating this corrective schema (or otherwise malformed):
        # there is no safe way to know what, if anything, Attempt 1 was
        # authorized to run, so authorization can only be inherited as
        # "none". Never silently trust or silently broaden it.
        if authorized_commands:
            raise IntegrityFailure(
                "AUTHORIZED_COMMAND_EVIDENCE_INVALID",
                "previous implementation evidence has no authorized_command_set_fingerprint to inherit from "
                "(predates this schema or is malformed); a repair cannot supply --authorized-command in this case",
            )
    else:
        original_set_fingerprint = original_receipt["authorized_command_set_fingerprint"]
        if current_set_fingerprint != original_set_fingerprint:
            raise IntegrityFailure(
                "AUTHORIZED_COMMAND_CONTRACT_MISMATCH",
                "repair --authorized-command set's fingerprint does not exactly match the original "
                "implementation's authorized-command-set fingerprint (addition, removal, substitution, or "
                "reorder is never permitted)",
            )

    # The generated/side-effect authorization is inherited under the exact
    # same governance rule as authorized_commands: Attempt 2 must present the
    # identical ordered set Attempt 1 was approved for. A REQUEST_CHANGES
    # finding is never grounds to widen filesystem scope -- it can only ask
    # Claude to fix code within the paths, commands, and side-effect roots
    # already approved. The approved roots are Owner-authored directory
    # prefixes (not sensitive), so the persisted receipt records them
    # verbatim and this compares the ordered tuple directly, which detects
    # addition, removal, substitution, and reordering alike.
    try:
        allowed_side_effect_paths = normalize_allowed_side_effect_paths(args.allowed_side_effect_path)
    except AllowedSideEffectPathError as exc:
        raise PreflightFailure(exc.category, exc.message) from None

    if "allowed_side_effect_paths" not in original_receipt:
        # Evidence predating this schema cannot prove what filesystem scope
        # Attempt 1 held, so the only safe inheritance is "none".
        if allowed_side_effect_paths:
            raise IntegrityFailure(
                "SIDE_EFFECT_PATH_EVIDENCE_INVALID",
                "previous implementation evidence has no allowed_side_effect_paths to inherit from "
                "(predates this schema or is malformed); a repair cannot supply "
                "--allowed-side-effect-path in this case",
            )
    else:
        original_side_effect_paths = original_receipt["allowed_side_effect_paths"]
        if not isinstance(original_side_effect_paths, list) or list(allowed_side_effect_paths) != [
            str(entry) for entry in original_side_effect_paths
        ]:
            raise IntegrityFailure(
                "SIDE_EFFECT_PATH_CONTRACT_MISMATCH",
                "repair --allowed-side-effect-path set does not exactly match the original "
                "implementation's authorized side-effect paths (addition, removal, substitution, or "
                "reorder is never permitted)",
            )

    repair_run_id = args.repair_run_id or str(uuid.uuid4())
    if not repair_run_id or "/" in repair_run_id or "\\" in repair_run_id or repair_run_id in (".", ".."):
        raise PreflightFailure("INVALID_REPAIR_RUN_ID", "repair run id must be a simple non-empty identifier")

    evidence_directory = resolve_repair_evidence_directory(args.evidence_directory, repair_run_id, workspace)

    return {
        "workspace": workspace,
        "original_task_prompt": original_task_prompt,
        "previous_run_directory": previous_run_directory,
        "previous_evidence": previous_evidence,
        "review_directory": review_directory,
        "review_evidence": review_evidence,
        "baseline_commit": baseline_commit,
        "allowed_paths": allowed_paths,
        "finding_ids": finding_ids,
        "fingerprint": fingerprint,
        "repair_run_id": repair_run_id,
        "evidence_directory": evidence_directory,
        "authorized_commands": authorized_commands,
        "allowed_side_effect_paths": allowed_side_effect_paths,
    }


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
        return 2
    except IntegrityFailure as exc:
        if provider_observer is not None:
            provider_observer(False)
        print(f"STATUS=INTEGRITY_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3
    except Exception as exc:  # noqa: BLE001
        if provider_observer is not None:
            provider_observer(False)
        print(f"STATUS=INTERNAL_ERROR:{exc.__class__.__name__}")
        return 3

    workspace: Path = pre["workspace"]
    previous_evidence: dict[str, Any] = pre["previous_evidence"]
    review_evidence: dict[str, Any] = pre["review_evidence"]
    allowed_paths: tuple[str, ...] = pre["allowed_paths"]
    finding_ids: tuple[str, ...] = pre["finding_ids"]
    fingerprint = pre["fingerprint"]
    repair_run_id: str = pre["repair_run_id"]
    evidence_directory: Path = pre["evidence_directory"]
    authorized_commands: tuple[tuple[str, ...], ...] = pre["authorized_commands"]
    allowed_side_effect_paths: tuple[str, ...] = pre["allowed_side_effect_paths"]

    previous_run_id = previous_evidence["receipt"].get("run_id")
    review_run_id = review_evidence["receipt"].get("review_run_id")
    check_commands = list(args.check_command)

    previous_impl_hashes_before = _hash_evidence_files(pre["previous_run_directory"], REPAIR_EVIDENCE_FILES)
    review_hashes_before = _hash_evidence_files(pre["review_directory"], REVIEW_EVIDENCE_FILES)

    prompt_text = build_repair_prompt(
        original_task_prompt=pre["original_task_prompt"],
        baseline_commit=pre["baseline_commit"],
        allowed_paths=allowed_paths,
        previous_receipt=previous_evidence["receipt"],
        previous_patch_text=previous_evidence["patch_text"],
        review_result=review_evidence["result"],
        review_checks=review_evidence["checks"],
        finding_ids=finding_ids,
        repair_check_commands=check_commands,
    )

    request = ClaudeCodeCLIRequest(
        task_id=f"repair-{repair_run_id}",
        run_id=repair_run_id,
        attempt=REPAIR_ATTEMPT_NUMBER,
        goal="Owner-directed bounded Codex-finding repair (Attempt 2, final)",
        prompt=prompt_text,
        working_directory=workspace,
        timeout_seconds=args.timeout_seconds,
        allowed_changed_paths=allowed_paths,
        model=args.model,
        permission_mode=args.permission_mode,
        authorized_commands=authorized_commands,
        allowed_side_effect_paths=allowed_side_effect_paths,
    )

    before_contents = snapshot_workspace_contents(workspace)
    try:
        result = ClaudeCodeCLIAdapter().run(request, executable_path=args.claude_executable)
    except Exception as exc:  # noqa: BLE001
        if provider_observer is not None:
            provider_observer(None)
        print(f"STATUS=INTERNAL_ERROR:{exc.__class__.__name__}")
        return 3
    if provider_observer is not None:
        provider_observer(result.provider_invoked)
    after_contents = snapshot_workspace_contents(workspace)

    try:
        check_results = [
            run_review.run_check_command(c, workspace, args.timeout_seconds)
            for c in check_commands
        ]
    except run_review.PreflightFailure as exc:
        print(f"STATUS=CHECK_EXECUTION_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    try:
        finalized = write_evidence(
            result, before_contents=before_contents, after_contents=after_contents, output_dir=evidence_directory
        )
        adapter_patch = Path(finalized.patch_path or "")
        adapter_manifest = Path(finalized.evidence_manifest_path or "")
        patch_path = evidence_directory / "diff.patch"
        manifest_path = evidence_directory / "result.json"
        adapter_patch.replace(patch_path)
        adapter_manifest.replace(manifest_path)
        finalized = dataclasses.replace(finalized, patch_path=str(patch_path), evidence_manifest_path=str(manifest_path))
        manifest_path.write_text(render_evidence_manifest_json(finalized), encoding="utf-8")

        (evidence_directory / "repair-prompt.txt").write_text(prompt_text, encoding="utf-8")
        (evidence_directory / "checks.json").write_text(
            json.dumps([c.to_dict() for c in check_results], indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        previous_impl_hashes_after = _hash_evidence_files(pre["previous_run_directory"], REPAIR_EVIDENCE_FILES)
        review_hashes_after = _hash_evidence_files(pre["review_directory"], REVIEW_EVIDENCE_FILES)
        previous_evidence_unchanged = (
            previous_impl_hashes_before == previous_impl_hashes_after and review_hashes_before == review_hashes_after
        )

        repair_context = {
            "repair_run_id": repair_run_id,
            "attempt": REPAIR_ATTEMPT_NUMBER,
            "original_task_prompt_hash": run_review._sha256(pre["original_task_prompt"].strip().encode("utf-8")),
            "previous_implementation_run_id": previous_run_id,
            "previous_implementation_evidence_hashes": previous_impl_hashes_before,
            "review_run_id": review_run_id,
            "review_evidence_hashes": review_hashes_before,
            "baseline_commit": pre["baseline_commit"],
            "pre_repair_workspace_fingerprint": fingerprint.to_dict(),
            "allowed_paths": list(allowed_paths),
            "finding_ids": list(finding_ids),
            "authorized_command_fingerprints": [
                compute_authorized_command_fingerprint(argv) for argv in authorized_commands
            ],
            "authorized_command_set_fingerprint": (
                compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
            ),
            "authorized_command_count": len(authorized_commands),
        }
        (evidence_directory / "repair-context.json").write_text(
            json.dumps(repair_context, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"STATUS=INTERNAL_ERROR:{exc.__class__.__name__}")
        return 3

    allowed_set = set(allowed_paths)
    checks_passed = all(c.exit_code == 0 and not c.timed_out for c in check_results)
    scope_preserved = set(finalized.changed_paths).issubset(allowed_set) and not finalized.unexpected_changed_paths
    claude_succeeded = finalized.status.value == "SUCCEEDED" and finalized.success

    success = bool(claude_succeeded and scope_preserved and checks_passed and previous_evidence_unchanged)

    error_category: str | None = None
    error_message: str | None = None
    if not claude_succeeded:
        error_category = finalized.error_category or "REPAIR_ATTEMPT_NOT_SUCCESSFUL"
        error_message = finalized.error_message or "the repair invocation did not succeed"
    elif not scope_preserved:
        error_category = "UNEXPECTED_CHANGED_PATH"
        error_message = f"unexpected changed paths: {list(finalized.unexpected_changed_paths)}"
    elif not checks_passed:
        error_category = "CHECK_COMMAND_FAILED"
        error_message = "one or more required deterministic checks failed"
    elif not previous_evidence_unchanged:
        error_category = "PREVIOUS_EVIDENCE_MUTATED"
        error_message = "previous implementation or review evidence changed during this repair run"

    receipt = {
        "run_id": repair_run_id,
        "repair_run_id": repair_run_id,
        "attempt": REPAIR_ATTEMPT_NUMBER,
        "workspace": str(workspace),
        "status": "REPAIR_ATTEMPT_COMPLETED",
        "success": success,
        "exit_code": finalized.exit_code,
        "timed_out": finalized.timed_out,
        "provider_invoked": finalized.provider_invoked,
        "duration_ms": finalized.duration_ms,
        "changed_paths": list(finalized.changed_paths),
        "unexpected_changed_paths": list(finalized.unexpected_changed_paths),
        "allowed_changed_paths": list(allowed_paths),
        "previous_implementation_run_id": previous_run_id,
        "review_run_id": review_run_id,
        "finding_ids_received": list(finding_ids),
        "check_results": [c.to_dict() for c in check_results],
        "workspace_bound_to_reviewed_state": True,
        "previous_evidence_unchanged": previous_evidence_unchanged,
        "authorized_command_fingerprints": [
            compute_authorized_command_fingerprint(argv) for argv in authorized_commands
        ],
        "authorized_command_set_fingerprint": (
            compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
        ),
        "authorized_command_count": len(authorized_commands),
        "authorized_command_cli_rules": list(finalized.authorized_command_cli_rules),
        # Inherited unchanged from Attempt 1 (verified in preflight above).
        "allowed_side_effect_paths": list(finalized.allowed_side_effect_paths),
        "allowed_side_effect_path_count": len(finalized.allowed_side_effect_paths),
        "observed_authorized_side_effect_count": finalized.side_effect_changed_path_count,
        "observed_authorized_side_effect_counts_by_root": {
            root: count for root, count in finalized.side_effect_changed_path_counts_by_root
        },
        "observed_authorized_side_effect_sample": list(finalized.side_effect_changed_paths_sample),
        "error_category": error_category,
        "error_message": error_message,
        "evidence_paths": {
            "result": str(evidence_directory / "result.json"),
            "diff_patch": str(evidence_directory / "diff.patch"),
            "receipt": str(evidence_directory / "receipt.json"),
            "repair_prompt": str(evidence_directory / "repair-prompt.txt"),
            "checks": str(evidence_directory / "checks.json"),
            "repair_context": str(evidence_directory / "repair-context.json"),
        },
        "recorded_at": _utc_now_iso(),
    }
    (evidence_directory / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    _print_summary(
        repair_run_id=repair_run_id,
        status="REPAIR_ATTEMPT_COMPLETED",
        success=success,
        exit_code=finalized.exit_code,
        timed_out=finalized.timed_out,
        duration_ms=finalized.duration_ms,
        previous_implementation_run_id=previous_run_id,
        review_run_id=review_run_id,
        finding_ids=finding_ids,
        changed_paths=finalized.changed_paths,
        unexpected_changed_paths=finalized.unexpected_changed_paths,
        checks_passed=checks_passed,
        workspace_bound=True,
        previous_evidence_unchanged=previous_evidence_unchanged,
        evidence_directory=evidence_directory,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
