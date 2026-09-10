"""Real Product Runtime execution: a bound workspace, a worker, and its checks.

This is the vertical slice that gives Product Runtime something to *be* the
source revision of. Without it ``tomtit.runtime.Runtime`` ran
:class:`~tomtit.fakes.FakeWorker` against no workspace at all, so "the revision
this run executed against" named nothing.

Authority, unchanged
--------------------
Runtime remains the only canonical authority. The pieces here produce claims and
observations: :class:`ProductRuntimeClaudeWorker` returns a ``WorkerResult``
(a claim), :class:`ProductRuntimeCheckRunner` returns observations. Neither
decides completion, transitions state, or touches provenance.

Why the three are one object
----------------------------
:class:`ProductRuntimeExecution` exists because workspace, worker and check
runner were previously separable. A caller could hand Runtime workspace A while
the worker and check runner acted on repository B, and the published report
would claim A's revision for work done in B. Binding them together, verified by
object identity, removes that composition rather than documenting against it.

Scope layering
--------------
Two different mechanisms, deliberately not conflated.
``TaskContract.allowed_scope`` is a glob authority that Runtime enforces after
execution over the complete base-relative delta. ``allowed_changed_paths`` is
the adapter's *literal* preventive write guard. A literal may narrow contract
authority and may never widen it, which is enforced before any provider runs.
Where a glob cannot be losslessly expressed as literals it is not translated at
all: a mistranslation would either block legitimate writes or weaken a real
guard, and the post-execution check is what makes false success impossible
regardless.

This is one bounded slice — one provider, one attempt shape, deterministic argv
checks — and it shares nothing with the dogfood orchestrator.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from tomtit.adapters.claude_code_cli import (
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIResult,
    ClaudeCodeCLIStatus,
)
from tomtit.domain.contracts import (
    RequiredCheck,
    TaskContract,
    VerificationPlan,
    WorkerResult,
)
from tomtit.domain.enums import ScopeOperation, WorkerStatus
from tomtit.runtime.events import InMemoryEventLog
from tomtit.runtime.runtime import Runtime, utc_now
from tomtit.runtime.state import (
    ObservedChangedFile,
    ObservedCheckResult,
    ObservedPatchEvidence,
)
from tomtit.runtime.workspace import (
    BoundWorkspace,
    assert_output_outside_workspace,
    bind_workspace,
)

#: Adapter outcome -> worker claim. Only SUCCEEDED is a completion claim; the
#: rest are reported honestly rather than flattened, because Runtime
#: distinguishes them when deciding what to do next.
_STATUS_TO_WORKER_STATUS = {
    ClaudeCodeCLIStatus.SUCCEEDED: WorkerStatus.COMPLETED,
    ClaudeCodeCLIStatus.FAILED: WorkerStatus.FAILED,
    ClaudeCodeCLIStatus.TIMED_OUT: WorkerStatus.FAILED,
    ClaudeCodeCLIStatus.UNAVAILABLE: WorkerStatus.BLOCKED,
    ClaudeCodeCLIStatus.SCOPE_VIOLATION: WorkerStatus.BLOCKED,
}

#: Delta operation names -> the contract's scope operations.
_OPERATION_TO_SCOPE = {
    "CREATE": ScopeOperation.CREATE,
    "MODIFY": ScopeOperation.MODIFY,
    "DELETE": ScopeOperation.DELETE,
}

#: Glob metacharacters. A pattern containing one cannot be expressed in the
#: adapter's literal allowlist.
_GLOB_METACHARACTERS = frozenset("*?[]")


class ScopeAuthorityError(ValueError):
    """Raised when a literal allowlist would exceed TaskContract authority.

    The adapter allowlist feeds a *preventive* write guard, so a path listed
    there is a path the worker is permitted to write. Permitting one the task
    contract withheld would let configuration silently widen authority, so this
    fails closed rather than quietly intersecting.
    """


class ExecutionBindingError(ValueError):
    """Raised when execution components are not bound to one same workspace."""


class NoObservedChangesError(ValueError):
    """Raised when an attempt produced no workspace change to evidence.

    ``ObservedPatchEvidence`` requires at least one changed file and Runtime
    requires patch evidence to complete, so an attempt that changed nothing has
    nothing to offer. Failing closed beats fabricating an empty artifact that
    would read as a verified no-op.
    """


def assert_literals_within_task_scope(
    literals: tuple[str, ...], task_contract: TaskContract
) -> None:
    """Prove every literal write path is already permitted by the contract.

    ``effective_worker_write_authority`` must be a subset of
    ``TaskContract.allowed_scope``; this is where that is enforced, before any
    provider is invoked.
    """

    outside = tuple(
        literal
        for literal in literals
        if not any(
            Path(literal).match(rule.pattern) for rule in task_contract.allowed_scope
        )
    )
    if outside:
        raise ScopeAuthorityError(
            f"allowed_changed_paths {list(outside)} are not permitted by "
            f"TaskContract {task_contract.task_id} scope "
            f"{[rule.pattern for rule in task_contract.allowed_scope]}; the "
            "adapter allowlist may narrow contract authority but never widen it"
        )


def _attempt_directory(root: Path, attempt_number: int) -> Path:
    directory = root / f"attempt-{attempt_number}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass(frozen=True)
class ExecutionProfile:
    """Runtime-owned description of *how* a run executed.

    Owned by the composition layer, never by the worker: a worker able to
    describe its own execution mode could claim filesystem enforcement it does
    not have. No ``WorkerResult`` field reaches these values.
    """

    implementer: str
    adapter_capability_limitations: tuple[str, ...]


#: The fixture scenarios. Preserved verbatim so existing reports keep their
#: historical meaning.
FIXTURE_EXECUTION_PROFILE = ExecutionProfile(
    implementer="FakeWorker",
    adapter_capability_limitations=(
        "fake dependency; no filesystem, network, or command enforcement claims",
        "patch evidence is deterministic fixture evidence, not a real Git workspace snapshot",
    ),
)

#: Real execution. Describes what ran, including what it still cannot claim.
REAL_EXECUTION_PROFILE = ExecutionProfile(
    implementer="ProductRuntimeClaudeWorker",
    adapter_capability_limitations=(
        "provider execution is mediated by ClaudeCodeCLIAdapter with exact-argv "
        "command guards and OS-level sandbox settings",
        "source evidence is a real Git delta relative to the bound execution base, "
        "covering committed, staged, unstaged and untracked changes with rename "
        "detection disabled so both halves of a move are reported",
        "workspace binding is process-local and time-of-check; it does not resist "
        "a concurrent external writer",
    ),
)


@dataclass(frozen=True, slots=True)
class ProductRuntimeClaudeWorker:
    """Runs one attempt through the Claude Code CLI adapter, in a bound workspace.

    Satisfies :class:`~tomtit.runtime.ports.WorkerPort`. The adapter is reused
    as-is — its command guards, sandbox settings and workspace confinement are
    the security boundary and nothing here relaxes them.

    ``executable_path`` is the adapter's own injectable provider seam. A
    deterministic local script exercises this whole layer without calling a paid
    model; ``None`` lets the adapter discover the real CLI.
    """

    workspace: BoundWorkspace
    run_id: str
    artifact_directory: Path
    timeout_seconds: int = 120
    model: str | None = None
    executable_path: str | None = None
    #: Literal relative paths for the adapter's preventive guard. Left as
    #: ``None``, only scope patterns that are already literal are forwarded;
    #: a glob is never translated. See the module docstring.
    allowed_changed_paths: tuple[str, ...] | None = None
    adapter: ClaudeCodeCLIAdapter = field(default_factory=ClaudeCodeCLIAdapter)
    max_stdout_bytes: int = 1_048_576
    max_stderr_bytes: int = 1_048_576
    #: Append-only bookkeeping. Frozen forbids *rebinding* a field, which is the
    #: point -- the workspace cannot be swapped after the execution binding was
    #: verified -- while appending to these lists stays possible so the object
    #: can still record what it did.
    calls: list[tuple[str, int]] = field(default_factory=list)
    _results: list[ClaudeCodeCLIResult] = field(default_factory=list)

    @property
    def last_result(self) -> ClaudeCodeCLIResult | None:
        return self._results[-1] if self._results else None

    def execute(
        self, task_contract: TaskContract, attempt_number: int
    ) -> WorkerResult:
        """Execute one attempt and return the worker's *claim* about it.

        Authority first, then freshness, then the provider. Both refusals happen
        before anything is launched: a literal allowlist that exceeds contract
        scope, and a workspace that drifted since it was bound.
        """

        self.calls.append((task_contract.task_id, attempt_number))
        allowed = self._adapter_allowed_paths(task_contract)
        assert_literals_within_task_scope(allowed, task_contract)
        # Both halves, in this order, as close to invocation as possible. Clean
        # alone is not enough: a clean *descendant* commit created after binding
        # passes cleanliness while making the recorded execution base false, so
        # the run would start from a commit it never claims.
        self.workspace.assert_base_unchanged()
        self.workspace.assert_clean()

        directory = _attempt_directory(self.artifact_directory, attempt_number)
        request = ClaudeCodeCLIRequest(
            task_id=task_contract.task_id,
            run_id=self.run_id,
            attempt=attempt_number,
            goal=task_contract.goal,
            prompt=self._build_prompt(task_contract),
            working_directory=self.workspace.path,
            timeout_seconds=self.timeout_seconds,
            allowed_changed_paths=allowed,
            max_stdout_bytes=self.max_stdout_bytes,
            max_stderr_bytes=self.max_stderr_bytes,
            model=self.model,
        )
        # The request is built from the bound workspace's own immutable path at
        # invocation time, so a caller holding the adapter cannot redirect where
        # this runs. Re-assert it here rather than trusting construction alone.
        if Path(request.working_directory).resolve() != self.workspace.path:
            raise ExecutionBindingError(
                f"provider would run in {request.working_directory}, not in the "
                f"bound workspace {self.workspace.path}"
            )
        result = self.adapter.run(request, executable_path=self.executable_path)
        self._results.append(result)

        raw_output_path = directory / "worker-result.json"
        raw_output_path.write_text(
            json.dumps(
                {
                    "adapter_name": result.adapter_name,
                    "adapter_version": result.adapter_version,
                    "status": result.status.value,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "stdout_sha256": result.stdout_sha256,
                    "stderr_sha256": result.stderr_sha256,
                    "changed_paths": list(result.changed_paths),
                    "unexpected_changed_paths": list(result.unexpected_changed_paths),
                    # Adapter-side note only. Runtime's own binding is the
                    # authority, and this value never feeds provenance.
                    "bound_execution_base": self.workspace.head_commit,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return self._to_worker_result(result, raw_output_path)

    def _adapter_allowed_paths(self, task_contract: TaskContract) -> tuple[str, ...]:
        """Literal paths for the adapter guard; never a translated glob."""

        if self.allowed_changed_paths is not None:
            return tuple(self.allowed_changed_paths)
        return tuple(
            rule.pattern
            for rule in task_contract.allowed_scope
            if not (_GLOB_METACHARACTERS & set(rule.pattern))
        )

    @staticmethod
    def _build_prompt(task_contract: TaskContract) -> str:
        criteria = "\n".join(
            f"- {criterion.id}: {criterion.description}"
            for criterion in task_contract.acceptance_criteria
        )
        scope = "\n".join(
            f"- {rule.pattern} ({', '.join(op.value for op in rule.operations)})"
            for rule in task_contract.allowed_scope
        )
        return (
            f"Goal: {task_contract.goal}\n\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            f"You may only change paths matching:\n{scope}\n"
        )

    def _to_worker_result(
        self, result: ClaudeCodeCLIResult, raw_output_path: Path
    ) -> WorkerResult:
        """Adapt the adapter's result into the existing worker-claim model."""

        risks: list[str] = []
        if result.timed_out:
            risks.append("provider invocation timed out")
        if result.error_message:
            risks.append(result.error_message)
        return WorkerResult(
            status=_STATUS_TO_WORKER_STATUS.get(result.status, WorkerStatus.FAILED),
            summary=(
                f"{result.adapter_name} {result.status.value} "
                f"(exit_code={result.exit_code}, changed={len(result.changed_paths)})"
            ),
            changed_files=result.changed_paths,
            commands_executed=result.sanitized_argv,
            claimed_checks=(),
            scope_concerns=result.unexpected_changed_paths,
            remaining_risks=tuple(risks),
            questions=(),
            session_reference=None,
            raw_output_path=str(raw_output_path),
        )


