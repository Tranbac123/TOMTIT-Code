#!/usr/bin/env python3
"""Local-only, one-shot manual dogfood implement-review-repair-review orchestrator.

Composes the three existing dogfood runners into one bounded state machine:

    Original task
    -> Claude Code implementation Attempt 1   (run_task.py)
    -> Codex independent review Attempt 1     (run_review.py)
    -> APPROVE: terminal success
    -> REQUEST_CHANGES: one bounded Claude repair Attempt 2 (run_repair.py),
       only if --allow-one-repair was given
    -> Codex independent review Attempt 2     (run_review.py)
    -> APPROVE, BLOCKED, or bounded stop

This is a local dogfood orchestrator, not the TOMTIT product Runtime: it does
not integrate with `src/tomtit/runtime`, does not persist anywhere but local
evidence files, does not loop unboundedly, and never claims acceptance except
on a validated, independent Codex APPROVE verdict. Each child runner keeps
full ownership of its own evidence; this tool never reconstructs or edits a
child's result -- it only reads back what each child already wrote.

Owner rerun example:

    python3 .tomtit/dogfood/run_loop.py \\
      --workspace /absolute/path/to/target-repository \\
      --task-prompt-file .tomtit/dogfood/tasks/task.md \\
      --baseline-ref HEAD \\
      --allowed-path app/page.tsx \\
      --check-command "npm run lint" \\
      --check-command "npm run build" \\
      --implementation-timeout-seconds 900 \\
      --review-timeout-seconds 900 \\
      --allow-one-repair
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    AllowedSideEffectPathError,
    AuthorizedCommandError,
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    classify_workspace_changes,
    discover_claude_executable,
    normalize_allowed_side_effect_paths,
    normalize_authorized_commands,
    render_authorized_command_display,
    snapshot_workspace_hashes,
    summarize_side_effect_paths,
    workspace_hash,
)

import run_repair  # noqa: E402
import run_review  # noqa: E402
import run_task  # noqa: E402

MAX_IMPLEMENTATION_ATTEMPTS = 2
MAX_REVIEW_ATTEMPTS = 2
MAX_REPAIR_ROUNDS = 1

# ---------------------------------------------------------------------------
# Explicit deterministic state machine.
# ---------------------------------------------------------------------------

STATES = (
    "CREATED",
    "PREFLIGHT",
    "IMPLEMENTING_ATTEMPT_1",
    "REVIEWING_ATTEMPT_1",
    "REPAIRING_ATTEMPT_2",
    "REVIEWING_ATTEMPT_2",
    "COMPLETED",
    "BLOCKED",
    "FAILED",
    "STOPPED",
)
TERMINAL_STATES = frozenset({"COMPLETED", "BLOCKED", "FAILED", "STOPPED"})
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"PREFLIGHT"}),
    "PREFLIGHT": frozenset({"IMPLEMENTING_ATTEMPT_1", "BLOCKED"}),
    "IMPLEMENTING_ATTEMPT_1": frozenset({"REVIEWING_ATTEMPT_1", "FAILED", "BLOCKED"}),
    "REVIEWING_ATTEMPT_1": frozenset({"COMPLETED", "REPAIRING_ATTEMPT_2", "STOPPED", "BLOCKED", "FAILED"}),
    "REPAIRING_ATTEMPT_2": frozenset({"REVIEWING_ATTEMPT_2", "FAILED", "BLOCKED"}),
    "REVIEWING_ATTEMPT_2": frozenset({"COMPLETED", "STOPPED", "BLOCKED", "FAILED"}),
}


class InvalidTransition(Exception):
    """A requested state transition is not in VALID_TRANSITIONS. Never expected in
    normal operation -- this is a defensive guard against an orchestrator bug,
    not a condition any input can trigger."""


class ConfigFailure(Exception):
    """A failure discovered before the state machine can be safely entered:
    malformed arguments, a workspace that cannot even be located, or a loop
    evidence collision that would make event logging unsafe. Maps to exit 4.
    No loop evidence is written for this category.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class PreflightBlocked(Exception):
    """A deeper precondition failed once the loop is already tracked (state
    machine entered, task-contract.json and events.jsonl already exist).
    Drives the PREFLIGHT -> BLOCKED transition. Maps to exit 2.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class OrchestratorIntegrityFailure(Exception):
    """The orchestrator's own bookkeeping broke down: an evidence-immutability
    violation, an invalid transition attempt, or an unexpected internal error.
    Distinct from a legitimate child-attempt failure (which maps to exit 3);
    this maps to exit 5.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


