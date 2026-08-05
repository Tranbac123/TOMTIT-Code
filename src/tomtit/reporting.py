"""Immutable terminal report contract and write-once JSON persistence."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from tomtit.domain.contracts import (
    DomainModel,
    ScopeRule,
    WorkerResult,
    _list_to_tuple,
)
from tomtit.domain.enums import (
    CriterionStatus,
    FindingSeverity,
    Outcome,
    StopReason,
    VerificationMethod,
    VerificationMode,
    VerificationStatus,
)
from tomtit.runtime.state import (
    CompletionBasis,
    ObservedCheckResult,
    ObservedPatchEvidence,
    RunRecord,
    validate_completed_record,
)


class ReportCriterion(DomainModel):
    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    verification_method: VerificationMethod
    status: CriterionStatus
    evidence_refs: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )


class TerminalReport(DomainModel):
    schema_version: Literal["report.v1"] = "report.v1"
    generated_at: datetime
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    stop_reason: StopReason
    verification_status: VerificationStatus
    completion_basis: CompletionBasis
    goal: str = Field(min_length=1)
    interpreted_scope: Annotated[tuple[ScopeRule, ...], BeforeValidator(_list_to_tuple)]
    acceptance_criteria: Annotated[
        tuple[ReportCriterion, ...], BeforeValidator(_list_to_tuple)
    ]
    attempts: int = Field(ge=1)
    implementer: str = Field(min_length=1)
    verifier: str | None
    verification_mode: VerificationMode
    adapter_capability_limitations: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    worker_claims: WorkerResult
    observed_evidence: Annotated[
        tuple[ObservedCheckResult, ...], BeforeValidator(_list_to_tuple)
    ]
    observed_patch_evidence: ObservedPatchEvidence
    changed_files: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)]
    checks_executed: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)]
    scope_violations: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    blocking_findings: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    remaining_risks: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    missing_evidence: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    recommended_next_action: str = Field(min_length=1)


def build_terminal_report(
    record: RunRecord,
    *,
    generated_at: datetime,
    expected_task_contract_hash: str | None = None,
) -> TerminalReport:
    """The sole public trust boundary for building a terminal report.

    The input record is reconstructed and re-proven by the shared completion gate
    before any field is read, so tampering applied through
    ``model_copy(update=...)`` cannot reach ``report.v1.json``. Every caller
    outside Runtime's own validated completion preparation must use this function.

    A revised record additionally requires the caller to supply the trusted
    contract hash the run is bound to; without it the gate refuses to treat the
    record's own embedded TaskContract as canonical.
    """

    return _build_terminal_report_from_validated_record(
        validate_completed_record(
            record,
            expected_task_contract_hash=expected_task_contract_hash,
        ),
        generated_at=generated_at,
    )


def _build_terminal_report_from_validated_record(
    validated_record: RunRecord,
    *,
    generated_at: datetime,
) -> TerminalReport:
    """Private: build the report from a record the caller has already proven.

    This performs no completion validation and touches no filesystem, so it is
    only sound immediately after :func:`validate_completed_record` has proven the
    same record. Runtime's completion preparation is the only permitted caller;
    everyone else uses :func:`build_terminal_report`.
    """

    if validated_record.outcome is None or validated_record.stop_reason is None:
        raise ValueError("terminal report requires a terminal outcome")
    if (
        validated_record.verification_status is None
        or validated_record.completion_basis is None
    ):
        raise ValueError("terminal report requires verification and completion basis")
    if validated_record.worker_result is None:
        raise ValueError("terminal report requires WorkerResult")
    if validated_record.observed_patch is None:
        raise ValueError("terminal report requires observed patch evidence")

    evaluations = {
        item.criterion_id: item for item in validated_record.criterion_evaluations
    }
    criteria = tuple(
        ReportCriterion(
            criterion_id=criterion.id,
            description=criterion.description,
            verification_method=criterion.verification_method,
            status=evaluations[criterion.id].status,
            evidence_refs=evaluations[criterion.id].evidence_refs,
        )
        for criterion in validated_record.task_contract.acceptance_criteria
    )
    return TerminalReport(
        generated_at=generated_at,
        task_id=validated_record.task_contract.task_id,
        run_id=validated_record.run_id,
        outcome=validated_record.outcome,
        stop_reason=validated_record.stop_reason,
        verification_status=validated_record.verification_status,
        completion_basis=validated_record.completion_basis,
        goal=validated_record.task_contract.goal,
        interpreted_scope=validated_record.task_contract.allowed_scope,
        acceptance_criteria=criteria,
        attempts=validated_record.attempt_count,
        implementer="FakeWorker",
        verifier=None,
        verification_mode=validated_record.verification_plan.mode,
        adapter_capability_limitations=(
            "fake dependency; no filesystem, network, or command enforcement claims",
            "patch evidence is deterministic fixture evidence, not a real Git workspace snapshot",
        ),
        worker_claims=validated_record.worker_result,
        observed_evidence=validated_record.observed_checks,
        observed_patch_evidence=validated_record.observed_patch,
        changed_files=tuple(
            item.path for item in validated_record.observed_patch.changed_files
        ),
        checks_executed=tuple(
            item.check_id for item in validated_record.observed_checks
        ),
        scope_violations=validated_record.observed_patch.scope_violations,
        blocking_findings=tuple(
            finding.fingerprint
            for finding in validated_record.unresolved_findings
            if finding.severity is FindingSeverity.BLOCKING
        ),
        remaining_risks=validated_record.worker_result.remaining_risks,
        recommended_next_action="review generated patch artifact",
    )


STAGED_REPORT_NAME = ".report.v1.json.staged"


def stage_terminal_report(directory: Path, rendered: str) -> Path:
    """Write the complete report to a private temporary file in the final directory.

    The staged file is never the terminal report. It exists so that all report
    bytes are on disk before Runtime commits canonical completion, leaving only a
    single link operation afterwards.
    """

    directory.mkdir(parents=True, exist_ok=True)
    staged = directory / STAGED_REPORT_NAME
    with staged.open("w", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
    return staged


def publish_staged_report(staged: Path, final: Path) -> Path:
    """Create the write-once terminal report from a staged report.

    This performs exactly one filesystem operation and returns as soon as the
    final report exists. ``os.link`` either creates the final name and returns,
    or raises without creating it, so the caller can always infer from the return
    whether the terminal report now exists.

    Removing the staged file is deliberately *not* done here. A publisher that
    created the final report and then raised would leave its caller unable to
    tell that the terminal result had already been committed, which is exactly
    how a rollback could delete or contradict a committed report.
    """

    os.link(staged, final)
    return final


def render_terminal_report(report: TerminalReport) -> str:
    """Serialize a terminal report without touching the filesystem.

    Runtime renders during completion preparation so serialization cannot fail
    after canonical state has been committed.
    """

    return report.model_dump_json(indent=2) + "\n"


def write_rendered_terminal_report(path: Path, rendered: str) -> Path:
    """Persist an already rendered report exactly once."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(rendered)
    return path


def write_terminal_report(path: Path, report: TerminalReport) -> Path:
    """Persist report.v1.json exactly once; existing history is never overwritten."""

    return write_rendered_terminal_report(path, render_terminal_report(report))
