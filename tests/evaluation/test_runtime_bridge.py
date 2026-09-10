"""EVAL-RUNTIME-BRIDGE-01: real Runtime publication authority into Eval history.

Every test drives an actual :class:`~tomtit.runtime.runtime.Runtime` to a real
completion. What authorizes a canonical append is not the ``report.v1.json``
that run leaves behind — anyone can write one of those — but the receipt
Runtime's publication ledger issued when it published it.

Two open dependencies are asserted here rather than papered over:

* **Publication authenticity is process-local.** The ledger lives in memory, so
  it proves "this process published this artifact" and nothing wider.
* **No source revision authority exists.** Nothing in this repository
  establishes the revision a run executed against, so Runtime publishes none and
  Contract V1 §4.2 makes every report published today unassessable. The tests
  under "no source revision authority" pin that as the required fail-closed
  outcome; the tests under "provenance present" show the mechanism that will
  authorize once an authority exists, using a publication no production path
  can currently produce.

Contract anchors: §4.1 (Runtime-assigned immutable identity), §4.2 (exact-byte
hash over report.v1.json containing report_id/run_id/source_revision/
schema_version), §5.1–§5.3 (only GIT_COMMIT is ACTIVE), §9/§11 (exact target
match), §8 (Eval's sequence is its own).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    EvaluationAssessmentStore,
    OperationalSource,
    OwnerAssessmentEventDraft,
    OwnerOutcome,
    OwnerReviewStatus,
    PublicationTargetMismatchError,
    PublishedReportVerificationError,
    ReportBoundAppendUnavailableError,
    ReviewAssessmentEventDraft,
    ReviewOutcome,
    RevisionType,
    SourceRevision,
    TerminalReportUnavailableError,
    UnassessableTerminalReportError,
    UnsupportedAssessmentError,
    VerifiedTerminalReport,
    effective_assessment,
    escaped_defect_status,
    owner_review_status,
    report_target_key,
    verify_published_report,
)
from tomtit.evaluation.assessments import (
    EscapedDefectEventDraft,
    EscapedDefectSeverity,
)
from tomtit.reporting import publication_authority

from ._runtime_fixture import (
    OTHER_COMMIT,
    RUNTIME_COMMIT,
    published_report,
    run_product_runtime,
)

RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime(tmp_path: Path):
    """A completed real Product Runtime run."""

    return run_product_runtime(tmp_path)


@pytest.fixture
def published(tmp_path: Path, runtime) -> Path:
    return published_report(tmp_path)


@pytest.fixture
def receipt_id(runtime) -> str:
    """The opaque evidence that Runtime published this run's report."""

    assert runtime.publication_receipt is not None
    return runtime.publication_receipt.receipt_id


# -- representing the state a source revision authority would produce --------


def publish_with_provenance(
    directory: Path,
    genuine_report: Path,
    *,
    revision_value: str = RUNTIME_COMMIT,
    run_id: str | None = None,
) -> tuple[str, Path]:
    """Publish a complete report that carries a source revision, and record it.

    No production path can produce this today, and that is the point: it is the
    artifact a future execution provenance authority would let Runtime publish.
    Building it needs the ledger's private allocation entry point, which is
    exactly the in-process boundary :class:`tomtit.reporting.
    _TerminalReportPublicationAuthority` documents — a caller who can reach it
    is Runtime as far as this process can tell.

    Everything except ``report_id``, ``source_revision`` and optionally
    ``run_id`` is carried over from a genuinely completed run, so the artifact
    is a complete terminal report and not an identity-shaped stub.
    """

    authority = publication_authority()
    payload = json.loads(genuine_report.read_text(encoding="utf-8"))
    payload["report_id"] = authority._allocate_report_id()
    payload["source_revision"] = {
        "revision_type": "GIT_COMMIT",
        "revision_value": revision_value,
    }
    if run_id is not None:
        payload["run_id"] = run_id

    path = directory / "provenance-report.v1.json"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    receipt = authority.record_publication(path)
    assert receipt is not None
    return receipt.receipt_id, path


@pytest.fixture
def provenance(tmp_path: Path, published: Path) -> tuple[str, Path]:
    return publish_with_provenance(tmp_path / "future", published)


@pytest.fixture
def provenance_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "future"
    directory.mkdir()
    return directory


