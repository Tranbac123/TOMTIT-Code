#!/usr/bin/env python3
"""DOGFOOD_EVALUATION_V0: minimum observational evaluation telemetry for one
completed (or blocked) dogfood `run_loop.py` run.

Purpose (see DOGFOOD_EVALUATION_TELEMETRY_V0.md for the full locked
contract): this tool exists to evaluate

    H1 = TOMTIT reduces active human supervision required to confidently
         accept a software change

during the first 3-5 real dogfood tasks. It is read-only with respect to
canonical Runtime evidence and has NO execution authority:

    Runtime evidence (read-only) + Owner-supplied fields -> DogfoodEvaluationV0

This tool never modifies `run_loop.py`/`run_repair.py`/`run_review.py`
evidence, never changes a run's terminal outcome, and never authorizes a
retry or repair. `EVALUATION_CANONICAL_AUTHORITY=NO`.

Run with:

    python3 .tomtit/dogfood/evaluate_dogfood_run.py \\
      --evidence-root /path/to/loops/<loop-run-id> \\
      --owner-disposition ACCEPTED \\
      --active-human-seconds 120 \\
      --owner-interruptions 1 \\
      --clarification-count 0 \\
      --manual-edit-lines 0

    python3 .tomtit/dogfood/evaluate_dogfood_run.py \\
      --summarize /path/to/evaluations
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "DOGFOOD_EVALUATION_V0"

REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical evidence file names this evaluator reads (never writes). See
# DOGFOOD_EVALUATION_TELEMETRY_V0.md#evidence-completeness for the exact
# trajectory-aware rule these are used under.
REQUIRED_LOOP_FILES = ("loop-result.json", "loop-receipt.json", "events.jsonl")
IMPLEMENTATION_REQUIRED_FILES = ("receipt.json", "result.json", "diff.patch")
REPAIR_ADDITIONAL_REQUIRED_FILES = ("repair-context.json", "checks.json")
REVIEW_REQUIRED_FILES = ("review-receipt.json", "review-result.json", "checks.json")


class OwnerDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_MANUAL_EDIT = "ACCEPTED_WITH_MANUAL_EDIT"
    NOT_USABLE = "NOT_USABLE"


class BoolTriState(str, Enum):
    """TRUE/FALSE/UNKNOWN -- for concepts that are a real yes/no fact but may
    not be determinable from the evidence actually available (Section 6)."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class ObservabilityState(str, Enum):
    """KNOWN/UNKNOWN/UNAVAILABLE -- for cost/token telemetry (Section 7).
    UNAVAILABLE means the adapter/provider does not expose the concept at
    all in this evidence shape; UNKNOWN means it is measurable in general
    but this particular run's evidence did not provide a trustworthy value.
    Never inferred, never zero-filled."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class EvaluationError(Exception):
    """A clearly-categorized failure to build or write an evaluation record.
    Never raised by, or caught inside, canonical Runtime code -- this
    module is never imported by run_loop.py/run_repair.py/run_review.py."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


