from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tomtit.domain.contracts import (
    AcceptanceCriterion,
    CriterionAssignment,
    Finding,
    ForbiddenAction,
    ProgressSnapshot,
    RequiredCheck,
    ScopeRule,
    StopDecision,
    TaskContract,
    VerificationPlan,
    VerificationResult,
    VerifierIdentity,
    WorkerCapabilities,
    WorkerResult,
    resolve_owner_decision,
)
from tomtit.domain.enums import (
    CommandControl,
    CommandVisibility,
    EnforcementLevel,
    FilesystemIsolation,
    FindingSeverity,
    ForbiddenActionType,
    ImplementerRelationship,
    NetworkIsolation,
    NextAction,
    Outcome,
    OwnerDecision,
    ScopeOperation,
    StopReason,
    VerificationMethod,
    VerificationResultStatus,
    VerificationMode,
    WorkerStatus,
    WorkspaceIsolation,
)


def unit_check() -> RequiredCheck:
    return RequiredCheck(
        id="unit-tests",
        argv=["python", "-m", "pytest"],
        cwd_relative=".",
        timeout_seconds=300,
        expected_exit_codes=[0],
    )


def deterministic_contract() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        goal="Add the domain contracts",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="criterion-1",
                description="Unit tests pass",
                verification_method=VerificationMethod.DETERMINISTIC,
                required_check_ids=["unit-tests"],
            )
        ],
        allowed_scope=[
            ScopeRule(pattern="src/**", operations=[ScopeOperation.CREATE, ScopeOperation.MODIFY])
        ],
        required_checks=[unit_check()],
        time_budget_seconds=600,
    )


def test_task_contract_round_trips_json() -> None:
    contract = deterministic_contract()
    restored = TaskContract.model_validate_json(contract.model_dump_json())
    assert restored == contract


@pytest.mark.parametrize("cwd", ["/tmp", "../repo", "src/../secrets", "C:/repo"])
def test_required_check_rejects_unsafe_cwd(cwd: str) -> None:
    with pytest.raises(ValidationError):
        RequiredCheck(
            id="unsafe",
            argv=["python", "-m", "pytest"],
            cwd_relative=cwd,
            timeout_seconds=30,
            expected_exit_codes=[0],
        )


@pytest.mark.parametrize(
    "argv",
    [["pytest", "&&", "echo"], ["echo", "$(whoami)"], [], "python -m pytest"],
)
def test_required_check_rejects_shell_or_empty_argv(argv: object) -> None:
    with pytest.raises(ValidationError):
        RequiredCheck(
            id="unsafe",
            argv=argv,
            cwd_relative=".",
            timeout_seconds=30,
            expected_exit_codes=[0],
        )


@pytest.mark.parametrize("pattern", ["/src/**", "../src/**", "src/../secret", "src\\**"])
def test_scope_rule_rejects_unsafe_pattern(pattern: str) -> None:
    with pytest.raises(ValidationError):
        ScopeRule(pattern=pattern, operations=[ScopeOperation.MODIFY])


def test_task_contract_rejects_unknown_required_check_reference() -> None:
    with pytest.raises(ValidationError, match="unknown required_check_ids"):
        TaskContract(
            task_id="task-1",
            goal="Invalid contract",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="criterion-1",
                    description="Unknown check",
                    verification_method=VerificationMethod.DETERMINISTIC,
                    required_check_ids=["missing"],
                )
            ],
            allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
            required_checks=[unit_check()],
            time_budget_seconds=60,
        )


def test_task_contract_rejects_missing_mandatory_forbidden_actions() -> None:
    with pytest.raises(ValidationError, match="missing mandatory forbidden actions"):
        TaskContract(
            task_id="task-1",
            goal="Invalid policy",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="criterion-1",
                    description="Owner checks",
                    verification_method=VerificationMethod.OWNER,
                )
            ],
            allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
            forbidden_actions=[
                ForbiddenAction(
                    action=ForbiddenActionType.GIT_PUSH,
                    enforcement=EnforcementLevel.REQUIRED,
                )
            ],
            time_budget_seconds=60,
        )


def test_custom_forbidden_action_is_advisory_and_described() -> None:
    with pytest.raises(ValidationError):
        ForbiddenAction(
            action=ForbiddenActionType.CUSTOM,
            enforcement=EnforcementLevel.REQUIRED,
            description="Do not replace auth",
        )
    with pytest.raises(ValidationError):
        ForbiddenAction(
            action=ForbiddenActionType.CUSTOM,
            enforcement=EnforcementLevel.ADVISORY,
        )


