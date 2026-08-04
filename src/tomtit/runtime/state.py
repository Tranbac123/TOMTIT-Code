"""Canonical runtime state for the bounded TOMTIT fake loop."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from tomtit.domain.contracts import (
    DomainModel,
    StopDecision,
    TaskContract,
    VerificationPlan,
    WorkerResult,
    _list_to_tuple,
)
from tomtit.domain.enums import (
    CriterionStatus,
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

        evaluation_ids = [item.criterion_id for item in self.criterion_evaluations]
        if len(evaluation_ids) != len(set(evaluation_ids)):
            raise ValueError("criterion evaluations must have unique criterion IDs")

        if self.state is RunState.COMPLETED:
            if self.observed_patch is None:
                raise ValueError("COMPLETED requires observed patch evidence")
            if (
                not self.observed_patch.diff_exists
                or not self.observed_patch.evidence_complete
                or self.observed_patch.scope_violations
            ):
                raise ValueError("COMPLETED requires complete in-scope patch evidence")
            if any(item.run_id != self.run_id for item in self.observed_checks):
                raise ValueError("COMPLETED observed checks must belong to the canonical run")
            if any(
                item.attempt_number != self.attempt_count for item in self.observed_checks
            ):
                raise ValueError("COMPLETED observed checks must belong to canonical attempt")
            if self.observed_patch.run_id != self.run_id:
                raise ValueError("COMPLETED observed patch must belong to the canonical run")
            if self.observed_patch.attempt_number != self.attempt_count:
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
