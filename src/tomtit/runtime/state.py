"""Canonical runtime state for the bounded TOMTIT fake loop."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from tomtit.domain.contracts import (
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


class CriterionEvaluation(DomainModel):
    """Canonical status derived from the criterion's authoritative method."""

    criterion_id: str = Field(min_length=1)
    status: CriterionStatus
    evidence_refs: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )


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
    stop_decision: StopDecision | None = None
    outcome: Outcome | None = None
    stop_reason: StopReason | None = None
    verification_status: VerificationStatus | None = None
    completion_basis: CompletionBasis | None = None
    report_path: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_runtime_snapshot(self) -> RunRecord:
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
        return self


def validate_completed_record(record: RunRecord) -> RunRecord:
    """Prove every completion invariant and return the revalidated canonical record.

    This is the single completion gate. Runtime calls it before it commits the
    transition into COMPLETED, and the terminal-report boundary calls it before it
    reads any report field, so neither side can drift from the other.

    Structural invariants live in :meth:`RunRecord.validate_runtime_snapshot` and are
    re-proven here by revalidating a detached dump, which defeats unsafe
    ``model_copy(update=...)`` tampering. Filesystem-backed artifact proofs cannot
    live in the model validator, so they run here against the revalidated record.
    """

    validated = RunRecord.model_validate(record.model_dump(mode="python"))

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
