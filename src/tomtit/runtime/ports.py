"""Structural ports Runtime executes through.

Runtime owns canonical run state; it does not own *doing the work*. These two
protocols are the entire surface it delegates across, and they are deliberately
narrow: a Worker produces a claim, a CheckRunner produces observations, and
neither can move the state machine. Runtime decides transitions from the
evidence it gets back.

They are :class:`typing.Protocol` rather than base classes so an implementation
is bound by the calls Runtime actually makes and nothing else. That keeps the
fakes and the real adapters interchangeable without a hierarchy, and it is why
:class:`~tomtit.fakes.FakeWorker` satisfies :class:`WorkerPort` without
importing it.

Neither port receives a workspace. That is not an omission: the workspace an
implementation may touch is fixed when the implementation is constructed, so a
per-call argument could never redirect it, and Runtime never hands one over.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tomtit.domain.contracts import RequiredCheck, TaskContract, WorkerResult
from tomtit.runtime.state import ObservedCheckResult, ObservedPatchEvidence


@runtime_checkable
class WorkerPort(Protocol):
    """Performs one attempt and reports what it claims to have done.

    The return value is a *claim*, never a verdict: Contract semantics treat
    ``WorkerResult`` as worker-asserted input that Runtime checks against
    independently observed evidence. An implementation may not decide
    completion, transition state, widen its own permissions, choose the source
    revision, or commit, merge or push.
    """

    def execute(
        self, task_contract: TaskContract, attempt_number: int
    ) -> WorkerResult:
        """Run attempt ``attempt_number`` for ``task_contract``."""
        ...


@runtime_checkable
class CheckRunnerPort(Protocol):
    """Observes required checks and patch evidence for one attempt.

    Reports what it observed and nothing more. It does not decide completion,
    mutate canonical state, repair code, or establish source provenance.
    """

    def run(
        self, check: RequiredCheck, run_id: str, attempt_number: int
    ) -> ObservedCheckResult:
        """Execute one required check and return the observed result."""
        ...

    def collect_patch_evidence(
        self, run_id: str, attempt_number: int
    ) -> ObservedPatchEvidence:
        """Observe what the attempt changed, as a durable evidence artifact."""
        ...