@dataclass(frozen=True, slots=True)
class ProductRuntimeCheckRunner:
    """Runs the contract's required checks and observes base-relative evidence.

    Satisfies :class:`~tomtit.runtime.ports.CheckRunnerPort`. Every check runs
    as an exact argv with no shell, in a directory resolved and proven to be
    inside the bound workspace, so a contract cannot direct execution outside it.

    Evidence comes from the complete delta since the execution base, not from
    ``git status``: a worker that commits its work leaves a status showing
    nothing, and an out-of-scope committed change would otherwise be invisible.
    """

    workspace: BoundWorkspace
    artifact_directory: Path
    calls: list[tuple[str, int]] = field(default_factory=list)

    def run(
        self, check: RequiredCheck, run_id: str, attempt_number: int
    ) -> ObservedCheckResult:
        self.calls.append((check.id, attempt_number))
        directory = _attempt_directory(self.artifact_directory, attempt_number)

        cwd = (self.workspace.path / check.cwd_relative).resolve()
        if not cwd.is_dir() or not self._within_workspace(cwd):
            raise ValueError(
                f"required check {check.id} resolves cwd_relative "
                f"{check.cwd_relative!r} to {cwd}, which is not a directory "
                "inside the bound workspace"
            )

        started = datetime.now().timestamp()
        try:
            completed = subprocess.run(
                list(check.argv),
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=check.timeout_seconds,
                check=False,
            )
            exit_code, stdout, stderr = (
                completed.returncode,
                completed.stdout,
                completed.stderr,
            )
        except subprocess.TimeoutExpired:
            # A timeout is an observation, not an exception Runtime handles:
            # the check did not pass, and that is evidence.
            exit_code, stdout, stderr = (
                124,
                "",
                f"check {check.id} exceeded {check.timeout_seconds}s\n",
            )
        except OSError as error:
            exit_code, stdout, stderr = (
                127,
                "",
                f"could not execute {check.id}: {error}\n",
            )
        duration = max(datetime.now().timestamp() - started, 0.0)

        key = sha256(check.id.encode("utf-8")).hexdigest()[:16]
        stdout_path = directory / f"{key}.stdout.txt"
        stderr_path = directory / f"{key}.stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        return ObservedCheckResult(
            run_id=run_id,
            check_id=check.id,
            attempt_number=attempt_number,
            argv=check.argv,
            cwd_relative=check.cwd_relative,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    def collect_patch_evidence(
        self, run_id: str, attempt_number: int
    ) -> ObservedPatchEvidence:
        """Observe what changed since the execution base. Observation only.

        Reports no ``scope_violations``: scope is a *contract* question and the
        canonical TaskContract belongs to Runtime, which applies it to this
        evidence. A check runner carrying its own contract would be a second,
        independently configurable owner of the same decision, and a broader one
        would quietly authorise what the canonical contract forbids.
        """

        delta = self.workspace.collect_delta()
        changed = tuple(
            ObservedChangedFile(
                path=path,
                operation=_OPERATION_TO_SCOPE.get(operation, ScopeOperation.MODIFY),
            )
            for path, operation in delta.operations
        )
        if not changed:
            raise NoObservedChangesError(
                f"attempt {attempt_number} of run {run_id} changed nothing in "
                f"{self.workspace.path}; there is no patch evidence to observe"
            )

        directory = _attempt_directory(self.artifact_directory, attempt_number)
        artifact_path = directory / "patch-evidence.json"
        evidence = ObservedPatchEvidence(
            run_id=run_id,
            attempt_number=attempt_number,
            changed_files=changed,
            diff_exists=True,
            scope_violations=(),
            evidence_complete=True,
            artifact_path=str(artifact_path),
        )
        artifact_path.write_text(
            evidence.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return evidence

    def _within_workspace(self, candidate: Path) -> bool:
        root = self.workspace.path.resolve()
        return candidate == root or root in candidate.parents



@dataclass(frozen=True)
class ProductRuntimeExecution:
    """One workspace, and the worker and check runner bound to *that* workspace.

    The checks are deliberately strict:

    * exact types, so a fixture component cannot be attached to a real
      workspace and inherit its provenance;
    * ``is`` identity on the workspace, not path equality, so two separately
      bound objects for the same path are still refused — equal paths are the
      easiest thing for a caller to arrange, and are not evidence that the same
      binding was used.
    """

    workspace: BoundWorkspace
    worker: ProductRuntimeClaudeWorker
    check_runner: ProductRuntimeCheckRunner

    @property
    def profile(self) -> ExecutionProfile:
        """How this execution describes itself in a terminal report.

        A read-only property with no constructor input, because report metadata
        is a trust claim: a caller able to attach the fixture profile to a real
        run could publish a report saying the work was fake, and a caller able
        to attach the real profile to a fixture run could claim enforcement that
        never happened. Reaching this property at all already required
        constructing a binding whose components are the real ones.
        """

        return REAL_EXECUTION_PROFILE

    def __post_init__(self) -> None:
        if type(self.workspace) is not BoundWorkspace:
            raise ExecutionBindingError(
                "a real execution binding requires a BoundWorkspace, not "
                f"{type(self.workspace).__name__}"
            )
        for label, component, expected in (
            ("worker", self.worker, ProductRuntimeClaudeWorker),
            ("check_runner", self.check_runner, ProductRuntimeCheckRunner),
        ):
            if type(component) is not expected:
                raise ExecutionBindingError(
                    f"{label} must be a {expected.__name__} to carry real "
                    f"execution provenance, not {type(component).__name__}; a "
                    "fixture component cannot acquire a real workspace's revision"
                )
            if component.workspace is not self.workspace:
                raise ExecutionBindingError(
                    f"{label} is bound to {component.workspace.path}, not to this "
                    f"execution's workspace {self.workspace.path}; provenance "
                    "would describe a repository the work did not happen in"
                )


def create_product_runtime(
    *,
    run_id: str,
    task_contract: TaskContract,
    verification_plan: VerificationPlan,
    workspace_path: Path | str,
    report_directory: Path,
    artifact_directory: Path | None = None,
    worker_timeout_seconds: int = 120,
    model: str | None = None,
    executable_path: str | None = None,
    allowed_changed_paths: tuple[str, ...] | None = None,
    event_log: InMemoryEventLog | None = None,
    clock=utc_now,
) -> Runtime:
    """Compose one real Product Runtime run. The production entry point.

    Order matters: the literal allowlist is checked against contract authority
    first, the workspace is then validated and pinned to its ``HEAD``, and only
    the resulting binding reaches the worker, the check runner and Runtime.

    Raises before any worker exists when the workspace is not a clean Git
    repository root, or when the allowlist would widen contract authority. Both
    are refusals to execute, not degraded modes.
    """

    if allowed_changed_paths is not None:
        assert_literals_within_task_scope(tuple(allowed_changed_paths), task_contract)

    workspace = bind_workspace(workspace_path)
    artifacts = (
        Path(artifact_directory)
        if artifact_directory
        else Path(report_directory) / "artifacts"
    )
    # Before a provider runs, not at publication time: Runtime's own writes must
    # never be able to modify the source it is about to describe.
    assert_output_outside_workspace(
        Path(report_directory), workspace.path, "report_directory"
    )
    assert_output_outside_workspace(artifacts, workspace.path, "artifact_directory")

    execution = ProductRuntimeExecution(
        workspace=workspace,
        worker=ProductRuntimeClaudeWorker(
            workspace=workspace,
            run_id=run_id,
            artifact_directory=artifacts,
            timeout_seconds=worker_timeout_seconds,
            model=model,
            executable_path=executable_path,
            allowed_changed_paths=allowed_changed_paths,
        ),
        check_runner=ProductRuntimeCheckRunner(
            workspace=workspace,
            artifact_directory=artifacts,
        ),
    )

    return Runtime(
        run_id=run_id,
        task_contract=task_contract,
        verification_plan=verification_plan,
        event_log=event_log if event_log is not None else InMemoryEventLog(),
        report_directory=Path(report_directory),
        clock=clock,
        execution=execution,
    )
