"""Canonical runtime state for the bounded TOMTIT fake loop."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationInfo, model_validator

from tomtit.domain.contracts import (
    AcceptanceCriterion,
    DomainModel,
    Finding,
    RequiredCheck,
    StopDecision,
    TaskContract,
    VerificationPlan,
    WorkerResult,
    _list_to_tuple,
)
from tomtit.domain.enums import (
    CriterionStatus,
    FindingSeverity,
    NextAction,
    Outcome,
    RunState,
    ScopeOperation,
    StopReason,
    VerificationStatus,
)


class TransitionReason(str, Enum):
    """Closed transition vocabulary used by the Commit 2 state machine."""

    CONTRACT_RESOLVED = "CONTRACT_RESOLVED"
    WORKSPACE_PREPARED = "WORKSPACE_PREPARED"
    WORKER_ASSIGNED = "WORKER_ASSIGNED"
    WORKER_FINISHED = "WORKER_FINISHED"
    EVIDENCE_READY = "EVIDENCE_READY"
    REVISION_APPROVED = "REVISION_APPROVED"
    TERMINATION_DECIDED = "TERMINATION_DECIDED"


class CompletionBasis(str, Enum):
    """Completion basis supported by the current fake deterministic path."""

    DETERMINISTIC_EVIDENCE = "DETERMINISTIC_EVIDENCE"


class ObservedCheckResult(DomainModel):
    """Runtime-observed result of one required check."""

    run_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    argv: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(min_length=1)
    cwd_relative: str = Field(min_length=1)
    exit_code: int
    duration_seconds: float = Field(ge=0)
    stdout_path: str = Field(min_length=1)
    stderr_path: str = Field(min_length=1)

    @property
    def snapshot_id(self) -> str:
        """Stable evidence identity bound to this run, attempt, and check."""

        return f"check:{self.run_id}:{self.attempt_number}:{self.check_id}"


class ObservedChangedFile(DomainModel):
    """One changed path observed by the fake evidence collector."""

    path: str = Field(min_length=1)
    operation: ScopeOperation


class ObservedPatchEvidence(DomainModel):
    """Fake but Runtime-observed patch evidence for Scenario 1."""

    run_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    changed_files: Annotated[
        tuple[ObservedChangedFile, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)
    diff_exists: bool
    scope_violations: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    evidence_complete: bool
    artifact_path: str = Field(min_length=1)

    @property
    def snapshot_id(self) -> str:
        """Stable evidence identity bound to this run and attempt."""

        return f"patch:{self.run_id}:{self.attempt_number}"


class CriterionEvaluation(DomainModel):
    """Canonical status derived from the criterion's authoritative method."""

    criterion_id: str = Field(min_length=1)
    status: CriterionStatus
    evidence_refs: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )


