"""Machine-readable execution contracts for TOMTIT-Code MVP V0."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
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
    RunState,
    ScopeOperation,
    StopReason,
    VerificationMethod,
    VerificationMode,
    VerificationResultStatus,
    WorkerStatus,
    WorkspaceIsolation,
)

NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]


def _list_to_tuple(value: object) -> object:
    """Accept ergonomic list input while storing canonical collections as tuples."""

    if isinstance(value, list):
        return tuple(value)
    return value


_SHELL_META_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "<<", "2>", "2>>"}
_STANDARD_REQUIRED_FORBIDDEN_ACTIONS = {
    ForbiddenActionType.GIT_PUSH,
    ForbiddenActionType.GIT_MERGE,
    ForbiddenActionType.DEPLOY,
    ForbiddenActionType.DESTRUCTIVE_DATA_OPERATION,
    ForbiddenActionType.CREDENTIAL_ACCESS,
    ForbiddenActionType.WRITE_OUTSIDE_WORKSPACE,
}


class DomainModel(BaseModel):
    """Strict immutable value object used for public domain contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


def _validate_root_relative_posix(value: str, *, allow_dot: bool) -> str:
    if not value or not value.strip():
        raise ValueError("path must be non-empty")
    if "\\" in value:
        raise ValueError("path must use POSIX separators")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError("absolute or drive-qualified paths are forbidden")

    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("absolute paths are forbidden")
    if ".." in path.parts:
        raise ValueError("parent traversal is forbidden")
    if not allow_dot and value in {".", "./"}:
        raise ValueError("scope pattern must identify a path")

    normalized = path.as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized != value and not (allow_dot and value == "./" and normalized == "."):
        raise ValueError("path must already be POSIX-normalized")
    return normalized


