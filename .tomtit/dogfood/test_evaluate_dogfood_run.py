"""Offline, deterministic tests for the DOGFOOD_EVALUATION_V0 telemetry tool.

None of these tests invoke a real Claude/Codex service and none modify any
historical Target-04 run evidence (Run001-006). Disposable synthetic loop
evidence trees are used for every write-path test; the one test that reads
real Run006 evidence never calls the write path against it.

Run with:

    python3 -m pytest .tomtit/dogfood/test_evaluate_dogfood_run.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

DOGFOOD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DOGFOOD_DIR.parents[1]

_spec = importlib.util.spec_from_file_location("evaluate_dogfood_run", DOGFOOD_DIR / "evaluate_dogfood_run.py")
assert _spec is not None and _spec.loader is not None
evaluate_dogfood_run = importlib.util.module_from_spec(_spec)
sys.modules["evaluate_dogfood_run"] = evaluate_dogfood_run
_spec.loader.exec_module(evaluate_dogfood_run)

REAL_RUN006_EVIDENCE_ROOT = (
    Path.home() / "Documents" / "AI" / "ai-agent" / "TOMTIT-E2E-Evidence" / "real-e2e-target04a-006-sonnet-medium"
)


# ---------------------------------------------------------------------------
# Fixture builders: disposable, synthetic loop evidence trees matching the
# exact canonical shapes real_e2e-target04a run_loop.py evidence uses.
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def make_impl_child(evidence_root: Path, run_id: str, *, repair: bool = False, omit: tuple[str, ...] = ()) -> None:
    child = evidence_root / "children" / run_id
    child.mkdir(parents=True, exist_ok=True)
    files = {
        "receipt.json": {"run_id": run_id, "status": "SUCCEEDED", "success": True},
        "result.json": {"run_id": run_id, "status": "SUCCEEDED"},
        "diff.patch": "--- a/app.txt\n+++ b/app.txt\n",
    }
    if repair:
        files["repair-context.json"] = {"repair_run_id": run_id}
        files["checks.json"] = []
        files["repair-prompt.txt"] = "repair prompt"
    for name, content in files.items():
        if name in omit:
            continue
        path = child / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            _write_json(path, content)


def make_review_child(evidence_root: Path, run_id: str, *, verdict: str = "APPROVE", omit: tuple[str, ...] = ()) -> None:
    child = evidence_root / "children" / run_id
    child.mkdir(parents=True, exist_ok=True)
    files = {
        "review-receipt.json": {"review_run_id": run_id, "verdict": verdict},
        "review-result.json": {"verdict": verdict, "findings": []},
        "review-prompt.txt": "review prompt",
        "checks.json": [],
    }
    for name, content in files.items():
        if name in omit:
            continue
        path = child / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            _write_json(path, content)


def make_loop_evidence(
    tmp_path: Path,
    *,
    run_id: str = "loop-1",
    trajectory: str = "first_pass",
    terminal_outcome: str = "COMPLETED",
    stop_reason: str = "REVIEW_APPROVED",
    accepted: bool = True,
    usage: dict | None | str = "default",
    unexpected_changed_paths: list | None = (),
    duration_ms: int | None = 262458,
    omit_loop_files: tuple[str, ...] = (),
    omit_review1_files: tuple[str, ...] = (),
    include_task_contract: bool = True,
    dir_name: str | None = None,
) -> Path:
    """`trajectory`: "first_pass" (impl-1 + review-1 APPROVE), "repair"
    (impl-1 + review-1 REQUEST_CHANGES + repair-2 + review-2 APPROVE, the
    Run006 shape), or "preflight_blocked" (no children at all)."""

    evidence_root = tmp_path / (dir_name or run_id)
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "children").mkdir(parents=True, exist_ok=True)

    if trajectory == "first_pass":
        implementation_run_ids = [f"{run_id}-impl-1"]
        review_run_ids = [f"{run_id}-review-1"]
        make_impl_child(evidence_root, implementation_run_ids[0])
        make_review_child(evidence_root, review_run_ids[0], verdict="APPROVE", omit=omit_review1_files)
        implementation_attempts, repair_rounds, review_attempts, model_invocations = 1, 0, 1, 2
    elif trajectory == "repair":
        implementation_run_ids = [f"{run_id}-impl-1", f"{run_id}-repair-2"]
        review_run_ids = [f"{run_id}-review-1", f"{run_id}-review-2"]
        make_impl_child(evidence_root, implementation_run_ids[0])
        make_review_child(evidence_root, review_run_ids[0], verdict="REQUEST_CHANGES", omit=omit_review1_files)
        make_impl_child(evidence_root, implementation_run_ids[1], repair=True)
        make_review_child(evidence_root, review_run_ids[1], verdict="APPROVE")
        implementation_attempts, repair_rounds, review_attempts, model_invocations = 2, 1, 2, 4
    elif trajectory == "preflight_blocked":
        implementation_run_ids = []
        review_run_ids = []
        implementation_attempts, repair_rounds, review_attempts, model_invocations = 0, 0, 0, 0
    else:
        raise ValueError(trajectory)

    if usage == "default":
        usage_block: dict | None = {"total_model_cost_usd": 0.5, "total_input_tokens": 100, "total_output_tokens": 200}
    else:
        usage_block = usage  # type: ignore[assignment]

    loop_result = {
        "accepted": accepted,
        "loop_run_id": run_id,
        "run_state": terminal_outcome,
        "terminal_outcome": terminal_outcome,
        "stop_reason": stop_reason,
        "implementation_attempts": implementation_attempts,
        "repair_rounds": repair_rounds,
        "review_attempts": review_attempts,
        "model_invocation_count": model_invocations,
        "implementation_run_ids": implementation_run_ids,
        "review_run_ids": review_run_ids,
        "duration_ms": duration_ms,
        "unexpected_changed_paths": list(unexpected_changed_paths) if unexpected_changed_paths is not None else None,
    }
    if usage_block is not None:
        loop_result["usage"] = usage_block

    loop_receipt = {"loop_run_id": run_id, "terminal_outcome": terminal_outcome, "accepted": accepted}

    if "loop-result.json" not in omit_loop_files:
        _write_json(evidence_root / "loop-result.json", loop_result)
    if "loop-receipt.json" not in omit_loop_files:
        _write_json(evidence_root / "loop-receipt.json", loop_receipt)
    if "events.jsonl" not in omit_loop_files:
        (evidence_root / "events.jsonl").write_text('{"event_type": "LOOP_CREATED"}\n', encoding="utf-8")

    if include_task_contract:
        _write_json(
            evidence_root / "task-contract.json",
            {
                "loop_run_id": run_id,
                "baseline_commit": "6bb0d1b2f8ad69d6c6a91b1c5516b5da86211a4e",
                "task_prompt_sha256": "ce6b4693d5a7634ba26974449ca3dca5f1a1e4d0cbdd4c012bbfb11a7f7cceae",
            },
        )

    return evidence_root


def make_owner(
    *,
    owner_disposition: str = "ACCEPTED",
    active_human_seconds: int = 120,
    owner_interruptions: int = 1,
    clarification_count: int = 0,
    manual_edit_lines: int = 0,
    owner_note: str | None = None,
    escaped_defect_reported: str = "UNKNOWN",
) -> "evaluate_dogfood_run.OwnerSuppliedFacts":
    return evaluate_dogfood_run.OwnerSuppliedFacts(
        owner_disposition=owner_disposition,
        active_human_seconds=active_human_seconds,
        owner_interruptions=owner_interruptions,
        clarification_count=clarification_count,
        manual_edit_lines=manual_edit_lines,
        owner_note=owner_note,
        escaped_defect_reported=escaped_defect_reported,
    )


# ---------------------------------------------------------------------------
# Identity / schema.
# ---------------------------------------------------------------------------


def test_schema_version_constant() -> None:
    assert evaluate_dogfood_run.SCHEMA_VERSION == "DOGFOOD_EVALUATION_V0"


def test_build_evaluation_record_identity_fields(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-identity")
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root, owner=make_owner(), runtime_commit="deadbeef"
    )
    assert record["schema_version"] == "DOGFOOD_EVALUATION_V0"
    assert record["run_id"] == "loop-identity"
    assert record["task_contract_sha256"] == "ce6b4693d5a7634ba26974449ca3dca5f1a1e4d0cbdd4c012bbfb11a7f7cceae"
    assert record["workspace_baseline"] == "6bb0d1b2f8ad69d6c6a91b1c5516b5da86211a4e"
    assert record["runtime_commit"] == "deadbeef"
    assert isinstance(record["evaluation_id"], str) and record["evaluation_id"]
    assert isinstance(record["evaluated_at"], str) and record["evaluated_at"]


def test_evaluation_id_unique_per_call(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-uuid")
    r1 = evaluate_dogfood_run.build_evaluation_record(evidence_root=evidence_root, owner=make_owner(), runtime_commit=None)
    r2 = evaluate_dogfood_run.build_evaluation_record(evidence_root=evidence_root, owner=make_owner(), runtime_commit=None)
    assert r1["evaluation_id"] != r2["evaluation_id"]


# ---------------------------------------------------------------------------
# Owner disposition derivation matrix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "disposition,manual_edit_lines,expected_accepted,expected_accepted_no_edit",
    [
        ("ACCEPTED", 0, True, True),
        ("ACCEPTED", 5, True, False),
        ("ACCEPTED_WITH_MANUAL_EDIT", 3, True, False),
        ("ACCEPTED_WITH_MANUAL_EDIT", 0, True, False),
        ("NOT_USABLE", 0, False, False),
    ],
)
def test_owner_disposition_derivation(
    tmp_path: Path, disposition: str, manual_edit_lines: int, expected_accepted: bool, expected_accepted_no_edit: bool
) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id=f"loop-disp-{disposition}-{manual_edit_lines}")
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root,
        owner=make_owner(owner_disposition=disposition, manual_edit_lines=manual_edit_lines),
        runtime_commit=None,
    )
    assert record["accepted"] is expected_accepted
    assert record["accepted_without_manual_edit"] is expected_accepted_no_edit


def test_active_human_minutes_derived_only_when_accepted(tmp_path: Path) -> None:
    accepted_root = make_loop_evidence(tmp_path, run_id="loop-minutes-accepted")
    accepted_record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=accepted_root,
        owner=make_owner(owner_disposition="ACCEPTED", active_human_seconds=180),
        runtime_commit=None,
    )
    assert accepted_record["active_human_minutes_to_confident_accept"] == 3.0

    not_usable_root = make_loop_evidence(tmp_path, run_id="loop-minutes-not-usable")
    not_usable_record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=not_usable_root,
        owner=make_owner(owner_disposition="NOT_USABLE", active_human_seconds=999),
        runtime_commit=None,
    )
    # (Section 8) Do not reinterpret NOT_USABLE as infinite time -- null, not 0, not 999/60.
    assert not_usable_record["active_human_minutes_to_confident_accept"] is None


# ---------------------------------------------------------------------------
# Runtime-derived outcome extraction (Section 5).
# ---------------------------------------------------------------------------


def test_runtime_derived_outcome_extraction_first_pass(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-first-pass", trajectory="first_pass")
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.runtime_terminal_outcome == "COMPLETED"
    assert facts.runtime_stop_reason == "REVIEW_APPROVED"
    assert facts.implementation_attempt_count == 1
    assert facts.repair_count == 0
    assert facts.review_count == 1
    assert facts.model_invocation_count == 2
    assert facts.elapsed_seconds == 262.458
    assert facts.evidence_complete is True


def test_runtime_accepted_never_confused_with_owner_accepted(tmp_path: Path) -> None:
    """(Section 5) runtime_accepted (Runtime's own field) and accepted
    (Owner-derived) are DISTINCT fields and must never be conflated."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-runtime-accepted-distinct", accepted=True)
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root, owner=make_owner(owner_disposition="NOT_USABLE"), runtime_commit=None
    )
    assert record["runtime_accepted"] is True
    assert record["accepted"] is False