# ---------------------------------------------------------------------------
# Read-only Runtime evidence extraction.
# ---------------------------------------------------------------------------


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationError("EVIDENCE_FILE_UNREADABLE", f"{path}: {exc.__class__.__name__}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvaluationError("MALFORMED_EVIDENCE_JSON", f"{path}: {exc}") from None
    if not isinstance(data, dict):
        raise EvaluationError("MALFORMED_EVIDENCE_JSON", f"{path}: expected a JSON object")
    return data


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceCompletenessResult:
    complete: bool
    missing_paths: tuple[str, ...]


def check_evidence_completeness(evidence_root: Path, loop_result: dict[str, Any]) -> EvidenceCompletenessResult:
    """Bounded, deterministic, trajectory-aware completeness check
    (Section 12): only the canonical evidence the run's OWN recorded
    trajectory actually requires. A first-pass APPROVE never requires
    repair/Review2 artifacts; a run that never left preflight
    (`implementation_run_ids`/`review_run_ids` both empty) requires only
    the three loop-level files.
    """

    missing: list[str] = []
    for name in REQUIRED_LOOP_FILES:
        if not (evidence_root / name).is_file():
            missing.append(name)

    implementation_run_ids = loop_result.get("implementation_run_ids") or []
    for index, run_id in enumerate(implementation_run_ids):
        if not isinstance(run_id, str) or not run_id:
            missing.append(f"children/<invalid-implementation-run-id-at-index-{index}>")
            continue
        child_dir = evidence_root / "children" / run_id
        required = IMPLEMENTATION_REQUIRED_FILES
        if index > 0:
            # Every implementation run after the first is a bounded repair
            # attempt (see run_repair.py) and carries its own additional
            # required evidence.
            required = IMPLEMENTATION_REQUIRED_FILES + REPAIR_ADDITIONAL_REQUIRED_FILES
        for name in required:
            if not (child_dir / name).is_file():
                missing.append(f"children/{run_id}/{name}")

    review_run_ids = loop_result.get("review_run_ids") or []
    for run_id in review_run_ids:
        if not isinstance(run_id, str) or not run_id:
            missing.append("children/<invalid-review-run-id>")
            continue
        child_dir = evidence_root / "children" / run_id
        for name in REVIEW_REQUIRED_FILES:
            if not (child_dir / name).is_file():
                missing.append(f"children/{run_id}/{name}")

    return EvidenceCompletenessResult(complete=not missing, missing_paths=tuple(missing))


def _tri_state_numeric(usage: Any, key: str) -> tuple[str, float | None]:
    """UNAVAILABLE if the `usage` block itself (or this specific field) is
    absent from this evidence shape; UNKNOWN if present but not a
    trustworthy number; KNOWN with the real value otherwise. Never zero."""

    if not isinstance(usage, dict) or key not in usage:
        return (ObservabilityState.UNAVAILABLE.value, None)
    value = usage[key]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (ObservabilityState.KNOWN.value, value)
    return (ObservabilityState.UNKNOWN.value, None)


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeDerivedFacts:
    """Everything this evaluator can mechanically derive from canonical
    Runtime evidence alone -- provenance RUNTIME_DERIVED throughout (see
    DOGFOOD_EVALUATION_TELEMETRY_V0.md#provenance)."""

    run_id: str | None
    task_contract_sha256: str | None
    workspace_baseline: str | None
    runtime_terminal_outcome: str | None
    runtime_stop_reason: str | None
    runtime_accepted: bool | None
    implementation_attempt_count: int | None
    repair_count: int | None
    review_count: int | None
    model_invocation_count: int | None
    elapsed_seconds: float | None
    total_cost_usd_status: str
    total_cost_usd_value: float | None
    total_input_tokens_status: str
    total_input_tokens_value: float | None
    total_output_tokens_status: str
    total_output_tokens_value: float | None
    scope_violation: str
    evidence_complete: bool
    missing_evidence_paths: tuple[str, ...]


def extract_runtime_derived_facts(evidence_root: Path) -> RuntimeDerivedFacts:
    """Read `loop-result.json` (and, if present, `task-contract.json`)
    under `evidence_root` and derive every Runtime-owned fact this
    evaluator records. Read-only: never writes, never mutates the passed
    evidence in memory or on disk.
    """

    if not evidence_root.is_dir():
        raise EvaluationError("EVIDENCE_ROOT_MISSING", f"evidence root is not a directory: {evidence_root}")
    result_path = evidence_root / "loop-result.json"
    if not result_path.is_file():
        raise EvaluationError("EVIDENCE_ROOT_MISSING", f"loop-result.json not found under: {evidence_root}")
    loop_result = _read_json_object(result_path)

    for required_key in ("loop_run_id", "run_state", "terminal_outcome"):
        if required_key not in loop_result:
            raise EvaluationError(
                "MALFORMED_LOOP_RESULT", f"loop-result.json missing required key: {required_key}"
            )

    completeness = check_evidence_completeness(evidence_root, loop_result)

    task_contract_sha256: str | None = None
    workspace_baseline: str | None = None
    contract_path = evidence_root / "task-contract.json"
    if contract_path.is_file():
        try:
            contract = _read_json_object(contract_path)
        except EvaluationError:
            contract = {}
        # run_loop.py's own task-contract.json has no field named literally
        # "task_contract_sha256" -- the closest canonical, deterministic
        # identity hash it records is `task_prompt_sha256` (the sha256 of
        # the exact task prompt text this run was bound to). Documented
        # explicitly in DOGFOOD_EVALUATION_TELEMETRY_V0.md.
        value = contract.get("task_prompt_sha256")
        task_contract_sha256 = value if isinstance(value, str) else None
        baseline = contract.get("baseline_commit")
        workspace_baseline = baseline if isinstance(baseline, str) else None

    duration_ms = loop_result.get("duration_ms")
    elapsed_seconds = duration_ms / 1000.0 if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) else None

    usage = loop_result.get("usage")
    cost_status, cost_value = _tri_state_numeric(usage, "total_model_cost_usd")
    input_tokens_status, input_tokens_value = _tri_state_numeric(usage, "total_input_tokens")
    output_tokens_status, output_tokens_value = _tri_state_numeric(usage, "total_output_tokens")

    unexpected_changed_paths = loop_result.get("unexpected_changed_paths")
    if not completeness.complete or unexpected_changed_paths is None:
        scope_violation = BoolTriState.UNKNOWN.value
    elif unexpected_changed_paths:
        scope_violation = BoolTriState.TRUE.value
    else:
        scope_violation = BoolTriState.FALSE.value

    runtime_accepted = loop_result.get("accepted")
    if not isinstance(runtime_accepted, bool):
        runtime_accepted = None

    def _int_or_none(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return RuntimeDerivedFacts(
        run_id=loop_result.get("loop_run_id") if isinstance(loop_result.get("loop_run_id"), str) else None,
        task_contract_sha256=task_contract_sha256,
        workspace_baseline=workspace_baseline,
        runtime_terminal_outcome=loop_result.get("terminal_outcome"),
        runtime_stop_reason=loop_result.get("stop_reason"),
        runtime_accepted=runtime_accepted,
        implementation_attempt_count=_int_or_none(loop_result.get("implementation_attempts")),
        repair_count=_int_or_none(loop_result.get("repair_rounds")),
        review_count=_int_or_none(loop_result.get("review_attempts")),
        model_invocation_count=_int_or_none(loop_result.get("model_invocation_count")),
        elapsed_seconds=elapsed_seconds,
        total_cost_usd_status=cost_status,
        total_cost_usd_value=cost_value,
        total_input_tokens_status=input_tokens_status,
        total_input_tokens_value=input_tokens_value,
        total_output_tokens_status=output_tokens_status,
        total_output_tokens_value=output_tokens_value,
        scope_violation=scope_violation,
        evidence_complete=completeness.complete,
        missing_evidence_paths=completeness.missing_paths,
    )


# ---------------------------------------------------------------------------
# Owner-supplied facts and the combined record.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class OwnerSuppliedFacts:
    owner_disposition: str
    active_human_seconds: int
    owner_interruptions: int
    clarification_count: int
    manual_edit_lines: int
    owner_note: str | None
    escaped_defect_reported: str


def build_evaluation_record(
    *,
    evidence_root: Path,
    owner: OwnerSuppliedFacts,
    runtime_commit: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The single non-authoritative merge point: Runtime-derived facts
    (read-only) + Owner-supplied facts -> one DOGFOOD_EVALUATION_V0 record.
    Never validates the two sources against each other -- a Runtime FAILED
    run and an Owner ACCEPTED_WITH_MANUAL_EDIT disposition are both
    recorded exactly as given (Section 16.D)."""

    facts = extract_runtime_derived_facts(evidence_root)

    accepted = owner.owner_disposition in (
        OwnerDisposition.ACCEPTED.value,
        OwnerDisposition.ACCEPTED_WITH_MANUAL_EDIT.value,
    )
    accepted_without_manual_edit = (
        owner.owner_disposition == OwnerDisposition.ACCEPTED.value and owner.manual_edit_lines == 0
    )
    active_human_minutes_to_confident_accept = (
        owner.active_human_seconds / 60.0 if accepted else None
    )

    evaluated_at = (now or datetime.now(timezone.utc)).isoformat()

    return {
        # Identity (tool-generated: schema_version/evaluation_id/evaluated_at;
        # RUNTIME_DERIVED: run_id/task_contract_sha256/workspace_baseline;
        # mixed tool-detected/owner-overridable: runtime_commit).
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": str(uuid.uuid4()),
        "run_id": facts.run_id,
        "task_contract_sha256": facts.task_contract_sha256,
        "runtime_commit": runtime_commit,
        "workspace_baseline": facts.workspace_baseline,
        "evaluated_at": evaluated_at,
        # OWNER_SUPPLIED.
        "owner_disposition": owner.owner_disposition,
        "active_human_seconds": owner.active_human_seconds,
        "owner_interruptions": owner.owner_interruptions,
        "clarification_count": owner.clarification_count,
        "manual_edit_lines": owner.manual_edit_lines,
        "owner_note": owner.owner_note,
        # DERIVED_FROM_OWNER_AND_RUNTIME.
        "accepted": accepted,
        "accepted_without_manual_edit": accepted_without_manual_edit,
        "active_human_minutes_to_confident_accept": active_human_minutes_to_confident_accept,
        # RUNTIME_DERIVED.
        "runtime_terminal_outcome": facts.runtime_terminal_outcome,
        "runtime_stop_reason": facts.runtime_stop_reason,
        "runtime_accepted": facts.runtime_accepted,
        "implementation_attempt_count": facts.implementation_attempt_count,
        "repair_count": facts.repair_count,
        "review_count": facts.review_count,
        "model_invocation_count": facts.model_invocation_count,
        "elapsed_seconds": facts.elapsed_seconds,
        "total_cost_usd": {"status": facts.total_cost_usd_status, "value": facts.total_cost_usd_value},
        "total_input_tokens": {"status": facts.total_input_tokens_status, "value": facts.total_input_tokens_value},
        "total_output_tokens": {"status": facts.total_output_tokens_status, "value": facts.total_output_tokens_value},
        "scope_violation": facts.scope_violation,
        "evidence_complete": facts.evidence_complete,
        "missing_evidence_paths": list(facts.missing_evidence_paths),
        # OWNER_SUPPLIED (defaults to UNKNOWN -- never inferred from Review2).
        "escaped_defect_reported": owner.escaped_defect_reported,
    }


# ---------------------------------------------------------------------------
# Output: one immutable per-run record, plus append-only aggregation.
# ---------------------------------------------------------------------------


def evaluation_output_paths(evidence_root: Path, run_id: str) -> tuple[Path, Path]:
    evaluations_dir = evidence_root.parent / "evaluations"
    record_path = evaluations_dir / f"{run_id}.evaluation.json"
    jsonl_path = evaluations_dir / "dogfood-evaluations-v0.jsonl"
    return record_path, jsonl_path


def write_evaluation_record(evidence_root: Path, record: dict[str, Any]) -> tuple[Path, Path]:
    """Write the one immutable per-run JSON artifact and append it to the
    aggregation JSONL. Never overwrites an existing per-run record
    (Section 11/16.H) -- for Batch 1, evaluation records are immutable."""

    run_id = record.get("run_id")
    if not run_id:
        raise EvaluationError("MISSING_RUN_ID", "loop-result.json did not provide a usable loop_run_id")
    record_path, jsonl_path = evaluation_output_paths(evidence_root, run_id)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    if record_path.exists():
        raise EvaluationError(
            "EVALUATION_ALREADY_EXISTS",
            f"an evaluation record already exists for run {run_id!r}: {record_path}",
        )
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record_path, jsonl_path


# ---------------------------------------------------------------------------
# Tiny deterministic Batch-1 summary helper (Section 14). Arithmetic only --
# no dashboard, no visualization, no statistical framework.
# ---------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_evaluations(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"total_evaluations": 0}

    accepted = [r for r in records if r.get("accepted")]
    accepted_no_edit = [r for r in records if r.get("accepted_without_manual_edit")]
    first_pass = [r for r in records if r.get("repair_count") == 0]
    repaired = [r for r in records if (r.get("repair_count") or 0) > 0]
    evidence_complete = [r for r in records if r.get("evidence_complete")]
    scope_violations = [r for r in records if r.get("scope_violation") == BoolTriState.TRUE.value]
    escaped_defects = [r for r in records if r.get("escaped_defect_reported") == BoolTriState.TRUE.value]
    human_minutes = [
        r["active_human_minutes_to_confident_accept"]
        for r in accepted
        if isinstance(r.get("active_human_minutes_to_confident_accept"), (int, float))
    ]
    known_costs = [
        r["total_cost_usd"]["value"]
        for r in accepted
        if isinstance(r.get("total_cost_usd"), dict) and r["total_cost_usd"].get("status") == ObservabilityState.KNOWN.value
    ]

    return {
        "total_evaluations": total,
        "owner_acceptance_rate": len(accepted) / total,
        "accepted_without_manual_edit_rate": len(accepted_no_edit) / total,
        "first_pass_success_rate": len(first_pass) / total,
        "repair_rate": len(repaired) / total,
        "average_implementation_attempts": sum(r.get("implementation_attempt_count") or 0 for r in records) / total,
        "average_review_count": sum(r.get("review_count") or 0 for r in records) / total,
        "median_active_human_minutes_to_confident_accept": _median(human_minutes),
        "total_owner_interruptions": sum(r.get("owner_interruptions") or 0 for r in records),
        "total_clarification_count": sum(r.get("clarification_count") or 0 for r in records),
        "total_manual_edit_lines": sum(r.get("manual_edit_lines") or 0 for r in records),
        "scope_violation_rate": len(scope_violations) / total,
        "evidence_completeness_rate": len(evidence_complete) / total,
        "cost_known_accepted_task_count": len(known_costs),
        "average_cost_usd_per_accepted_task_when_known": (sum(known_costs) / len(known_costs)) if known_costs else None,
        "escaped_defect_reported_count": len(escaped_defects),
    }


def _load_all_records(evaluations_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(evaluations_dir.glob("*.evaluation.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _detect_runtime_commit() -> str | None:
    """Best-effort: the Coordinator repository commit this evaluator tool
    itself is running from. This is metadata about the TOOL, not something
    read from the run's own Runtime evidence (which does not record it) --
    documented explicitly in DOGFOOD_EVALUATION_TELEMETRY_V0.md. Overridable
    via --runtime-commit; never guessed if git is unavailable."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DOGFOOD_EVALUATION_V0: record observational evaluation telemetry for one dogfood run_loop.py run."
    )
    parser.add_argument("--evidence-root", metavar="PATH")
    parser.add_argument("--owner-disposition", choices=[d.value for d in OwnerDisposition])
    parser.add_argument("--active-human-seconds", type=int)
    parser.add_argument("--owner-interruptions", type=int)
    parser.add_argument("--clarification-count", type=int)
    parser.add_argument("--manual-edit-lines", type=int)
    parser.add_argument(
        "--escaped-defect-reported",
        choices=[s.value for s in BoolTriState],
        default=BoolTriState.UNKNOWN.value,
    )
    parser.add_argument("--owner-note", default=None)
    parser.add_argument("--runtime-commit", default=None)
    parser.add_argument("--summarize", metavar="EVALUATIONS_DIR", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.summarize:
        evaluations_dir = Path(args.summarize).expanduser()
        if not evaluations_dir.is_dir():
            print("STATUS=SUMMARY_FAILURE:EVALUATIONS_DIRECTORY_MISSING")
            print(f"MESSAGE=not a directory: {evaluations_dir}")
            return 3
        records = _load_all_records(evaluations_dir)
        print(json.dumps(summarize_evaluations(records), indent=2, sort_keys=True))
        return 0

    required = {
        "--evidence-root": args.evidence_root,
        "--owner-disposition": args.owner_disposition,
        "--active-human-seconds": args.active_human_seconds,
        "--owner-interruptions": args.owner_interruptions,
        "--clarification-count": args.clarification_count,
        "--manual-edit-lines": args.manual_edit_lines,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        print("STATUS=CONFIG_FAILURE:MISSING_REQUIRED_ARGUMENT")
        print(f"MESSAGE=missing required argument(s): {', '.join(missing)}")
        return 2

    for name, value in (
        ("--active-human-seconds", args.active_human_seconds),
        ("--owner-interruptions", args.owner_interruptions),
        ("--clarification-count", args.clarification_count),
        ("--manual-edit-lines", args.manual_edit_lines),
    ):
        if value < 0:
            print("STATUS=CONFIG_FAILURE:NEGATIVE_ARGUMENT")
            print(f"MESSAGE={name} must be >= 0, got {value}")
            return 2

    evidence_root = Path(args.evidence_root).expanduser()
    if not evidence_root.is_absolute():
        evidence_root = (Path.cwd() / evidence_root).resolve(strict=False)
    else:
        evidence_root = evidence_root.resolve(strict=False)

    owner = OwnerSuppliedFacts(
        owner_disposition=args.owner_disposition,
        active_human_seconds=args.active_human_seconds,
        owner_interruptions=args.owner_interruptions,
        clarification_count=args.clarification_count,
        manual_edit_lines=args.manual_edit_lines,
        owner_note=args.owner_note,
        escaped_defect_reported=args.escaped_defect_reported,
    )
    runtime_commit = args.runtime_commit or _detect_runtime_commit()

    try:
        record = build_evaluation_record(evidence_root=evidence_root, owner=owner, runtime_commit=runtime_commit)
        record_path, jsonl_path = write_evaluation_record(evidence_root, record)
    except EvaluationError as exc:
        print(f"STATUS=EVALUATION_FAILURE:{exc.category}")
        print(f"MESSAGE={exc.message}")
        return 3

    print("STATUS=EVALUATION_RECORDED")
    print(f"RUN_ID={record['run_id']}")
    print(f"EVALUATION_ID={record['evaluation_id']}")
    print(f"ACCEPTED={str(record['accepted']).lower()}")
    print(f"EVIDENCE_COMPLETE={str(record['evidence_complete']).lower()}")
    print(f"RECORD_PATH={record_path}")
    print(f"JSONL_PATH={jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
