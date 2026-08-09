from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tomtit_decision_eval_v0 import (
    AcceptanceRequirement,
    CheckStatus,
    ClaimStatus,
    CounterfactualEvidence,
    CounterfactualSource,
    CounterfactualSourceType,
    DecisionEvaluationInput,
    DecisionLabel,
    EvidenceIntegrityError,
    EvidenceProvenance,
    FindingAdjudication,
    FindingImpact,
    ObjectiveTaskNeed,
    PostRetryEvidence,
    RequirementStatus,
    TomtitAction,
    evaluate_decision,
    evaluate_evidence_file,
    load_evaluation_input,
)

from .conftest import canonical_evidence, post_retry


def counterfactual_source(
    source_type: CounterfactualSourceType,
    evidence_ref: str,
    *,
    source_attempt: int,
) -> CounterfactualSource:
    return CounterfactualSource(
        source_id=f"source-{evidence_ref}",
        source_type=source_type,
        run_id="run-1",
        decision_id="decision-1",
        source_attempt=source_attempt,
        evaluates_attempt=1,
        evidence_ref=evidence_ref,
    )


def test_persisted_evidence_is_consumed_read_only(make_case, tmp_path: Path) -> None:
    case = make_case(TomtitAction.STOP)
    evidence_path = tmp_path / "decision.json"
    evidence_path.write_text(case.model_dump_json(indent=2), encoding="utf-8")
    before_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    before_entries = tuple(sorted(path.name for path in tmp_path.iterdir()))

    result = evaluate_evidence_file(evidence_path)

    assert result.decision_label is DecisionLabel.CORRECT
    assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == before_hash
    assert tuple(sorted(path.name for path in tmp_path.iterdir())) == before_entries


def test_missing_evidence_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(EvidenceIntegrityError) as raised:
        load_evaluation_input(tmp_path / "missing.json")
    assert raised.value.category == "MISSING_EVIDENCE"


def test_malformed_json_fails_closed(tmp_path: Path) -> None:
    evidence_path = tmp_path / "bad.json"
    evidence_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError) as raised:
        load_evaluation_input(evidence_path)
    assert raised.value.category == "INVALID_EVIDENCE"


def test_unknown_evidence_provenance_fails_closed(make_case, tmp_path: Path) -> None:
    payload = make_case(TomtitAction.STOP).model_dump(mode="json")
    payload["decision_point"]["pre_decision_evidence"][0]["provenance"] = "UNKNOWN_ROLE"
    evidence_path = tmp_path / "unknown-provenance.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError) as raised:
        load_evaluation_input(evidence_path)
    assert raised.value.category == "INVALID_EVIDENCE"


def test_mismatched_run_identity_fails_closed(make_case, tmp_path: Path) -> None:
    payload = make_case(TomtitAction.STOP).model_dump(mode="json")
    payload["decision_point"]["pre_decision_evidence"][0]["run_id"] = "other-run"
    evidence_path = tmp_path / "mismatch.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError) as raised:
        load_evaluation_input(evidence_path)
    assert raised.value.category == "INVALID_EVIDENCE"


def test_mismatched_decision_identity_fails_closed(make_case) -> None:
    point_type = type(make_case(TomtitAction.STOP).decision_point)
    payload = make_case(TomtitAction.STOP).decision_point.model_dump()
    payload["pre_decision_evidence"][0]["decision_id"] = "other-decision"

    with pytest.raises(ValidationError, match="identity does not match"):
        point_type.model_validate(payload)


def test_future_evidence_cannot_enter_decision_point(make_case) -> None:
    point_type = type(make_case(TomtitAction.STOP).decision_point)
    payload = make_case(TomtitAction.STOP).decision_point.model_dump()
    payload["pre_decision_evidence"][0]["sequence"] = payload["decision_sequence"]

    with pytest.raises(ValidationError, match="not available at the decision point"):
        point_type.model_validate(payload)


def test_duplicate_canonical_evidence_identity_fails_closed(make_case) -> None:
    valid = make_case(TomtitAction.STOP)
    payload = valid.model_dump()
    payload["evaluation_evidence"] = (valid.decision_point.pre_decision_evidence[0],)

    with pytest.raises(ValidationError, match="IDs must be unique"):
        DecisionEvaluationInput.model_validate(payload)


def test_failed_required_check_cannot_contradict_satisfied_requirement(make_case) -> None:
    point_type = type(make_case(TomtitAction.STOP).decision_point)
    payload = make_case(TomtitAction.STOP).decision_point.model_dump()
    payload["deterministic_checks"][0]["status"] = CheckStatus.FAIL

    with pytest.raises(ValidationError, match="contradicts a satisfied"):
        point_type.model_validate(payload)


def test_unknown_check_evidence_reference_fails_closed(make_case) -> None:
    point_type = type(make_case(TomtitAction.STOP).decision_point)
    payload = make_case(TomtitAction.STOP).decision_point.model_dump()
    payload["deterministic_checks"][0]["evidence_id"] = "missing-evidence"

    with pytest.raises(ValidationError, match="unknown pre-decision evidence"):
        point_type.model_validate(payload)


