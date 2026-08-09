"""Read-only persisted-evidence loading for Decision Evaluation V0."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .errors import EvidenceIntegrityError
from .evaluator import evaluate_decision
from .models import DecisionEvaluationInput, DecisionEvaluationResult


def load_evaluation_input(path: str | Path) -> DecisionEvaluationInput:
    """Read and validate one JSON record without writing to any location."""

    evidence_path = Path(path)
    if not evidence_path.is_file():
        raise EvidenceIntegrityError(
            "MISSING_EVIDENCE", "decision evaluation evidence file does not exist"
        )
    try:
        payload = evidence_path.read_bytes()
    except OSError as exc:
        raise EvidenceIntegrityError(
            "UNREADABLE_EVIDENCE", f"evidence could not be read: {exc.__class__.__name__}"
        ) from None
    try:
        return DecisionEvaluationInput.model_validate_json(payload, strict=True)
    except ValidationError as exc:
        raise EvidenceIntegrityError(
            "INVALID_EVIDENCE", f"evidence schema or identity validation failed: {exc.error_count()} error(s)"
        ) from None


def evaluate_evidence_file(path: str | Path) -> DecisionEvaluationResult:
    """Evaluate a persisted record without mutating it or its directory."""

    return evaluate_decision(load_evaluation_input(path))