# ---------------------------------------------------------------------------
# Tri-state semantics (Sections 6/7).
# ---------------------------------------------------------------------------


def test_cost_and_token_known_when_usage_present(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(
        tmp_path,
        run_id="loop-usage-known",
        usage={"total_model_cost_usd": 1.23, "total_input_tokens": 10, "total_output_tokens": 20},
    )
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.total_cost_usd_status == "KNOWN" and facts.total_cost_usd_value == 1.23
    assert facts.total_input_tokens_status == "KNOWN" and facts.total_input_tokens_value == 10
    assert facts.total_output_tokens_status == "KNOWN" and facts.total_output_tokens_value == 20


def test_cost_and_token_unavailable_when_usage_block_absent(tmp_path: Path) -> None:
    """(Section 7/16.E) No `usage` key at all in this evidence shape ->
    UNAVAILABLE, never zero, never inferred."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-usage-absent", usage=None)
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.total_cost_usd_status == "UNAVAILABLE" and facts.total_cost_usd_value is None
    assert facts.total_input_tokens_status == "UNAVAILABLE" and facts.total_input_tokens_value is None


def test_cost_unknown_when_usage_present_but_field_malformed(tmp_path: Path) -> None:
    """(Section 7) The concept is measurable (usage block exists) but this
    specific field is not a trustworthy number -> UNKNOWN, not UNAVAILABLE,
    not zero."""

    evidence_root = make_loop_evidence(
        tmp_path,
        run_id="loop-usage-malformed",
        usage={"total_model_cost_usd": "not-a-number", "total_input_tokens": 10, "total_output_tokens": 20},
    )
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.total_cost_usd_status == "UNKNOWN" and facts.total_cost_usd_value is None


def test_scope_violation_true_when_unexpected_changed_paths_present(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-scope-violation", unexpected_changed_paths=["evil.txt"])
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.scope_violation == "TRUE"


def test_scope_violation_false_when_no_unexpected_changed_paths(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-scope-clean", unexpected_changed_paths=[])
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.scope_violation == "FALSE"


def test_scope_violation_unknown_when_field_absent(tmp_path: Path) -> None:
    """(Section 6) Never manufacture certainty: if scope validity cannot be
    deterministically established, scope_violation = UNKNOWN."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-scope-unknown", unexpected_changed_paths=None)
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.scope_violation == "UNKNOWN"