def _review(verified: VerifiedTerminalReport, **overrides) -> ReviewAssessmentEventDraft:
    payload = {
        "assessment_id": "assess-review-1",
        "run_id": verified.run_id,
        "target_terminal_report_id": verified.report_id,
        "target_terminal_report_hash": verified.report_hash,
        "target_source_revision": verified.source_revision,
        "review_outcome": ReviewOutcome.REVIEWER_APPROVED,
        "reviewer": "codex",
        "timestamp": NOW,
        "evidence_refs": ("evidence:r1",),
    }
    payload.update(overrides)
    return ReviewAssessmentEventDraft(**payload)


def _owner(verified: VerifiedTerminalReport, **overrides) -> OwnerAssessmentEventDraft:
    payload = {
        "assessment_id": "assess-owner-1",
        "run_id": verified.run_id,
        "target_terminal_report_id": verified.report_id,
        "target_terminal_report_hash": verified.report_hash,
        "target_source_revision": verified.source_revision,
        "owner_outcome": OwnerOutcome.OWNER_ACCEPTED,
        "timestamp": NOW,
        "evidence_refs": ("evidence:o1",),
    }
    payload.update(overrides)
    return OwnerAssessmentEventDraft(**payload)


def _unassessable_draft(published: Path) -> ReviewAssessmentEventDraft:
    """A draft aimed at a real report, using the revision Runtime never had."""

    payload = json.loads(published.read_text(encoding="utf-8"))
    return ReviewAssessmentEventDraft(
        assessment_id="assess-review-1",
        run_id=payload["run_id"],
        target_terminal_report_id=payload["report_id"],
        target_terminal_report_hash=hashlib.sha256(published.read_bytes()).hexdigest(),
        target_source_revision=SourceRevision(
            revision_type=RevisionType.GIT_COMMIT, revision_value=RUNTIME_COMMIT
        ),
        review_outcome=ReviewOutcome.REVIEWER_APPROVED,
        reviewer="codex",
        timestamp=NOW,
        evidence_refs=("evidence:r1",),
    )


# -- Runtime-owned report identity (§4.1) ------------------------------------