def test_verified_counterfactual_dangling_reference_is_rejected(make_case) -> None:
    dangling = counterfactual_source(
        CounterfactualSourceType.EXPLICIT_REPLAY,
        "does-not-exist",
        source_attempt=2,
    )

    with pytest.raises(ValidationError, match="dangling canonical evidence"):
        make_case(
            TomtitAction.STOP,
            counterfactual=CounterfactualEvidence.VERIFIED,
            explicit_counterfactual_sources=(dangling,),
        )


def test_normal_attempt_two_success_cannot_be_verified_counterfactual(make_case) -> None:
    normal_outcome = counterfactual_source(
        CounterfactualSourceType.NORMAL_RUNTIME_OUTCOME,
        "attempt-2-evidence",
        source_attempt=2,
    )

    with pytest.raises(ValidationError, match="invalid provenance"):
        make_case(
            TomtitAction.RETRY,
            post=post_retry(final_outcome="COMPLETED"),
            counterfactual=CounterfactualEvidence.VERIFIED,
            explicit_counterfactual_sources=(normal_outcome,),
        )


def test_normal_attempt_two_outcome_may_be_inferred_but_does_not_prove_need(
    make_case, material_finding
) -> None:
    normal_outcome = counterfactual_source(
        CounterfactualSourceType.NORMAL_RUNTIME_OUTCOME,
        "attempt-2-evidence",
        source_attempt=2,
    )
    case = make_case(
        TomtitAction.RETRY,
        finding=material_finding,
        requirement_status=RequirementStatus.UNKNOWN,
        check_status=CheckStatus.UNKNOWN,
        post=post_retry(final_outcome="COMPLETED"),
        counterfactual=CounterfactualEvidence.INFERRED,
        explicit_counterfactual_sources=(normal_outcome,),
    )

    assert evaluate_decision(case).objective_task_need is ObjectiveTaskNeed.AMBIGUOUS


def test_explicit_replay_with_canonical_identity_is_verified(make_case) -> None:
    replay = canonical_evidence(
        "replay-evidence",
        EvidenceProvenance.COUNTERFACTUAL_REPLAY,
        attempt=2,
        sequence=7,
        kind="explicit-decision-point-replay",
    )
    replay_source = counterfactual_source(
        CounterfactualSourceType.EXPLICIT_REPLAY,
        replay.evidence_id,
        source_attempt=2,
    )

    case = make_case(
        TomtitAction.STOP,
        counterfactual=CounterfactualEvidence.VERIFIED,
        extra_evaluation_evidence=(replay,),
        explicit_counterfactual_sources=(replay_source,),
    )

    assert case.counterfactual_evidence is CounterfactualEvidence.VERIFIED


def test_counterfactual_source_provenance_must_match_evidence(make_case) -> None:
    replay = canonical_evidence(
        "replay-evidence",
        EvidenceProvenance.COUNTERFACTUAL_REPLAY,
        attempt=2,
        sequence=7,
        kind="explicit-decision-point-replay",
    )
    mislabeled = counterfactual_source(
        CounterfactualSourceType.OWNER_ADJUDICATION,
        replay.evidence_id,
        source_attempt=2,
    )

    with pytest.raises(ValidationError, match="provenance does not match"):
        make_case(
            TomtitAction.STOP,
            counterfactual=CounterfactualEvidence.VERIFIED,
            extra_evaluation_evidence=(replay,),
            explicit_counterfactual_sources=(mislabeled,),
        )


def test_runtime_reviewer_cannot_ground_finding_adjudication(
    make_case, material_finding
) -> None:
    circular = FindingAdjudication(
        finding_id=material_finding.finding_id,
        evidence_refs=(material_finding.evidence_id,),
        impact=FindingImpact(affects_correctness=ClaimStatus.YES),
        repairable=ClaimStatus.YES,
        criterion_ids=("criterion-1",),
    )
    payload = make_case(
        TomtitAction.RETRY,
        finding=material_finding,
        requirement_status=RequirementStatus.UNKNOWN,
        check_status=CheckStatus.UNKNOWN,
    ).model_dump()
    payload["finding_adjudications"] = (circular,)

    with pytest.raises(ValidationError, match="lacks trusted provenance"):
        DecisionEvaluationInput.model_validate(payload)


def test_post_retry_reference_must_exist(make_case) -> None:
    valid = make_case(TomtitAction.RETRY, post=post_retry(before=1, after=0))
    payload = valid.model_dump()
    payload["evaluation_evidence"] = ()

    with pytest.raises(ValidationError, match="dangling canonical evidence"):
        DecisionEvaluationInput.model_validate(payload)


def test_stop_cannot_claim_post_retry_evidence(make_case) -> None:
    with pytest.raises(ValidationError, match="STOP decision"):
        make_case(TomtitAction.STOP, post=post_retry(before=1, after=0))


def test_partial_material_issue_counts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="both before and after"):
        PostRetryEvidence(
            run_id="run-1",
            decision_id="decision-1",
            source_attempt=2,
            evidence_refs=("post",),
            material_issue_count_before=1,
        )


def test_determinate_acceptance_status_requires_evidence_reference() -> None:
    with pytest.raises(ValidationError, match="must cite evidence"):
        AcceptanceRequirement(
            criterion_id="criterion",
            description="Required behavior",
            status=RequirementStatus.SATISFIED,
        )