def test_scope_violation_unknown_when_evidence_incomplete(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(
        tmp_path, run_id="loop-scope-incomplete", trajectory="first_pass", omit_review1_files=("checks.json",)
    )
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.evidence_complete is False
    assert facts.scope_violation == "UNKNOWN"


def test_escaped_defect_reported_defaults_to_unknown_never_inferred_from_approve(tmp_path: Path) -> None:
    """(Section 6/13) Even a clean Review2 APPROVE must never be used to
    infer "no escaped defect" -- default is UNKNOWN unless Owner-supplied."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-escaped-default", trajectory="repair")
    record = evaluate_dogfood_run.build_evaluation_record(evidence_root=evidence_root, owner=make_owner(), runtime_commit=None)
    assert record["escaped_defect_reported"] == "UNKNOWN"


@pytest.mark.parametrize("value", ["TRUE", "FALSE", "UNKNOWN"])
def test_escaped_defect_reported_owner_supplied(tmp_path: Path, value: str) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id=f"loop-escaped-{value}")
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root, owner=make_owner(escaped_defect_reported=value), runtime_commit=None
    )
    assert record["escaped_defect_reported"] == value


# ---------------------------------------------------------------------------
# Trajectory-aware evidence completeness (Section 12).
# ---------------------------------------------------------------------------


def test_evidence_complete_first_pass_never_requires_repair_artifacts(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-complete-first-pass", trajectory="first_pass")
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.evidence_complete is True
    assert facts.missing_evidence_paths == ()


def test_evidence_complete_repair_path_requires_repair_and_review2(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-complete-repair", trajectory="repair")
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.evidence_complete is True


def test_evidence_incomplete_when_repair_context_missing(tmp_path: Path) -> None:
    """The repair child is missing its repair-specific required file
    (repair-context.json) -- must be detected, and named precisely."""

    evidence_root = tmp_path / "loop-incomplete-repair"
    evidence_root.mkdir()
    (evidence_root / "children").mkdir()
    run_id = "loop-incomplete-repair"
    make_impl_child(evidence_root, f"{run_id}-impl-1")
    make_review_child(evidence_root, f"{run_id}-review-1", verdict="REQUEST_CHANGES")
    make_impl_child(evidence_root, f"{run_id}-repair-2", repair=True, omit=("repair-context.json",))
    make_review_child(evidence_root, f"{run_id}-review-2", verdict="APPROVE")
    _write_json(
        evidence_root / "loop-result.json",
        {
            "loop_run_id": run_id,
            "run_state": "COMPLETED",
            "terminal_outcome": "COMPLETED",
            "stop_reason": "REVIEW_APPROVED",
            "implementation_attempts": 2,
            "repair_rounds": 1,
            "review_attempts": 2,
            "model_invocation_count": 4,
            "implementation_run_ids": [f"{run_id}-impl-1", f"{run_id}-repair-2"],
            "review_run_ids": [f"{run_id}-review-1", f"{run_id}-review-2"],
            "unexpected_changed_paths": [],
        },
    )
    _write_json(evidence_root / "loop-receipt.json", {"loop_run_id": run_id})
    (evidence_root / "events.jsonl").write_text("{}\n", encoding="utf-8")

    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.evidence_complete is False
    assert f"children/{run_id}-repair-2/repair-context.json" in facts.missing_evidence_paths
    # The first-pass impl child's own (non-repair) required files are
    # unaffected by the repair child's omission.
    assert not any("impl-1" in p for p in facts.missing_evidence_paths)


def test_evidence_complete_preflight_blocked_requires_only_loop_level_files(tmp_path: Path) -> None:
    """(Section 12) A failed-preflight trajectory records no
    implementation/review run ids at all -- no child evidence is required."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-preflight-blocked", trajectory="preflight_blocked")
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.evidence_complete is True
    assert facts.missing_evidence_paths == ()


