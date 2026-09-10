"""Canonical deterministic Runtime for Commit 2 Scenario 1 only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath

from tomtit.domain.contracts import (
    Finding,
    StopDecision,
    TaskContract,
    VerificationPlan,
)
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
from tomtit.domain.evaluation import (
    BudgetSnapshot,
    DecisionEventDraft,
    DecisionReasonCode,
    EvaluationEvent,
    PersistedDecisionEvent,
)
from tomtit.runtime.ports import CheckRunnerPort, WorkerPort
from tomtit.runtime.workspace import (
    UnrelatedHistoryError,
    assert_output_outside_workspace,
)
from tomtit.reporting import (
    STAGED_REPORT_NAME,
    ReportSourceRevision,
    PublicationReceipt,
    _build_terminal_report_from_validated_record,
    publication_authority,
    publish_staged_report,
    render_terminal_report,
    stage_terminal_report,
)
from tomtit.runtime.events import (
    InMemoryEventLog,
    PreparedDecision,
    PreparedEvent,
    utc_now,
)
from tomtit.runtime.state import (
    AttemptEvidence,
    CompletionBasis,
    CriterionEvaluation,
    ObservedCheckResult,
    RunRecord,
    TransitionReason,
    artifact_file_error,
    attempt_evidence_from_observations,
    canonical_repairable_branch_finding,
    objective_improvement_error,
    patch_evidence_error,
    repairable_stop_decision_error,
    required_check_coverage_error,
    task_contract_hash,
    trusted_contract_context,
    validate_completed_record,
)


class InvalidTransitionError(RuntimeError):
    """Raised when a caller attempts to bypass the canonical state machine."""


@dataclass(frozen=True, slots=True)
class _PreparedCompletion:
    """Everything terminal completion needs, built before any canonical mutation."""

    record: RunRecord
    staged_report: Path
    final_report: Path
    events: tuple[PreparedEvent, ...]
    #: The ledger allocation stamped into the staged bytes, so a commit that
    #: rolls back can hand it back instead of stranding it as publishable.
    report_id: str


def _remove_quietly(path: Path) -> None:
    """Best-effort cleanup that never masks the failure being recovered from."""

    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - cleanup failure is a documented risk
        pass


class CompletionConditionsNotMet(RuntimeError):
    """Raised when Scenario 1 cannot complete from deterministic evidence."""


_ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.CONTRACT_READY},
    RunState.CONTRACT_READY: {RunState.PREPARING},
    RunState.PREPARING: {RunState.IMPLEMENTING},
    RunState.IMPLEMENTING: {RunState.COLLECTING_EVIDENCE},
    RunState.COLLECTING_EVIDENCE: {RunState.CHECKING},
    RunState.CHECKING: {RunState.COMPLETED, RunState.REVISING},
    RunState.REVISING: {RunState.IMPLEMENTING},
}

_EXPECTED_TRANSITION_REASONS: dict[tuple[RunState, RunState], TransitionReason] = {
    (RunState.CREATED, RunState.CONTRACT_READY): TransitionReason.CONTRACT_RESOLVED,
    (RunState.CONTRACT_READY, RunState.PREPARING): TransitionReason.WORKSPACE_PREPARED,
    (RunState.PREPARING, RunState.IMPLEMENTING): TransitionReason.WORKER_ASSIGNED,
    (RunState.IMPLEMENTING, RunState.COLLECTING_EVIDENCE): TransitionReason.WORKER_FINISHED,
    (RunState.COLLECTING_EVIDENCE, RunState.CHECKING): TransitionReason.EVIDENCE_READY,
    (RunState.CHECKING, RunState.REVISING): TransitionReason.REVISION_APPROVED,
    (RunState.REVISING, RunState.IMPLEMENTING): TransitionReason.WORKER_ASSIGNED,
    (RunState.CHECKING, RunState.COMPLETED): TransitionReason.TERMINATION_DECIDED,
}

_TRANSITION_EVENT_TYPES: dict[RunState, EvaluationEventType] = {
    RunState.CONTRACT_READY: EvaluationEventType.CONTRACT_INFERRED,
    RunState.PREPARING: EvaluationEventType.WORKSPACE_PREPARED,
    RunState.IMPLEMENTING: EvaluationEventType.WORKER_ASSIGNED,
    RunState.COLLECTING_EVIDENCE: EvaluationEventType.WORKER_FINISHED,
    RunState.CHECKING: EvaluationEventType.EVIDENCE_COLLECTED,
    RunState.REVISING: EvaluationEventType.REVISION_REQUESTED,
    RunState.COMPLETED: EvaluationEventType.RUN_COMPLETED,
}

TOMTIT_POLICY_VERSION = "mvp-execution-policy-v0.3"

# Entering these states carries its own canonical record, so the generic public
# transition API must never be the writer.
_RUNTIME_COMMITTED_STATES: dict[RunState, str] = {
    RunState.COMPLETED: (
        "COMPLETED is committed by complete_run() as one prepared operation"
    ),
    RunState.REVISING: (
        "REVISING is committed by commit_revision_decision() together with its "
        "DecisionEvent; the generic transition API cannot enter it"
    ),
}


class RevisionNotAllowed(RuntimeError):
    """Raised when a REVISE proposal cannot be accepted as canonical."""


@dataclass(frozen=True, slots=True)
class _RevisionMetadata:
    """Decision metadata derived from canonical Runtime truth only."""

    policy_version: str
    allowed_actions: tuple[NextAction, ...]
    binding_constraints: tuple[str, ...]
    budget_snapshot: BudgetSnapshot


@dataclass(frozen=True, slots=True)
class _PreparedRevision:
    """Decision and canonical transition, both built before anything is committed."""

    record: RunRecord
    decision: PreparedDecision
    event: PreparedEvent


class Runtime:
    """Only canonical writer for RunRecord, attempts, evidence, outcome, and report."""

    def __init__(
        self,
        *,
        run_id: str,
        task_contract: TaskContract,
        verification_plan: VerificationPlan,
        event_log: InMemoryEventLog,
        report_directory: Path,
        worker: WorkerPort | None = None,
        check_runner: CheckRunnerPort | None = None,
        clock: Callable[[], datetime] = utc_now,
        execution: object | None = None,
    ) -> None:
        # Exactly one of the two shapes, never a mixture. A real execution
        # binding supplies its own worker and check runner, both already proven
        # bound to its workspace; accepting extras alongside it would reopen the
        # substitution the binding exists to close.
        if execution is not None:
            if worker is not None or check_runner is not None:
                raise ValueError(
                    "an execution binding already supplies its worker and check "
                    "runner; passing either separately would let a component "
                    "bound to a different workspace run under this run's "
                    "provenance"
                )
            from tomtit.runtime.execution import ProductRuntimeExecution

            if type(execution) is not ProductRuntimeExecution:
                raise ValueError(
                    "execution must be a ProductRuntimeExecution, not "
                    f"{type(execution).__name__}"
                )
            worker = execution.worker
            check_runner = execution.check_runner

            # Runtime-owned output must not sit inside the source it is about to
            # describe. Enforced here rather than only in the composition
            # factory: every supported real path reaches this constructor, so
            # this is the point where the rule is actually an invariant.
            root = execution.workspace.path
            assert_output_outside_workspace(report_directory, root, "report_directory")
            assert_output_outside_workspace(
                worker.artifact_directory, root, "worker artifact_directory"
            )
            assert_output_outside_workspace(
                check_runner.artifact_directory, root, "check runner artifact_directory"
            )
        else:
            if worker is None or check_runner is None:
                raise ValueError(
                    "a run needs either an execution binding or both a worker "
                    "and a check runner"
                )

            # The loose pair exists for the fixture scenarios and stays open to
            # them. A *real* component arriving this way would skip everything
            # the binding exists to guarantee -- canonical scope application,
            # workspace and history validation, the trusted profile -- and the
            # run would publish itself as fixture work. Refuse rather than try to
            # reconstruct a binding from loose parts.
            from tomtit.runtime.execution import (
                ProductRuntimeCheckRunner,
                ProductRuntimeClaudeWorker,
            )

            for label, component, real in (
                ("worker", worker, ProductRuntimeClaudeWorker),
                ("check_runner", check_runner, ProductRuntimeCheckRunner),
            ):
                if isinstance(component, real):
                    raise ValueError(
                        f"{label} is a {type(component).__name__}, which carries "
                        "real execution authority and must be supplied through a "
                        "ProductRuntimeExecution binding. Passing it directly "
                        "would skip workspace, history and scope validation and "
                        "publish the run under the fixture profile."
                    )

        self._worker = worker
        self._check_runner = check_runner
        self._execution = execution
        self._event_log = event_log
        self._report_directory = report_directory
        self._clock = clock
        # Contract V1 §4.1: report identity comes from the process publication
        # ledger, not from this constructor. There is deliberately no seam for
        # supplying it — an injectable id factory would let a caller choose the
        # identity a published report is later assessed under, which is exactly
        # the authority §4.1 reserves to Runtime.
        self._publication_authority = publication_authority()
        # Set once, past the commit point, by the run that actually published.
        self._publication_receipt: PublicationReceipt | None = None
        # The canonical contract binding for this run's lifetime. It is captured
        # from the original TaskContract before any record can be copied, and is
        # never recomputed from self._record, whose task_contract a caller could
        # replace on a copy.
        self._canonical_task_contract_hash = task_contract_hash(task_contract)
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
    def publication_receipt(self) -> PublicationReceipt | None:
        """Evidence that this run published its terminal report, once it has.

        ``None`` until completion commits. It is the only thing an assessment
        caller can present to Evaluation, and it asserts nothing by itself:
        Evaluation resolves it against the publication ledger.
        """

        return self._publication_receipt

    @property
    def execution(self) -> object | None:
        """The immutable execution binding this run uses, if any."""

        return self._execution

    @property
    def workspace(self):
        """The bound workspace this run executes in, if any."""

        return self._execution.workspace if self._execution is not None else None

    @property
    def execution_base_revision(self) -> ReportSourceRevision | None:
        """The commit execution started from. Internal fact, not §4.1.

        Provenance about the *input*, exposed under a name that says so. It is
        never published as the report's ``source_revision``, which §4.1 reserves
        for the output.
        """

        workspace = self.workspace
        return workspace.execution_base_revision if workspace is not None else None

    @property
    def canonical_task_contract_hash(self) -> str:
        """The immutable contract hash this run is bound to."""

        return self._canonical_task_contract_hash

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
        observed_checks = self._execute_attempt()

        if not self._attempt_satisfies_completion(observed_checks):
            raise CompletionConditionsNotMet(
                "observed evidence did not satisfy every criterion and required check"
            )

        return self._decide_completion()

    def run_scenario_2(self) -> RunRecord:
        """Attempt 1 fails deterministically, one bounded revision, Attempt 2 completes.

        The revision decision is proposed Coordinator-side and accepted by Runtime
        as one atomic DecisionEvent-plus-transition. Attempt 2 collects fresh
        observed evidence, and completion runs through the frozen Scenario 1 path.
        """

        if self._record.state is not RunState.CREATED:
            raise InvalidTransitionError("Scenario 2 can start only from CREATED")
        self._append_event(
            EvaluationEventType.TASK_SUBMITTED,
            payload={"goal": self._record.task_contract.goal},
        )
        self.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)
        self._assert_scenario_1_preflight()
        self._append_event(EvaluationEventType.RUN_STARTED, payload={"state": "CONTRACT_READY"})
        self.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)

        self.start_attempt()
        attempt_1_checks = self._execute_attempt()
        if self._attempt_satisfies_completion(attempt_1_checks):
            raise CompletionConditionsNotMet(
                "Scenario 2 requires a deterministic Attempt 1 failure"
            )

        self.record_repairable_failure()
        self.commit_revision_decision(self.build_revision_proposal())
        self.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)

        attempt_2_checks = self._execute_attempt()
        if not self._attempt_satisfies_completion(attempt_2_checks):
            raise CompletionConditionsNotMet(
                "Attempt 2 observed evidence did not satisfy every criterion and check"
            )
        improvement_error = objective_improvement_error(
            self._record.attempt_history[-1],
            self._current_attempt_evidence(),
        )
        if improvement_error is not None:
            raise CompletionConditionsNotMet(improvement_error)

        return self._decide_completion()

    def _execute_attempt(self) -> tuple[ObservedCheckResult, ...]:
        """Run one attempt from IMPLEMENTING to CHECKING and evaluate its evidence.

        Evidence is always collected fresh for the canonical attempt number, so a
        later attempt can never present an earlier attempt's observations.
        """

        attempt_number = self._record.attempt_count
        self._append_event(
            EvaluationEventType.WORKER_STARTED,
            payload={"attempt_number": attempt_number},
        )
        worker_result = self._worker.execute(self._record.task_contract, attempt_number)
        self._replace_record(worker_result=worker_result)
        self.transition(RunState.COLLECTING_EVIDENCE, TransitionReason.WORKER_FINISHED)

        observed_checks = tuple(
            self._check_runner.run(check, self._record.run_id, attempt_number)
            for check in self._record.task_contract.required_checks
        )
        observed_patch = self._check_runner.collect_patch_evidence(
            self._record.run_id,
            attempt_number,
        )
        observed_patch = self._apply_canonical_scope(observed_patch)
        self._replace_record(
            observed_checks=observed_checks,
            observed_patch=observed_patch,
        )
        self.transition(RunState.CHECKING, TransitionReason.EVIDENCE_READY)

        evaluations = self._evaluate_deterministic_criteria(observed_checks)
        self._replace_record(criterion_evaluations=evaluations)
        self._append_check_events(observed_checks)
        return observed_checks

    def _attempt_satisfies_completion(
        self,
        observed_checks: tuple[ObservedCheckResult, ...],
    ) -> bool:
        evaluations = self._record.criterion_evaluations
        return bool(
            evaluations
            and all(
                evaluation.status is CriterionStatus.SATISFIED
                for evaluation in evaluations
            )
            and self._completion_evidence_is_sufficient(observed_checks)
        )

    def _decide_completion(self) -> RunRecord:
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
        return self.complete_run()

    def start_attempt(self) -> None:
        """Let Runtime start canonical Attempt 1 and enter IMPLEMENTING."""

        if self._record.state is not RunState.PREPARING:
            raise InvalidTransitionError("Attempt 1 can start only from PREPARING")
        if self._record.attempt_count != 0:
            raise InvalidTransitionError("Attempt 1 can start only once")
        self._replace_record(attempt_count=1)
        self.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)

    # ---- Scenario 2: one bounded revision ----------------------------------

    def record_repairable_failure(self) -> RunRecord:
        """Materialize the canonical repairable branch result for a failed attempt.

        Policy requires every branch that asks for another attempt to produce a
        `StopDecision` with `next_action=REVISE`, `repairable=true`,
        `owner_input_required=false`, and null outcome and stop reason, plus the
        finding that justifies repair. Both are derived here from observed
        evidence; Worker claims are not an input.
        """

        record = self._record
        if record.state is not RunState.CHECKING:
            raise InvalidTransitionError(
                "a repairable failure can only be recorded from CHECKING"
            )
        finding = self._canonical_repairable_finding()
        if finding is None:
            raise RevisionNotAllowed(
                "a repairable failure requires at least one failed required check"
            )
        # Canonical attempt-budget policy is decided here, before anything is
        # written. Recording REVISE while policy allows only FAIL would leave a
        # contradictory canonical branch result even though no Attempt 3 follows.
        contract = record.task_contract
        metadata = self._canonical_revision_metadata()
        if NextAction.REVISE not in metadata.allowed_actions:
            raise RevisionNotAllowed(
                f"canonical allowed actions are "
                f"{[action.value for action in metadata.allowed_actions]}; "
                "a repairable REVISE branch cannot be recorded"
            )
        if record.attempt_count >= contract.max_attempts:
            raise RevisionNotAllowed(
                f"attempt budget exhausted: {record.attempt_count} of "
                f"{contract.max_attempts} attempts used"
            )
        if record.attempt_count >= contract.hard_max_attempts:
            raise RevisionNotAllowed("hard attempt limit reached")
        self._replace_record(
            stop_decision=StopDecision(
                outcome=None,
                stop_reason=None,
                repairable=True,
                owner_input_required=False,
                next_action=NextAction.REVISE,
            ),
            unresolved_findings=(finding,),
        )
        return self._record

    def _canonical_repairable_finding(self) -> Finding | None:
        """Derive the one repairable finding for the first failed required check.

        The same derivation re-proves an archived finding at the trust boundary,
        so Runtime and validation can never disagree about what it should be.
        """

        record = self._record
        return canonical_repairable_branch_finding(
            contract=record.task_contract,
            run_id=record.run_id,
            attempt_number=record.attempt_count,
            observed_checks=record.observed_checks,
        )

    def _canonical_revision_metadata(self) -> _RevisionMetadata:
        """Derive decision metadata from canonical Runtime truth.

        Used both to build the proposal and, independently, to re-derive expected
        values at the commit boundary. A caller field is never validated against
        another caller field.
        """

        record = self._record
        contract = record.task_contract
        budget_remains = record.attempt_count + 1 <= min(
            contract.max_attempts, contract.hard_max_attempts
        )
        allowed_actions = (
            (NextAction.REVISE, NextAction.FAIL) if budget_remains else (NextAction.FAIL,)
        )
        return _RevisionMetadata(
            policy_version=TOMTIT_POLICY_VERSION,
            allowed_actions=allowed_actions,
            binding_constraints=(
                f"max_attempts={contract.max_attempts}",
                "deterministic_evidence_required",
            ),
            budget_snapshot=BudgetSnapshot(
                attempt_number=record.attempt_count,
                max_attempts=contract.max_attempts,
                hard_max_attempts=contract.hard_max_attempts,
                time_budget_seconds=contract.time_budget_seconds,
            ),
        )

    def build_revision_proposal(self) -> DecisionEventDraft:
        """Build a Coordinator-side REVISE proposal from current observed evidence.

        This is a *proposal only*. It appends nothing, mutates nothing, and
        carries no identity, timestamp, or sequence. Runtime remains the only
        writer; a caller may equally construct this draft itself, and the commit
        boundary re-derives every canonical field regardless.
        """

        record = self._record
        metadata = self._canonical_revision_metadata()
        evidence_ids = [item.snapshot_id for item in record.observed_checks]
        if record.observed_patch is not None:
            evidence_ids.append(record.observed_patch.snapshot_id)
        return DecisionEventDraft(
            policy_version=metadata.policy_version,
            task_contract_hash=task_contract_hash(record.task_contract),
            run_id=record.run_id,
            attempt_number=record.attempt_count,
            state_before=record.state,
            evidence_snapshot_ids=tuple(evidence_ids),
            allowed_actions=metadata.allowed_actions,
            selected_action=NextAction.REVISE,
            reason_code=DecisionReasonCode.DETERMINISTIC_REPAIRABLE_CHECK_FAILURE,
            binding_constraints=metadata.binding_constraints,
            budget_snapshot=metadata.budget_snapshot,
            state_after=RunState.REVISING,
        )

    def commit_revision_decision(
        self,
        draft: DecisionEventDraft,
    ) -> PersistedDecisionEvent:
        """Validate a REVISE proposal, then commit decision and transition together.

        Either the persisted DecisionEvent exists *and* the canonical transition
        to the next attempt is applied, or neither happened. Every fallible step
        — validation, identity and sequence assignment, model construction and
        serialization — runs during preparation, before anything is committed.
        """

        prepared = self._prepare_revision(draft)
        return self._commit_revision(prepared)

    def _prepare_revision(self, draft: DecisionEventDraft) -> _PreparedRevision:
        record = self._record
        contract = record.task_contract

        # Reconstruct the untrusted proposal through full validation first.
        validated = DecisionEventDraft.model_validate(draft.model_dump(mode="python"))

        if validated.selected_action is not NextAction.REVISE:
            raise RevisionNotAllowed("only REVISE decisions are supported by this gate")
        if record.state is not RunState.CHECKING:
            raise RevisionNotAllowed(
                f"REVISE requires state CHECKING, not {record.state.value}"
            )
        if validated.state_before is not record.state:
            raise RevisionNotAllowed("proposal state_before must match canonical state")
        if validated.state_after is not RunState.REVISING:
            raise RevisionNotAllowed("a REVISE decision must move the run to REVISING")
        if validated.run_id != record.run_id:
            raise RevisionNotAllowed("proposal must belong to the current run")
        if validated.attempt_number != record.attempt_count:
            raise RevisionNotAllowed("proposal must belong to the current attempt")
        if validated.task_contract_hash != self._canonical_task_contract_hash:
            raise RevisionNotAllowed(
                "proposal task_contract_hash does not match the canonical contract "
                "this run is bound to"
            )
        if task_contract_hash(contract) != self._canonical_task_contract_hash:
            raise RevisionNotAllowed(
                "the record's TaskContract no longer matches the canonical contract "
                "this run is bound to"
            )
        if record.attempt_count + 1 > contract.max_attempts:
            raise RevisionNotAllowed(
                f"attempt budget exhausted: {record.attempt_count} of "
                f"{contract.max_attempts} attempts used"
            )

        # Every remaining proposal field is re-derived from canonical truth and
        # compared for exact equality. No caller field validates another.
        metadata = self._canonical_revision_metadata()
        if validated.policy_version != metadata.policy_version:
            raise RevisionNotAllowed(
                f"proposal policy_version {validated.policy_version!r} does not match "
                f"the canonical policy {metadata.policy_version!r}"
            )
        if validated.allowed_actions != metadata.allowed_actions:
            raise RevisionNotAllowed(
                f"proposal allowed_actions {[a.value for a in validated.allowed_actions]} "
                f"do not match canonical {[a.value for a in metadata.allowed_actions]}"
            )
        if validated.binding_constraints != metadata.binding_constraints:
            raise RevisionNotAllowed(
                f"proposal binding_constraints {list(validated.binding_constraints)} "
                f"do not match canonical {list(metadata.binding_constraints)}"
            )
        if validated.budget_snapshot != metadata.budget_snapshot:
            raise RevisionNotAllowed(
                "proposal budget_snapshot does not match the canonical budget"
            )

        # The policy-required repairable branch result must already exist. The
        # same predicate re-proves an archived branch at the trust boundary.
        stop_decision = record.stop_decision
        decision_error = repairable_stop_decision_error(stop_decision)
        if decision_error is not None:
            raise RevisionNotAllowed(decision_error)

        expected_finding = self._canonical_repairable_finding()
        if expected_finding is None:
            raise RevisionNotAllowed("REVISE requires at least one failed required check")
        if len(record.unresolved_findings) != 1:
            raise RevisionNotAllowed(
                f"REVISE requires exactly one repairable finding, found "
                f"{len(record.unresolved_findings)}"
            )
        if record.unresolved_findings[0] != expected_finding:
            raise RevisionNotAllowed(
                "the recorded finding does not match the canonical repairable finding "
                f"for run {record.run_id} attempt {record.attempt_count}"
            )

        current_evidence = self._current_attempt_evidence()
        if not current_evidence.failed_check_ids:
            raise RevisionNotAllowed("REVISE requires at least one failed required check")
        expected_ids = set(current_evidence.evidence_snapshot_ids)
        proposed_ids = set(validated.evidence_snapshot_ids)
        if proposed_ids != expected_ids:
            missing = sorted(expected_ids - proposed_ids)
            unexpected = sorted(proposed_ids - expected_ids)
            raise RevisionNotAllowed(
                "proposal evidence must be exactly the current attempt's observed "
                f"evidence: missing={missing}, unexpected={unexpected}"
            )

        # Preparation: fallible work only, nothing canonical is touched.
        # The archive carries the branch result forward; the current attempt's
        # decision and findings are cleared because they belong to Attempt 1.
        archived = current_evidence.model_copy(
            update={
                "stop_decision": stop_decision,
                "findings": record.unresolved_findings,
            }
        )
        next_attempt = record.attempt_count + 1
        candidate = self._build_record(
            state=RunState.REVISING,
            attempt_count=next_attempt,
            attempt_history=(*record.attempt_history, archived),
            observed_checks=(),
            observed_patch=None,
            criterion_evaluations=(),
            worker_result=None,
            stop_decision=None,
            unresolved_findings=(),
        )
        prepared_decision = self._event_log.prepare_decision(
            validated,
            decision_timestamp=self._clock(),
        )
        prepared_event = self._prepare_event(
            candidate,
            timestamp=self._clock(),
            event_type=_TRANSITION_EVENT_TYPES[RunState.REVISING],
            payload={
                "from_state": record.state.value,
                "to_state": RunState.REVISING.value,
                "transition_reason": TransitionReason.REVISION_APPROVED.value,
                "decision_id": prepared_decision.decision.decision_id,
                "append_sequence": prepared_decision.decision.append_sequence,
                "next_attempt_number": next_attempt,
            },
        )
        return _PreparedRevision(
            record=candidate,
            decision=prepared_decision,
            event=prepared_event,
        )

    def _commit_revision(self, prepared: _PreparedRevision) -> PersistedDecisionEvent:
        """Commit the decision and the canonical transition as one unit."""

        previous_record = self._record
        event_checkpoint = self._event_log._checkpoint()
        decision_checkpoint = self._event_log._decision_checkpoint()
        try:
            self._record = prepared.record
            self._event_log._append_prepared_decision(prepared.decision)
            self._event_log._append_prepared_batch((prepared.event,))
        except BaseException:
            self._record = previous_record
            self._event_log._restore_decisions_to_checkpoint(decision_checkpoint)
            self._event_log._restore_to_checkpoint(event_checkpoint)
            raise
        return prepared.decision.decision

    def _current_attempt_evidence(self) -> AttemptEvidence:
        record = self._record
        return attempt_evidence_from_observations(
            run_id=record.run_id,
            attempt_number=record.attempt_count,
            required_checks=record.task_contract.required_checks,
            observed_checks=record.observed_checks,
            observed_patch=record.observed_patch,
            criterion_evaluations=record.criterion_evaluations,
        )

    def transition(self, target: RunState, reason: TransitionReason) -> None:
        """Validate and record one canonical unprotected transition.

        States whose entry carries its own canonical record — the terminal report
        for COMPLETED, the DecisionEvent for REVISING — are refused here. The
        edge is checked first so a genuinely illegal move is still reported as
        such; the protected-target refusal then applies whatever reason was
        supplied.
        """

        current = self._record.state
        self._assert_transition_edge(current, target)
        protected = _RUNTIME_COMMITTED_STATES.get(target)
        if protected is not None:
            raise InvalidTransitionError(protected)
        self._assert_transition_reason(current, target, reason)
        self._validate_transition_preconditions(current, target)
        self._replace_record(state=target)
        self._append_transition_event(current, target, reason)

    def complete_run(self) -> RunRecord:
        """Terminal completion as one prepare/commit/recover unit.

        Completion has exactly two externally observable results. Either the run
        is COMPLETED with its report and both terminal events, or nothing moved
        and the run is still retryable from CHECKING. Every normally fallible
        step — timestamps, event IDs, model validation, serialization, report
        rendering, report staging, and the authoritative evidence gate — happens
        in :meth:`_prepare_completion` before canonical state or the event journal
        is touched. :meth:`_commit_completion` then performs only assignment,
        publication, and an append of already serialized events, and undoes them
        all if any of those raise.
        """

        current = self._record.state
        reason = TransitionReason.TERMINATION_DECIDED
        self._assert_transition_legal(current, RunState.COMPLETED, reason)
        self._validate_transition_preconditions(current, RunState.COMPLETED)
        decision = self._record.stop_decision
        if decision is None:
            raise InvalidTransitionError("StopDecision is required before completion")

        prepared = self._prepare_completion(current, reason, decision)
        return self._commit_completion(prepared)

    def _prepare_completion(
        self,
        current: RunState,
        reason: TransitionReason,
        decision: StopDecision,
    ) -> _PreparedCompletion:
        """Do every fallible piece of work, mutating nothing canonical.

        The evidence gate runs last so that required artifacts are proven to exist
        at the final Runtime-controlled moment before canonical assignment. No
        clock call, ID allocation, model construction, serialization, rendering,
        or staging happens after it.
        """

        final_report = self._report_directory / "report.v1.json"
        # Known up front so a staging call that fails part-way through still has
        # its partial file removed by the handler below.
        staged_report = self._report_directory / STAGED_REPORT_NAME
        # On the completion path, not beside it. complete_run() is the only
        # route to COMPLETED -- the public transition() refuses COMPLETED as a
        # protected target -- so validating the final source state here is what
        # makes the guarantee real rather than a helper nobody calls.
        source_revision = self._validated_output_revision()
        profile = self._execution_profile()
        # Allocated before the try so a failure anywhere below can hand it back
        # unused; an allocation that never publishes must not stay publishable.
        report_id = self._publication_authority._allocate_report_id()
        try:
            completed_at = self._clock()
            generated_at = self._clock()
            run_completed_at = self._clock()
            report_generated_at = self._clock()

            candidate = self._build_record(
                state=RunState.COMPLETED,
                outcome=decision.outcome,
                stop_reason=decision.stop_reason,
                completion_basis=CompletionBasis.DETERMINISTIC_EVIDENCE,
                completed_at=completed_at,
                report_path=str(final_report),
            )

            run_completed = self._prepare_event(
                candidate,
                timestamp=run_completed_at,
                event_type=_TRANSITION_EVENT_TYPES[RunState.COMPLETED],
                payload={
                    "from_state": current.value,
                    "to_state": RunState.COMPLETED.value,
                    "transition_reason": reason.value,
                },
            )
            report_generated = self._prepare_event(
                candidate,
                timestamp=report_generated_at,
                event_type=EvaluationEventType.REPORT_GENERATED,
                payload={
                    "artifact_path": str(final_report),
                    "schema_version": "report.v1",
                },
            )

            report = _build_terminal_report_from_validated_record(
                candidate,
                generated_at=generated_at,
                # Allocated by the publication ledger, inside the single
                # Runtime-controlled preparation, so the id reaches the exact
                # published bytes and nothing outside Runtime can choose it
                # (§4.1). Only this id will be recordable as published.
                report_id=report_id,
                # §4.1: the exact source output this report represents. Resolved
                # on the completion path from the workspace's final state --
                # never the base it started from, and never worker-reported.
                source_revision=source_revision,
                implementer=profile.implementer,
                adapter_capability_limitations=profile.adapter_capability_limitations,
            )
            rendered = render_terminal_report(report)
            stage_terminal_report(self._report_directory, rendered)

            # Final Runtime-controlled proof; nothing fallible follows before commit.
            validate_completed_record(
                candidate,
                expected_task_contract_hash=self._canonical_task_contract_hash,
            )
        except BaseException:
            self._publication_authority._release_report_id(report_id)
            _remove_quietly(staged_report)
            raise

        return _PreparedCompletion(
            record=candidate,
            staged_report=staged_report,
            final_report=final_report,
            events=(run_completed, report_generated),
            report_id=report_id,
        )

    def _apply_canonical_scope(self, evidence):
        """Interpret observed changes against *this run's* TaskContract.

        The check runner observes; Runtime decides. Scope is a contract
        question, and the canonical contract is the one Runtime was created
        with, so the decision is made here from ``self._record.task_contract``
        and nowhere else. A component holding its own contract would be a second
        configurable owner of the same fact, and a broader one would authorise
        what the canonical contract withholds.

        Only real executions are re-interpreted. The fixture scenarios keep
        supplying their own fixture violations, which is what their recorded
        history has always meant.
        """

        if self._execution is None:
            return evidence

        allowed = self._record.task_contract.allowed_scope
        violations = tuple(
            item.path
            for item in evidence.changed_files
            if not any(
                item.operation in rule.operations
                and PurePath(item.path).match(rule.pattern)
                for rule in allowed
            )
        )
        if violations == evidence.scope_violations:
            return evidence
        return evidence.model_copy(update={"scope_violations": violations})

    def _validated_output_revision(self) -> ReportSourceRevision | None:
        """Validate the final source state and return its §4.1 revision.

        Two jobs, done from one observation of the repository so they cannot
        disagree with each other:

        1. **Integrity, which can fail the run.** If the final ``HEAD`` does not
           descend from the bound execution base, the history was reset or
           rewritten and every delta computed against that base is meaningless.
           That raises, so no canonical success occurs.
        2. **Representability, which cannot.** A dirty tree is a perfectly
           ordinary outcome of a coding run; it simply has no active Contract V1
           revision describing it, so the report publishes ``None`` and
           Evaluation refuses to assess it. Execution success and Eval
           assessability are separate questions and stay separate.

        A fixture run has no workspace and therefore no revision -- the same
        fail-closed answer, for a different reason.
        """

        workspace = self.workspace
        if workspace is None:
            return None

        delta = workspace.collect_delta()
        if not delta.base_is_ancestor:
            raise UnrelatedHistoryError(
                f"workspace {workspace.path} is at {delta.current_head}, which "
                f"does not descend from the bound execution base "
                f"{delta.base_commit}; the source present cannot be explained by "
                "this run's own base, so no evidence computed against it is "
                "trustworthy"
            )
        return workspace.resolve_output_revision(delta)

    def _execution_profile(self):
        """How this run executed. Owned by Runtime, never by the worker."""

        from tomtit.runtime.execution import FIXTURE_EXECUTION_PROFILE

        if self._execution is None:
            return FIXTURE_EXECUTION_PROFILE
        return self._execution.profile

    def _commit_completion(self, prepared: _PreparedCompletion) -> RunRecord:
        """Apply the prepared completion, or restore the pre-completion run.

        Creating the final report is the commit point, and it is deliberately the
        last fallible step. Everything before it is reversible in memory, so a
        failure rolls the record and journal back with no terminal report ever
        having existed — rollback therefore never has to delete a final report,
        and a retry never collides with a surviving one.

        Once the final report exists the terminal result is committed: the
        completed record, the terminal-event pair, and the report stand together.
        Only best-effort cleanup of the private staged file follows, and its
        failure must never undo that commit.
        """

        previous_record = self._record
        checkpoint = self._event_log._checkpoint()
        try:
            self._record = prepared.record
            self._event_log._append_prepared_batch(prepared.events)
            # Commit point. publish_staged_report performs one atomic link, so if
            # it raises the final report was not created by this operation.
            publish_staged_report(prepared.staged_report, prepared.final_report)
        except BaseException:
            self._publication_authority._release_report_id(prepared.report_id)
            self._record = previous_record
            self._event_log._restore_to_checkpoint(checkpoint)
            _remove_quietly(prepared.staged_report)
            raise

        # Past the commit point: best effort only, never a rollback trigger.
        # Recording reads the published artifact back and never raises, so a
        # ledger failure leaves the committed report standing — unassessable
        # rather than uncommitted.
        self._publication_receipt = self._publication_authority.record_publication(
            prepared.final_report
        )
        _remove_quietly(prepared.staged_report)
        return self._record

    def _prepare_event(
        self,
        record: RunRecord,
        *,
        timestamp: datetime,
        event_type: EvaluationEventType,
        payload: dict[str, object],
    ) -> PreparedEvent:
        return self._event_log.prepare(
            timestamp=timestamp,
            event_type=event_type,
            task_id=record.task_contract.task_id,
            run_id=record.run_id,
            attempt_number=(record.attempt_count or None),
            actor="runtime",
            payload=payload,
        )

    def _assert_transition_legal(
        self,
        current: RunState,
        target: RunState,
        reason: TransitionReason,
    ) -> None:
        self._assert_transition_edge(current, target)
        self._assert_transition_reason(current, target, reason)

    @staticmethod
    def _assert_transition_edge(current: RunState, target: RunState) -> None:
        if current in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.BUDGET_EXHAUSTED,
            RunState.CANCELLED,
        }:
            raise InvalidTransitionError("terminal states cannot transition")
        if target not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(f"illegal transition: {current.value} -> {target.value}")

    @staticmethod
    def _assert_transition_reason(
        current: RunState,
        target: RunState,
        reason: TransitionReason,
    ) -> None:
        expected_reason = _EXPECTED_TRANSITION_REASONS[(current, target)]
        if reason is not expected_reason:
            raise InvalidTransitionError(
                f"transition {current.value} -> {target.value} requires "
                f"reason {expected_reason.value}"
            )

    def _append_transition_event(
        self,
        current: RunState,
        target: RunState,
        reason: TransitionReason,
    ) -> None:
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
        if current is RunState.REVISING and target is RunState.IMPLEMENTING:
            # The revision decision already advanced the canonical attempt and
            # archived the previous attempt's evidence.
            if self._record.attempt_count < 2:
                raise InvalidTransitionError("revision must advance the canonical attempt")
            if self._record.attempt_count > self._record.task_contract.max_attempts:
                raise InvalidTransitionError("attempt budget exhausted")
            if not self._record.attempt_history:
                raise InvalidTransitionError("revision must archive the previous attempt")
            if self._record.observed_checks or self._record.observed_patch is not None:
                raise InvalidTransitionError("a new attempt must start without stale evidence")
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
            # Completion evidence itself is proven once, by validate_completed_record.
            # Only the transition-legality precondition belongs here.
            if self._record.stop_decision is None:
                raise InvalidTransitionError("StopDecision is required before completion")
            if self._record.stop_decision.next_action is not NextAction.COMPLETE:
                raise InvalidTransitionError("completion requires next_action=complete")

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
        error = artifact_file_error(raw_path, label)
        if error is not None:
            raise InvalidTransitionError(error)

    def _completion_evidence_is_sufficient(
        self,
        observed_checks: tuple[ObservedCheckResult, ...],
    ) -> bool:
        """Screen deterministic evidence using the shared completion predicates."""

        return (
            required_check_coverage_error(
                self._record.task_contract.required_checks,
                observed_checks,
            )
            is None
            and patch_evidence_error(self._record.observed_patch) is None
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

    def _build_record(self, **updates: object) -> RunRecord:
        values = {
            field_name: getattr(self._record, field_name)
            for field_name in type(self._record).model_fields
        }
        values.update(updates)
        # Revised records are only valid against a trusted external binding, and
        # Runtime supplies its own captured hash rather than anything derived
        # from the record being built.
        return RunRecord.model_validate(
            values,
            context=trusted_contract_context(self._canonical_task_contract_hash),
        )

    def _replace_record(self, **updates: object) -> None:
        self._record = self._build_record(**updates)