def test_verification_plan_accepts_mode_b_deterministic_contract() -> None:
    plan = VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=True,
            )
        ],
    )
    plan.assert_compatible(deterministic_contract())


def test_verification_plan_rejects_unavailable_method_before_worker() -> None:
    plan = VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=False,
            )
        ],
    )
    with pytest.raises(ValueError, match="unavailable"):
        plan.assert_compatible(deterministic_contract())


def test_llm_verifier_criterion_requires_mode_c() -> None:
    contract = TaskContract(
        task_id="task-llm",
        goal="Review semantics",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="semantic-review",
                description="Behavior matches intent",
                verification_method=VerificationMethod.LLM_VERIFIER,
            )
        ],
        allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
        time_budget_seconds=60,
    )
    plan = VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="semantic-review",
                verification_method=VerificationMethod.LLM_VERIFIER,
                available=True,
            )
        ],
    )
    with pytest.raises(ValueError, match="Mode C"):
        plan.assert_compatible(contract)


def test_owner_assignment_must_anticipate_waiting_owner() -> None:
    with pytest.raises(ValidationError, match="WAITING_OWNER"):
        CriterionAssignment(
            criterion_id="owner-review",
            verification_method=VerificationMethod.OWNER,
            available=True,
        )


def test_attempt_invariants_are_machine_checked() -> None:
    with pytest.raises(ValidationError):
        TaskContract.model_validate(
            {**deterministic_contract().model_dump(), "max_attempts": 3}
        )
    with pytest.raises(ValidationError):
        TaskContract(
            **{
                **deterministic_contract().model_dump(),
                "max_attempts": 2,
                "hard_max_attempts": 1,
            }
        )


def test_stop_decision_enforces_deterministic_mapping() -> None:
    decision = StopDecision(
        outcome=Outcome.COMPLETED,
        stop_reason=StopReason.ACCEPTANCE_SATISFIED,
        repairable=False,
        owner_input_required=False,
        next_action=NextAction.COMPLETE,
    )
    assert decision.outcome is Outcome.COMPLETED

    with pytest.raises(ValidationError):
        StopDecision(
            outcome=Outcome.COMPLETED,
            stop_reason=StopReason.NO_PROGRESS,
            repairable=False,
            owner_input_required=False,
            next_action=NextAction.COMPLETE,
        )


def test_owner_decision_mapping() -> None:
    accepted = resolve_owner_decision(
        OwnerDecision.ACCEPT,
        completion_conditions_met=True,
    )
    assert accepted.outcome is Outcome.COMPLETED

    revised = resolve_owner_decision(OwnerDecision.REVISE, revision_allowed=True)
    assert revised.next_state.value == "REVISING"

    rejected = resolve_owner_decision(OwnerDecision.REJECT)
    assert rejected.stop_reason is StopReason.OWNER_REJECTED

    cancelled = resolve_owner_decision(OwnerDecision.CANCEL)
    assert cancelled.stop_reason is StopReason.USER_CANCELLED


@pytest.mark.parametrize(
    "decision,kwargs",
    [
        (OwnerDecision.ACCEPT, {}),
        (OwnerDecision.REVISE, {}),
    ],
)
def test_owner_decision_requires_remaining_conditions(
    decision: OwnerDecision, kwargs: dict[str, bool]
) -> None:
    with pytest.raises(ValueError):
        resolve_owner_decision(decision, **kwargs)


def test_contract_rejects_whitespace_only_goal() -> None:
    with pytest.raises(ValidationError):
        TaskContract(
            task_id="task-1",
            goal="   ",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="owner-check",
                    description="Owner confirms",
                    verification_method=VerificationMethod.OWNER,
                )
            ],
            allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
            time_budget_seconds=60,
        )


def test_deterministic_criterion_requires_check_reference() -> None:
    with pytest.raises(ValidationError, match="at least one required check"):
        AcceptanceCriterion(
            id="criterion-1",
            description="Must be checked",
            verification_method=VerificationMethod.DETERMINISTIC,
        )


def test_contract_rejects_duplicate_ids() -> None:
    criterion = AcceptanceCriterion(
        id="owner-check",
        description="Owner confirms",
        verification_method=VerificationMethod.OWNER,
    )
    with pytest.raises(ValidationError, match="criterion IDs"):
        TaskContract(
            task_id="task-1",
            goal="Duplicate criteria",
            acceptance_criteria=[criterion, criterion],
            allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
            time_budget_seconds=60,
        )

    check = unit_check()
    deterministic = AcceptanceCriterion(
        id="criterion-1",
        description="Tests pass",
        verification_method=VerificationMethod.DETERMINISTIC,
        required_check_ids=[check.id],
    )
    with pytest.raises(ValidationError, match="check IDs"):
        TaskContract(
            task_id="task-2",
            goal="Duplicate checks",
            acceptance_criteria=[deterministic],
            allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
            required_checks=[check, check],
            time_budget_seconds=60,
        )


