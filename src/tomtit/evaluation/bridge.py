"""Runtime -> Evaluation terminal-report bridge (EVAL-RUNTIME-BRIDGE-01).

What this closes
----------------
EVAL-01 shipped report-bound canonical append as fail-closed: Contract V1 §4.1
makes ``report_id`` and ``source_revision`` Runtime-assigned, Evaluation can
neither originate nor verify them, and a value a caller hands to Evaluation is
the caller attesting to itself. This module supplies the missing half.

Where the trust comes from
--------------------------
Not from a file, and not from a Python object. A well-formed ``report.v1.json``
proves only that someone can write JSON; a class named after Runtime proves
only that someone can call a constructor. Both are things an assessment caller
can produce at will, so neither can be the root of trust.

The root of trust is Runtime's own publication ledger — see
:class:`tomtit.reporting._TerminalReportPublicationAuthority`. Runtime records
each terminal report it publishes there, by reading the artifact back, under an
opaque receipt. :func:`verify_published_report` takes such a receipt and asks
the ledger to re-prove it. The caller supplies a lookup key and nothing else:
they may *select* which publication to assess, but every fact the assessment is
then held to comes from the ledger and the artifact, never from them.

This is why there is no binding object here any more. The previous design let a
caller present something carrying its own ``verify()`` and ``target_key()``, and
whatever those methods returned became the truth the store compared against. A
receipt id is an opaque string, so there is nothing on it to override.

Scope of the guarantee
----------------------
Authenticity is process-local for V0A and modelled as such: the ledger is in
memory, so it establishes "this process published this artifact", not that some
other process or host did. Cross-process attestation of publication is a real
open dependency.

Source revision is a second, separate open dependency. Nothing in this
repository establishes the revision a run executed against, so Runtime
publishes none, and §4.2 therefore makes every report published today
unassessable. :func:`verify_published_report` refuses those reports rather than
accepting a revision from anyone — that refusal is the correct behaviour, not a
gap in it.

Direction
---------
Runtime terminal publication -> this bridge -> Evaluation ingestion. This module
is the one place in ``tomtit.evaluation`` that may import ``tomtit.reporting``,
and it imports only the read-only verification surface. ``tomtit.reporting``
gains no dependency on Evaluation, so the edge stays acyclic and Evaluation
still cannot write a report.
"""

from __future__ import annotations

from dataclasses import dataclass

from tomtit.evaluation.provenance import (
    OperationalSource,
    RevisionType,
    SourceRevision,
    require_active_source_revision,
)
from tomtit.reporting import (
    PublishedReportChangedError,
    UnknownPublicationReceiptError,
    publication_authority,
)

#: Contract V1 §4.2 names the serialization whose exact bytes are hashed.
TERMINAL_REPORT_SCHEMA_VERSION = "report.v1"

#: The canonical write-once artifact name Runtime publishes.
TERMINAL_REPORT_NAME = "report.v1.json"


class TerminalReportUnavailableError(ValueError):
    """Raised when a receipt names no verifiable Runtime publication.

    Covers every way of arriving with no publication evidence: no receipt, a
    value that is not a receipt id, or an id the publication ledger never
    issued. All of them mean the same thing — nothing establishes that Runtime
    published the report being assessed.
    """


class PublishedReportVerificationError(ValueError):
    """Raised when the published artifact no longer matches what was published."""


class UnassessableTerminalReportError(ValueError):
    """Raised when a genuinely published report cannot carry an assessment.

    Contract V1 §4.2: a terminal report lacking ``report_id`` or
    ``source_revision`` admits no valid Review or Owner assessment. §5.2/§5.3
    add that a reserved revision type cannot bind one either. Both are refusals
    about the *artifact*, not about the caller — and today every real report
    lands here, because no execution provenance authority exists to give one a
    source revision.
    """


@dataclass(frozen=True)
class VerifiedTerminalReport:
    """Facts the publication ledger re-proved about one published report.

    Constructed only by :func:`verify_published_report`, from ledger facts and
    the artifact's own bytes. The store compares an assessment against
    :attr:`target_key`, which is a plain tuple computed here — never a value
    read off anything the caller passed in.
    """

    operational_source: OperationalSource
    report_path: str
    report_id: str
    run_id: str
    report_hash: str
    schema_version: str
    source_revision: SourceRevision

    @property
    def target_key(self) -> tuple[str, str, str, str, str, str]:
        """The source-qualified §11 target this artifact can be assessed under."""

        return (
            self.operational_source.value,
            self.run_id,
            self.report_id,
            self.report_hash,
            self.source_revision.revision_type.value,
            self.source_revision.revision_value,
        )


