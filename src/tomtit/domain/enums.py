"""Closed vocabularies used by TOMTIT domain contracts."""

from enum import Enum


class StringEnum(str, Enum):
    """String enum with stable JSON serialization."""


class VerificationMethod(StringEnum):
    DETERMINISTIC = "deterministic"
    LLM_VERIFIER = "llm_verifier"
    OWNER = "owner"


class VerificationMode(StringEnum):
    A = "A"
    B = "B"
    C = "C"


class CriterionStatus(StringEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNRESOLVED = "UNRESOLVED"


class ScopeOperation(StringEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class ForbiddenActionType(StringEnum):
    GIT_PUSH = "git_push"
    GIT_MERGE = "git_merge"
    DEPLOY = "deploy"
    DESTRUCTIVE_DATA_OPERATION = "destructive_data_operation"
    CREDENTIAL_ACCESS = "credential_access"
    WRITE_OUTSIDE_WORKSPACE = "write_outside_workspace"
    CUSTOM = "custom"


class EnforcementLevel(StringEnum):
    REQUIRED = "required"
    ADVISORY = "advisory"


class WorkerStatus(StringEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class CommandVisibility(StringEnum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class CommandControl(StringEnum):
    ENFORCEABLE = "enforceable"
    ADVISORY = "advisory"
    UNAVAILABLE = "unavailable"


class WorkspaceIsolation(StringEnum):
    WORKTREE = "worktree"
    TEMPORARY_CLONE = "temporary_clone"
    SANDBOX = "sandbox"
    NONE = "none"


class FilesystemIsolation(StringEnum):
    SANDBOXED = "sandboxed"
    NONE = "none"


class NetworkIsolation(StringEnum):
    DISABLED = "disabled"
    RESTRICTED = "restricted"
    NONE = "none"


class FindingSeverity(StringEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class VerificationResultStatus(StringEnum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_OWNER = "needs_owner"


class VerificationStatus(StringEnum):
    SELF_CHECK_ONLY = "SELF_CHECK_ONLY"
    DETERMINISTICALLY_CHECKED = "DETERMINISTICALLY_CHECKED"
    LLM_REVIEWED = "LLM_REVIEWED"


class EvaluationEventType(StringEnum):
    TASK_SUBMITTED = "TASK_SUBMITTED"
    CONTRACT_INFERRED = "CONTRACT_INFERRED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    CLARIFICATION_RECEIVED = "CLARIFICATION_RECEIVED"
    RUN_STARTED = "RUN_STARTED"
    WORKSPACE_PREPARED = "WORKSPACE_PREPARED"
    WORKER_ASSIGNED = "WORKER_ASSIGNED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_FINISHED = "WORKER_FINISHED"
    EVIDENCE_COLLECTED = "EVIDENCE_COLLECTED"
    CHECK_PASSED = "CHECK_PASSED"
    CHECK_FAILED = "CHECK_FAILED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_FINISHED = "VERIFICATION_FINISHED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    WAITING_OWNER = "WAITING_OWNER"
    OWNER_DECISION_RECORDED = "OWNER_DECISION_RECORDED"
    RUN_RESUMED = "RUN_RESUMED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SUSPENSION_REPORT_GENERATED = "SUSPENSION_REPORT_GENERATED"
    REPORT_GENERATED = "REPORT_GENERATED"
    RESULT_ACCEPTED = "RESULT_ACCEPTED"
    RESULT_EDITED = "RESULT_EDITED"
    RESULT_REJECTED = "RESULT_REJECTED"
    ESCAPED_DEFECT_REPORTED = "ESCAPED_DEFECT_REPORTED"
    ESCAPED_DEFECT_CLEARED = "ESCAPED_DEFECT_CLEARED"
    OUTCOME_SNAPSHOT_GENERATED = "OUTCOME_SNAPSHOT_GENERATED"


class ImplementerRelationship(StringEnum):
    SAME_MODEL_NEW_SESSION = "same_model_new_session"
    DIFFERENT_MODEL = "different_model"
    DIFFERENT_PROVIDER = "different_provider"


class Outcome(StringEnum):
    COMPLETED = "COMPLETED"
    WAITING_OWNER = "WAITING_OWNER"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"


class RunState(StringEnum):
    CREATED = "CREATED"
    CONTRACT_READY = "CONTRACT_READY"
    PREPARING = "PREPARING"
    IMPLEMENTING = "IMPLEMENTING"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    CHECKING = "CHECKING"
    VERIFYING = "VERIFYING"
    REVISING = "REVISING"
    WAITING_OWNER = "WAITING_OWNER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"


class StopReason(StringEnum):
    ACCEPTANCE_SATISFIED = "ACCEPTANCE_SATISFIED"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
    OWNER_REJECTED = "OWNER_REJECTED"
    NO_PROGRESS = "NO_PROGRESS"
    REPEATED_FINDING = "REPEATED_FINDING"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    INVALID_WORKER_OUTPUT = "INVALID_WORKER_OUTPUT"
    ATTEMPT_LIMIT = "ATTEMPT_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    QUOTA_LIMIT = "QUOTA_LIMIT"
    COST_LIMIT = "COST_LIMIT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    USER_CANCELLED = "USER_CANCELLED"
    WORKER_FAILURE = "WORKER_FAILURE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


class NextAction(StringEnum):
    COMPLETE = "complete"
    WAIT_OWNER = "wait_owner"
    REVISE = "revise"
    FAIL = "fail"
    CANCEL = "cancel"


class OwnerDecision(StringEnum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"


class ExperimentArm(StringEnum):
    BASELINE = "baseline"
    TOMTIT = "tomtit"


class CarryoverRisk(StringEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValidityStatus(StringEnum):
    VALID = "valid"
    INVALID = "invalid"
    EXCLUDED = "excluded"


class AlignmentScore(StringEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class OwnerGoalStatus(StringEnum):
    YES = "yes"
    PARTIAL = "partial"
    NO = "no"


class OwnerCriterionStatus(StringEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNRESOLVED = "unresolved"


class TernaryStatus(StringEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class OverallResult(StringEnum):
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class HumanInterventionSource(StringEnum):
    EXPLICIT_USER_REPORT = "explicit_user_report"
    INTERACTION_TIMESTAMPS = "interaction_timestamps"
    UNAVAILABLE = "unavailable"


class CostSource(StringEnum):
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class EscapedDefectStatus(StringEnum):
    UNKNOWN = "unknown"
    NONE_REPORTED = "none_reported"
    REPORTED = "reported"
    CLEARED = "cleared"


class FailureCategory(StringEnum):
    REQUIREMENT_MISUNDERSTOOD = "REQUIREMENT_MISUNDERSTOOD"
    INSUFFICIENT_CLARIFICATION = "INSUFFICIENT_CLARIFICATION"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    WRONG_ASSIGNMENT = "WRONG_ASSIGNMENT"
    CONTEXT_MISSING = "CONTEXT_MISSING"
    IMPLEMENTATION_DEFECT = "IMPLEMENTATION_DEFECT"
    CHECK_INADEQUATE = "CHECK_INADEQUATE"
    VERIFIER_FALSE_POSITIVE = "VERIFIER_FALSE_POSITIVE"
    VERIFIER_MISSED_DEFECT = "VERIFIER_MISSED_DEFECT"
    RETRY_LOOP = "RETRY_LOOP"
    TOOL_FAILURE = "TOOL_FAILURE"
    WORKER_OUTPUT_INVALID = "WORKER_OUTPUT_INVALID"
    USER_CANCELLED = "USER_CANCELLED"
    COST_TOO_HIGH = "COST_TOO_HIGH"
    TIME_TOO_HIGH = "TIME_TOO_HIGH"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNKNOWN = "UNKNOWN"