def test_custom_forbidden_action_positive_case() -> None:
    action = ForbiddenAction(
        action=ForbiddenActionType.CUSTOM,
        description="Do not replace the authentication library",
        enforcement=EnforcementLevel.ADVISORY,
    )
    assert action.enforcement is EnforcementLevel.ADVISORY


def test_owner_criterion_plan_supports_mode_a_waiting_owner() -> None:
    contract = TaskContract(
        task_id="task-owner",
        goal="Owner reviews result",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="owner-review",
                description="Owner accepts the patch",
                verification_method=VerificationMethod.OWNER,
            )
        ],
        allowed_scope=[ScopeRule(pattern="src/**", operations=[ScopeOperation.MODIFY])],
        time_budget_seconds=60,
    )
    plan = VerificationPlan(
        mode=VerificationMode.A,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="owner-review",
                verification_method=VerificationMethod.OWNER,
                available=True,
                requires_owner_decision=True,
            )
        ],
    )
    plan.assert_compatible(contract)


def test_verification_plan_rejects_assignment_mismatch_and_duplicates() -> None:
    duplicate_plan = VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=True,
            ),
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=True,
            ),
        ],
    )
    with pytest.raises(ValueError, match="unique criterion IDs"):
        duplicate_plan.assert_compatible(deterministic_contract())

    wrong_method = VerificationPlan(
        mode=VerificationMode.C,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.LLM_VERIFIER,
                available=True,
            )
        ],
    )
    with pytest.raises(ValueError, match="wrong verification method"):
        wrong_method.assert_compatible(deterministic_contract())


def test_worker_capabilities_reports_limited_without_full_isolation() -> None:
    limited = WorkerCapabilities(
        non_interactive_invocation=True,
        structured_output=True,
        command_visibility=CommandVisibility.FULL,
        command_control=CommandControl.ENFORCEABLE,
        session_resume=False,
        workspace_isolation=WorkspaceIsolation.WORKTREE,
        filesystem_isolation=FilesystemIsolation.NONE,
        network_isolation=NetworkIsolation.NONE,
    )
    assert limited.limited is True

    controlled = WorkerCapabilities(
        non_interactive_invocation=True,
        structured_output=True,
        command_visibility=CommandVisibility.FULL,
        command_control=CommandControl.ENFORCEABLE,
        session_resume=False,
        workspace_isolation=WorkspaceIsolation.SANDBOX,
        filesystem_isolation=FilesystemIsolation.SANDBOXED,
        network_isolation=NetworkIsolation.DISABLED,
    )
    assert controlled.limited is False


def test_verification_result_rejects_false_pass() -> None:
    identity = VerifierIdentity(
        provider="fake",
        model="fake-verifier",
        relationship_to_implementer=ImplementerRelationship.DIFFERENT_MODEL,
    )
    blocking = Finding(
        finding_id="finding-1",
        fingerprint="scope:src/auth.py:criterion-1",
        severity=FindingSeverity.BLOCKING,
        category="scope",
        file="src/auth.py",
        line=1,
        message="Outside approved scope",
    )
    with pytest.raises(ValidationError, match="blocking finding"):
        VerificationResult(
            status=VerificationResultStatus.PASS,
            verifier_identity=identity,
            findings=[blocking],
        )
    with pytest.raises(ValidationError, match="missing evidence"):
        VerificationResult(
            status=VerificationResultStatus.PASS,
            verifier_identity=identity,
            missing_evidence=["unit-tests"],
        )


def test_stop_decision_revision_contract() -> None:
    revision = StopDecision(
        outcome=None,
        stop_reason=None,
        repairable=True,
        owner_input_required=False,
        next_action=NextAction.REVISE,
    )
    assert revision.next_action is NextAction.REVISE

    with pytest.raises(ValidationError, match="repairable=true"):
        StopDecision(
            outcome=None,
            stop_reason=None,
            repairable=False,
            owner_input_required=False,
            next_action=NextAction.REVISE,
        )


def test_progress_snapshot_rejects_duplicate_set_members() -> None:
    with pytest.raises(ValidationError, match="failed_check_ids"):
        ProgressSnapshot(
            attempt_number=1,
            failed_check_ids=["unit-tests", "unit-tests"],
            patch_digest="sha256:abc",
        )