# ---------------------------------------------------------------------------
# Small local helpers.
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        evidence_snapshot = run_review.read_canonical_evidence(
            path.parent,
            (path.name,),
        )
    except run_review.EvidenceIntegrityFailure:
        return None
    try:
        return json.loads(evidence_snapshot.file_bytes[path.name].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def hash_directory(path: Path) -> dict[str, str]:
    """An exact lstat-based identity for an entire canonical evidence tree."""

    return run_review.snapshot_evidence_tree(path)


def _derive_final_workspace_evidence(
    *,
    workspace: Path,
    baseline_commit: str,
    baseline_hashes: dict[str, str],
    allowed_source_paths: tuple[str, ...],
    allowed_side_effect_paths: tuple[str, ...],
    baseline_git_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Measure the actual terminal workspace, independent of child receipts."""

    final_hashes = snapshot_workspace_hashes(workspace)
    snapshot_observation = run_review.classify_review_workspace_preservation(
        baseline_hashes,
        final_hashes,
        allowed_source_paths=allowed_source_paths,
        allowed_side_effect_paths=allowed_side_effect_paths,
        workspace=workspace,
    )
    git_visible_paths = tuple(run_review.git_changed_paths(workspace, baseline_commit))
    git_source, git_side_effects, git_unexpected = classify_workspace_changes(
        git_visible_paths,
        allowed_source_paths,
        allowed_side_effect_paths,
        workspace_root=workspace,
    )
    source = tuple(sorted(set(snapshot_observation.source_changed_paths) | set(git_source)))
    unexpected = tuple(
        sorted(set(snapshot_observation.unexpected_changed_paths) | set(git_unexpected))
    )
    side_effects = tuple(
        sorted(
            (set(snapshot_observation.authorized_side_effect_changed_paths) | set(git_side_effects))
            - set(source)
            - set(unexpected)
        )
    )
    side_effect_counts, side_effect_sample = summarize_side_effect_paths(
        side_effects,
        allowed_side_effect_paths,
    )
    try:
        final_git_metadata = run_review.protected_git_metadata_snapshot(
            workspace, trusted_baseline=baseline_git_metadata
        )
    except run_review.EvidenceIntegrityFailure:
        final_git_metadata = {}
    git_metadata_changed_paths = (
        run_review.diff_workspace_hashes(baseline_git_metadata, final_git_metadata)
        if baseline_git_metadata is not None
        else ()
    )
    git_metadata_preserved = (
        bool(final_git_metadata)
        and baseline_git_metadata is not None
        and not git_metadata_changed_paths
    )
    final_git_entry_hash = (
        run_review.workspace_git_entry_hash_from_snapshot(final_git_metadata)
        if final_git_metadata
        else None
    )
    baseline_git_entry_hash = (
        run_review.workspace_git_entry_hash_from_snapshot(baseline_git_metadata)
        if baseline_git_metadata is not None
        else None
    )
    return {
        "workspace_final_hash": workspace_hash(final_hashes),
        "workspace_final_changed_paths": list(sorted((*source, *unexpected))),
        "workspace_final_source_changed_paths": list(source),
        "workspace_final_unexpected_changed_paths": list(unexpected),
        "workspace_final_authorized_side_effect_changed_path_count": len(side_effects),
        "workspace_final_authorized_side_effect_changed_counts_by_root": {
            root: count for root, count in side_effect_counts
        },
        "workspace_final_authorized_side_effect_changed_paths_sample": list(
            side_effect_sample
        ),
        "protected_git_metadata_final_hash": (
            workspace_hash(final_git_metadata) if final_git_metadata else None
        ),
        "protected_git_metadata_baseline_hash": (
            workspace_hash(baseline_git_metadata)
            if baseline_git_metadata is not None
            else None
        ),
        "protected_git_metadata_preserved": git_metadata_preserved,
        "workspace_git_entry_final_hash": final_git_entry_hash,
        "workspace_git_entry_baseline_hash": baseline_git_entry_hash,
        "workspace_git_entry_preserved": (
            final_git_entry_hash is not None
            and final_git_entry_hash == baseline_git_entry_hash
        ),
        "protected_git_metadata_changed_path_count": len(
            git_metadata_changed_paths
        ),
        "protected_git_metadata_changed_paths_sample": list(
            git_metadata_changed_paths[:20]
        ),
    }


# ---------------------------------------------------------------------------
# Append-only event log.
#
# Each event is written as one JSON line, opened in append mode and flushed +
# fsynced immediately after every write. This is "atomic append" in the sense
# that matters for a single local single-writer process: POSIX guarantees a
# single write() to a file opened O_APPEND is not interleaved with other
# writes to the same file, and this process is the only writer. It is not a
# claim of crash-safe multi-writer atomicity, which this local tool does not
# need.
# ---------------------------------------------------------------------------


class EventLog:
    def __init__(self, path: Path, loop_run_id: str) -> None:
        self._path = path
        self._loop_run_id = loop_run_id
        self._sequence = 0

    def append(
        self,
        *,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        attempt: int | None = None,
        child_run_id: str | None = None,
        evidence_path: str | None = None,
        provider_invoked: bool | None = None,
        summary: str = "",
    ) -> None:
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "timestamp": _utc_now_iso(),
            "loop_run_id": self._loop_run_id,
            "event_type": event_type,
            "from_state": from_state,
            "to_state": to_state,
            "attempt": attempt,
            "child_run_id": child_run_id,
            "evidence_path": evidence_path,
            "provider_invoked": provider_invoked,
            "summary": summary,
        }
        line = json.dumps(event, sort_keys=True) + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            import os as _os

            _os.fsync(handle.fileno())

    @property
    def sequence(self) -> int:
        return self._sequence


def _provider_invocation_observation(
    child_outcome: dict[str, Any],
) -> bool | None:
    """Return TRUE/FALSE only from consistent explicit provider-boundary evidence."""

    observations: list[bool] = []
    direct = child_outcome.get("provider_invoked_observation")
    if isinstance(direct, bool):
        observations.append(direct)
    receipt = child_outcome.get("receipt")
    result = child_outcome.get("result")
    if isinstance(receipt, dict):
        receipt_provider = receipt.get("provider_invoked")
        if isinstance(receipt_provider, bool):
            observations.append(receipt_provider)
        invocation_count = receipt.get("invocation_count")
        if invocation_count is not None and (
            not isinstance(invocation_count, int)
            or isinstance(invocation_count, bool)
            or invocation_count not in (0, 1)
            or (isinstance(receipt_provider, bool) and bool(invocation_count) != receipt_provider)
        ):
            return None
    if isinstance(result, dict):
        result_provider = result.get("provider_invoked")
        if isinstance(result_provider, bool):
            observations.append(result_provider)
    if not observations or any(value != observations[0] for value in observations[1:]):
        return None
    return observations[0]


def _provider_process_summary(child_outcome: dict[str, Any]) -> str:
    receipt = child_outcome.get("receipt")
    result = child_outcome.get("result")
    if isinstance(receipt, dict) and "process_exit_code" in receipt:
        exit_code = receipt.get("process_exit_code")
        timed_out = receipt.get("timed_out")
    elif isinstance(result, dict):
        exit_code = result.get("exit_code")
        timed_out = result.get("timed_out")
    else:
        exit_code = None
        timed_out = None
    return (
        f"provider_exit_code={exit_code}; timed_out={timed_out}; "
        f"runner_exit_code={child_outcome['exit_code']}"
    )


def _record_completed_child_runner(
    *,
    event_log: EventLog,
    state: str,
    attempt: int,
    child_outcome: dict[str, Any],
) -> tuple[int, bool | None]:
    """Record provider events only after child evidence proves the boundary was reached."""

    child_id = str(child_outcome["child_id"])
    provider_invoked = _provider_invocation_observation(child_outcome)
    if provider_invoked is True:
        event_log.append(
            event_type="MODEL_INVOCATION_START",
            from_state=state,
            to_state=state,
            attempt=attempt,
            child_run_id=child_id,
            provider_invoked=True,
            summary=(
                "retrospective provider-process start attestation from child evidence; "
                "event timestamp is observation time"
            ),
        )
        event_log.append(
            event_type="MODEL_INVOCATION_COMPLETE",
            from_state=state,
            to_state=state,
            attempt=attempt,
            child_run_id=child_id,
            evidence_path=str(child_outcome["run_directory"]),
            provider_invoked=True,
            summary=_provider_process_summary(child_outcome),
        )
    event_log.append(
        event_type="RUNNER_INVOCATION_COMPLETE",
        from_state=state,
        to_state=state,
        attempt=attempt,
        child_run_id=child_id,
        evidence_path=str(child_outcome["run_directory"]),
        provider_invoked=provider_invoked,
        summary=f"exit_code={child_outcome['exit_code']}",
    )
    return (1 if provider_invoked is True else 0, provider_invoked)


class StateMachine:
    def __init__(self, event_log: EventLog) -> None:
        self._state = "CREATED"
        self._event_log = event_log

    @property
    def state(self) -> str:
        return self._state

    def transition(self, to_state: str, *, event_type: str, **event_kwargs: Any) -> None:
        allowed = VALID_TRANSITIONS.get(self._state, frozenset())
        if to_state not in allowed:
            raise InvalidTransition(f"{self._state} -> {to_state} is not a permitted transition")
        from_state = self._state
        self._state = to_state
        self._event_log.append(event_type=event_type, from_state=from_state, to_state=to_state, **event_kwargs)


# ---------------------------------------------------------------------------
# Task contract.
# ---------------------------------------------------------------------------


def build_task_contract(
    *,
    loop_run_id: str,
    workspace: Path,
    task_prompt_path: Path,
    task_prompt_text: str,
    baseline_ref: str,
    baseline_commit: str,
    allowed_paths: tuple[str, ...],
    check_commands: list[str],
    authorized_commands: tuple[tuple[str, ...], ...],
    allowed_side_effect_paths: tuple[str, ...],
    implementation_timeout_seconds: int,
    review_timeout_seconds: int,
    implementer_model: str | None,
    reviewer_model: str | None,
    permission_mode: str,
    repair_authorized: bool,
) -> dict[str, Any]:
    return {
        "loop_run_id": loop_run_id,
        "workspace": str(workspace),
        "task_prompt_path": str(task_prompt_path),
        "task_prompt_sha256": run_review._sha256(task_prompt_text.strip().encode("utf-8")),
        "baseline_ref": baseline_ref,
        "baseline_commit": baseline_commit,
        "allowed_paths": list(allowed_paths),
        "check_commands": list(check_commands),
        # Fingerprints only -- never the raw authorized-command argv (Codex
        # corrective review, HIGH finding 2). The in-memory tuple bound once
        # in main() is what actually propagates to both attempts; this
        # contract only records proof of what that immutable set was.
        "authorized_command_fingerprints": [
            compute_authorized_command_fingerprint(argv) for argv in authorized_commands
        ],
        "authorized_command_set_fingerprint": (
            compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
        ),
        "authorized_command_count": len(authorized_commands),
        # The Owner-approved generated/side-effect roots. Recorded verbatim
        # (they are directory prefixes the Owner typed, not sensitive) so
        # both attempts and any audit can verify the exact inherited set.
        "allowed_side_effect_paths": list(allowed_side_effect_paths),
        "allowed_side_effect_path_count": len(allowed_side_effect_paths),
        "implementation_timeout_seconds": implementation_timeout_seconds,
        "review_timeout_seconds": review_timeout_seconds,
        "implementer_model": implementer_model,
        "reviewer_model": reviewer_model,
        "permission_mode": permission_mode,
        "max_implementation_attempts": MAX_IMPLEMENTATION_ATTEMPTS,
        "max_review_attempts": MAX_REVIEW_ATTEMPTS,
        "max_repair_rounds": MAX_REPAIR_ROUNDS,
        "repair_authorized": repair_authorized,
        "created_at": _utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# Child invocation. Each child runner keeps full ownership of its own
# evidence; these helpers only build its argv, call its existing `main()`,
# and read back what it wrote. Nothing is reconstructed or edited.
# ---------------------------------------------------------------------------


def invoke_implementation(
    *,
    child_id: str,
    workspace: Path,
    task_prompt_path: Path,
    allowed_paths: tuple[str, ...],
    authorized_commands: tuple[tuple[str, ...], ...] = (),
    allowed_side_effect_paths: tuple[str, ...] = (),
    timeout_seconds: int,
    model: str | None,
    permission_mode: str,
    claude_executable: str | None,
    children_root: Path,
) -> dict[str, Any]:
    run_dir = children_root / child_id
    argv = [
        "--workspace",
        str(workspace),
        "--prompt-file",
        str(task_prompt_path),
        "--timeout-seconds",
        str(timeout_seconds),
        "--run-id",
        child_id,
        "--task-id",
        child_id,
        "--permission-mode",
        permission_mode,
        "--evidence-directory",
        str(run_dir),
    ]
    for path in allowed_paths:
        argv.extend(["--allowed-path", path])
    for argv_command in authorized_commands:
        argv.extend(["--authorized-command", render_authorized_command_display(argv_command)])
    for side_effect_path in allowed_side_effect_paths:
        argv.extend(["--allowed-side-effect-path", side_effect_path])
    if model:
        argv.extend(["--model", model])
    if claude_executable:
        argv.extend(["--executable", claude_executable])
    provider_observations: list[bool | None] = []
    exit_code = run_task.main(
        argv,
        provider_observer=provider_observations.append,
    )
    return {
        "child_id": child_id,
        "run_directory": run_dir,
        "exit_code": exit_code,
        "receipt": _read_json(run_dir / "receipt.json"),
        "result": _read_json(run_dir / "result.json"),
        "provider_invoked_observation": (
            provider_observations[-1] if provider_observations else None
        ),
    }


def invoke_review(
    *,
    child_id: str,
    workspace: Path,
    task_prompt_path: Path,
    implementation_run_directory: Path,
    implementation_run_id: str,
    implementation_attempt: int,
    baseline_ref: str,
    allowed_paths: tuple[str, ...],
    check_commands: list[str],
    timeout_seconds: int,
    model: str | None,
    codex_executable: str | None,
    children_root: Path,
) -> dict[str, Any]:
    # run_review.py's own --evidence-directory convention is "parent, with the
    # review-run-id appended under it" (unlike run_task.py/run_repair.py,
    # which treat the override as the final directory). children_root is
    # passed as-is here so the resulting layout still matches every other
    # child: children_root/<child_id>/.
    argv = [
        "--workspace",
        str(workspace),
        "--task-prompt-file",
        str(task_prompt_path),
        "--implementation-run-directory",
        str(implementation_run_directory),
        "--baseline-ref",
        baseline_ref,
        "--timeout-seconds",
        str(timeout_seconds),
        "--review-run-id",
        child_id,
        "--evidence-directory",
        str(children_root),
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
    ]
    for path in allowed_paths:
        argv.extend(["--allowed-path", path])
    for command in check_commands:
        argv.extend(["--check-command", command])
    if model:
        argv.extend(["--model", model])
    if codex_executable:
        argv.extend(["--codex-executable", codex_executable])
    provider_observations: list[bool | None] = []
    exit_code = run_review.main(
        argv,
        provider_observer=provider_observations.append,
    )
    run_dir = children_root / child_id
    return {
        "child_id": child_id,
        "run_directory": run_dir,
        "exit_code": exit_code,
        "receipt": _read_json(run_dir / "review-receipt.json"),
        "result": _read_json(run_dir / "review-result.json"),
        "provider_invoked_observation": (
            provider_observations[-1] if provider_observations else None
        ),
    }


def invoke_repair(
    *,
    child_id: str,
    workspace: Path,
    task_prompt_path: Path,
    previous_implementation_run_directory: Path,
    implementation_run_id: str,
    implementation_attempt: int,
    review_run_directory: Path,
    allowed_paths: tuple[str, ...],
    check_commands: list[str],
    authorized_commands: tuple[tuple[str, ...], ...] = (),
    allowed_side_effect_paths: tuple[str, ...] = (),
    timeout_seconds: int,
    model: str | None,
    permission_mode: str,
    claude_executable: str | None,
    children_root: Path,
) -> dict[str, Any]:
    run_dir = children_root / child_id
    argv = [
        "--workspace",
        str(workspace),
        "--original-task-prompt-file",
        str(task_prompt_path),
        "--previous-implementation-run-directory",
        str(previous_implementation_run_directory),
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
        "--review-run-directory",
        str(review_run_directory),
        "--timeout-seconds",
        str(timeout_seconds),
        "--repair-run-id",
        child_id,
        "--permission-mode",
        permission_mode,
        "--evidence-directory",
        str(run_dir),
    ]
    for path in allowed_paths:
        argv.extend(["--allowed-path", path])
    for command in check_commands:
        argv.extend(["--check-command", command])
    for argv_command in authorized_commands:
        argv.extend(["--authorized-command", render_authorized_command_display(argv_command)])
    for side_effect_path in allowed_side_effect_paths:
        argv.extend(["--allowed-side-effect-path", side_effect_path])
    if model:
        argv.extend(["--model", model])
    if claude_executable:
        argv.extend(["--claude-executable", claude_executable])
    provider_observations: list[bool | None] = []
    exit_code = run_repair.main(
        argv,
        provider_observer=provider_observations.append,
    )
    return {
        "child_id": child_id,
        "run_directory": run_dir,
        "exit_code": exit_code,
        "receipt": _read_json(run_dir / "receipt.json"),
        "result": _read_json(run_dir / "result.json"),
        "provider_invoked_observation": (
            provider_observations[-1] if provider_observations else None
        ),
    }


# ---------------------------------------------------------------------------
# Review-verdict interpretation. Re-validated independently at this layer too
# (schema + logic), matching the project-wide "revalidate canonical data at
# trust boundaries" discipline -- never trust a child's exit code alone.
# ---------------------------------------------------------------------------


def extract_validated_verdict(review_outcome: dict[str, Any]) -> dict[str, Any] | None:
    result = review_outcome.get("result")
    if not isinstance(result, dict):
        return None
    try:
        validated = run_review.validate_verdict_schema(result)
        run_review.enforce_verdict_rules(validated)
    except run_review.ReviewFailure:
        return None
    return validated


# ---------------------------------------------------------------------------
# Usage/cost aggregation. Never invents a zero -- missing usage stays null.
# ---------------------------------------------------------------------------


def extract_usage_from_implementation_result(result: dict[str, Any] | None) -> dict[str, float | int | None]:
    unavailable: dict[str, float | int | None] = {"cost_usd": None, "input_tokens": None, "output_tokens": None}
    if not isinstance(result, dict):
        return unavailable

    # Provider-output persistence boundary (M-1 / M-2): when exact-command
    # authorization is active, the adapter withholds provider-controlled
    # text entirely and `stdout_excerpt` carries only a fixed placeholder,
    # so there is no JSON left here to parse. The same numbers are instead
    # published as typed, persist-safe scalar fields on the manifest --
    # prefer those whenever they are present. Runs without authorized
    # commands keep the legacy excerpt-parsing path below unchanged.
    if result.get("provider_output_withheld") is True:
        def _number_or_none(key: str) -> float | int | None:
            value = result.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return value

        return {
            "cost_usd": _number_or_none("provider_total_cost_usd"),
            "input_tokens": _number_or_none("provider_input_tokens"),
            "output_tokens": _number_or_none("provider_output_tokens"),
        }

    excerpt = result.get("stdout_excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip():
        return unavailable
    try:
        parsed = json.loads(excerpt.strip())
    except json.JSONDecodeError:
        return unavailable
    if not isinstance(parsed, dict):
        return unavailable
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    return {
        "cost_usd": parsed.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def aggregate_usage(usages: list[dict[str, float | int | None]]) -> dict[str, float | int | None]:
    def _sum_or_none(key: str) -> float | int | None:
        values = [u[key] for u in usages if u.get(key) is not None]
        if not values:
            return None
        return sum(values)

    return {
        "total_model_cost_usd": _sum_or_none("cost_usd"),
        "total_input_tokens": _sum_or_none("input_tokens"),
        "total_output_tokens": _sum_or_none("output_tokens"),
    }


# ---------------------------------------------------------------------------
# CLI argument parsing.
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded local implement-review-repair-review loop.")
    parser.add_argument("--workspace", required=True, metavar="PATH")
    parser.add_argument("--task-prompt-file", required=True, metavar="PATH")
    parser.add_argument("--baseline-ref", required=True, metavar="REF")
    parser.add_argument("--allowed-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--check-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--authorized-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--allowed-side-effect-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--implementation-timeout-seconds", type=int, required=True, metavar="INT")
    parser.add_argument("--review-timeout-seconds", type=int, required=True, metavar="INT")
    parser.add_argument("--claude-executable", metavar="PATH")
    parser.add_argument("--codex-executable", metavar="PATH")
    parser.add_argument("--implementer-model", metavar="TEXT")
    parser.add_argument("--reviewer-model", metavar="TEXT")
    parser.add_argument("--permission-mode", default="acceptEdits", metavar="TEXT")
    parser.add_argument("--allow-one-repair", action="store_true")
    parser.add_argument("--loop-run-id", metavar="TEXT")
    parser.add_argument("--evidence-directory", metavar="PATH")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main orchestration.
# ---------------------------------------------------------------------------


def _config_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Phase A: checks that must pass before the state machine (and therefore
    event logging) can safely begin. No loop evidence exists yet for these.
    """

    if args.implementation_timeout_seconds <= 0 or args.review_timeout_seconds <= 0:
        raise ConfigFailure("INVALID_TIMEOUT", "timeouts must be positive")

    try:
        workspace = run_review._validate_workspace(args.workspace)
    except run_review.PreflightFailure as exc:
        raise ConfigFailure(exc.category, exc.message) from None

    task_prompt_path = Path(args.task_prompt_file).expanduser()
    if not task_prompt_path.is_absolute():
        task_prompt_path = (Path.cwd() / task_prompt_path).resolve(strict=False)
    try:
        task_prompt_text = task_prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigFailure("MISSING_TASK_PROMPT", f"task prompt file could not be read: {exc.__class__.__name__}") from None
    if not task_prompt_text.strip():
        raise ConfigFailure("EMPTY_TASK_PROMPT", "task prompt must not be empty")

    loop_run_id = args.loop_run_id or str(uuid.uuid4())
    if not loop_run_id or "/" in loop_run_id or "\\" in loop_run_id or loop_run_id in (".", ".."):
        raise ConfigFailure("INVALID_LOOP_RUN_ID", "loop run id must be a simple non-empty identifier")

    raw_root = Path(args.evidence_directory).expanduser() if args.evidence_directory else REPO_ROOT / ".tomtit" / "dogfood" / "loops"
    if not raw_root.is_absolute():
        raw_root = (Path.cwd() / raw_root).resolve(strict=False)
    else:
        raw_root = raw_root.resolve(strict=False)
    loop_dir = raw_root / loop_run_id
    if run_repair._is_within(loop_dir, workspace):
        raise ConfigFailure("EVIDENCE_DIRECTORY_INSIDE_WORKSPACE", "loop evidence directory must be outside the target workspace")
    if loop_dir.exists() and any(loop_dir.iterdir()):
        raise ConfigFailure("LOOP_EVIDENCE_COLLISION", "loop evidence directory already exists and is non-empty")

    for command in args.check_command:
        try:
            run_review.validate_check_command_text(command)
        except run_review.PreflightFailure as exc:
            raise ConfigFailure(exc.category, exc.message) from None

    try:
        authorized_commands = normalize_authorized_commands(args.authorized_command)
    except AuthorizedCommandError as exc:
        raise ConfigFailure(exc.category, exc.message) from None

    # Independent Owner contract from --authorized-command; see
    # WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md. Bound once here
    # and threaded unchanged to both attempts, exactly like the authorized
    # command set, so no later stage can introduce or broaden it.
    try:
        allowed_side_effect_paths = normalize_allowed_side_effect_paths(args.allowed_side_effect_path)
    except AllowedSideEffectPathError as exc:
        raise ConfigFailure(exc.category, exc.message) from None

    return {
        "workspace": workspace,
        "task_prompt_path": task_prompt_path,
        "task_prompt_text": task_prompt_text,
        "loop_run_id": loop_run_id,
        "loop_dir": loop_dir,
        "authorized_commands": authorized_commands,
        "allowed_side_effect_paths": allowed_side_effect_paths,
    }


def _deep_preflight(args: argparse.Namespace, workspace: Path, loop_dir: Path) -> dict[str, Any]:
    """Phase B / state=PREFLIGHT: checks that require the loop to already be
    tracked (evidence directory exists, event log open). Failure here drives
    PREFLIGHT -> BLOCKED rather than a pre-state-machine config failure.
    """

    if not run_review.git_index_clean(workspace):
        raise PreflightBlocked("DIRTY_INDEX", "workspace Git index must be clean before starting a loop")
    special_state = run_review.git_special_state(workspace)
    if special_state is not None:
        raise PreflightBlocked("ACTIVE_GIT_OPERATION", f"workspace has an active Git operation: {special_state}")

    baseline_commit = run_review.git_rev_parse(workspace, args.baseline_ref)
    if baseline_commit is None:
        raise PreflightBlocked("BASELINE_REF_UNRESOLVED", "baseline ref does not resolve in the workspace")

    try:
        allowed_paths = run_review.normalize_allowed_paths(args.allowed_path, workspace)
    except run_review.PreflightFailure as exc:
        raise PreflightBlocked(exc.category, exc.message) from None

    claude_executable = discover_claude_executable(args.claude_executable)
    if claude_executable is None:
        raise PreflightBlocked("CLAUDE_NOT_FOUND", "claude executable was not discovered")

    capability_probe_before = _snapshot_workspace_for_review(workspace)
    codex_contract: tuple[str, dict[str, bool], tuple[str, str]] | None = None
    codex_contract_failure: run_review.PreflightFailure | None = None
    try:
        codex_contract = run_review.resolve_codex_contract(args.codex_executable)
    except run_review.PreflightFailure as exc:
        codex_contract_failure = exc
    capability_probe_after = _snapshot_workspace_for_review(
        workspace, trusted_git_metadata_baseline=capability_probe_before[5]
    )
    if capability_probe_after != capability_probe_before:
        raise PreflightBlocked(
            "IMMUTABILITY_VIOLATION",
            "Codex capability probes changed workspace or repository state",
        )
    if codex_contract_failure is not None:
        raise PreflightBlocked(
            codex_contract_failure.category,
            codex_contract_failure.message,
        ) from None
    assert codex_contract is not None
    codex_executable, _codex_caps, _codex_approval = codex_contract

    children_root = loop_dir / "children"
    for suffix in ("-impl-1", "-review-1", "-repair-2", "-review-2"):
        if (children_root / f"{args.loop_run_id or ''}{suffix}").exists():
            raise PreflightBlocked("PREVIOUS_CHILD_INVOCATION_DETECTED", "a child evidence directory for this loop id already exists")

    return {
        "baseline_commit": baseline_commit,
        "allowed_paths": allowed_paths,
        "claude_executable": claude_executable,
        "codex_executable": codex_executable,
        "children_root": children_root,
    }


def _print_receipt(result: dict[str, Any]) -> None:
    print(f"LOOP_RUN_ID={result['loop_run_id']}")
    print(f"RUN_STATE={result['run_state']}")
    print(f"TERMINAL_OUTCOME={result['terminal_outcome']}")
    print(f"STOP_REASON={result['stop_reason'] or ''}")
    print(f"ACCEPTED={str(result['accepted']).lower()}")
    print(f"IMPLEMENTATION_ATTEMPTS={result['implementation_attempts']}")
    print(f"MODEL_INVOCATION_COUNT={result['model_invocation_count']}")
    print(
        "MODEL_INVOCATION_OBSERVATION_COMPLETE="
        f"{str(result['model_invocation_observation_complete']).lower()}"
    )
    print(f"REVIEW_ATTEMPTS={result['review_attempts']}")
    print(f"REPAIR_ROUNDS={result['repair_rounds']}")
    print(f"IMPLEMENTATION_RUN_IDS={json.dumps(result['implementation_run_ids'])}")
    print(f"REVIEW_RUN_IDS={json.dumps(result['review_run_ids'])}")
    print(f"FINAL_VERDICT={result['final_verdict'] or ''}")
    counts = result["finding_counts"]
    print(f"HIGH_FINDINGS={counts.get('HIGH', 0)}")
    print(f"MEDIUM_FINDINGS={counts.get('MEDIUM', 0)}")
    print(f"LOW_FINDINGS={counts.get('LOW', 0)}")
    print(f"UNEXPECTED_CHANGED_PATHS={json.dumps(result['unexpected_changed_paths'])}")
    print(f"WORKSPACE_PRESERVED_DURING_REVIEWS={str(result['workspace_preserved_during_reviews']).lower()}")
    print(f"PREVIOUS_EVIDENCE_IMMUTABLE={str(result['previous_evidence_immutable']).lower()}")
    print(f"TOTAL_DURATION_MS={result['duration_ms']}")
    cost = result["usage"]["total_model_cost_usd"]
    print(f"TOTAL_MODEL_COST_USD={cost if cost is not None else 'unavailable'}")
    print(f"EVIDENCE_DIRECTORY={result['evidence_paths']['loop_directory']}")


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    args = parse_args(argv)
    started_monotonic_started_at = _utc_now_iso()

    try:
        phase_a = _config_preflight(args)
    except ConfigFailure as exc:
        print(f"STATUS=CONFIG_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"STATUS=CONFIG_FAILURE:{exc.__class__.__name__}")
        return 4

    workspace: Path = phase_a["workspace"]
    task_prompt_path: Path = phase_a["task_prompt_path"]
    task_prompt_text: str = phase_a["task_prompt_text"]
    loop_run_id: str = phase_a["loop_run_id"]
    loop_dir: Path = phase_a["loop_dir"]
    authorized_commands: tuple[tuple[str, ...], ...] = phase_a["authorized_commands"]
    # Bound exactly once, here, and threaded unchanged to Attempt 1 and the
    # optional repair Attempt 2 -- never re-derived from a child's evidence,
    # so a reviewer finding cannot introduce or broaden it.
    allowed_side_effect_paths: tuple[str, ...] = phase_a["allowed_side_effect_paths"]

    loop_dir.mkdir(parents=True, exist_ok=True)
    (loop_dir / "children").mkdir(parents=True, exist_ok=True)
    events_path = loop_dir / "events.jsonl"
    events_path.touch(exist_ok=True)
    event_log = EventLog(events_path, loop_run_id)
    machine = StateMachine(event_log)
    event_log.append(event_type="LOOP_CREATED", from_state=None, to_state="CREATED", summary="loop initialized")

    evidence_snapshots: dict[str, dict[str, str]] = {}
    baseline_commit_for_final: str | None = None
    workspace_baseline_hashes: dict[str, str] | None = None
    baseline_git_metadata: dict[str, str] | None = None
    allowed_paths_for_final: tuple[str, ...] = ()
    model_invocation_count = 0
    provider_invocation_observations: list[dict[str, Any]] = []
    review_workspace_observations: list[dict[str, Any]] = []

    def _record_child_evidence(child_id: str, run_directory: Path) -> None:
        try:
            run_directory.lstat()
        except OSError:
            # A child rejected during preflight has no canonical evidence tree
            # to protect; absence is not fabricated into an empty snapshot.
            return
        evidence_snapshots[child_id] = hash_directory(run_directory)

    def _verify_previous_evidence_unchanged() -> tuple[bool, list[str]]:
        drifted: list[str] = []
        for child_id, snapshot in evidence_snapshots.items():
            try:
                current = hash_directory(loop_dir / "children" / child_id)
            except run_review.EvidenceIntegrityFailure:
                drifted.append(child_id)
                continue
            if current != snapshot:
                drifted.append(child_id)
        return (not drifted, drifted)

    def _finalize_current(**kwargs: Any) -> dict[str, Any]:
        if evidence_snapshots:
            immutable, _drifted_child_ids = _verify_previous_evidence_unchanged()
            kwargs["previous_evidence_immutable"] = immutable
        return _finalize(
            **kwargs,
            baseline_commit=baseline_commit_for_final,
            workspace_baseline_hashes=workspace_baseline_hashes,
            baseline_git_metadata=baseline_git_metadata,
            allowed_side_effect_paths=allowed_side_effect_paths,
            model_invocation_count=model_invocation_count,
            provider_invocation_observations=provider_invocation_observations,
            review_workspace_observations=review_workspace_observations,
        )

    try:
        machine.transition("PREFLIGHT", event_type="STATE_TRANSITION", summary="running deep preflight checks")
        candidate_baseline = run_review.git_rev_parse(workspace, args.baseline_ref)
        if candidate_baseline is not None:
            baseline_commit_for_final = candidate_baseline
            workspace_baseline_hashes = snapshot_workspace_hashes(workspace)
            try:
                baseline_git_metadata = run_review.protected_git_metadata_snapshot(
                    workspace
                )
            except run_review.EvidenceIntegrityFailure:
                baseline_git_metadata = None
            try:
                candidate_allowed_paths = run_review.normalize_allowed_paths(
                    args.allowed_path,
                    workspace,
                )
            except run_review.PreflightFailure:
                candidate_allowed_paths = ()
            else:
                allowed_paths_for_final = candidate_allowed_paths
        try:
            phase_b = _deep_preflight(args, workspace, loop_dir)
        except PreflightBlocked as exc:
            machine.transition(
                "BLOCKED",
                event_type="PREFLIGHT_BLOCKED",
                summary=f"{exc.category}: {exc.message}",
            )
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="PREFLIGHT_FAILED",
                error_category=exc.category,
                error_message=exc.message,
                implementation_run_ids=[],
                review_run_ids=[],
                repair_rounds=0,
                final_verdict=None,
                finding_counts={"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                allowed_paths=allowed_paths_for_final,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=[],
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=True,
                previous_evidence_immutable=True,
                usages=[],
            )
            _write_and_print(loop_dir, result)
            return 2

        allowed_paths: tuple[str, ...] = phase_b["allowed_paths"]
        baseline_commit: str = phase_b["baseline_commit"]
        baseline_commit_for_final = baseline_commit
        workspace_baseline_hashes = snapshot_workspace_hashes(workspace)
        allowed_paths_for_final = allowed_paths
        claude_executable: str = phase_b["claude_executable"]
        codex_executable: str = phase_b["codex_executable"]
        children_root: Path = phase_b["children_root"]

        contract = build_task_contract(
            loop_run_id=loop_run_id,
            authorized_commands=authorized_commands,
            allowed_side_effect_paths=allowed_side_effect_paths,
            workspace=workspace,
            task_prompt_path=task_prompt_path,
            task_prompt_text=task_prompt_text,
            baseline_ref=args.baseline_ref,
            baseline_commit=baseline_commit,
            allowed_paths=allowed_paths,
            check_commands=list(args.check_command),
            implementation_timeout_seconds=args.implementation_timeout_seconds,
            review_timeout_seconds=args.review_timeout_seconds,
            implementer_model=args.implementer_model,
            reviewer_model=args.reviewer_model,
            permission_mode=args.permission_mode,
            repair_authorized=bool(args.allow_one_repair),
        )
        (loop_dir / "task-contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        implementation_run_ids: list[str] = []
        review_run_ids: list[str] = []
        usages: list[dict[str, float | int | None]] = []
        repair_rounds = 0

        machine.transition("IMPLEMENTING_ATTEMPT_1", event_type="STATE_TRANSITION", summary="starting Attempt 1")
        impl1_id = f"{loop_run_id}-impl-1"
        event_log.append(
            event_type="RUNNER_INVOCATION_START",
            from_state=machine.state,
            to_state=machine.state,
            attempt=1,
            child_run_id=impl1_id,
        )
        impl1 = invoke_implementation(
            child_id=impl1_id,
            workspace=workspace,
            task_prompt_path=task_prompt_path,
            allowed_paths=allowed_paths,
            authorized_commands=authorized_commands,
            allowed_side_effect_paths=allowed_side_effect_paths,
            timeout_seconds=args.implementation_timeout_seconds,
            model=args.implementer_model,
            permission_mode=args.permission_mode,
            claude_executable=claude_executable,
            children_root=children_root,
        )
        implementation_run_ids.append(impl1_id)
        _record_child_evidence(impl1_id, impl1["run_directory"])
        usages.append(extract_usage_from_implementation_result(impl1["result"]))
        impl1_receipt = impl1["receipt"]
        observed_count, provider_observation = _record_completed_child_runner(
            event_log=event_log,
            state=machine.state,
            attempt=1,
            child_outcome=impl1,
        )
        model_invocation_count += observed_count
        provider_invocation_observations.append(
            {"child_run_id": impl1_id, "provider_invoked": provider_observation}
        )

        impl1_ok = (
            isinstance(impl1_receipt, dict)
            and impl1_receipt.get("status") == "SUCCEEDED"
            and impl1_receipt.get("success") is True
            and not impl1_receipt.get("unexpected_changed_paths")
        )
        if not impl1_ok:
            unexpected = impl1_receipt.get("unexpected_changed_paths", []) if isinstance(impl1_receipt, dict) else []
            to_state = "BLOCKED" if unexpected else "FAILED"
            machine.transition(
                to_state,
                event_type="ATTEMPT_1_OUTCOME",
                attempt=1,
                child_run_id=impl1_id,
                summary="implementation did not succeed" if to_state == "FAILED" else "unexpected changed paths",
            )
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason=None,
                error_category="IMPLEMENTATION_ATTEMPT_1_FAILED" if to_state == "FAILED" else "UNEXPECTED_CHANGED_PATH",
                error_message="Claude implementation Attempt 1 did not succeed" if to_state == "FAILED" else f"unexpected changed paths: {unexpected}",
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict=None,
                finding_counts={"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(impl1_receipt.get("changed_paths", [])) if isinstance(impl1_receipt, dict) else [],
                unexpected_changed_paths=unexpected,
                workspace_preserved_during_reviews=True,
                previous_evidence_immutable=True,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 3 if to_state == "FAILED" else 2

        machine.transition("REVIEWING_ATTEMPT_1", event_type="STATE_TRANSITION", summary="starting Review 1")
        review1_id = f"{loop_run_id}-review-1"
        pre_review1_snapshot = _snapshot_workspace_for_review(workspace)
        event_log.append(
            event_type="RUNNER_INVOCATION_START",
            from_state=machine.state,
            to_state=machine.state,
            attempt=1,
            child_run_id=review1_id,
        )
        review1 = invoke_review(
            child_id=review1_id,
            workspace=workspace,
            task_prompt_path=task_prompt_path,
            implementation_run_directory=impl1["run_directory"],
            implementation_run_id=impl1_id,
            implementation_attempt=1,
            baseline_ref=args.baseline_ref,
            allowed_paths=allowed_paths,
            check_commands=list(args.check_command),
            timeout_seconds=args.review_timeout_seconds,
            model=args.reviewer_model,
            codex_executable=codex_executable,
            children_root=children_root,
        )
        review_run_ids.append(review1_id)
        _record_child_evidence(review1_id, review1["run_directory"])
        post_review1_snapshot = _snapshot_workspace_for_review(
            workspace, trusted_git_metadata_baseline=pre_review1_snapshot[5]
        )
        review1_observation = _review_preservation_evidence(
            attempt=1,
            workspace=workspace,
            before=pre_review1_snapshot,
            after=post_review1_snapshot,
            allowed_source_paths=allowed_paths,
            allowed_side_effect_paths=allowed_side_effect_paths,
        )
        review_workspace_observations.append(review1_observation)
        review1_receipt = review1.get("receipt")
        review1_child_preserved = bool(
            isinstance(review1_receipt, dict)
            and review1_receipt.get("workspace_unchanged") is True
        )
        review1_observation["child_workspace_unchanged"] = review1_child_preserved
        review1_observation["workspace_preserved"] = bool(
            review1_observation["workspace_preserved"]
            and review1_child_preserved
        )
        if not review1_child_preserved:
            review1_observation["status"] = "UNEXPECTED_DRIFT"
        review1_preserved = bool(review1_observation["workspace_preserved"])
        observed_count, provider_observation = _record_completed_child_runner(
            event_log=event_log,
            state=machine.state,
            attempt=1,
            child_outcome=review1,
        )
        model_invocation_count += observed_count
        provider_invocation_observations.append(
            {"child_run_id": review1_id, "provider_invoked": provider_observation}
        )

        verdict1 = extract_validated_verdict(review1)
        workspace_preserved_during_reviews = review1_preserved
        post_review1_state_preserved = (
            _snapshot_workspace_for_review(
                workspace, trusted_git_metadata_baseline=post_review1_snapshot[5]
            )
            == post_review1_snapshot
        )
        review1_observation["post_review_state_preserved"] = (
            post_review1_state_preserved
        )
        if not post_review1_state_preserved:
            review1_preserved = False
            workspace_preserved_during_reviews = False
            review1_observation["workspace_preserved"] = False
            review1_observation["status"] = "UNEXPECTED_DRIFT"

        if verdict1 is None or not review1_preserved:
            to_state = "FAILED"
            reason = "invalid or missing review evidence" if verdict1 is None else "review mutated the workspace"
            machine.transition(to_state, event_type="REVIEW_1_OUTCOME", attempt=1, child_run_id=review1_id, summary=reason)
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason=None,
                error_category="REVIEW_1_INVALID" if verdict1 is None else "REVIEW_1_MUTATED_WORKSPACE",
                error_message=reason,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict=None,
                finding_counts={"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(impl1_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=True,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 3

        if verdict1["verdict"] == "APPROVE":
            machine.transition("COMPLETED", event_type="REVIEW_1_OUTCOME", attempt=1, child_run_id=review1_id, summary="APPROVE")
            immutable_ok, _drift = _verify_previous_evidence_unchanged()
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="REVIEW_APPROVED",
                error_category=None,
                error_message=None,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="APPROVE",
                finding_counts=run_review.finding_counts(verdict1),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(impl1_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=immutable_ok,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 0

        if verdict1["verdict"] == "BLOCKED":
            machine.transition("BLOCKED", event_type="REVIEW_1_OUTCOME", attempt=1, child_run_id=review1_id, summary="BLOCKED")
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="REVIEW_BLOCKED",
                error_category=None,
                error_message=None,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="BLOCKED",
                finding_counts=run_review.finding_counts(verdict1),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(impl1_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=True,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 2

        # verdict1 == REQUEST_CHANGES
        if not args.allow_one_repair:
            machine.transition(
                "STOPPED", event_type="REVIEW_1_OUTCOME", attempt=1, child_run_id=review1_id, summary="REQUEST_CHANGES, repair not authorized"
            )
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="OWNER_REPAIR_NOT_AUTHORIZED",
                error_category=None,
                error_message=None,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="REQUEST_CHANGES",
                finding_counts=run_review.finding_counts(verdict1),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(impl1_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=True,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 1

        # Repair authorized: exactly one bounded repair round.
        machine.transition("REPAIRING_ATTEMPT_2", event_type="STATE_TRANSITION", summary="starting Attempt 2 (repair)")
        repair_rounds += 1
        repair2_id = f"{loop_run_id}-repair-2"
        event_log.append(
            event_type="RUNNER_INVOCATION_START",
            from_state=machine.state,
            to_state=machine.state,
            attempt=2,
            child_run_id=repair2_id,
        )
        repair2 = invoke_repair(
            child_id=repair2_id,
            workspace=workspace,
            task_prompt_path=task_prompt_path,
            previous_implementation_run_directory=impl1["run_directory"],
            implementation_run_id=impl1_id,
            implementation_attempt=1,
            review_run_directory=review1["run_directory"],
            allowed_paths=allowed_paths,
            check_commands=list(args.check_command),
            authorized_commands=authorized_commands,
            allowed_side_effect_paths=allowed_side_effect_paths,
            timeout_seconds=args.implementation_timeout_seconds,
            model=args.implementer_model,
            permission_mode=args.permission_mode,
            claude_executable=claude_executable,
            children_root=children_root,
        )
        implementation_run_ids.append(repair2_id)
        outer_previous_evidence_immutable, _outer_drift = (
            _verify_previous_evidence_unchanged()
        )
        _record_child_evidence(repair2_id, repair2["run_directory"])
        usages.append(extract_usage_from_implementation_result(repair2["result"]))
        repair2_receipt = repair2["receipt"]
        observed_count, provider_observation = _record_completed_child_runner(
            event_log=event_log,
            state=machine.state,
            attempt=2,
            child_outcome=repair2,
        )
        model_invocation_count += observed_count
        provider_invocation_observations.append(
            {"child_run_id": repair2_id, "provider_invoked": provider_observation}
        )

        repair_receipt_immutable_ok = bool(
            isinstance(repair2_receipt, dict)
            and repair2_receipt.get("previous_evidence_unchanged") is True
        )
        repair_immutable_ok = (
            repair_receipt_immutable_ok and outer_previous_evidence_immutable
        )
        repair_ok = (
            isinstance(repair2_receipt, dict)
            and repair2_receipt.get("success") is True
            and not repair2_receipt.get("unexpected_changed_paths")
            and repair_receipt_immutable_ok
            and outer_previous_evidence_immutable
        )
        if not repair_ok:
            unexpected = repair2_receipt.get("unexpected_changed_paths", []) if isinstance(repair2_receipt, dict) else []
            to_state = "BLOCKED" if unexpected else "FAILED"
            machine.transition(
                to_state, event_type="ATTEMPT_2_OUTCOME", attempt=2, child_run_id=repair2_id, summary="repair did not succeed"
            )
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason=None,
                error_category="REPAIR_ATTEMPT_2_FAILED" if to_state == "FAILED" else "UNEXPECTED_CHANGED_PATH",
                error_message="Claude repair Attempt 2 did not succeed",
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="REQUEST_CHANGES",
                finding_counts=run_review.finding_counts(verdict1),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(repair2_receipt.get("changed_paths", [])) if isinstance(repair2_receipt, dict) else [],
                unexpected_changed_paths=unexpected,
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=outer_previous_evidence_immutable,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 3 if to_state == "FAILED" else 2

        machine.transition("REVIEWING_ATTEMPT_2", event_type="STATE_TRANSITION", summary="starting Review 2")
        review2_id = f"{loop_run_id}-review-2"
        pre_review2_snapshot = _snapshot_workspace_for_review(workspace)
        event_log.append(
            event_type="RUNNER_INVOCATION_START",
            from_state=machine.state,
            to_state=machine.state,
            attempt=2,
            child_run_id=review2_id,
        )
        review2 = invoke_review(
            child_id=review2_id,
            workspace=workspace,
            task_prompt_path=task_prompt_path,
            implementation_run_directory=repair2["run_directory"],
            implementation_run_id=repair2_id,
            implementation_attempt=run_repair.REPAIR_ATTEMPT_NUMBER,
            baseline_ref=args.baseline_ref,
            allowed_paths=allowed_paths,
            check_commands=list(args.check_command),
            timeout_seconds=args.review_timeout_seconds,
            model=args.reviewer_model,
            codex_executable=codex_executable,
            children_root=children_root,
        )
        review_run_ids.append(review2_id)
        _record_child_evidence(review2_id, review2["run_directory"])
        post_review2_snapshot = _snapshot_workspace_for_review(
            workspace, trusted_git_metadata_baseline=pre_review2_snapshot[5]
        )
        review2_observation = _review_preservation_evidence(
            attempt=2,
            workspace=workspace,
            before=pre_review2_snapshot,
            after=post_review2_snapshot,
            allowed_source_paths=allowed_paths,
            allowed_side_effect_paths=allowed_side_effect_paths,
        )
        review_workspace_observations.append(review2_observation)
        review2_receipt = review2.get("receipt")
        review2_child_preserved = bool(
            isinstance(review2_receipt, dict)
            and review2_receipt.get("workspace_unchanged") is True
        )
        review2_observation["child_workspace_unchanged"] = review2_child_preserved
        review2_observation["workspace_preserved"] = bool(
            review2_observation["workspace_preserved"]
            and review2_child_preserved
        )
        if not review2_child_preserved:
            review2_observation["status"] = "UNEXPECTED_DRIFT"
        review2_preserved = bool(review2_observation["workspace_preserved"])
        workspace_preserved_during_reviews = workspace_preserved_during_reviews and review2_preserved
        observed_count, provider_observation = _record_completed_child_runner(
            event_log=event_log,
            state=machine.state,
            attempt=2,
            child_outcome=review2,
        )
        model_invocation_count += observed_count
        provider_invocation_observations.append(
            {"child_run_id": review2_id, "provider_invoked": provider_observation}
        )

        verdict2 = extract_validated_verdict(review2)
        post_review2_state_preserved = (
            _snapshot_workspace_for_review(
                workspace, trusted_git_metadata_baseline=post_review2_snapshot[5]
            )
            == post_review2_snapshot
        )
        review2_observation["post_review_state_preserved"] = (
            post_review2_state_preserved
        )
        if not post_review2_state_preserved:
            review2_preserved = False
            workspace_preserved_during_reviews = False
            review2_observation["workspace_preserved"] = False
            review2_observation["status"] = "UNEXPECTED_DRIFT"
        if verdict2 is None or not review2_preserved:
            to_state = "FAILED"
            reason = "invalid or missing review evidence" if verdict2 is None else "review mutated the workspace"
            machine.transition(to_state, event_type="REVIEW_2_OUTCOME", attempt=2, child_run_id=review2_id, summary=reason)
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason=None,
                error_category="REVIEW_2_INVALID" if verdict2 is None else "REVIEW_2_MUTATED_WORKSPACE",
                error_message=reason,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict=None,
                finding_counts={"HIGH": 0, "MEDIUM": 0, "LOW": 0},
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(repair2_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=repair_immutable_ok,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 3

        if verdict2["verdict"] == "APPROVE":
            machine.transition("COMPLETED", event_type="REVIEW_2_OUTCOME", attempt=2, child_run_id=review2_id, summary="APPROVE")
            immutable_ok, _drift = _verify_previous_evidence_unchanged()
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="REVIEW_APPROVED",
                error_category=None,
                error_message=None,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="APPROVE",
                finding_counts=run_review.finding_counts(verdict2),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(repair2_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=immutable_ok,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 0

        if verdict2["verdict"] == "BLOCKED":
            machine.transition("BLOCKED", event_type="REVIEW_2_OUTCOME", attempt=2, child_run_id=review2_id, summary="BLOCKED")
            result = _finalize_current(
                loop_run_id=loop_run_id,
                authorized_commands=authorized_commands,
                workspace=workspace,
                machine=machine,
                loop_dir=loop_dir,
                started_at=started_monotonic_started_at,
                stop_reason="REVIEW_BLOCKED",
                error_category=None,
                error_message=None,
                implementation_run_ids=implementation_run_ids,
                review_run_ids=review_run_ids,
                repair_rounds=repair_rounds,
                final_verdict="BLOCKED",
                finding_counts=run_review.finding_counts(verdict2),
                allowed_paths=allowed_paths,
                check_commands=list(args.check_command),
                workspace_final_changed_paths=list(repair2_receipt.get("changed_paths", [])),
                unexpected_changed_paths=[],
                workspace_preserved_during_reviews=workspace_preserved_during_reviews,
                previous_evidence_immutable=True,
                usages=usages,
            )
            _write_and_print(loop_dir, result)
            return 2

        # verdict2 == REQUEST_CHANGES: repair limit reached, must never start Attempt 3.
        machine.transition(
            "STOPPED", event_type="REVIEW_2_OUTCOME", attempt=2, child_run_id=review2_id, summary="REQUEST_CHANGES, repair limit reached"
        )
        result = _finalize_current(
            loop_run_id=loop_run_id,
            authorized_commands=authorized_commands,
            workspace=workspace,
            machine=machine,
            loop_dir=loop_dir,
            started_at=started_monotonic_started_at,
            stop_reason="MAX_REPAIR_ROUNDS_REACHED",
            error_category=None,
            error_message=None,
            implementation_run_ids=implementation_run_ids,
            review_run_ids=review_run_ids,
            repair_rounds=repair_rounds,
            final_verdict="REQUEST_CHANGES",
            finding_counts=run_review.finding_counts(verdict2),
            allowed_paths=allowed_paths,
            check_commands=list(args.check_command),
            workspace_final_changed_paths=list(repair2_receipt.get("changed_paths", [])),
            unexpected_changed_paths=[],
            workspace_preserved_during_reviews=workspace_preserved_during_reviews,
            previous_evidence_immutable=True,
            usages=usages,
        )
        _write_and_print(loop_dir, result)
        return 1

    except InvalidTransition as exc:
        event_log.append(event_type="INVALID_TRANSITION_REJECTED", from_state=machine.state, to_state=None, summary=str(exc))
        print("STATUS=ORCHESTRATOR_FAILURE:INVALID_TRANSITION")
        print(f"MESSAGE={exc}")
        return 5
    except Exception as exc:  # noqa: BLE001
        print(f"STATUS=ORCHESTRATOR_FAILURE:{exc.__class__.__name__}")
        return 5


def _snapshot_workspace_for_review(
    workspace: Path,
    *,
    trusted_git_metadata_baseline: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], bool, str | None, str | None, dict[str, str]]:
    """`trusted_git_metadata_baseline`, when given, must be the protected
    git_metadata component (index 5) of a `_snapshot_workspace_for_review()`
    call already known to be part of a coherent pair -- e.g. the "before"
    half of a before/probe/after sequence -- never a value produced by this
    same call. It is the only thing that can make a same-window
    timestamp-only measurement instability retry-eligible instead of
    failing closed; see `run_review.protected_git_metadata_snapshot`.
    """

    head = run_review.git_rev_parse(workspace, "HEAD") or ""
    try:
        git_metadata = run_review.protected_git_metadata_snapshot(
            workspace, trusted_baseline=trusted_git_metadata_baseline
        )
    except run_review.EvidenceIntegrityFailure:
        git_metadata = {}
    return (
        head,
        snapshot_workspace_hashes(workspace),
        run_review.git_index_clean(workspace),
        run_review.git_special_state(workspace),
        run_review.git_index_identity(workspace),
        git_metadata,
    )


def _review_preservation_evidence(
    *,
    attempt: int,
    workspace: Path,
    before: tuple[str, dict[str, str], bool, str | None, str | None, dict[str, str]],
    after: tuple[str, dict[str, str], bool, str | None, str | None, dict[str, str]],
    allowed_source_paths: tuple[str, ...],
    allowed_side_effect_paths: tuple[str, ...],
) -> dict[str, Any]:
    observation = run_review.classify_review_workspace_preservation(
        before[1],
        after[1],
        allowed_source_paths=allowed_source_paths,
        allowed_side_effect_paths=allowed_side_effect_paths,
        workspace=workspace,
    )
    repository_identity_preserved = (
        before[0] == after[0]
        and before[2]
        and after[2]
        and before[3] is None
        and after[3] is None
        and before[4] is not None
        and before[4] == after[4]
        and bool(before[5])
        and before[5] == after[5]
    )
    workspace_preserved = observation.workspace_preserved and repository_identity_preserved
    side_effect_counts, side_effect_sample = summarize_side_effect_paths(
        observation.authorized_side_effect_changed_paths,
        allowed_side_effect_paths,
    )
    return {
        "attempt": attempt,
        "status": observation.status if repository_identity_preserved else "UNEXPECTED_DRIFT",
        "workspace_preserved": workspace_preserved,
        "product_state_preserved": observation.product_state_preserved,
        "repository_identity_preserved": repository_identity_preserved,
        "index_clean_before": before[2],
        "index_clean_after": after[2],
        "git_special_state_before": before[3],
        "git_special_state_after": after[3],
        "index_identity_before": before[4],
        "index_identity_after": after[4],
        "protected_git_metadata_hash_before": (
            workspace_hash(before[5]) if before[5] else None
        ),
        "protected_git_metadata_hash_after": (
            workspace_hash(after[5]) if after[5] else None
        ),
        "protected_git_metadata_preserved": bool(before[5]) and before[5] == after[5],
        "workspace_git_entry_hash_before": (
            run_review.workspace_git_entry_hash_from_snapshot(before[5])
            if before[5]
            else None
        ),
        "workspace_git_entry_hash_after": (
            run_review.workspace_git_entry_hash_from_snapshot(after[5])
            if after[5]
            else None
        ),
        "workspace_git_entry_preserved": (
            bool(before[5])
            and bool(after[5])
            and run_review.workspace_git_entry_hash_from_snapshot(before[5])
            == run_review.workspace_git_entry_hash_from_snapshot(after[5])
        ),
        "protected_git_metadata_changed_path_count": len(
            run_review.diff_workspace_hashes(before[5], after[5])
        ),
        "protected_git_metadata_changed_paths_sample": list(
            run_review.diff_workspace_hashes(before[5], after[5])[:20]
        ),
        "authorized_side_effect_drift": observation.authorized_side_effect_drift,
        "source_changed_paths": list(observation.source_changed_paths),
        "unexpected_changed_paths": list(observation.unexpected_changed_paths),
        "authorized_side_effect_changed_path_count": len(
            observation.authorized_side_effect_changed_paths
        ),
        "authorized_side_effect_changed_counts_by_root": {
            root: count for root, count in side_effect_counts
        },
        "authorized_side_effect_changed_paths_sample": list(side_effect_sample),
        "workspace_full_hash_before": workspace_hash(before[1]),
        "workspace_full_hash_after": workspace_hash(after[1]),
    }


def _finalize(
    *,
    loop_run_id: str,
    workspace: Path,
    machine: StateMachine,
    loop_dir: Path,
    started_at: str,
    stop_reason: str | None,
    error_category: str | None,
    error_message: str | None,
    implementation_run_ids: list[str],
    review_run_ids: list[str],
    repair_rounds: int,
    final_verdict: str | None,
    finding_counts: dict[str, int],
    allowed_paths: tuple[str, ...],
    check_commands: list[str],
    authorized_commands: tuple[tuple[str, ...], ...] = (),
    workspace_final_changed_paths: list[str],
    unexpected_changed_paths: list[str],
    workspace_preserved_during_reviews: bool,
    previous_evidence_immutable: bool,
    usages: list[dict[str, float | int | None]],
    baseline_commit: str | None = None,
    workspace_baseline_hashes: dict[str, str] | None = None,
    baseline_git_metadata: dict[str, str] | None = None,
    allowed_side_effect_paths: tuple[str, ...] = (),
    model_invocation_count: int = 0,
    provider_invocation_observations: list[dict[str, Any]] | None = None,
    review_workspace_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run_state = machine.state
    if run_state == "COMPLETED":
        terminal_outcome = "COMPLETED"
        accepted = True
    elif run_state == "BLOCKED":
        terminal_outcome = "BLOCKED"
        accepted = False
    elif run_state == "STOPPED":
        terminal_outcome = "NOT_ACCEPTED"
        accepted = False
    else:  # FAILED
        terminal_outcome = "FAILED"
        accepted = False

    finished_at = _utc_now_iso()
    usage = aggregate_usage(usages)
    duration_ms = _elapsed_ms(started_at, finished_at)
    try:
        fallback_final_git_metadata = run_review.protected_git_metadata_snapshot(
            workspace, trusted_baseline=baseline_git_metadata
        )
    except run_review.EvidenceIntegrityFailure:
        fallback_final_git_metadata = {}
    fallback_git_metadata_changes = (
        run_review.diff_workspace_hashes(
            baseline_git_metadata,
            fallback_final_git_metadata,
        )
        if baseline_git_metadata is not None
        else ()
    )
    fallback_final_git_entry_hash = (
        run_review.workspace_git_entry_hash_from_snapshot(
            fallback_final_git_metadata
        )
        if fallback_final_git_metadata
        else None
    )
    fallback_baseline_git_entry_hash = (
        run_review.workspace_git_entry_hash_from_snapshot(baseline_git_metadata)
        if baseline_git_metadata is not None
        else None
    )
    final_workspace = {
        "workspace_final_hash": workspace_hash(snapshot_workspace_hashes(workspace)),
        "workspace_final_changed_paths": list(workspace_final_changed_paths),
        "workspace_final_source_changed_paths": list(workspace_final_changed_paths),
        "workspace_final_unexpected_changed_paths": list(unexpected_changed_paths),
        "workspace_final_authorized_side_effect_changed_path_count": 0,
        "workspace_final_authorized_side_effect_changed_counts_by_root": {
            root: 0 for root in allowed_side_effect_paths
        },
        "workspace_final_authorized_side_effect_changed_paths_sample": [],
        "protected_git_metadata_final_hash": (
            workspace_hash(fallback_final_git_metadata)
            if fallback_final_git_metadata
            else None
        ),
        "protected_git_metadata_baseline_hash": (
            workspace_hash(baseline_git_metadata)
            if baseline_git_metadata is not None
            else None
        ),
        "protected_git_metadata_preserved": (
            bool(fallback_final_git_metadata)
            and baseline_git_metadata is not None
            and not fallback_git_metadata_changes
        ),
        "workspace_git_entry_final_hash": fallback_final_git_entry_hash,
        "workspace_git_entry_baseline_hash": fallback_baseline_git_entry_hash,
        "workspace_git_entry_preserved": (
            fallback_final_git_entry_hash is not None
            and fallback_final_git_entry_hash
            == fallback_baseline_git_entry_hash
        ),
        "protected_git_metadata_changed_path_count": len(
            fallback_git_metadata_changes
        ),
        "protected_git_metadata_changed_paths_sample": list(
            fallback_git_metadata_changes[:20]
        ),
    }
    if baseline_commit is not None and workspace_baseline_hashes is not None:
        final_workspace = _derive_final_workspace_evidence(
            workspace=workspace,
            baseline_commit=baseline_commit,
            baseline_hashes=workspace_baseline_hashes,
            allowed_source_paths=allowed_paths,
            allowed_side_effect_paths=allowed_side_effect_paths,
            baseline_git_metadata=baseline_git_metadata,
        )

    if accepted and final_workspace["protected_git_metadata_preserved"] is not True:
        accepted = False
        terminal_outcome = "FAILED"
        if error_category is None:
            error_category = "FINAL_REPOSITORY_IDENTITY_DRIFT"
        if error_message is None:
            error_message = "final protected Git metadata differs from the starting repository identity"

    provider_observations = list(provider_invocation_observations or [])
    return {
        "loop_run_id": loop_run_id,
        "workspace": str(workspace),
        "run_state": run_state,
        "terminal_outcome": terminal_outcome,
        "stop_reason": stop_reason,
        "accepted": accepted,
        "implementation_attempts": len(implementation_run_ids),
        "model_invocation_count": model_invocation_count,
        "model_invocation_count_semantics": (
            "positively observed model-execution CLI subprocess starts"
        ),
        "model_invocation_observation_complete": all(
            observation.get("provider_invoked") is not None
            for observation in provider_observations
        ),
        "provider_invocation_observations": provider_observations,
        "review_attempts": len(review_run_ids),
        "repair_rounds": repair_rounds,
        "implementation_run_ids": implementation_run_ids,
        "review_run_ids": review_run_ids,
        "final_review_run_id": review_run_ids[-1] if review_run_ids else None,
        "final_verdict": final_verdict,
        "finding_counts": finding_counts,
        "allowed_paths": list(allowed_paths),
        "check_commands": check_commands,
        "authorized_command_fingerprints": [
            compute_authorized_command_fingerprint(argv) for argv in authorized_commands
        ],
        "authorized_command_set_fingerprint": (
            compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
        ),
        "authorized_command_count": len(authorized_commands),
        "workspace_final_changed_paths": final_workspace["workspace_final_changed_paths"],
        "workspace_final_source_changed_paths": final_workspace[
            "workspace_final_source_changed_paths"
        ],
        "workspace_final_hash": final_workspace["workspace_final_hash"],
        "workspace_final_authorized_side_effect_changed_path_count": final_workspace[
            "workspace_final_authorized_side_effect_changed_path_count"
        ],
        "workspace_final_authorized_side_effect_changed_counts_by_root": final_workspace[
            "workspace_final_authorized_side_effect_changed_counts_by_root"
        ],
        "workspace_final_authorized_side_effect_changed_paths_sample": final_workspace[
            "workspace_final_authorized_side_effect_changed_paths_sample"
        ],
        "unexpected_changed_paths": final_workspace[
            "workspace_final_unexpected_changed_paths"
        ],
        "protected_git_metadata_final_hash": final_workspace[
            "protected_git_metadata_final_hash"
        ],
        "protected_git_metadata_baseline_hash": final_workspace[
            "protected_git_metadata_baseline_hash"
        ],
        "protected_git_metadata_preserved": final_workspace[
            "protected_git_metadata_preserved"
        ],
        "workspace_git_entry_final_hash": final_workspace[
            "workspace_git_entry_final_hash"
        ],
        "workspace_git_entry_baseline_hash": final_workspace[
            "workspace_git_entry_baseline_hash"
        ],
        "workspace_git_entry_preserved": final_workspace[
            "workspace_git_entry_preserved"
        ],
        "protected_git_metadata_changed_path_count": final_workspace[
            "protected_git_metadata_changed_path_count"
        ],
        "protected_git_metadata_changed_paths_sample": final_workspace[
            "protected_git_metadata_changed_paths_sample"
        ],
        "workspace_preserved_during_reviews": workspace_preserved_during_reviews,
        "review_workspace_observations": list(review_workspace_observations or []),
        "previous_evidence_immutable": previous_evidence_immutable,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "error_category": error_category,
        "error_message": error_message,
        "usage": usage,
        "evidence_paths": {
            "loop_directory": str(loop_dir),
            "task_contract": str(loop_dir / "task-contract.json"),
            "events": str(loop_dir / "events.jsonl"),
            "loop_result": str(loop_dir / "loop-result.json"),
            "loop_receipt": str(loop_dir / "loop-receipt.json"),
        },
    }


def _elapsed_ms(started_at_iso: str, finished_at_iso: str) -> int:
    start = datetime.fromisoformat(started_at_iso)
    finish = datetime.fromisoformat(finished_at_iso)
    return int((finish - start).total_seconds() * 1000)


def _write_and_print(loop_dir: Path, result: dict[str, Any]) -> None:
    (loop_dir / "loop-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "loop_run_id": result["loop_run_id"],
        "run_state": result["run_state"],
        "terminal_outcome": result["terminal_outcome"],
        "stop_reason": result["stop_reason"],
        "accepted": result["accepted"],
        "final_verdict": result["final_verdict"],
        "implementation_attempts": result["implementation_attempts"],
        "model_invocation_count": result["model_invocation_count"],
        "model_invocation_observation_complete": result[
            "model_invocation_observation_complete"
        ],
        "review_attempts": result["review_attempts"],
        "repair_rounds": result["repair_rounds"],
        "protected_git_metadata_final_hash": result[
            "protected_git_metadata_final_hash"
        ],
        "protected_git_metadata_preserved": result[
            "protected_git_metadata_preserved"
        ],
        "workspace_git_entry_final_hash": result[
            "workspace_git_entry_final_hash"
        ],
        "workspace_git_entry_preserved": result[
            "workspace_git_entry_preserved"
        ],
        "protected_git_metadata_changed_path_count": result[
            "protected_git_metadata_changed_path_count"
        ],
        "protected_git_metadata_changed_paths_sample": result[
            "protected_git_metadata_changed_paths_sample"
        ],
        "duration_ms": result["duration_ms"],
        "total_model_cost_usd": result["usage"]["total_model_cost_usd"],
        "evidence_directory": result["evidence_paths"]["loop_directory"],
    }
    (loop_dir / "loop-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_receipt(result)


if __name__ == "__main__":
    raise SystemExit(main())
