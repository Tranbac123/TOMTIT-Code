"""Immutable terminal report contract and write-once JSON persistence.

Report identity (Evaluation Contract V1 §4)
------------------------------------------
§4.2 requires the exact serialized ``report.v1.json`` bytes to contain
``report_id``, ``run_id``, ``source_revision`` and ``schema_version``, and §4.1
makes ``report_id`` and ``source_revision`` Runtime-assigned and immutable.
Both are therefore fields of :class:`TerminalReport` itself rather than of any
companion artifact — a sidecar cannot satisfy "the exact bytes must contain".

Both are optional here, and that is the contract's own fail-closed rule, not a
weakening of it: §4.2 states that a terminal report lacking ``report_id`` or
``source_revision`` admits no valid Review or Owner assessment. A report built
outside Runtime's completion path simply carries neither and can never be
assessed. Runtime assigns ``report_id`` on the canonical path; nothing else may.

``source_revision`` is currently never assigned at all — see
:class:`ReportSourceRevision` — because no component in this repository
establishes the source revision a run executed against. That is recorded as an
open dependency rather than papered over, and it is why a report published
today is unassessable under §4.2.

Publication authority
---------------------
Holding well-formed ``report.v1.json`` bytes proves integrity, never origin.
:class:`_TerminalReportPublicationAuthority` is the ledger that separates the
two: it hands out the ``report_id`` values Runtime is allowed to publish under,
and it records — by reading the artifact back — the reports this process
actually published. Evaluation asks it to verify a receipt; it never asks a
caller. See that class for the exact scope of the guarantee.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator

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


_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ReportSourceRevision(DomainModel):
    """Contract V1 §5 ``SourceRevision``, as owned and written by Runtime.

    Only ``GIT_COMMIT`` is accepted. §5.1 makes it the single ACTIVE revision
    type; §5.2/§5.3 reserve ``PATCH_SHA256`` and ``WORKTREE_SNAPSHOT_SHA256``
    and forbid their use for assessment binding until an approved
    canonicalization contract exists. Refusing them here means Runtime cannot
    publish a report claiming provenance the contract has not activated, so a
    run over uncommitted output carries no source revision at all and is
    unassessable — which is §5.3's required outcome, not an omission.

    Declared in this module rather than imported from ``tomtit.evaluation`` so
    terminal reporting keeps no dependency on Evaluation.

    Nothing in this repository constructs one on a production path, and that is
    deliberate. A source revision is only provenance if it was established by
    whoever prepared the source state the run executed against; no component
    here does that, and accepting one from a caller would make the caller its
    own authority. So Runtime publishes ``source_revision = None`` and §4.2
    makes the report unassessable. This model defines the shape an execution
    provenance authority will fill once one exists; it does not pretend one
    does.
    """

    revision_type: Literal["GIT_COMMIT"] = "GIT_COMMIT"
    revision_value: str = Field(min_length=1)

    @field_validator("revision_value")
    @classmethod
    def validate_commit_shape(cls, value: str) -> str:
        if not _GIT_COMMIT_PATTERN.match(value):
            raise ValueError(
                "GIT_COMMIT revision_value must be a full 40-character "
                "lowercase hexadecimal commit hash"
            )
        return value


class TerminalReport(DomainModel):
    schema_version: Literal["report.v1"] = "report.v1"
    generated_at: datetime
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    # Contract V1 §4.1: Runtime-assigned, immutable, never created by a
    # reviewer, an Owner or Evaluation. None means "unassessable" (§4.2).
    report_id: str | None = Field(default=None, min_length=1)
    source_revision: ReportSourceRevision | None = None
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
    report_id: str | None = None,
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
        report_id=report_id,
        # Never set: no execution provenance authority exists to establish it.
        source_revision=None,
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


# -- terminal-report publication authority (Contract V1 §4.1) ----------------


class UnknownPublicationReceiptError(ValueError):
    """Raised when a receipt does not name a report this process published."""


class PublishedReportChangedError(ValueError):
    """Raised when a published artifact no longer matches what was published."""


@dataclass(frozen=True)
class PublishedTerminalReportFacts:
    """What the publication operation itself observed about one published report.

    Every field is read back out of the artifact the publication operation
    created. None of them is supplied by a caller, which is the whole point:
    these are the facts an assessment must match, and they are the artifact's,
    not anyone's claim about it.
    """

    report_path: str
    report_sha256: str
    schema_version: str
    report_id: str
    run_id: str
    source_revision: ReportSourceRevision | None


@dataclass(frozen=True)
class PublicationReceipt:
    """Evidence that this process published one terminal report.

    Carries no report facts on purpose. It is an opaque lookup key plus the
    path it refers to; everything an assessment needs comes from asking the
    authority to verify it. A receipt handed to Evaluation therefore cannot
    assert anything — it can only identify a publication the authority already
    recorded, or fail to.
    """

    receipt_id: str
    report_path: str


class _TerminalReportPublicationAuthority:
    """Process-local ledger of terminal reports this process actually published.

    What it establishes
    -------------------
    Contract V1 §4.1 makes ``report_id`` Runtime-assigned. This class is what
    makes that true rather than aspirational, in two steps:

    1. :meth:`_allocate_report_id` mints an unguessable id and remembers it as
       publishable exactly once. Runtime calls it inside its completion
       preparation; the id reaches the published bytes and nothing else.
    2. :meth:`record_publication` is given only a *path*. It reads that file
       back, hashes it, parses it with the canonical :class:`TerminalReport`
       model, and records the result **only if** the ``report_id`` in those
       bytes is a live allocation, which it then consumes.

    So a well-formed ``report.v1.json`` a caller wrote themselves earns no
    receipt: they cannot guess an allocated id, and each allocation publishes
    once. Copying published bytes elsewhere earns nothing either — the ledger
    records the path that was published, and verification re-reads *that* path,
    never one a caller names.

    What it does not establish
    --------------------------
    Authenticity here is process-local, and deliberately modelled as such for
    V0A. The ledger lives in memory, so it says "this process published this
    artifact" and nothing about another process, another host, or a report
    found on disk after a restart. Code running in this process that imports
    this module's private allocation entry point is Runtime for all practical
    purposes; no in-process boundary can say otherwise. Cross-process or
    cryptographic attestation of publication remains a genuine open dependency,
    not something a Python object can supply.
    """

    def __init__(self) -> None:
        self._allocated: set[str] = set()
        self._published: dict[str, PublishedTerminalReportFacts] = {}

    # -- Runtime-only publication side --------------------------------------

    def _allocate_report_id(self) -> str:
        """Mint one unguessable, single-use publishable report id (§4.1)."""

        report_id = secrets.token_hex(16)
        self._allocated.add(report_id)
        return report_id

    def _release_report_id(self, report_id: str) -> None:
        """Return an allocation whose run never reached publication."""

        self._allocated.discard(report_id)

    def record_publication(self, report_path: Path) -> PublicationReceipt | None:
        """Record a completed publication by reading the artifact back.

        Never raises. It runs after the terminal report has been committed to
        disk, and a bookkeeping failure must not undo a committed terminal
        result. A publication that cannot be recorded simply has no receipt,
        which makes it unassessable — the fail-closed direction.
        """

        try:
            report_bytes = Path(report_path).read_bytes()
            report = TerminalReport.model_validate_json(report_bytes)
            report_id = report.report_id
            if report_id is None or report_id not in self._allocated:
                return None
            self._allocated.discard(report_id)

            receipt_id = secrets.token_urlsafe(32)
            self._published[receipt_id] = PublishedTerminalReportFacts(
                report_path=str(report_path),
                report_sha256=hashlib.sha256(report_bytes).hexdigest(),
                schema_version=report.schema_version,
                report_id=report_id,
                run_id=report.run_id,
                source_revision=report.source_revision,
            )
            return PublicationReceipt(
                receipt_id=receipt_id, report_path=str(report_path)
            )
        except Exception:
            return None

    # -- read-only verification side ----------------------------------------

    def verify_publication(self, receipt_id: str) -> PublishedTerminalReportFacts:
        """Re-prove a recorded publication, or refuse.

        Takes an opaque key and nothing else. The path re-read is the one the
        ledger recorded, so a caller cannot redirect verification at a file of
        their choosing, and the bytes must still hash to what was published.
        """

        # noqa rationale: isinstance() would admit a str subclass that can
        # override __eq__/__hash__ and steer its own ledger lookup. The key
        # into this ledger must be an exact built-in string.
        if type(receipt_id) is not str:  # noqa: E721
            raise UnknownPublicationReceiptError(
                "a publication receipt id must be a string; an object shaped "
                "like one proves nothing, because the authority is this ledger "
                "and not the value presented"
            )
        facts = self._published.get(receipt_id)
        if facts is None:
            raise UnknownPublicationReceiptError(
                "no terminal report was published under this receipt; a report "
                "file alone is not evidence that Runtime published it"
            )

        try:
            current = Path(facts.report_path).read_bytes()
        except OSError as error:
            raise PublishedReportChangedError(
                f"published terminal report {facts.report_path} is no longer "
                f"readable: {error}"
            ) from error
        if hashlib.sha256(current).hexdigest() != facts.report_sha256:
            raise PublishedReportChangedError(
                f"published terminal report {facts.report_path} no longer "
                "matches the bytes that were published under this receipt; the "
                "artifact changed after publication, so it cannot authorize an "
                "assessment"
            )
        return facts


#: The single process-local publication ledger. Runtime records into it during
#: completion; Evaluation only ever asks it to verify a receipt.
_PUBLICATION_AUTHORITY = _TerminalReportPublicationAuthority()


def publication_authority() -> _TerminalReportPublicationAuthority:
    """The process's terminal-report publication ledger."""

    return _PUBLICATION_AUTHORITY
