"""Provenance axes owned by TOMTIT Evaluation.

Two axes are represented here and are deliberately kept separate:

``OperationalSource``
    *Which operational system produced the observation* — the Product Runtime
    or the local dogfood orchestrator. This is an Evaluation-supplied
    classification of where an observation came from.

``SourceRevision``
    *Which exact source output an assessment is bound to*, per Contract V1 §5.
    This is supplied by the observed operational source; Evaluation validates
    and stores it but never fabricates it.

Collapsing these axes would make it impossible to say "this dogfood
observation is bound to commit X" versus "this Product Runtime observation is
bound to commit X". Contract V1 §5 governs only the second axis; the first is
required by the R2.2 integration amendment and is additive to V1 semantics
rather than a reinterpretation of them.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]

_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvaluationModel(BaseModel):
    """Strict immutable base for every Evaluation-owned record.

    Mirrors the configuration used by ``tomtit.domain.contracts.DomainModel``
    and ``tomtit_decision_eval_v0.models.FrozenModel`` so Evaluation records
    behave like every other canonical record in this repository. Defined here
    rather than imported because Evaluation must not depend on being able to
    modify those modules.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class StringEnum(str, Enum):
    """Stable string enum for Evaluation records."""


class OperationalSource(StringEnum):
    """The operational system that produced an observation.

    This is an identity axis, not a location: it must never be inferred from a
    repository path, a directory name, or a schema name. The caller states
    which system it observed.
    """

    PRODUCT_RUNTIME = "PRODUCT_RUNTIME"
    DOGFOOD_ORCHESTRATOR = "DOGFOOD_ORCHESTRATOR"


class RevisionType(StringEnum):
    """Revision types reserved by Contract V1 §5."""

    GIT_COMMIT = "GIT_COMMIT"
    PATCH_SHA256 = "PATCH_SHA256"
    WORKTREE_SNAPSHOT_SHA256 = "WORKTREE_SNAPSHOT_SHA256"


class RevisionStatus(StringEnum):
    """Activation status of a revision type, per Contract V1 §5.1 and §5.2."""

    ACTIVE = "ACTIVE"
    RESERVED_NOT_ACTIVE = "RESERVED_NOT_ACTIVE"


#: Contract V1 §5.1 makes exactly one revision type active for current build
#: governance. §5.2 reserves the other two, and §5.3 forbids activating them
#: until an approved canonicalization contract exists. That approval does not
#: exist, so this mapping is fixed here rather than made configurable: a
#: configurable switch would let a caller activate a reserved type without the
#: contract §5.3 requires.
_REVISION_TYPE_STATUS: dict[RevisionType, RevisionStatus] = {
    RevisionType.GIT_COMMIT: RevisionStatus.ACTIVE,
    RevisionType.PATCH_SHA256: RevisionStatus.RESERVED_NOT_ACTIVE,
    RevisionType.WORKTREE_SNAPSHOT_SHA256: RevisionStatus.RESERVED_NOT_ACTIVE,
}


def revision_type_status(revision_type: RevisionType) -> RevisionStatus:
    """Return the Contract V1 activation status for `revision_type`."""

    return _REVISION_TYPE_STATUS[revision_type]


class SourceRevision(EvaluationModel):
    """Contract V1 §5 `SourceRevision`.

    A revision is a *typed* pair. A bare commit string is not a
    ``SourceRevision``: without ``revision_type`` there is no way to enforce
    §5.2's reservation, because a 64-hex patch digest and a 64-hex worktree
    snapshot digest are indistinguishable as raw strings.

    Reserved types may be *constructed* — they are reserved, not invalid, and
    Evaluation must be able to record that it saw one — but
    :meth:`is_active` is False for them and assessment binding refuses them.
    """

    revision_type: RevisionType
    revision_value: NonEmptyString

    @model_validator(mode="after")
    def validate_revision_value_shape(self) -> SourceRevision:
        value = self.revision_value
        if self.revision_type is RevisionType.GIT_COMMIT:
            if not _GIT_COMMIT_PATTERN.match(value):
                raise ValueError(
                    "GIT_COMMIT revision_value must be a full 40-character "
                    "lowercase hexadecimal commit hash"
                )
        elif not _SHA256_PATTERN.match(value):
            raise ValueError(
                f"{self.revision_type.value} revision_value must be a "
                "64-character lowercase hexadecimal SHA-256 digest"
            )
        return self

    @property
    def status(self) -> RevisionStatus:
        """Contract V1 activation status of this revision's type."""

        return revision_type_status(self.revision_type)

    @property
    def is_active(self) -> bool:
        """Whether this revision may be used for valid assessment binding."""

        return self.status is RevisionStatus.ACTIVE


class InactiveSourceRevisionError(ValueError):
    """Raised when binding is attempted against a reserved revision type.

    Contract V1 §5.2/§5.3: reserved types "must not be used for valid
    assessment binding until an approved canonicalization contract exists".
    """


def require_active_source_revision(revision: SourceRevision) -> SourceRevision:
    """Return `revision` when its type is active, else fail closed."""

    if not revision.is_active:
        raise InactiveSourceRevisionError(
            f"source revision type {revision.revision_type.value} is "
            f"{revision.status.value}; Contract V1 §5.3 requires an approved "
            "canonicalization contract before it may bind an assessment"
        )
    return revision