class RequiredCheck(DomainModel):
    id: NonEmptyString
    argv: Annotated[tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)] = Field(
        min_length=1
    )
    cwd_relative: NonEmptyString = "."
    timeout_seconds: PositiveInt
    expected_exit_codes: Annotated[tuple[int, ...], BeforeValidator(_list_to_tuple)] = Field(
        min_length=1
    )

    @field_validator("argv")
    @classmethod
    def reject_shell_syntax(cls, argv: tuple[str, ...]) -> tuple[str, ...]:
        for token in argv:
            if token in _SHELL_META_TOKENS:
                raise ValueError(f"shell syntax token is unsupported: {token}")
            if "$(" in token or "`" in token or "${" in token:
                raise ValueError("shell expansion is unsupported")
        return argv

    @field_validator("cwd_relative")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return _validate_root_relative_posix(value, allow_dot=True)

    @field_validator("expected_exit_codes")
    @classmethod
    def unique_exit_codes(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if len(values) != len(set(values)):
            raise ValueError("expected_exit_codes must be unique")
        return values


class ScopeRule(DomainModel):
    pattern: NonEmptyString
    operations: Annotated[
        tuple[ScopeOperation, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        normalized = _validate_root_relative_posix(value, allow_dot=False)
        if any(token in normalized for token in ("[", "]", "{", "}")):
            raise ValueError("scope pattern supports only literal paths, *, ?, and **")
        if re.search(r"\*{3,}", normalized):
            raise ValueError("scope pattern cannot contain three or more consecutive *")
        return normalized

    @field_validator("operations")
    @classmethod
    def unique_operations(
        cls, values: tuple[ScopeOperation, ...]
    ) -> tuple[ScopeOperation, ...]:
        if len(values) != len(set(values)):
            raise ValueError("scope operations must be unique")
        return values


class ForbiddenAction(DomainModel):
    action: ForbiddenActionType
    enforcement: EnforcementLevel
    description: str | None = None

    @model_validator(mode="after")
    def validate_custom_action(self) -> ForbiddenAction:
        if self.action is ForbiddenActionType.CUSTOM:
            if not self.description or not self.description.strip():
                raise ValueError("custom forbidden actions require a description")
            if self.enforcement is not EnforcementLevel.ADVISORY:
                raise ValueError("custom forbidden actions are advisory in V0")
        else:
            if self.enforcement is not EnforcementLevel.REQUIRED:
                raise ValueError("standard forbidden actions require required enforcement")
            if self.description is not None and not self.description.strip():
                raise ValueError("description cannot be blank")
        return self


def default_forbidden_actions() -> tuple[ForbiddenAction, ...]:
    return tuple(
        ForbiddenAction(action=action, enforcement=EnforcementLevel.REQUIRED)
        for action in sorted(_STANDARD_REQUIRED_FORBIDDEN_ACTIONS, key=lambda item: item.value)
    )


class AcceptanceCriterion(DomainModel):
    id: NonEmptyString
    description: NonEmptyString
    verification_method: VerificationMethod
    required_check_ids: Annotated[
        tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_method_requirements(self) -> AcceptanceCriterion:
        if len(self.required_check_ids) != len(set(self.required_check_ids)):
            raise ValueError("required_check_ids must be unique")
        if (
            self.verification_method is VerificationMethod.DETERMINISTIC
            and not self.required_check_ids
        ):
            raise ValueError("deterministic criteria require at least one required check")
        return self


class TaskContract(DomainModel):
    task_id: NonEmptyString
    goal: NonEmptyString
    acceptance_criteria: Annotated[
        tuple[AcceptanceCriterion, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)
    allowed_scope: Annotated[
        tuple[ScopeRule, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)
    forbidden_actions: Annotated[
        tuple[ForbiddenAction, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=default_forbidden_actions)
    required_checks: Annotated[
        tuple[RequiredCheck, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    max_attempts: int = Field(default=2, ge=1, le=2)
    hard_max_attempts: int = Field(default=3, ge=1, le=3)
    time_budget_seconds: PositiveInt
    cost_budget_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_invariants(self) -> TaskContract:
        if self.max_attempts > self.hard_max_attempts:
            raise ValueError("max_attempts cannot exceed hard_max_attempts")

        criterion_ids = [criterion.id for criterion in self.acceptance_criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion IDs must be unique")

        check_ids = [check.id for check in self.required_checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("required check IDs must be unique")
        known_checks = set(check_ids)
        missing_refs = {
            check_id
            for criterion in self.acceptance_criteria
            for check_id in criterion.required_check_ids
            if check_id not in known_checks
        }
        if missing_refs:
            raise ValueError(f"unknown required_check_ids: {sorted(missing_refs)}")

        standard_actions = [
            item.action
            for item in self.forbidden_actions
            if item.action is not ForbiddenActionType.CUSTOM
        ]
        if len(standard_actions) != len(set(standard_actions)):
            raise ValueError("standard forbidden actions must be unique by action type")

        custom_descriptions = [
            " ".join(item.description.split()).casefold()
            for item in self.forbidden_actions
            if item.action is ForbiddenActionType.CUSTOM and item.description is not None
        ]
        if len(custom_descriptions) != len(set(custom_descriptions)):
            raise ValueError("custom forbidden action descriptions must be unique")

        missing_actions = _STANDARD_REQUIRED_FORBIDDEN_ACTIONS.difference(standard_actions)
        if missing_actions:
            missing = sorted(action.value for action in missing_actions)
            raise ValueError(f"missing mandatory forbidden actions: {missing}")
        return self


class CriterionAssignment(DomainModel):
    criterion_id: NonEmptyString
    verification_method: VerificationMethod
    available: bool
    requires_owner_decision: bool = False

    @model_validator(mode="after")
    def validate_owner_handling(self) -> CriterionAssignment:
        if (
            self.verification_method is VerificationMethod.OWNER
            and not self.requires_owner_decision
        ):
            raise ValueError("owner verification must anticipate WAITING_OWNER")
        if (
            self.verification_method is not VerificationMethod.OWNER
            and self.requires_owner_decision
        ):
            raise ValueError("requires_owner_decision is valid only for owner verification")
        return self


class VerificationPlan(DomainModel):
    mode: VerificationMode
    criterion_assignments: Annotated[
        tuple[CriterionAssignment, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)

    def assert_compatible(self, task_contract: TaskContract) -> None:
        """Fail closed before PREPARING when verification cannot satisfy the contract."""

        assignments = {item.criterion_id: item for item in self.criterion_assignments}
        if len(assignments) != len(self.criterion_assignments):
            raise ValueError("criterion assignments must have unique criterion IDs")

        expected_ids = {criterion.id for criterion in task_contract.acceptance_criteria}
        actual_ids = set(assignments)
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            unexpected = sorted(actual_ids - expected_ids)
            raise ValueError(
                f"criterion assignment mismatch: missing={missing}, unexpected={unexpected}"
            )

        for criterion in task_contract.acceptance_criteria:
            assignment = assignments[criterion.id]
            if assignment.verification_method is not criterion.verification_method:
                raise ValueError(
                    f"criterion {criterion.id} assigned to the wrong verification method"
                )
            if not assignment.available:
                raise ValueError(f"verification method unavailable for criterion {criterion.id}")
            if criterion.verification_method is VerificationMethod.DETERMINISTIC:
                if self.mode not in {VerificationMode.B, VerificationMode.C}:
                    raise ValueError("deterministic criteria require Mode B or C")
                if not criterion.required_check_ids:
                    raise ValueError(
                        f"deterministic criterion {criterion.id} has no required checks"
                    )
            if (
                criterion.verification_method is VerificationMethod.LLM_VERIFIER
                and self.mode is not VerificationMode.C
            ):
                raise ValueError("llm_verifier criteria require Mode C")
            if (
                criterion.verification_method is VerificationMethod.OWNER
                and not assignment.requires_owner_decision
            ):
                raise ValueError("owner criteria must lead to WAITING_OWNER")


class WorkerCapabilities(DomainModel):
    non_interactive_invocation: bool
    structured_output: bool
    command_visibility: CommandVisibility
    command_control: CommandControl
    session_resume: bool
    workspace_isolation: WorkspaceIsolation
    filesystem_isolation: FilesystemIsolation
    network_isolation: NetworkIsolation

    @property
    def limited(self) -> bool:
        return (
            self.command_visibility is not CommandVisibility.FULL
            or self.command_control is not CommandControl.ENFORCEABLE
            or self.workspace_isolation is WorkspaceIsolation.NONE
            or self.filesystem_isolation is FilesystemIsolation.NONE
            or self.network_isolation is NetworkIsolation.NONE
        )


class WorkerResult(DomainModel):
    status: WorkerStatus
    summary: NonEmptyString
    changed_files: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    commands_executed: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    claimed_checks: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    scope_concerns: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    remaining_risks: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    questions: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    session_reference: str | None = None
    raw_output_path: NonEmptyString


class Finding(DomainModel):
    finding_id: NonEmptyString
    fingerprint: NonEmptyString
    severity: FindingSeverity
    category: NonEmptyString
    file: str | None = None
    line: PositiveInt | None = None
    message: NonEmptyString
    evidence_refs: Annotated[
        tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    acceptance_criterion_id: str | None = None

    @field_validator("file")
    @classmethod
    def validate_optional_file(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_root_relative_posix(value, allow_dot=False)


class VerifierIdentity(DomainModel):
    provider: NonEmptyString
    model: NonEmptyString
    session_reference: str | None = None
    relationship_to_implementer: ImplementerRelationship


class VerificationResult(DomainModel):
    status: VerificationResultStatus
    verifier_identity: VerifierIdentity
    findings: Annotated[tuple[Finding, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    missing_evidence: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    remaining_risks: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def validate_status(self) -> VerificationResult:
        has_blocking = any(
            finding.severity is FindingSeverity.BLOCKING for finding in self.findings
        )
        if self.status is VerificationResultStatus.PASS and has_blocking:
            raise ValueError("pass result cannot contain a blocking finding")
        if self.status is VerificationResultStatus.PASS and self.missing_evidence:
            raise ValueError("pass result cannot have missing evidence")
        return self


class StopDecision(DomainModel):
    outcome: Outcome | None
    stop_reason: StopReason | None
    repairable: bool
    owner_input_required: bool
    next_action: NextAction

    @model_validator(mode="after")
    def validate_decision(self) -> StopDecision:
        if self.next_action is NextAction.REVISE:
            if self.outcome is not None or self.stop_reason is not None:
                raise ValueError("revise must not set outcome or stop_reason")
            if not self.repairable or self.owner_input_required:
                raise ValueError("revise requires repairable=true and no owner input")
            return self

        if self.outcome is None or self.stop_reason is None:
            raise ValueError("stopping or suspending decisions require outcome and stop_reason")

        expected_outcomes = {
            StopReason.ACCEPTANCE_SATISFIED: Outcome.COMPLETED,
            StopReason.USER_DECISION_REQUIRED: Outcome.WAITING_OWNER,
            StopReason.OWNER_REJECTED: Outcome.FAILED,
            StopReason.TIME_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.QUOTA_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.COST_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.USER_CANCELLED: Outcome.CANCELLED,
        }
        expected = expected_outcomes.get(self.stop_reason, Outcome.FAILED)
        if self.outcome is not expected:
            raise ValueError(
                f"stop reason {self.stop_reason.value} must map to outcome {expected.value}"
            )

        if self.next_action is NextAction.COMPLETE and self.outcome is not Outcome.COMPLETED:
            raise ValueError("complete action requires COMPLETED outcome")
        if self.next_action is NextAction.WAIT_OWNER:
            if self.outcome is not Outcome.WAITING_OWNER or not self.owner_input_required:
                raise ValueError("wait_owner requires WAITING_OWNER and owner input")
        elif self.owner_input_required:
            raise ValueError("owner_input_required requires wait_owner")
        if self.next_action is NextAction.CANCEL and self.outcome is not Outcome.CANCELLED:
            raise ValueError("cancel action requires CANCELLED outcome")
        if self.next_action is NextAction.FAIL and self.outcome not in {
            Outcome.FAILED,
            Outcome.BUDGET_EXHAUSTED,
        }:
            raise ValueError("fail action requires FAILED or BUDGET_EXHAUSTED outcome")
        return self


class OwnerDecisionResolution(DomainModel):
    next_state: RunState
    outcome: Outcome | None
    stop_reason: StopReason | None


def resolve_owner_decision(
    decision: OwnerDecision,
    *,
    completion_conditions_met: bool = False,
    revision_allowed: bool = False,
) -> OwnerDecisionResolution:
    """Resolve an owner decision without granting the owner canonical write authority."""

    if decision is OwnerDecision.ACCEPT:
        if not completion_conditions_met:
            raise ValueError("ACCEPT requires all remaining completion conditions to be met")
        return OwnerDecisionResolution(
            next_state=RunState.COMPLETED,
            outcome=Outcome.COMPLETED,
            stop_reason=StopReason.ACCEPTANCE_SATISFIED,
        )
    if decision is OwnerDecision.REVISE:
        if not revision_allowed:
            raise ValueError("REVISE requires remaining attempt and budget")
        return OwnerDecisionResolution(
            next_state=RunState.REVISING,
            outcome=None,
            stop_reason=None,
        )
    if decision is OwnerDecision.REJECT:
        return OwnerDecisionResolution(
            next_state=RunState.FAILED,
            outcome=Outcome.FAILED,
            stop_reason=StopReason.OWNER_REJECTED,
        )
    return OwnerDecisionResolution(
        next_state=RunState.CANCELLED,
        outcome=Outcome.CANCELLED,
        stop_reason=StopReason.USER_CANCELLED,
    )


class ProgressSnapshot(DomainModel):
    attempt_number: PositiveInt
    failed_check_ids: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    blocking_finding_fingerprints: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    scope_violations: Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)] = Field(
        default_factory=tuple
    )
    satisfied_criterion_ids: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    critical_regressions: Annotated[
        tuple[str, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    patch_digest: NonEmptyString

    @model_validator(mode="after")
    def reject_duplicate_set_members(self) -> ProgressSnapshot:
        for field_name in (
            "failed_check_ids",
            "blocking_finding_fingerprints",
            "scope_violations",
            "satisfied_criterion_ids",
            "critical_regressions",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        return self
