"""Canonical deterministic Runtime for Commit 2 Scenario 1 only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tomtit.domain.contracts import StopDecision, TaskContract, VerificationPlan
from tomtit.domain.enums import (
    CriterionStatus,
    EvaluationEventType,
    NextAction,
    Outcome,
    RunState,
    StopReason,
    VerificationMethod,
    VerificationMode,
    VerificationStatus,
)
from tomtit.domain.evaluation import EvaluationEvent
from tomtit.fakes import FakeCheckRunner, FakeWorker
from tomtit.reporting import build_terminal_report, write_terminal_report
from tomtit.runtime.events import InMemoryEventLog, utc_now
from tomtit.runtime.state import (
    CompletionBasis,
    CriterionEvaluation,
    ObservedCheckResult,
    ObservedPatchEvidence,
    RunRecord,
    TransitionReason,
)


class InvalidTransitionError(RuntimeError):
    """Raised when a caller attempts to bypass the canonical state machine."""


class CompletionConditionsNotMet(RuntimeError):
    """Raised when Scenario 1 cannot complete from deterministic evidence."""


_ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.CONTRACT_READY},
    RunState.CONTRACT_READY: {RunState.PREPARING},
    RunState.PREPARING: {RunState.IMPLEMENTING},
    RunState.IMPLEMENTING: {RunState.COLLECTING_EVIDENCE},
    RunState.COLLECTING_EVIDENCE: {RunState.CHECKING},
    RunState.CHECKING: {RunState.COMPLETED},
}

_EXPECTED_TRANSITION_REASONS: dict[tuple[RunState, RunState], TransitionReason] = {
    (RunState.CREATED, RunState.CONTRACT_READY): TransitionReason.CONTRACT_RESOLVED,
    (RunState.CONTRACT_READY, RunState.PREPARING): TransitionReason.WORKSPACE_PREPARED,
    (RunState.PREPARING, RunState.IMPLEMENTING): TransitionReason.WORKER_ASSIGNED,
    (RunState.IMPLEMENTING, RunState.COLLECTING_EVIDENCE): TransitionReason.WORKER_FINISHED,
    (RunState.COLLECTING_EVIDENCE, RunState.CHECKING): TransitionReason.EVIDENCE_READY,
    (RunState.CHECKING, RunState.COMPLETED): TransitionReason.TERMINATION_DECIDED,
}

_TRANSITION_EVENT_TYPES: dict[RunState, EvaluationEventType] = {
    RunState.CONTRACT_READY: EvaluationEventType.CONTRACT_INFERRED,
    RunState.PREPARING: EvaluationEventType.WORKSPACE_PREPARED,
    RunState.IMPLEMENTING: EvaluationEventType.WORKER_ASSIGNED,
    RunState.COLLECTING_EVIDENCE: EvaluationEventType.WORKER_FINISHED,
    RunState.CHECKING: EvaluationEventType.EVIDENCE_COLLECTED,
    RunState.COMPLETED: EvaluationEventType.RUN_COMPLETED,
}


class Runtime:
    """Only canonical writer for RunRecord, attempts, evidence, outcome, and report."""

    def __init__(
        self,
        *,
        run_id: str,
        task_contract: TaskContract,
        verification_plan: VerificationPlan,
        worker: FakeWorker,
        check_runner: FakeCheckRunner,
        event_log: InMemoryEventLog,
        report_directory: Path,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._worker = worker
        self._check_runner = check_runner
        self._event_log = event_log
        self._report_directory = report_directory
        self._clock = clock
        self._record = RunRecord(
            run_id=run_id,
            task_contract=task_contract,
            verification_plan=verification_plan,
            started_at=clock(),
        )

    @property
    def record(self) -> RunRecord:
        return self._record

    @property
    def events(self) -> tuple[EvaluationEvent, ...]:
        return self._event_log.events

    def run_scenario_1(self) -> RunRecord:
        """Execute exactly one deterministic attempt and stop at COMPLETED."""

        if self._record.state is not RunState.CREATED:
            raise InvalidTransitionError("Scenario 1 can start only from CREATED")
        self._append_event(
            EvaluationEventType.TASK_SUBMITTED,
            payload={"goal": self._record.task_contract.goal},
        )
        self.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)
        self._assert_scenario_1_preflight()
        self._append_event(EvaluationEventType.RUN_STARTED, payload={"state": "CONTRACT_READY"})
        self.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)

        self.start_attempt()
        self._append_event(EvaluationEventType.WORKER_STARTED, payload={"attempt_number": 1})
        worker_result = self._worker.execute(self._record.task_contract, 1)
        self._replace_record(worker_result=worker_result)
        self.transition(RunState.COLLECTING_EVIDENCE, TransitionReason.WORKER_FINISHED)

        observed_checks = tuple(
            self._check_runner.run(check, self._record.run_id, 1)
            for check in self._record.task_contract.required_checks
        )
        observed_patch = self._check_runner.collect_patch_evidence(
            self._record.run_id,
            1,
        )
        self._replace_record(
            observed_checks=observed_checks,
            observed_patch=observed_patch,
        )
        self.transition(RunState.CHECKING, TransitionReason.EVIDENCE_READY)

        evaluations = self._evaluate_deterministic_criteria(observed_checks)
        self._replace_record(criterion_evaluations=evaluations)
        self._append_check_events(observed_checks)

        if (
            not evaluations
            or any(
                evaluation.status is not CriterionStatus.SATISFIED
                for evaluation in evaluations
            )
            or not self._all_required_checks_passed(observed_checks)
            or not self._patch_evidence_allows_completion(observed_patch)
        ):
            raise CompletionConditionsNotMet(
                "observed evidence did not satisfy every criterion and required check"
            )

        decision = StopDecision(
            outcome=Outcome.COMPLETED,
            stop_reason=StopReason.ACCEPTANCE_SATISFIED,
            repairable=False,
            owner_input_required=False,
            next_action=NextAction.COMPLETE,
        )
        self._replace_record(
            stop_decision=decision,
            verification_status=VerificationStatus.DETERMINISTICALLY_CHECKED,
        )
        self.transition(RunState.COMPLETED, TransitionReason.TERMINATION_DECIDED)

        report = build_terminal_report(self._record, generated_at=self._clock())
        report_path = write_terminal_report(
            self._report_directory / "report.v1.json",
            report,
        )
        self._replace_record(report_path=str(report_path))
        self._append_event(
            EvaluationEventType.REPORT_GENERATED,
            payload={"artifact_path": str(report_path), "schema_version": "report.v1"},
        )
        return self._record

    def start_attempt(self) -> None:
        """Let Runtime start canonical Attempt 1 and enter IMPLEMENTING."""

        if self._record.state is not RunState.PREPARING:
            raise InvalidTransitionError("Attempt 1 can start only from PREPARING")
        if self._record.attempt_count != 0:
            raise InvalidTransitionError("Commit 2 supports exactly one attempt")
        self._replace_record(attempt_count=1)
        self.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)

    def transition(self, target: RunState, reason: TransitionReason) -> None:
        """Validate and record one canonical transition."""

        current = self._record.state
        if current in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.BUDGET_EXHAUSTED,
            RunState.CANCELLED,
        }:
            raise InvalidTransitionError("terminal states cannot transition")
        if target not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(f"illegal transition: {current.value} -> {target.value}")
        expected_reason = _EXPECTED_TRANSITION_REASONS[(current, target)]
        if reason is not expected_reason:
            raise InvalidTransitionError(
                f"transition {current.value} -> {target.value} requires "
                f"reason {expected_reason.value}"
            )

        self._validate_transition_preconditions(current, target)
        if target is RunState.COMPLETED:
            decision = self._record.stop_decision
            if decision is None:
                raise InvalidTransitionError("StopDecision is required before completion")
            self._replace_record(
                state=target,
                outcome=decision.outcome,
                stop_reason=decision.stop_reason,
                completion_basis=CompletionBasis.DETERMINISTIC_EVIDENCE,
                completed_at=self._clock(),
            )
        else:
            self._replace_record(state=target)
        self._append_event(
            _TRANSITION_EVENT_TYPES[target],
            payload={
                "from_state": current.value,
                "to_state": target.value,
                "transition_reason": reason.value,
            },
        )

    def _assert_scenario_1_preflight(self) -> None:
        # This must run before the transition into PREPARING.
        self._record.verification_plan.assert_compatible(self._record.task_contract)
        if self._record.verification_plan.mode is not VerificationMode.B:
            raise ValueError("Commit 2 supports Verification Mode B only")
        if any(
            criterion.verification_method is not VerificationMethod.DETERMINISTIC
            for criterion in self._record.task_contract.acceptance_criteria
        ):
            raise ValueError("Commit 2 supports deterministic criteria only")
        if (self._report_directory / "report.v1.json").exists():
            raise ValueError("report.v1.json already exists for this run")

    def _validate_transition_preconditions(
        self,
        current: RunState,
        target: RunState,
    ) -> None:
        if target is RunState.PREPARING:
            # Defense in depth for direct transition() callers.
            self._assert_scenario_1_preflight()
        if current is RunState.PREPARING and target is RunState.IMPLEMENTING:
            if self._record.attempt_count != 1:
                raise InvalidTransitionError("Attempt 1 must start before IMPLEMENTING")
        if current is RunState.IMPLEMENTING and target is RunState.COLLECTING_EVIDENCE:
            if self._record.worker_result is None:
                raise InvalidTransitionError("WorkerResult is required after IMPLEMENTING")
        if current is RunState.COLLECTING_EVIDENCE and target is RunState.CHECKING:
            self._validate_observed_evidence_provenance_and_artifacts()
            observed_ids = [item.check_id for item in self._record.observed_checks]
            expected_ids = [item.id for item in self._record.task_contract.required_checks]
            if len(observed_ids) != len(set(observed_ids)):
                raise InvalidTransitionError("observed check IDs must be unique")
            if set(observed_ids) != set(expected_ids):
                raise InvalidTransitionError("observed evidence must cover every required check")
        if current is RunState.CHECKING and target is RunState.COMPLETED:
            self._validate_observed_evidence_provenance_and_artifacts()
            if self._record.stop_decision is None:
                raise InvalidTransitionError("StopDecision is required before completion")
            if self._record.stop_decision.next_action is not NextAction.COMPLETE:
                raise InvalidTransitionError("completion requires next_action=complete")
            if not self._record.criterion_evaluations or any(
                item.status is not CriterionStatus.SATISFIED
                for item in self._record.criterion_evaluations
            ):
                raise InvalidTransitionError("all criteria must be satisfied")
            if not self._all_required_checks_passed(self._record.observed_checks):
                raise InvalidTransitionError("all required checks must pass")
            if self._record.observed_patch is None or not self._patch_evidence_allows_completion(
                self._record.observed_patch
            ):
                raise InvalidTransitionError("complete in-scope patch evidence is required")

    def _validate_observed_evidence_provenance_and_artifacts(self) -> None:
        if not self._record.observed_checks:
            raise InvalidTransitionError("fresh observed evidence is required")
        for item in self._record.observed_checks:
            if item.run_id != self._record.run_id:
                raise InvalidTransitionError("observed evidence must belong to current run")
            if item.attempt_number != self._record.attempt_count:
                raise InvalidTransitionError("observed evidence must belong to current attempt")
            self._require_regular_file(item.stdout_path, "stdout artifact")
            self._require_regular_file(item.stderr_path, "stderr artifact")

        patch = self._record.observed_patch
        if patch is None:
            raise InvalidTransitionError("observed patch evidence is required")
        if patch.run_id != self._record.run_id:
            raise InvalidTransitionError("patch evidence must belong to current run")
        if patch.attempt_number != self._record.attempt_count:
            raise InvalidTransitionError("patch evidence must belong to current attempt")
        self._require_regular_file(patch.artifact_path, "patch artifact")

    @staticmethod
    def _require_regular_file(raw_path: str, label: str) -> None:
        if not Path(raw_path).is_file():
            raise InvalidTransitionError(f"{label} must exist and be a regular file")

    @staticmethod
    def _patch_evidence_allows_completion(evidence: ObservedPatchEvidence) -> bool:
        return (
            evidence.diff_exists
            and evidence.evidence_complete
            and not evidence.scope_violations
        )

    def _all_required_checks_passed(
        self,
        observed_checks: tuple[ObservedCheckResult, ...],
    ) -> bool:
        observed_by_id = {item.check_id: item for item in observed_checks}
        required_checks = self._record.task_contract.required_checks
        return (
            len(observed_by_id) == len(required_checks)
            and all(
                check.id in observed_by_id
                and observed_by_id[check.id].exit_code in check.expected_exit_codes
                for check in required_checks
            )
        )

    def _evaluate_deterministic_criteria(
        self,
        observed_checks: tuple[ObservedCheckResult, ...],
    ) -> tuple[CriterionEvaluation, ...]:
        observed_by_id = {result.check_id: result for result in observed_checks}
        check_contracts = {
            check.id: check for check in self._record.task_contract.required_checks
        }
        evaluations: list[CriterionEvaluation] = []

        for criterion in self._record.task_contract.acceptance_criteria:
            statuses: list[CriterionStatus] = []
            evidence_refs: list[str] = []
            for check_id in criterion.required_check_ids:
                observed = observed_by_id.get(check_id)
                if observed is None:
                    statuses.append(CriterionStatus.UNRESOLVED)
                    continue
                check = check_contracts[check_id]
                evidence_refs.extend((observed.stdout_path, observed.stderr_path))
                statuses.append(
                    CriterionStatus.SATISFIED
                    if observed.exit_code in check.expected_exit_codes
                    else CriterionStatus.UNSATISFIED
                )

            if CriterionStatus.UNSATISFIED in statuses:
                status = CriterionStatus.UNSATISFIED
            elif not statuses or CriterionStatus.UNRESOLVED in statuses:
                status = CriterionStatus.UNRESOLVED
            else:
                status = CriterionStatus.SATISFIED
            evaluations.append(
                CriterionEvaluation(
                    criterion_id=criterion.id,
                    status=status,
                    evidence_refs=tuple(evidence_refs),
                )
            )
        return tuple(evaluations)

    def _append_check_events(
        self,
        observed_checks: tuple[ObservedCheckResult, ...],
    ) -> None:
        checks = {check.id: check for check in self._record.task_contract.required_checks}
        for observed in observed_checks:
            passed = observed.exit_code in checks[observed.check_id].expected_exit_codes
            self._append_event(
                EvaluationEventType.CHECK_PASSED if passed else EvaluationEventType.CHECK_FAILED,
                payload={
                    "check_id": observed.check_id,
                    "argv": list(observed.argv),
                    "cwd_relative": observed.cwd_relative,
                    "exit_code": observed.exit_code,
                    "stdout_path": observed.stdout_path,
                    "stderr_path": observed.stderr_path,
                },
            )

    def _append_event(self, event_type: EvaluationEventType, *, payload: dict[str, object]) -> None:
        self._event_log.append(
            timestamp=self._clock(),
            event_type=event_type,
            task_id=self._record.task_contract.task_id,
            run_id=self._record.run_id,
            attempt_number=(self._record.attempt_count or None),
            actor="runtime",
            payload=payload,
        )

    def _replace_record(self, **updates: object) -> None:
        values = {
            field_name: getattr(self._record, field_name)
            for field_name in type(self._record).model_fields
        }
        values.update(updates)
        self._record = RunRecord(**values)