# ---------------------------------------------------------------------------
# Run006-shaped fixture (Section 15 compatibility target).
# ---------------------------------------------------------------------------


def test_run006_shaped_synthetic_fixture(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(
        tmp_path,
        run_id="real-e2e-target04a-006-sonnet-medium",
        trajectory="repair",
        terminal_outcome="COMPLETED",
        stop_reason="REVIEW_APPROVED",
        usage={"total_model_cost_usd": 0.784794, "total_input_tokens": 76, "total_output_tokens": 12430},
        duration_ms=262458,
    )
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert facts.implementation_attempt_count == 2
    assert facts.repair_count == 1
    assert facts.review_count == 2
    assert facts.model_invocation_count == 4
    assert facts.runtime_terminal_outcome == "COMPLETED"
    assert facts.runtime_stop_reason == "REVIEW_APPROVED"
    assert facts.evidence_complete is True


def test_real_run006_evidence_extraction_read_only() -> None:
    """(Section 15) Use the REAL Run006 evidence directly, as a read-only
    example -- never writing anywhere near it. Skips gracefully if that
    machine-local evidence is not present (e.g. a different checkout)."""

    if not REAL_RUN006_EVIDENCE_ROOT.is_dir():
        pytest.skip("real Run006 evidence not present on this machine")

    before_listing = sorted(p.name for p in REAL_RUN006_EVIDENCE_ROOT.parent.iterdir())
    facts = evaluate_dogfood_run.extract_runtime_derived_facts(REAL_RUN006_EVIDENCE_ROOT)
    after_listing = sorted(p.name for p in REAL_RUN006_EVIDENCE_ROOT.parent.iterdir())

    assert facts.implementation_attempt_count == 2
    assert facts.repair_count == 1
    assert facts.review_count == 2
    assert facts.model_invocation_count == 4
    assert facts.runtime_terminal_outcome == "COMPLETED"
    assert facts.runtime_stop_reason == "REVIEW_APPROVED"
    assert facts.evidence_complete is True
    # No sibling "evaluations/" directory (or anything else) was created.
    assert after_listing == before_listing


# ---------------------------------------------------------------------------
# Output: immutable per-run record, append-only aggregation, no overwrite.
# ---------------------------------------------------------------------------


def test_write_evaluation_record_creates_record_and_jsonl(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-write-1")
    record = evaluate_dogfood_run.build_evaluation_record(evidence_root=evidence_root, owner=make_owner(), runtime_commit=None)
    record_path, jsonl_path = evaluate_dogfood_run.write_evaluation_record(evidence_root, record)

    assert record_path == evidence_root.parent / "evaluations" / "loop-write-1.evaluation.json"
    assert json.loads(record_path.read_text(encoding="utf-8"))["run_id"] == "loop-write-1"
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "loop-write-1"
    # (Section 2) Never written inside the canonical evidence root itself.
    assert not (evidence_root / "evaluations").exists()


def test_write_evaluation_record_appends_to_shared_jsonl(tmp_path: Path) -> None:
    root1 = make_loop_evidence(tmp_path, run_id="loop-append-1")
    root2 = make_loop_evidence(tmp_path, run_id="loop-append-2")
    r1 = evaluate_dogfood_run.build_evaluation_record(evidence_root=root1, owner=make_owner(), runtime_commit=None)
    r2 = evaluate_dogfood_run.build_evaluation_record(evidence_root=root2, owner=make_owner(), runtime_commit=None)
    evaluate_dogfood_run.write_evaluation_record(root1, r1)
    _, jsonl_path = evaluate_dogfood_run.write_evaluation_record(root2, r2)

    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    run_ids = {json.loads(line)["run_id"] for line in lines}
    assert run_ids == {"loop-append-1", "loop-append-2"}


def test_no_silent_overwrite_of_existing_evaluation(tmp_path: Path) -> None:
    """(Section 11/16.H) A second evaluation of the same run must fail
    clearly, never silently overwrite."""

    evidence_root = make_loop_evidence(tmp_path, run_id="loop-no-overwrite")
    record = evaluate_dogfood_run.build_evaluation_record(evidence_root=evidence_root, owner=make_owner(), runtime_commit=None)
    record_path, _ = evaluate_dogfood_run.write_evaluation_record(evidence_root, record)
    original_bytes = record_path.read_bytes()

    second_record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root, owner=make_owner(owner_disposition="NOT_USABLE"), runtime_commit=None
    )
    with pytest.raises(evaluate_dogfood_run.EvaluationError) as excinfo:
        evaluate_dogfood_run.write_evaluation_record(evidence_root, second_record)
    assert excinfo.value.category == "EVALUATION_ALREADY_EXISTS"
    assert record_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# Negative cases (Section 16).
# ---------------------------------------------------------------------------


def test_missing_evidence_root_rejected_clearly(tmp_path: Path) -> None:
    with pytest.raises(evaluate_dogfood_run.EvaluationError) as excinfo:
        evaluate_dogfood_run.extract_runtime_derived_facts(tmp_path / "does-not-exist")
    assert excinfo.value.category == "EVIDENCE_ROOT_MISSING"


def test_malformed_loop_result_rejected(tmp_path: Path) -> None:
    evidence_root = tmp_path / "loop-malformed"
    evidence_root.mkdir()
    (evidence_root / "loop-result.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(evaluate_dogfood_run.EvaluationError) as excinfo:
        evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert excinfo.value.category == "MALFORMED_EVIDENCE_JSON"


def test_loop_result_missing_required_key_rejected(tmp_path: Path) -> None:
    evidence_root = tmp_path / "loop-missing-key"
    evidence_root.mkdir()
    _write_json(evidence_root / "loop-result.json", {"loop_run_id": "x"})  # missing run_state/terminal_outcome
    with pytest.raises(evaluate_dogfood_run.EvaluationError) as excinfo:
        evaluate_dogfood_run.extract_runtime_derived_facts(evidence_root)
    assert excinfo.value.category == "MALFORMED_LOOP_RESULT"


def test_runtime_completed_owner_not_usable_accepted_is_false(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-completed-not-usable", terminal_outcome="COMPLETED", accepted=True)
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root, owner=make_owner(owner_disposition="NOT_USABLE"), runtime_commit=None
    )
    assert record["runtime_terminal_outcome"] == "COMPLETED"
    assert record["accepted"] is False


def test_runtime_failed_owner_accepted_with_manual_edit_preserves_both_facts(tmp_path: Path) -> None:
    """(Section 16.D) Do not silently force Runtime and Owner facts to
    agree -- both are preserved independently."""

    evidence_root = make_loop_evidence(
        tmp_path, run_id="loop-failed-owner-accepted", terminal_outcome="FAILED", stop_reason="REPAIR_ATTEMPT_2_FAILED", accepted=False
    )
    record = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=evidence_root,
        owner=make_owner(owner_disposition="ACCEPTED_WITH_MANUAL_EDIT", manual_edit_lines=12),
        runtime_commit=None,
    )
    assert record["runtime_terminal_outcome"] == "FAILED"
    assert record["runtime_accepted"] is False
    assert record["accepted"] is True
    assert record["manual_edit_lines"] == 12


# (E) token/cost unavailable already covered above
# (F) first-pass evidence completeness already covered above
# (G) repair-path evidence completeness already covered above
# (H) no silent overwrite already covered above


# ---------------------------------------------------------------------------
# Summary helper (Section 14) -- trivial arithmetic only.
# ---------------------------------------------------------------------------


def test_summarize_evaluations_empty() -> None:
    assert evaluate_dogfood_run.summarize_evaluations([]) == {"total_evaluations": 0}


def test_summarize_evaluations_basic_arithmetic(tmp_path: Path) -> None:
    accepted_root = make_loop_evidence(tmp_path, run_id="loop-sum-1", trajectory="first_pass")
    repaired_root = make_loop_evidence(tmp_path, run_id="loop-sum-2", trajectory="repair")
    r1 = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=accepted_root,
        owner=make_owner(owner_disposition="ACCEPTED", active_human_seconds=60, owner_interruptions=1),
        runtime_commit=None,
    )
    r2 = evaluate_dogfood_run.build_evaluation_record(
        evidence_root=repaired_root,
        owner=make_owner(owner_disposition="NOT_USABLE", owner_interruptions=2),
        runtime_commit=None,
    )
    summary = evaluate_dogfood_run.summarize_evaluations([r1, r2])
    assert summary["total_evaluations"] == 2
    assert summary["owner_acceptance_rate"] == 0.5
    assert summary["first_pass_success_rate"] == 0.5
    assert summary["repair_rate"] == 0.5
    assert summary["total_owner_interruptions"] == 3
    assert summary["median_active_human_minutes_to_confident_accept"] == 1.0