def test_canonical_contract_collections_are_deeply_immutable() -> None:
    contract = deterministic_contract()
    plan = VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="criterion-1",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=True,
            )
        ],
    )
    worker_result = WorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Implemented the requested change",
        changed_files=["src/tomtit/domain/contracts.py"],
        commands_executed=["python -m pytest"],
        claimed_checks=["unit-tests"],
        scope_concerns=[],
        remaining_risks=[],
        questions=[],
        raw_output_path="artifacts/worker-result.json",
    )
    snapshot = ProgressSnapshot(
        attempt_number=1,
        failed_check_ids=[],
        blocking_finding_fingerprints=[],
        scope_violations=[],
        satisfied_criterion_ids=["criterion-1"],
        critical_regressions=[],
        patch_digest="sha256:abc",
    )

    collections = (
        contract.acceptance_criteria,
        contract.allowed_scope,
        contract.forbidden_actions,
        contract.required_checks,
        contract.acceptance_criteria[0].required_check_ids,
        contract.required_checks[0].argv,
        contract.required_checks[0].expected_exit_codes,
        contract.allowed_scope[0].operations,
        plan.criterion_assignments,
        worker_result.changed_files,
        worker_result.commands_executed,
        worker_result.claimed_checks,
        snapshot.satisfied_criterion_ids,
    )
    assert all(isinstance(collection, tuple) for collection in collections)

    with pytest.raises(AttributeError):
        contract.required_checks.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        contract.acceptance_criteria[0].required_check_ids.append(  # type: ignore[attr-defined]
            "other"
        )
    with pytest.raises(TypeError):
        contract.acceptance_criteria[0] = contract.acceptance_criteria[0]  # type: ignore[index]


def test_list_input_serializes_as_json_arrays_and_round_trips_to_tuples() -> None:
    contract = deterministic_contract()
    payload = json.loads(contract.model_dump_json())

    assert isinstance(payload["acceptance_criteria"], list)
    assert isinstance(payload["acceptance_criteria"][0]["required_check_ids"], list)
    assert isinstance(payload["allowed_scope"], list)
    assert isinstance(payload["required_checks"], list)
    assert isinstance(payload["required_checks"][0]["argv"], list)

    restored = TaskContract.model_validate_json(contract.model_dump_json())
    assert isinstance(restored.acceptance_criteria, tuple)
    assert isinstance(restored.acceptance_criteria[0].required_check_ids, tuple)
    assert isinstance(restored.required_checks[0].argv, tuple)
    assert restored == contract


def test_standard_forbidden_action_cannot_be_advisory() -> None:
    with pytest.raises(ValidationError, match="required enforcement"):
        ForbiddenAction(
            action=ForbiddenActionType.GIT_PUSH,
            enforcement=EnforcementLevel.ADVISORY,
        )


def test_task_contract_accepts_multiple_distinct_custom_forbidden_actions() -> None:
    base = deterministic_contract()
    contract = TaskContract(
        **{
            **base.model_dump(),
            "forbidden_actions": [
                *base.forbidden_actions,
                ForbiddenAction(
                    action=ForbiddenActionType.CUSTOM,
                    description="Do not replace the authentication library",
                    enforcement=EnforcementLevel.ADVISORY,
                ),
                ForbiddenAction(
                    action=ForbiddenActionType.CUSTOM,
                    description="Do not change the public API",
                    enforcement=EnforcementLevel.ADVISORY,
                ),
            ],
        }
    )

    custom_actions = [
        item for item in contract.forbidden_actions if item.action is ForbiddenActionType.CUSTOM
    ]
    assert len(custom_actions) == 2


def test_task_contract_rejects_duplicate_normalized_custom_descriptions() -> None:
    base = deterministic_contract()
    with pytest.raises(ValidationError, match="descriptions must be unique"):
        TaskContract(
            **{
                **base.model_dump(),
                "forbidden_actions": [
                    *base.forbidden_actions,
                    ForbiddenAction(
                        action=ForbiddenActionType.CUSTOM,
                        description="Do not replace the authentication library",
                        enforcement=EnforcementLevel.ADVISORY,
                    ),
                    ForbiddenAction(
                        action=ForbiddenActionType.CUSTOM,
                        description="  do   not replace THE authentication LIBRARY  ",
                        enforcement=EnforcementLevel.ADVISORY,
                    ),
                ],
            }
        )


@pytest.mark.parametrize("pattern", ["src/[ab].py", "src/{a,b}.py", "src/***.py"])
def test_scope_rule_rejects_glob_syntax_outside_v0(pattern: str) -> None:
    with pytest.raises(ValidationError, match="scope pattern"):
        ScopeRule(pattern=pattern, operations=[ScopeOperation.MODIFY])