class AttemptEvidence(DomainModel):
    """Immutable archived evidence for one finished attempt.

    Scenario 2 needs Attempt 1 evidence to stay auditable after Attempt 2 starts,
    and needs machine-checkable counters for objective improvement. Both come
    from observed evidence only; Worker claims never reach this model.

    `run_id` is an explicit field, not something inferred from a snapshot-ID
    string, so archived evidence can be bound to its run at every trust boundary.
    """

    run_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    observed_checks: Annotated[
        tuple[ObservedCheckResult, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)
    observed_patch: ObservedPatchEvidence | None = None
    criterion_evaluations: Annotated[
        tuple[CriterionEvaluation, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    failed_check_ids: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    satisfied_criterion_ids: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    stop_decision: StopDecision | None = None
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def validate_attempt_binding(self) -> AttemptEvidence:
        for item in self.observed_checks:
            if item.run_id != self.run_id:
                raise ValueError("archived checks must belong to the archived run")
            if item.attempt_number != self.attempt_number:
                raise ValueError("archived checks must belong to the archived attempt")
            if item.snapshot_id != f"check:{self.run_id}:{self.attempt_number}:{item.check_id}":
                raise ValueError("archived check snapshot identity is inconsistent")
        if self.observed_patch is not None:
            if self.observed_patch.run_id != self.run_id:
                raise ValueError("archived patch must belong to the archived run")
            if self.observed_patch.attempt_number != self.attempt_number:
                raise ValueError("archived patch must belong to the archived attempt")
        observed_ids = {item.check_id for item in self.observed_checks}
        if not set(self.failed_check_ids) <= observed_ids:
            raise ValueError("archived failed checks must reference archived observations")
        evaluated_id_list = [item.criterion_id for item in self.criterion_evaluations]
        if len(evaluated_id_list) != len(set(evaluated_id_list)):
            raise ValueError("archived criterion evaluations must have unique criterion IDs")
        evaluated_ids = set(evaluated_id_list)
        if not set(self.satisfied_criterion_ids) <= evaluated_ids:
            raise ValueError("archived satisfied criteria must reference archived evaluations")
        return self

    @property
    def evidence_snapshot_ids(self) -> tuple[str, ...]:
        ids = [item.snapshot_id for item in self.observed_checks]
        if self.observed_patch is not None:
            ids.append(self.observed_patch.snapshot_id)
        return tuple(ids)


def archived_check_membership_error(
    required_checks: tuple[RequiredCheck, ...],
    observed_checks: tuple[ObservedCheckResult, ...],
) -> str | None:
    """Every observation must name one distinct canonical required check.

    Membership is proven before any failure derivation. An unknown check ID must
    never fall through to an empty expected-exit-code tuple, because that would
    make an invented observation look like a genuine failure.
    """

    known = {check.id for check in required_checks}
    seen_check_ids: set[str] = set()
    seen_snapshot_ids: set[str] = set()
    for item in observed_checks:
        if item.check_id not in known:
            return (
                f"archived observation names unknown required check {item.check_id!r}; "
                f"canonical required checks are {sorted(known)}"
            )
        if item.check_id in seen_check_ids:
            return f"archived observations duplicate required check {item.check_id!r}"
        if item.snapshot_id in seen_snapshot_ids:
            return f"archived observations duplicate snapshot id {item.snapshot_id!r}"
        seen_check_ids.add(item.check_id)
        seen_snapshot_ids.add(item.snapshot_id)
    return None


def archived_criterion_membership_error(
    acceptance_criteria: tuple[AcceptanceCriterion, ...],
    criterion_evaluations: tuple[CriterionEvaluation, ...],
) -> str | None:
    """Every archived evaluation must name one distinct canonical criterion."""

    known = {criterion.id for criterion in acceptance_criteria}
    seen: set[str] = set()
    for item in criterion_evaluations:
        if item.criterion_id not in known:
            return (
                f"archived evaluation names unknown acceptance criterion "
                f"{item.criterion_id!r}; canonical criteria are {sorted(known)}"
            )
        if item.criterion_id in seen:
            return f"archived evaluations duplicate acceptance criterion {item.criterion_id!r}"
        seen.add(item.criterion_id)
    return None


def canonical_failed_required_check_ids(
    required_checks: tuple[RequiredCheck, ...],
    observed_checks: tuple[ObservedCheckResult, ...],
) -> tuple[str, ...]:
    """Failed check IDs, derived by iterating the contract in canonical order.

    Iterating the contract rather than the observations means the result can only
    ever contain canonical IDs, cannot contain duplicates, and cannot be reordered
    by whoever supplied the archive.
    """

    observed_by_id = {item.check_id: item for item in observed_checks}
    failed: list[str] = []
    for check in required_checks:
        observed = observed_by_id.get(check.id)
        if observed is not None and observed.exit_code not in check.expected_exit_codes:
            failed.append(check.id)
    return tuple(failed)


def canonical_satisfied_criterion_ids(
    acceptance_criteria: tuple[AcceptanceCriterion, ...],
    criterion_evaluations: tuple[CriterionEvaluation, ...],
) -> tuple[str, ...]:
    """Satisfied criterion IDs, derived by iterating the contract in canonical order."""

    evaluated_by_id = {item.criterion_id: item for item in criterion_evaluations}
    satisfied: list[str] = []
    for criterion in acceptance_criteria:
        evaluation = evaluated_by_id.get(criterion.id)
        if evaluation is not None and evaluation.status is CriterionStatus.SATISFIED:
            satisfied.append(criterion.id)
    return tuple(satisfied)


def attempt_evidence_from_observations(
    *,
    run_id: str,
    attempt_number: int,
    required_checks: tuple[RequiredCheck, ...],
    observed_checks: tuple[ObservedCheckResult, ...],
    observed_patch: ObservedPatchEvidence | None,
    criterion_evaluations: tuple[CriterionEvaluation, ...],
    acceptance_criteria: tuple[AcceptanceCriterion, ...] | None = None,
    stop_decision: StopDecision | None = None,
    findings: tuple[Finding, ...] = (),
) -> AttemptEvidence:
    """Derive one attempt's archived evidence from observed results only.

    The canonical run ID is supplied explicitly and every observation must agree
    with it, so a foreign-run observation cannot be archived. Derived tuples come
    from iterating the canonical contract, never the supplied evidence.

    `acceptance_criteria` is optional only because Runtime's writer call site
    supplies criterion evaluations it built by iterating the contract itself, so
    those are already canonical members in canonical order. Every record it
    produces is still re-proven against the full contract by
    :func:`revision_attempt_archive_error` during `RunRecord` validation, so the
    writer can only produce a strict subset of what the validator accepts.
    """

    for item in observed_checks:
        if item.run_id != run_id:
            raise ValueError("observed checks must belong to the canonical run")
    if observed_patch is not None and observed_patch.run_id != run_id:
        raise ValueError("observed patch must belong to the canonical run")

    membership_error = archived_check_membership_error(required_checks, observed_checks)
    if membership_error is not None:
        raise ValueError(membership_error)
    if acceptance_criteria is not None:
        criterion_error = archived_criterion_membership_error(
            acceptance_criteria, criterion_evaluations
        )
        if criterion_error is not None:
            raise ValueError(criterion_error)

    failed = canonical_failed_required_check_ids(required_checks, observed_checks)
    if acceptance_criteria is None:
        evaluated_ids = [item.criterion_id for item in criterion_evaluations]
        if len(evaluated_ids) != len(set(evaluated_ids)):
            raise ValueError("criterion evaluations must have unique criterion IDs")
        satisfied = tuple(
            item.criterion_id
            for item in criterion_evaluations
            if item.status is CriterionStatus.SATISFIED
        )
    else:
        satisfied = canonical_satisfied_criterion_ids(
            acceptance_criteria, criterion_evaluations
        )
    return AttemptEvidence(
        run_id=run_id,
        attempt_number=attempt_number,
        observed_checks=observed_checks,
        observed_patch=observed_patch,
        criterion_evaluations=criterion_evaluations,
        failed_check_ids=failed,
        satisfied_criterion_ids=satisfied,
        stop_decision=stop_decision,
        findings=findings,
    )


REPAIRABLE_FINDING_CATEGORY = "deterministic_check_failure"


def repairable_check_finding(
    *,
    run_id: str,
    attempt_number: int,
    check: RequiredCheck,
    observed: ObservedCheckResult,
    acceptance_criterion_id: str | None,
) -> Finding:
    """Derive the one canonical repairable finding for a failed required check.

    Identity and content are a deterministic function of canonical Runtime truth,
    so the same failure always yields the same finding and a finding built for a
    different run, attempt, or check can never compare equal.
    """

    return Finding(
        finding_id=f"finding:{run_id}:{attempt_number}:{check.id}",
        fingerprint=f"{REPAIRABLE_FINDING_CATEGORY}:{check.id}",
        severity=FindingSeverity.BLOCKING,
        category=REPAIRABLE_FINDING_CATEGORY,
        message=(
            f"required check {check.id} exited {observed.exit_code}; "
            f"expected one of {list(check.expected_exit_codes)}"
        ),
        evidence_refs=(
            observed.snapshot_id,
            observed.stdout_path,
            observed.stderr_path,
        ),
        acceptance_criterion_id=acceptance_criterion_id,
    )


def first_failed_required_check(
    required_checks: tuple[RequiredCheck, ...],
    observed_checks: tuple[ObservedCheckResult, ...],
) -> tuple[RequiredCheck, ObservedCheckResult] | None:
    """Select the failed check deterministically, in canonical contract order."""

    observed_by_id = {item.check_id: item for item in observed_checks}
    for check in required_checks:
        observed = observed_by_id.get(check.id)
        if observed is not None and observed.exit_code not in check.expected_exit_codes:
            return check, observed
    return None


def acceptance_criterion_for_check(
    contract: TaskContract,
    check_id: str,
) -> str | None:
    """The first acceptance criterion that requires `check_id`, in contract order."""

    return next(
        (
            criterion.id
            for criterion in contract.acceptance_criteria
            if check_id in criterion.required_check_ids
        ),
        None,
    )


def canonical_repairable_branch_finding(
    *,
    contract: TaskContract,
    run_id: str,
    attempt_number: int,
    observed_checks: tuple[ObservedCheckResult, ...],
) -> Finding | None:
    """Re-derive the one repairable finding a failed attempt must carry.

    This is the single derivation used both when Runtime records the branch and
    when a trust boundary re-proves an archived branch, so an archived finding is
    never trusted merely because Runtime once built one.
    """

    selected = first_failed_required_check(contract.required_checks, observed_checks)
    if selected is None:
        return None
    check, observed = selected
    return repairable_check_finding(
        run_id=run_id,
        attempt_number=attempt_number,
        check=check,
        observed=observed,
        acceptance_criterion_id=acceptance_criterion_for_check(contract, check.id),
    )


def repairable_stop_decision_error(decision: StopDecision | None) -> str | None:
    """Return why a StopDecision is not the policy-required repairable branch."""

    if decision is None:
        return "a repairable revision branch requires a StopDecision"
    if decision.next_action is not NextAction.REVISE:
        return "the repairable branch requires StopDecision.next_action=revise"
    if not decision.repairable:
        return "the repairable branch requires StopDecision.repairable=true"
    if decision.owner_input_required:
        return "the repairable branch requires owner_input_required=false"
    if decision.outcome is not None or decision.stop_reason is not None:
        return "a repairable branch StopDecision must not set outcome or stop reason"
    return None


def objective_improvement_error(
    previous: AttemptEvidence,
    current: AttemptEvidence,
) -> str | None:
    """Return why `current` fails to objectively improve on `previous`, or None.

    Only observed evidence counts. A changed patch or a Worker claim of success
    proves nothing on its own.
    """

    if current.attempt_number <= previous.attempt_number:
        return "a later attempt must have a higher attempt number"

    reused = sorted(
        set(current.evidence_snapshot_ids) & set(previous.evidence_snapshot_ids)
    )
    if reused:
        return f"attempt {current.attempt_number} reuses earlier evidence: {reused}"

    if not previous.failed_check_ids:
        return "objective improvement requires a previous attempt with a failed check"
    if current.failed_check_ids:
        return (
            f"attempt {current.attempt_number} still has failed required checks: "
            f"{list(current.failed_check_ids)}"
        )
    if len(current.satisfied_criterion_ids) <= len(previous.satisfied_criterion_ids):
        return (
            f"attempt {current.attempt_number} did not increase satisfied criteria "
            f"({len(previous.satisfied_criterion_ids)} -> "
            f"{len(current.satisfied_criterion_ids)})"
        )
    return None


def revision_attempt_archive_error(
    *,
    run_id: str,
    current_attempt: int,
    contract: TaskContract,
    archive: AttemptEvidence,
) -> str | None:
    """Re-prove one archived revision attempt from canonical truth, or explain why not.

    An archive is canonical run state, so a copied record must not be believed
    just because its identifiers look plausible. Every derived tuple is
    recomputed from the archived observations and the canonical contract and
    compared for exact equality, and the branch result is re-derived rather than
    trusted. This is the single definition used by every validation boundary.
    """

    if archive.run_id != run_id:
        return "archived attempt must belong to the canonical run"
    if archive.attempt_number >= current_attempt:
        return "an archived attempt must precede the current attempt"

    for item in archive.observed_checks:
        if item.run_id != run_id or item.attempt_number != archive.attempt_number:
            return "archived observations must belong to the archived run and attempt"
    if archive.observed_patch is not None and (
        archive.observed_patch.run_id != run_id
        or archive.observed_patch.attempt_number != archive.attempt_number
    ):
        return "archived patch must belong to the archived run and attempt"

    # Canonical membership is proven before anything is derived, so a coordinated
    # forgery cannot supply both an invented observation and a matching derived ID.
    check_membership_error = archived_check_membership_error(
        contract.required_checks, archive.observed_checks
    )
    if check_membership_error is not None:
        return check_membership_error
    observed_ids = {item.check_id for item in archive.observed_checks}
    required_ids = {check.id for check in contract.required_checks}
    if observed_ids != required_ids:
        return (
            f"archived observations must cover every required check: "
            f"missing={sorted(required_ids - observed_ids)}"
        )
    criterion_membership_error = archived_criterion_membership_error(
        contract.acceptance_criteria, archive.criterion_evaluations
    )
    if criterion_membership_error is not None:
        return criterion_membership_error

    # Recompute every derived tuple from the canonical contract; exact equality,
    # never subset, and never in an archive-controlled order.
    derived = attempt_evidence_from_observations(
        run_id=run_id,
        attempt_number=archive.attempt_number,
        required_checks=contract.required_checks,
        observed_checks=archive.observed_checks,
        observed_patch=archive.observed_patch,
        criterion_evaluations=archive.criterion_evaluations,
        acceptance_criteria=contract.acceptance_criteria,
    )
    if archive.failed_check_ids != derived.failed_check_ids:
        return (
            f"archived failed_check_ids {list(archive.failed_check_ids)} do not match "
            f"the checks derived from archived evidence {list(derived.failed_check_ids)}"
        )
    if archive.satisfied_criterion_ids != derived.satisfied_criterion_ids:
        return (
            f"archived satisfied_criterion_ids {list(archive.satisfied_criterion_ids)} "
            f"do not match the criteria derived from archived evaluations "
            f"{list(derived.satisfied_criterion_ids)}"
        )
    if not derived.failed_check_ids:
        return "a revision source attempt must have at least one failed required check"

    decision_error = repairable_stop_decision_error(archive.stop_decision)
    if decision_error is not None:
        return f"archived attempt {archive.attempt_number}: {decision_error}"

    expected_finding = canonical_repairable_branch_finding(
        contract=contract,
        run_id=run_id,
        attempt_number=archive.attempt_number,
        observed_checks=archive.observed_checks,
    )
    if expected_finding is None:  # pragma: no cover - guarded by derived.failed_check_ids
        return "a revision source attempt must have at least one failed required check"
    if len(archive.findings) != 1:
        return (
            f"an archived revision attempt requires exactly one repairable finding, "
            f"found {len(archive.findings)}"
        )
    if archive.findings[0] != expected_finding:
        return (
            "the archived finding does not match the canonical repairable finding for "
            f"run {run_id} attempt {archive.attempt_number}"
        )
    return None


TASK_CONTRACT_HASH_SCHEMA_VERSION = "tomtit.task_contract_hash.v1"

# Validation-context key carrying the trusted contract hash. The value must come
# from Runtime authority, never from the record payload being validated.
TRUSTED_CONTRACT_HASH_CONTEXT_KEY = "expected_task_contract_hash"


def trusted_contract_context(expected_task_contract_hash: str) -> dict[str, str]:
    """Build the validation context a revised record must be validated against."""

    return {TRUSTED_CONTRACT_HASH_CONTEXT_KEY: expected_task_contract_hash}


def task_contract_hash(contract: TaskContract) -> str:
    """SHA-256 over a versioned canonical envelope of the contract.

    The hashed bytes name their own schema version, so a future change to how the
    contract is canonicalized produces a different hash instead of silently
    colliding with values produced by this version.
    """

    envelope = {
        "schema_version": TASK_CONTRACT_HASH_SCHEMA_VERSION,
        "task_contract": json.loads(contract.model_dump_json()),
    }
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class CompletionInvariantError(ValueError):
    """Raised when a record cannot prove the canonical completion invariants."""


def required_check_coverage_error(
    required_checks: tuple[RequiredCheck, ...],
    observed_checks: tuple[ObservedCheckResult, ...],
) -> str | None:
    """Return why required-check coverage fails completion, or None when it holds.

    Runtime and the terminal-report boundary share this single definition so a
    Worker claim can never substitute for exactly one passing observed result.
    """

    observed_by_check: dict[str, list[ObservedCheckResult]] = {}
    for observed in observed_checks:
        observed_by_check.setdefault(observed.check_id, []).append(observed)

    required_ids = {check.id for check in required_checks}
    if not required_ids:
        return "deterministic completion requires at least one required check"
    if set(observed_by_check) != required_ids:
        missing = sorted(required_ids - set(observed_by_check))
        unexpected = sorted(set(observed_by_check) - required_ids)
        return (
            "observed results must cover exactly the required checks: "
            f"missing={missing}, unexpected={unexpected}"
        )
    for check in required_checks:
        results = observed_by_check[check.id]
        if len(results) != 1:
            return f"required check {check.id} has {len(results)} observed results, expected 1"
        observed = results[0]
        # The contract, not the observation, is authoritative about which command
        # a check ID names. A matching ID must not silently redefine it.
        if observed.argv != check.argv:
            return (
                f"required check {check.id} observed argv {list(observed.argv)} "
                f"does not match the contract argv {list(check.argv)}"
            )
        if observed.cwd_relative != check.cwd_relative:
            return (
                f"required check {check.id} observed cwd_relative "
                f"{observed.cwd_relative!r} does not match the contract "
                f"cwd_relative {check.cwd_relative!r}"
            )
        if observed.exit_code not in check.expected_exit_codes:
            return (
                f"required check {check.id} observed exit code {observed.exit_code} "
                f"is not an expected exit code"
            )
    return None


def patch_evidence_error(evidence: ObservedPatchEvidence | None) -> str | None:
    """Return why patch evidence cannot support completion, or None when it can."""

    if evidence is None:
        return "completion requires observed patch evidence"
    if not evidence.evidence_complete:
        return "completion requires evidence_complete patch evidence"
    if not evidence.diff_exists:
        return "completion requires patch evidence proving a diff exists"
    if evidence.scope_violations:
        return "completion requires patch evidence without scope violations"
    return None


def artifact_file_error(raw_path: str, label: str) -> str | None:
    """Return why an evidence artifact is unusable, or None when it is a regular file."""

    if not Path(raw_path).is_file():
        return f"{label} must exist and be a regular file"
    return None


class RunRecord(DomainModel):
    """Immutable snapshot of the canonical run state owned by Runtime."""

    run_id: str = Field(min_length=1)
    task_contract: TaskContract
    verification_plan: VerificationPlan
    state: RunState = RunState.CREATED
    attempt_count: int = Field(default=0, ge=0, le=3)
    worker_result: WorkerResult | None = None
    observed_checks: Annotated[
        tuple[ObservedCheckResult, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    observed_patch: ObservedPatchEvidence | None = None
    criterion_evaluations: Annotated[
        tuple[CriterionEvaluation, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    unresolved_findings: Annotated[
        tuple[Finding, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    attempt_history: Annotated[
        tuple[AttemptEvidence, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    stop_decision: StopDecision | None = None
    outcome: Outcome | None = None
    stop_reason: StopReason | None = None
    verification_status: VerificationStatus | None = None
    completion_basis: CompletionBasis | None = None
    report_path: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_runtime_snapshot(self, info: ValidationInfo) -> RunRecord:
        if self.attempt_count > self.task_contract.hard_max_attempts:
            raise ValueError("attempt_count cannot exceed the contract hard limit")

        terminal_states = {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.BUDGET_EXHAUSTED,
            RunState.CANCELLED,
        }
        if self.state in terminal_states:
            if self.outcome is None or self.stop_reason is None or self.stop_decision is None:
                raise ValueError("terminal state requires outcome, stop reason, and stop decision")
            if self.completed_at is None:
                raise ValueError("terminal state requires completed_at")
            if self.outcome is not self.stop_decision.outcome:
                raise ValueError("terminal outcome must match StopDecision outcome")
            if self.stop_reason is not self.stop_decision.stop_reason:
                raise ValueError("terminal stop reason must match StopDecision stop reason")
        elif any(
            value is not None
            for value in (self.outcome, self.stop_reason, self.completion_basis, self.completed_at)
        ):
            raise ValueError("non-terminal state cannot contain terminal outcome fields")

        # F-003: Attempt 1 has no previous ProgressSnapshot, so it can never be
        # canonicalized as NO_PROGRESS. Enforced here, independently of Runtime
        # reachability, so direct construction, revalidation at a trust boundary,
        # and report generation all reject the same invalid record.
        declared_stop_reasons = {self.stop_reason}
        if self.stop_decision is not None:
            declared_stop_reasons.add(self.stop_decision.stop_reason)
        if StopReason.NO_PROGRESS in declared_stop_reasons and self.attempt_count < 2:
            raise ValueError(
                "NO_PROGRESS requires attempt_count >= 2 because Attempt 1 has no "
                "previous ProgressSnapshot to compare against"
            )

        evaluation_ids = [item.criterion_id for item in self.criterion_evaluations]
        if len(evaluation_ids) != len(set(evaluation_ids)):
            raise ValueError("criterion evaluations must have unique criterion IDs")

        archived_attempts = [item.attempt_number for item in self.attempt_history]
        if archived_attempts != sorted(set(archived_attempts)):
            raise ValueError("attempt history must be unique and strictly increasing")
        if archived_attempts and archived_attempts[-1] >= max(self.attempt_count, 1):
            raise ValueError("attempt history cannot contain the current or a later attempt")
        if self.attempt_history:
            # A revised record's own TaskContract is caller-replaceable, so the
            # archive derivations below cannot be trusted against it alone: a
            # reordered-but-equivalent contract plus consistently rewritten
            # archive fields would validate. The expected hash must therefore
            # come from outside this payload, through validation context.
            expected_hash = (info.context or {}).get(TRUSTED_CONTRACT_HASH_CONTEXT_KEY)
            if not expected_hash:
                raise ValueError(
                    "a revised run record must be validated against a trusted "
                    f"{TRUSTED_CONTRACT_HASH_CONTEXT_KEY}; validating without one is "
                    "refused because the embedded TaskContract is caller-replaceable"
                )
            embedded_hash = task_contract_hash(self.task_contract)
            if embedded_hash != expected_hash:
                raise ValueError(
                    "the embedded TaskContract does not match the canonical contract "
                    f"this run is bound to: embedded={embedded_hash}, "
                    f"expected={expected_hash}"
                )

        # Archived evidence is canonical run state, so every archived revision
        # attempt is re-proven here rather than trusted. This runs at every
        # RunRecord construction and reconstruction, which is what makes copied
        # records fail at the completion and terminal-report boundaries too.
        for archive in self.attempt_history:
            archive_error = revision_attempt_archive_error(
                run_id=self.run_id,
                current_attempt=self.attempt_count,
                contract=self.task_contract,
                archive=archive,
            )
            if archive_error is not None:
                raise ValueError(archive_error)

        if self.state is RunState.COMPLETED:
            if self.attempt_count < 1:
                raise ValueError("COMPLETED requires at least one recorded attempt")
            observed_patch = self.observed_patch
            patch_error = patch_evidence_error(observed_patch)
            if patch_error is not None or observed_patch is None:
                raise ValueError(f"COMPLETED rejected: {patch_error}")
            coverage_error = required_check_coverage_error(
                self.task_contract.required_checks,
                self.observed_checks,
            )
            if coverage_error is not None:
                raise ValueError(f"COMPLETED rejected: {coverage_error}")
            if any(
                finding.severity is FindingSeverity.BLOCKING
                for finding in self.unresolved_findings
            ):
                raise ValueError("COMPLETED requires no unresolved blocking finding")
            if any(item.run_id != self.run_id for item in self.observed_checks):
                raise ValueError("COMPLETED observed checks must belong to the canonical run")
            if any(
                item.attempt_number != self.attempt_count for item in self.observed_checks
            ):
                raise ValueError("COMPLETED observed checks must belong to canonical attempt")
            if observed_patch.run_id != self.run_id:
                raise ValueError("COMPLETED observed patch must belong to the canonical run")
            if observed_patch.attempt_number != self.attempt_count:
                raise ValueError("COMPLETED observed patch must belong to canonical attempt")
            expected_ids = {item.id for item in self.task_contract.acceptance_criteria}
            if set(evaluation_ids) != expected_ids:
                raise ValueError("COMPLETED requires one evaluation per acceptance criterion")
            if self.outcome is not Outcome.COMPLETED:
                raise ValueError("COMPLETED state requires COMPLETED outcome")
            if self.stop_reason is not StopReason.ACCEPTANCE_SATISFIED:
                raise ValueError("COMPLETED state requires ACCEPTANCE_SATISFIED")
            if self.stop_decision is None:
                raise ValueError("COMPLETED state requires StopDecision")
            if self.stop_decision.next_action is not NextAction.COMPLETE:
                raise ValueError("COMPLETED state requires next_action=complete")
            if self.stop_decision.repairable:
                raise ValueError("COMPLETED state requires repairable=false")
            if self.stop_decision.owner_input_required:
                raise ValueError("COMPLETED state requires owner_input_required=false")
            if self.verification_status is not VerificationStatus.DETERMINISTICALLY_CHECKED:
                raise ValueError("Commit 2 completion requires deterministic verification")
            if self.completion_basis is not CompletionBasis.DETERMINISTIC_EVIDENCE:
                raise ValueError("Commit 2 completion requires deterministic evidence")
            if not self.criterion_evaluations or any(
                item.status is not CriterionStatus.SATISFIED
                for item in self.criterion_evaluations
            ):
                raise ValueError("COMPLETED requires all criteria satisfied")
            if self.attempt_history:
                # Gate 2 supports exactly one revision. This bounded shape is
                # asserted only for a revised completed record; an unrevised run
                # keeps an empty history and is unaffected.
                if len(self.attempt_history) != 1:
                    raise ValueError(
                        "a revised completed run supports exactly one archived attempt"
                    )
                if self.attempt_history[0].attempt_number != 1 or self.attempt_count != 2:
                    raise ValueError(
                        "a revised completed run must archive Attempt 1 and complete Attempt 2"
                    )
                # A revised run may only complete on proven objective improvement.
                # Runs with no earlier attempt are unaffected by this rule.
                current_evidence = attempt_evidence_from_observations(
                    run_id=self.run_id,
                    attempt_number=self.attempt_count,
                    required_checks=self.task_contract.required_checks,
                    observed_checks=self.observed_checks,
                    observed_patch=observed_patch,
                    criterion_evaluations=self.criterion_evaluations,
                    acceptance_criteria=self.task_contract.acceptance_criteria,
                )
                improvement_error = objective_improvement_error(
                    self.attempt_history[-1],
                    current_evidence,
                )
                if improvement_error is not None:
                    raise ValueError(f"COMPLETED rejected: {improvement_error}")
        return self


def validate_completed_record(
    record: RunRecord,
    *,
    expected_task_contract_hash: str | None = None,
) -> RunRecord:
    """Prove every completion invariant and return the revalidated canonical record.

    This is the single completion gate. Runtime calls it before it commits the
    transition into COMPLETED, and the terminal-report boundary calls it before it
    reads any report field, so neither side can drift from the other.

    Structural invariants live in :meth:`RunRecord.validate_runtime_snapshot` and are
    re-proven here by revalidating a detached dump, which defeats unsafe
    ``model_copy(update=...)`` tampering. Filesystem-backed artifact proofs cannot
    live in the model validator, so they run here against the revalidated record.

    `expected_task_contract_hash` is mandatory for a revised record and must come
    from Runtime authority — the hash captured when the run was created — never
    from the record being validated. An unrevised Gate 1 record does not need it.
    """

    context = (
        trusted_contract_context(expected_task_contract_hash)
        if expected_task_contract_hash is not None
        else None
    )
    validated = RunRecord.model_validate(
        record.model_dump(mode="python"),
        context=context,
    )

    if validated.state is not RunState.COMPLETED:
        raise CompletionInvariantError("completion requires COMPLETED state")

    for observed in validated.observed_checks:
        for raw_path, label in (
            (observed.stdout_path, "stdout artifact"),
            (observed.stderr_path, "stderr artifact"),
        ):
            error = artifact_file_error(raw_path, label)
            if error is not None:
                raise CompletionInvariantError(f"check {observed.check_id}: {error}")

    observed_patch = validated.observed_patch
    if observed_patch is None:
        raise CompletionInvariantError("completion requires observed patch evidence")
    patch_artifact_error = artifact_file_error(observed_patch.artifact_path, "patch artifact")
    if patch_artifact_error is not None:
        raise CompletionInvariantError(patch_artifact_error)
    return validated