# ---------------------------------------------------------------------------
# CLI end-to-end.
# ---------------------------------------------------------------------------


def test_cli_end_to_end_writes_record(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-cli-1")
    exit_code = evaluate_dogfood_run.main(
        [
            "--evidence-root",
            str(evidence_root),
            "--owner-disposition",
            "ACCEPTED",
            "--active-human-seconds",
            "90",
            "--owner-interruptions",
            "0",
            "--clarification-count",
            "0",
            "--manual-edit-lines",
            "0",
        ]
    )
    assert exit_code == 0
    record_path = evidence_root.parent / "evaluations" / "loop-cli-1.evaluation.json"
    assert record_path.is_file()


def test_cli_missing_required_argument_rejected(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-cli-missing-arg")
    exit_code = evaluate_dogfood_run.main(["--evidence-root", str(evidence_root)])
    assert exit_code == 2


def test_cli_negative_argument_rejected(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-cli-negative")
    exit_code = evaluate_dogfood_run.main(
        [
            "--evidence-root",
            str(evidence_root),
            "--owner-disposition",
            "ACCEPTED",
            "--active-human-seconds",
            "-1",
            "--owner-interruptions",
            "0",
            "--clarification-count",
            "0",
            "--manual-edit-lines",
            "0",
        ]
    )
    assert exit_code == 2


def test_cli_summarize_mode(tmp_path: Path) -> None:
    evidence_root = make_loop_evidence(tmp_path, run_id="loop-cli-summarize")
    evaluate_dogfood_run.main(
        [
            "--evidence-root",
            str(evidence_root),
            "--owner-disposition",
            "ACCEPTED",
            "--active-human-seconds",
            "30",
            "--owner-interruptions",
            "0",
            "--clarification-count",
            "0",
            "--manual-edit-lines",
            "0",
        ]
    )
    evaluations_dir = evidence_root.parent / "evaluations"
    exit_code = evaluate_dogfood_run.main(["--summarize", str(evaluations_dir)])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Authority coupling (Section 17): no canonical Runtime transition code may
# import or call this module.
# ---------------------------------------------------------------------------


def test_no_runtime_module_imports_the_evaluator() -> None:
    for name in ("run_loop.py", "run_repair.py", "run_review.py", "run_task.py"):
        source = (DOGFOOD_DIR / name).read_text(encoding="utf-8")
        assert "evaluate_dogfood_run" not in source, f"{name} must never import/reference the evaluator"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