def verify_published_report(
    publication_receipt_id: str,
    *,
    operational_source: OperationalSource = OperationalSource.PRODUCT_RUNTIME,
) -> VerifiedTerminalReport:
    """Re-prove one Runtime publication, or fail closed.

    ``publication_receipt_id`` is the opaque id from the
    :class:`~tomtit.reporting.PublicationReceipt` Runtime kept when it published
    (``runtime.publication_receipt.receipt_id``). It is a lookup key, not a
    claim: the ledger decides whether it names a real publication, re-reads the
    path *it* recorded, and re-hashes those bytes.

    Refuses, in order:

    * a source other than ``PRODUCT_RUNTIME``, which has no terminal-report
      identity to verify;
    * an id the ledger never issued, or a value that is not one — no evidence
      that Runtime published anything (§4.1);
    * an artifact that changed or vanished since publication (§4.2 binds to
      exact bytes);
    * a report with no ``report_id``/``run_id``, or no ``source_revision`` —
      unassessable under §4.2;
    * a reserved revision type (§5.2/§5.3).

    ``operational_source`` states which system's ledger is being consulted; it
    is never read from the artifact, because an artifact cannot attest to its
    own origin.
    """

    if not isinstance(operational_source, OperationalSource):
        raise TypeError("operational_source must be an OperationalSource member")
    if operational_source is not OperationalSource.PRODUCT_RUNTIME:
        raise UnassessableTerminalReportError(
            f"{operational_source.value} does not publish a Contract V1 "
            "TerminalReport; only PRODUCT_RUNTIME has terminal-report identity, "
            "and pretending otherwise would invent provenance for a source that "
            "has none"
        )

    try:
        facts = publication_authority().verify_publication(publication_receipt_id)
    except UnknownPublicationReceiptError as error:
        raise TerminalReportUnavailableError(
            f"no verifiable Product Runtime publication: {error}. Evaluation "
            "cannot originate report identity, and a report file a caller "
            "supplies is the caller attesting to itself."
        ) from error
    except PublishedReportChangedError as error:
        raise PublishedReportVerificationError(str(error)) from error

    if facts.schema_version != TERMINAL_REPORT_SCHEMA_VERSION:
        raise UnassessableTerminalReportError(
            f"published report {facts.report_path} declares schema_version "
            f"{facts.schema_version!r}; Contract V1 §4.2 binds assessments to "
            f"{TERMINAL_REPORT_SCHEMA_VERSION!r} bytes"
        )
    for field, value in (("report_id", facts.report_id), ("run_id", facts.run_id)):
        if not isinstance(value, str) or not value:
            raise UnassessableTerminalReportError(
                f"published report {facts.report_path} has no usable {field}; "
                "Contract V1 §4.2 makes a report lacking Runtime-assigned "
                "identity unassessable"
            )

    if facts.source_revision is None:
        raise UnassessableTerminalReportError(
            f"published report {facts.report_path} carries no source_revision, "
            "so Contract V1 §4.2 admits no Review or Owner assessment against "
            "it. This is a missing upstream execution fact: no component "
            "establishes the source revision a run executed against, so Runtime "
            "has none to publish. Evaluation must not supply one — a revision "
            "chosen by the party being assessed is not provenance. Open "
            "dependency: PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY."
        )

    try:
        revision = SourceRevision(
            revision_type=RevisionType(facts.source_revision.revision_type),
            revision_value=facts.source_revision.revision_value,
        )
    except (ValueError, TypeError) as error:
        raise UnassessableTerminalReportError(
            f"published report {facts.report_path} has an invalid "
            f"source_revision: {error}"
        ) from error
    # §5.3: a reserved revision type cannot bind an assessment.
    require_active_source_revision(revision)

    return VerifiedTerminalReport(
        operational_source=operational_source,
        report_path=facts.report_path,
        report_id=facts.report_id,
        run_id=facts.run_id,
        report_hash=facts.report_sha256,
        schema_version=facts.schema_version,
        source_revision=revision,
    )