def test_runtime_assigns_report_identity_into_the_published_bytes(
    published: Path,
) -> None:
    payload = json.loads(published.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "report.v1"
    assert payload["run_id"] == "run-bridge"
    assert isinstance(payload["report_id"], str) and payload["report_id"]
    # Allocated by the ledger, so it is unguessable rather than conventional.
    assert len(payload["report_id"]) == 32
    int(payload["report_id"], 16)


def test_runtime_offers_no_caller_seam_for_report_identity() -> None:
    """§4.1: nothing outside Runtime can choose the identity it publishes."""

    from tomtit.runtime.runtime import Runtime

    parameters = inspect.signature(Runtime.__init__).parameters
    assert "source_revision" not in parameters
    assert "report_id_factory" not in parameters

    source = inspect.getsource(Runtime._prepare_completion)
    assert "self._publication_authority._allocate_report_id()" in source
    assert "task_contract.report_id" not in source


def test_public_report_builder_cannot_stamp_identity() -> None:
    """The public trust boundary has no identity parameters to abuse."""

    from tomtit.reporting import build_terminal_report

    parameters = inspect.signature(build_terminal_report).parameters
    assert "report_id" not in parameters
    assert "source_revision" not in parameters


def test_each_run_publishes_a_distinct_report_id(tmp_path: Path) -> None:
    first = published_report(tmp_path / "a")
    second = published_report(tmp_path / "b")
    run_product_runtime(tmp_path / "a")
    run_product_runtime(tmp_path / "b")

    ids = {
        json.loads(path.read_text(encoding="utf-8"))["report_id"]
        for path in (first, second)
    }
    assert len(ids) == 2


def test_publication_receipt_exists_only_after_publication(tmp_path: Path) -> None:
    from tomtit.domain.enums import VerificationMode
    from tomtit.domain.contracts import CriterionAssignment, VerificationPlan
    from tomtit.domain.enums import VerificationMethod
    from tomtit.fakes import FakeCheckRunner, FakeWorker
    from tomtit.runtime.events import InMemoryEventLog
    from tomtit.runtime.runtime import Runtime

    from ._runtime_fixture import _task_contract, _worker_result

    unfinished = Runtime(
        run_id="run-unfinished",
        task_contract=_task_contract(),
        verification_plan=VerificationPlan(
            mode=VerificationMode.B,
            criterion_assignments=[
                CriterionAssignment(
                    criterion_id="tests-pass",
                    verification_method=VerificationMethod.DETERMINISTIC,
                    available=True,
                )
            ],
        ),
        worker=FakeWorker(_worker_result()),
        check_runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
        event_log=InMemoryEventLog(),
        report_directory=tmp_path,
    )
    assert unfinished.publication_receipt is None


def test_published_report_is_write_once_and_immutable(published: Path) -> None:
    from tomtit.reporting import write_rendered_terminal_report

    with pytest.raises(FileExistsError):
        write_rendered_terminal_report(published, "{}")


def test_terminal_report_model_rejects_mutation() -> None:
    from tomtit.reporting import ReportSourceRevision

    revision = ReportSourceRevision(
        revision_type="GIT_COMMIT", revision_value=RUNTIME_COMMIT
    )
    with pytest.raises(ValidationError):
        revision.revision_value = OTHER_COMMIT  # type: ignore[misc]


def test_reserved_revision_types_cannot_be_published() -> None:
    """§5.2/§5.3: Runtime cannot stamp a revision type V1 has not activated."""

    from tomtit.reporting import ReportSourceRevision

    for reserved in ("PATCH_SHA256", "WORKTREE_SNAPSHOT_SHA256"):
        with pytest.raises(ValidationError):
            ReportSourceRevision(
                revision_type=reserved,  # type: ignore[arg-type]
                revision_value="a" * 64,
            )


def test_malformed_commit_value_is_refused() -> None:
    from tomtit.reporting import ReportSourceRevision

    for bad in ("abc", "A" * 40, "g" * 40, ""):
        with pytest.raises(ValidationError):
            ReportSourceRevision(revision_type="GIT_COMMIT", revision_value=bad)


# -- publication authenticity (F01) ------------------------------------------


def test_arbitrary_report_file_cannot_authorize_an_append(
    tmp_path: Path, published: Path
) -> None:
    """The exact defect: a valid-looking file at a path of the caller's choosing."""

    forged = tmp_path / "forged" / "report.v1.json"
    forged.parent.mkdir()
    forged.write_bytes(published.read_bytes())

    assert publication_authority().record_publication(forged) is None

    store = EvaluationAssessmentStore()
    with pytest.raises(TerminalReportUnavailableError):
        store.append(
            _unassessable_draft(forged),
            operational_source=RUNTIME,
            publication_receipt_id="not-a-real-receipt",
        )
    assert len(store) == 0 and store.sequence == 0


def test_copied_runtime_bytes_do_not_inherit_publication_authority(
    tmp_path: Path, published: Path, receipt_id: str
) -> None:
    """Byte-identical is not the same publication; the ledger records the path."""

    copied = tmp_path / "copy.v1.json"
    copied.write_bytes(published.read_bytes())
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == (
        hashlib.sha256(published.read_bytes()).hexdigest()
    )

    assert publication_authority().record_publication(copied) is None
    facts = publication_authority().verify_publication(receipt_id)
    assert facts.report_path == str(published)


def test_minimal_identity_only_json_cannot_be_published(
    provenance_dir: Path,
) -> None:
    """A stub with perfect identity fields is not a terminal report."""

    authority = publication_authority()
    stub = provenance_dir / "stub.v1.json"
    stub.write_text(
        json.dumps(
            {
                "schema_version": "report.v1",
                "report_id": authority._allocate_report_id(),
                "run_id": "run-bridge",
                "source_revision": {
                    "revision_type": "GIT_COMMIT",
                    "revision_value": RUNTIME_COMMIT,
                },
            }
        ),
        encoding="utf-8",
    )
    assert authority.record_publication(stub) is None


def test_incomplete_report_cannot_be_published_even_with_an_allocation(
    provenance_dir: Path, published: Path
) -> None:
    """Complete-artifact validation, not just identity, gates the ledger."""

    authority = publication_authority()
    payload = json.loads(published.read_text(encoding="utf-8"))
    payload["report_id"] = authority._allocate_report_id()
    del payload["acceptance_criteria"]

    incomplete = provenance_dir / "incomplete.v1.json"
    incomplete.write_text(json.dumps(payload), encoding="utf-8")
    assert authority.record_publication(incomplete) is None


def test_an_allocation_publishes_at_most_once(
    provenance_dir: Path, published: Path
) -> None:
    authority = publication_authority()
    payload = json.loads(published.read_text(encoding="utf-8"))
    payload["report_id"] = authority._allocate_report_id()

    first = provenance_dir / "once.v1.json"
    first.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    assert authority.record_publication(first) is not None

    replay = provenance_dir / "replay.v1.json"
    replay.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    assert authority.record_publication(replay) is None


def test_unknown_receipt_is_refused(receipt_id: str) -> None:
    with pytest.raises(TerminalReportUnavailableError):
        verify_published_report(receipt_id + "x")
    with pytest.raises(TerminalReportUnavailableError):
        verify_published_report("")


def test_tampered_published_artifact_fails_verification(
    published: Path, receipt_id: str
) -> None:
    published.write_text(
        published.read_text(encoding="utf-8").replace('"run-bridge"', '"run-tampered"'),
        encoding="utf-8",
    )
    with pytest.raises(PublishedReportVerificationError):
        verify_published_report(receipt_id)


def test_removed_published_artifact_fails_verification(
    published: Path, receipt_id: str
) -> None:
    published.unlink()
    with pytest.raises(PublishedReportVerificationError):
        verify_published_report(receipt_id)


def test_verified_hash_is_sha256_of_the_exact_published_bytes(
    published: Path, provenance: tuple[str, Path]
) -> None:
    receipt, path = provenance
    verified = verify_published_report(receipt)
    assert verified.report_hash == hashlib.sha256(path.read_bytes()).hexdigest()


def test_dogfood_cannot_claim_terminal_report_identity(receipt_id: str) -> None:
    """Dogfood has no TerminalReport concept; pretending otherwise is refused."""

    with pytest.raises(UnassessableTerminalReportError):
        verify_published_report(receipt_id, operational_source=DOGFOOD)


# -- no source revision authority: fail closed (F02) -------------------------


def test_real_runtime_report_carries_no_source_revision(published: Path) -> None:
    """The exact missing upstream fact, surfaced rather than manufactured."""

    payload = json.loads(published.read_text(encoding="utf-8"))
    assert payload["source_revision"] is None


def test_real_runtime_publication_is_unassessable(receipt_id: str) -> None:
    with pytest.raises(UnassessableTerminalReportError) as excinfo:
        verify_published_report(receipt_id)
    message = str(excinfo.value)
    assert "source_revision" in message
    assert "PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY" in message


@pytest.mark.parametrize("kind", ["review", "owner"])
def test_real_runtime_report_cannot_enter_canonical_history(
    published: Path, receipt_id: str, kind: str
) -> None:
    """A genuine publication with no provenance rejects cleanly and changes nothing."""

    draft = _unassessable_draft(published)
    if kind == "owner":
        draft = OwnerAssessmentEventDraft(
            assessment_id="assess-owner-1",
            run_id=draft.run_id,
            target_terminal_report_id=draft.target_terminal_report_id,
            target_terminal_report_hash=draft.target_terminal_report_hash,
            target_source_revision=draft.target_source_revision,
            owner_outcome=OwnerOutcome.OWNER_ACCEPTED,
            timestamp=NOW,
            evidence_refs=("evidence:o1",),
        )

    store = EvaluationAssessmentStore()
    with pytest.raises(UnassessableTerminalReportError):
        store.append(
            draft, operational_source=RUNTIME, publication_receipt_id=receipt_id
        )
    assert len(store) == 0 and store.sequence == 0 and store.records == ()


def test_caller_supplied_revision_cannot_substitute_for_the_missing_one(
    published: Path, receipt_id: str
) -> None:
    """Naming a revision in the draft does not give the report one."""

    store = EvaluationAssessmentStore()
    for value in (RUNTIME_COMMIT, OTHER_COMMIT):
        with pytest.raises(UnassessableTerminalReportError):
            store.append(
                _unassessable_draft(published).model_copy(
                    update={
                        "target_source_revision": SourceRevision(
                            revision_type=RevisionType.GIT_COMMIT,
                            revision_value=value,
                        )
                    }
                ),
                operational_source=RUNTIME,
                publication_receipt_id=receipt_id,
            )
    assert len(store) == 0 and store.sequence == 0


# -- provenance present: the mechanism that will authorize -------------------


def test_publication_with_provenance_authorizes_review(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    envelope = store.append(
        _review(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    assert envelope.assessment.append_sequence == 1
    assert len(store) == 1


def test_publication_with_provenance_authorizes_owner(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    envelope = store.append(
        _owner(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    assert envelope.assessment.owner_outcome is OwnerOutcome.OWNER_ACCEPTED
    assert len(store) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_terminal_report_id", "report-somewhere-else"),
        ("run_id", "run-somewhere-else"),
        ("target_terminal_report_hash", "b" * 64),
    ],
)
def test_target_mismatch_fails_closed(
    provenance: tuple[str, Path], field: str, value: str
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(PublicationTargetMismatchError):
        store.append(
            _review(verified, **{field: value}),
            operational_source=RUNTIME,
            publication_receipt_id=receipt,
        )
    assert len(store) == 0 and store.sequence == 0


def test_mismatched_source_revision_fails_closed(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    other = SourceRevision(
        revision_type=RevisionType.GIT_COMMIT, revision_value=OTHER_COMMIT
    )
    store = EvaluationAssessmentStore()
    with pytest.raises(PublicationTargetMismatchError):
        store.append(
            _review(verified, target_source_revision=other),
            operational_source=RUNTIME,
            publication_receipt_id=receipt,
        )
    assert len(store) == 0 and store.sequence == 0


def test_caller_cannot_select_a_different_revision_than_was_published(
    tmp_path: Path, published: Path, provenance_dir: Path
) -> None:
    """Two publications differ only by revision; each binds only its own."""

    mine, _ = publish_with_provenance(
        provenance_dir, published, revision_value=RUNTIME_COMMIT
    )
    theirs, _ = publish_with_provenance(
        tmp_path / "other", published, revision_value=OTHER_COMMIT
    )
    verified_theirs = verify_published_report(theirs)

    store = EvaluationAssessmentStore()
    with pytest.raises(PublicationTargetMismatchError):
        store.append(
            _review(verified_theirs),
            operational_source=RUNTIME,
            publication_receipt_id=mine,
        )
    assert len(store) == 0 and store.sequence == 0


def test_tampered_artifact_fails_closed_at_append(
    provenance: tuple[str, Path],
) -> None:
    receipt, path = provenance
    verified = verify_published_report(receipt)
    path.write_text("{}", encoding="utf-8")

    store = EvaluationAssessmentStore()
    with pytest.raises(PublishedReportVerificationError):
        store.append(
            _review(verified), operational_source=RUNTIME, publication_receipt_id=receipt
        )
    assert len(store) == 0 and store.sequence == 0


def test_wrong_operational_source_fails_closed(provenance: tuple[str, Path]) -> None:
    """A Product Runtime artifact cannot authorize a dogfood assessment."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(UnassessableTerminalReportError):
        store.append(
            _review(verified), operational_source=DOGFOOD, publication_receipt_id=receipt
        )
    assert len(store) == 0 and store.sequence == 0


def test_rejections_do_not_burn_a_sequence(provenance: tuple[str, Path]) -> None:
    """A refused append leaves the next valid one at sequence 1."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(PublicationTargetMismatchError):
        store.append(
            _review(verified, run_id="wrong"),
            operational_source=RUNTIME,
            publication_receipt_id=receipt,
        )
    envelope = store.append(
        _review(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    assert envelope.assessment.append_sequence == 1


def test_eval_sequence_is_its_own(provenance: tuple[str, Path]) -> None:
    """§8: the Evaluation stream numbers itself, whatever Runtime did."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    first = store.append(
        _review(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    second = store.append(
        _owner(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    assert (first.append_sequence, second.append_sequence) == (1, 2)


# -- no caller-controlled object decides trust (F03) -------------------------


class _FakeReceipt:
    """A caller object shaped like publication evidence, with methods to match."""

    receipt_id = "pretend"
    operational_source = RUNTIME
    report_id = "report-bridge-1"
    run_id = "run-bridge"
    report_hash = "a" * 64

    def target_key(self):  # pragma: no cover - must never be called
        raise AssertionError("caller-supplied target_key() was invoked")

    def verify(self):  # pragma: no cover - must never be called
        raise AssertionError("caller-supplied verify() was invoked")


class _ReceiptLikeString(str):
    """A str subclass that lies about equality, to catch a loose type check."""

    def __eq__(self, other: object) -> bool:  # pragma: no cover - defensive
        return True

    def __hash__(self) -> int:
        return hash("")


@pytest.mark.parametrize(
    "fake",
    [
        None,
        object(),
        _FakeReceipt(),
        {"receipt_id": "pretend"},
        ["pretend"],
        123,
        b"pretend",
        _ReceiptLikeString("pretend"),
    ],
    ids=[
        "none",
        "bare-object",
        "duck-typed",
        "mapping",
        "sequence",
        "int",
        "bytes",
        "str-subclass",
    ],
)
def test_self_attested_evidence_cannot_authorize_an_append(
    provenance: tuple[str, Path], fake
) -> None:
    """A caller cannot substitute its own object for a recorded publication."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(
            _review(verified), operational_source=RUNTIME, publication_receipt_id=fake
        )
    assert len(store) == 0 and store.sequence == 0


def test_a_caller_built_verified_report_is_not_evidence(
    provenance: tuple[str, Path],
) -> None:
    """Constructing the verified-facts type proves nothing; only the ledger does."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    forged = VerifiedTerminalReport(
        operational_source=RUNTIME,
        report_path="/nowhere/report.v1.json",
        report_id=verified.report_id,
        run_id=verified.run_id,
        report_hash=verified.report_hash,
        schema_version="report.v1",
        source_revision=verified.source_revision,
    )

    store = EvaluationAssessmentStore()
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(
            _review(verified),
            operational_source=RUNTIME,
            publication_receipt_id=forged,  # type: ignore[arg-type]
        )
    assert len(store) == 0 and store.sequence == 0


def test_store_never_invokes_caller_supplied_callables(
    provenance: tuple[str, Path],
) -> None:
    """The fake's methods raise if called; the refusal must arrive first."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(
            _review(verified),
            operational_source=RUNTIME,
            publication_receipt_id=_FakeReceipt(),  # type: ignore[arg-type]
        )


def test_store_authorization_executes_no_caller_attribute_access() -> None:
    """Structural: the trust path names no method on the caller's value."""

    from tomtit.evaluation import store as store_module

    source = inspect.getsource(store_module._require_verified_publication)
    for forbidden in (
        "publication_receipt_id.",
        ".verify()",
        ".target_key()",
        "isinstance(publication_receipt_id",
    ):
        assert forbidden not in source, forbidden


def test_subclassing_the_store_cannot_reopen_the_gate(
    published: Path, receipt_id: str
) -> None:
    """append is the only write path, and the gate lives inside it."""

    class Sneaky(EvaluationAssessmentStore):
        pass

    store = Sneaky()
    with pytest.raises(UnassessableTerminalReportError):
        store.append(
            _unassessable_draft(published),
            operational_source=RUNTIME,
            publication_receipt_id=receipt_id,
        )
    assert len(store) == 0 and store.sequence == 0


def test_receipt_is_refused_for_non_report_bound_events(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    store = EvaluationAssessmentStore()
    defect = EscapedDefectEventDraft(
        event_id="d1",
        run_id="run-bridge",
        severity=EscapedDefectSeverity.MAJOR,
        detected_at=NOW,
        evidence_refs=("e",),
    )
    with pytest.raises(UnsupportedAssessmentError):
        store.append(
            defect, operational_source=RUNTIME, publication_receipt_id=receipt
        )
    assert len(store) == 0


def test_no_receipt_is_still_refused(provenance: tuple[str, Path]) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(_review(verified), operational_source=RUNTIME)
    assert len(store) == 0 and store.sequence == 0


# -- authority separation ----------------------------------------------------


def test_eval_ingestion_does_not_mutate_runtime_state(
    provenance: tuple[str, Path],
) -> None:
    from tomtit.runtime.events import InMemoryEventLog

    receipt, path = provenance
    before = path.read_bytes()
    runtime_log = InMemoryEventLog()

    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    store.append(
        _owner(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )

    assert path.read_bytes() == before
    assert len(runtime_log.events) == 0 and len(runtime_log.decisions) == 0


#: Report-writing and ledger-writing entry points Evaluation must never reach.
_REPORT_WRITE_ENTRY_POINTS = frozenset(
    {
        "write_terminal_report",
        "write_rendered_terminal_report",
        "render_terminal_report",
        "build_terminal_report",
        "stage_terminal_report",
        "publish_staged_report",
        "record_publication",
        "_allocate_report_id",
        "_release_report_id",
    }
)


def _eval_package() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "tomtit" / "evaluation"


def test_evaluation_never_writes_a_terminal_report() -> None:
    """Eval may read Runtime's publication ledger; it must not write reports.

    Checked against compiled code and the import graph, not source text, so
    documentation is free to name a writer in order to explain why it is
    forbidden — only code that can actually reach one fails this.
    """

    import ast
    import importlib
    import types

    for path in sorted(_eval_package().glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Nothing may be imported under a report-writing name.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in _REPORT_WRITE_ENTRY_POINTS, (
                        f"{path.name} imports {alias.name}"
                    )

        module = importlib.import_module(f"tomtit.evaluation.{path.stem}")
        # Nor called: walk every code object the module actually defines.
        pending = [
            value
            for value in vars(module).values()
            if isinstance(value, types.FunctionType)
            and value.__module__ == module.__name__
        ]
        pending += [
            member
            for value in vars(module).values()
            if isinstance(value, type) and value.__module__ == module.__name__
            for member in vars(value).values()
        ]
        for obj in pending:
            target = getattr(obj, "__func__", obj)
            target = getattr(target, "fget", target)
            code = getattr(target, "__code__", None)
            if code is None:
                continue
            reached = set(code.co_names)
            assert reached & _REPORT_WRITE_ENTRY_POINTS == set(), (
                path.name,
                getattr(target, "__name__", "?"),
                reached & _REPORT_WRITE_ENTRY_POINTS,
            )


def test_only_the_bridge_module_reaches_into_reporting() -> None:
    """The Runtime dependency is confined to the one module that is the seam."""

    import ast

    importers = set()
    for path in sorted(_eval_package().glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            if any(m.startswith("tomtit.reporting") for m in modules):
                importers.add(path.name)
    assert importers == {"bridge.py"}


def test_reporting_does_not_depend_on_evaluation() -> None:
    """The bridge edge stays acyclic: Runtime never learns about Evaluation."""

    import ast

    reporting = Path(__file__).resolve().parents[2] / "src" / "tomtit" / "reporting.py"
    tree = ast.parse(reporting.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("tomtit.evaluation"), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("tomtit.evaluation"), alias.name


# -- derived integration over ledger-verified history ------------------------


def test_owner_review_status_derives_from_verified_history(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    envelope = store.append(
        _owner(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    target = report_target_key(RUNTIME, envelope.assessment)
    assert owner_review_status(store, target) is OwnerReviewStatus.OWNER_ACCEPTED


def test_effective_assessment_prefers_owner_over_proxy(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    store.append(
        _review(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    envelope = store.append(
        _owner(verified, owner_outcome=OwnerOutcome.OWNER_REJECTED),
        operational_source=RUNTIME,
        publication_receipt_id=receipt,
    )
    target = report_target_key(RUNTIME, envelope.assessment)
    effective = effective_assessment(store, target)
    assert effective is not None
    assert effective.authority == "OWNER"
    assert effective.outcome == "OWNER_REJECTED"


def test_defect_window_opens_from_verified_owner_acceptance(
    provenance: tuple[str, Path],
) -> None:
    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    envelope = store.append(
        _owner(verified), operational_source=RUNTIME, publication_receipt_id=receipt
    )
    target = report_target_key(RUNTIME, envelope.assessment)

    store.append(
        EscapedDefectEventDraft(
            event_id="d1",
            run_id=verified.run_id,
            severity=EscapedDefectSeverity.MAJOR,
            detected_at=NOW + timedelta(days=3),
            evidence_refs=("e",),
        ),
        operational_source=RUNTIME,
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + timedelta(days=8))
    assert result.available
    assert result.window_started_at == NOW
    assert result.window_days == 7
    assert result.status.value == "FOUND"


def test_defect_window_stays_closed_without_acceptance(
    provenance: tuple[str, Path],
) -> None:
    """A rejected Owner assessment opens no window, even on verified history."""

    receipt, _ = provenance
    verified = verify_published_report(receipt)
    store = EvaluationAssessmentStore()
    envelope = store.append(
        _owner(verified, owner_outcome=OwnerOutcome.OWNER_REJECTED),
        operational_source=RUNTIME,
        publication_receipt_id=receipt,
    )
    target = report_target_key(RUNTIME, envelope.assessment)
    result = escaped_defect_status(store, target, evaluated_at=NOW + timedelta(days=8))
    assert not result.available


# -- a genuine Product Runtime run, and exactly where it stops -----------------
#
# These use the production composition root against a real Git repository: real
# binding, real adapter invocation, real checks, real publication ledger. What
# they demonstrate is the *fail-closed* boundary, not an enablement claim.
#
# Contract V1 §4.1 defines ``source_revision`` as identifying "the exact source
# output represented by the report". A coding worker normally leaves the
# workspace dirty, and §5.2/§5.3 keep the revision types that could describe
# uncommitted output RESERVED_NOT_ACTIVE. So a normal real run publishes
# ``source_revision = null``: the report is genuine, the publication verifies,
# and canonical Eval append still refuses. Publishing the execution base instead
# would be a report describing source it does not represent.


def _real_product_runtime_run(tmp_path: Path):
    """One real run: real repo, real adapter invocation, real checks."""

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime._workspace_fixture import (
        head_commit,
        make_repository,
        task_contract,
        verification_plan,
        write_provider,
    )

    from tomtit.runtime.execution import create_product_runtime

    repository = tmp_path / "project"
    make_repository(repository)
    base = head_commit(repository)

    runtime = create_product_runtime(
        run_id="run-product",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        workspace_path=repository,
        report_directory=tmp_path / "reports",
        executable_path=str(write_provider(tmp_path)),
        allowed_changed_paths=("src/fixture.py",),
    )
    runtime.run_scenario_1()
    return runtime, repository, base


def test_real_dirty_runtime_report_publishes_and_verifies(tmp_path: Path) -> None:
    """The publication is genuine even though the report is unassessable."""

    runtime, repository, base = _real_product_runtime_run(tmp_path)

    assert "VALUE = 42" in (repository / "src" / "fixture.py").read_text()
    assert runtime.workspace.collect_delta().has_uncommitted_changes

    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    # §4.1: no active revision type represents this output, so the field is null
    # and the execution base deliberately does not stand in for it.
    assert published["source_revision"] is None
    assert runtime.execution_base_revision.revision_value == base

    # The publication itself is real and the ledger verifies its exact bytes.
    from tomtit.reporting import publication_authority

    assert runtime.publication_receipt is not None
    facts = publication_authority().verify_publication(
        runtime.publication_receipt.receipt_id
    )
    assert facts.report_sha256 == hashlib.sha256(
        (tmp_path / "reports" / "report.v1.json").read_bytes()
    ).hexdigest()
    assert facts.source_revision is None


@pytest.mark.parametrize("kind", ["review", "owner"])
def test_real_dirty_runtime_report_cannot_be_assessed(
    tmp_path: Path, kind: str
) -> None:
    """Canonical Review and Owner append both fail closed, store unchanged."""

    runtime, _, base = _real_product_runtime_run(tmp_path)
    receipt = runtime.publication_receipt.receipt_id

    with pytest.raises(UnassessableTerminalReportError) as excinfo:
        verify_published_report(receipt)
    assert "source_revision" in str(excinfo.value)

    common = {
        "run_id": "run-product",
        "target_terminal_report_id": "any",
        "target_terminal_report_hash": "a" * 64,
        "target_source_revision": SourceRevision(
            revision_type=RevisionType.GIT_COMMIT, revision_value=base
        ),
        "timestamp": NOW,
        "evidence_refs": ("e",),
    }
    draft = (
        ReviewAssessmentEventDraft(
            assessment_id="rev-1",
            review_outcome=ReviewOutcome.REVIEWER_APPROVED,
            reviewer="codex",
            **common,
        )
        if kind == "review"
        else OwnerAssessmentEventDraft(
            assessment_id="own-1",
            owner_outcome=OwnerOutcome.OWNER_ACCEPTED,
            **common,
        )
    )

    store = EvaluationAssessmentStore()
    with pytest.raises(UnassessableTerminalReportError):
        store.append(draft, operational_source=RUNTIME, publication_receipt_id=receipt)
    assert len(store) == 0 and store.sequence == 0 and store.records == ()


def test_real_report_metadata_is_not_fixture_metadata(tmp_path: Path) -> None:
    """A11/A12: a real run and a fixture run describe themselves differently."""

    _real_product_runtime_run(tmp_path)
    real = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert real["implementer"] == "ProductRuntimeClaudeWorker"
    joined = " ".join(real["adapter_capability_limitations"])
    assert "fake dependency" not in joined
    assert "deterministic fixture evidence" not in joined
    assert "ClaudeCodeCLIAdapter" in joined
    # The limitation is stated, not hidden.
    assert "process-local" in joined

    fixture_runtime = run_product_runtime(tmp_path / "fixture")
    fixture = json.loads(
        published_report(tmp_path / "fixture").read_text(encoding="utf-8")
    )
    assert fixture_runtime.workspace is None
    assert fixture["implementer"] == "FakeWorker"
    assert "fake dependency" in " ".join(fixture["adapter_capability_limitations"])


def test_worker_result_cannot_self_attest_the_execution_profile(
    tmp_path: Path,
) -> None:
    """The profile is Runtime-owned; no worker field reaches it."""

    runtime, _, _ = _real_product_runtime_run(tmp_path)
    claim = runtime.record.worker_result.model_dump()
    for forbidden in ("implementer", "adapter_capability_limitations"):
        assert forbidden not in claim, forbidden
