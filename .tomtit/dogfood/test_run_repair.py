"""Offline, deterministic tests for the local Codex-repair runner.

None of these tests invoke the real Claude service. Each uses a small fake
`claude`-shaped executable and throwaway Git repositories plus fabricated
(but internally consistent) implementation/review evidence directories.

Run with:

    python3 -m pytest .tomtit/dogfood/test_run_repair.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

DOGFOOD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DOGFOOD_DIR))
REPO_ROOT = DOGFOOD_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    snapshot_workspace_hashes,
    workspace_hash,
)

_spec = importlib.util.spec_from_file_location("run_repair", DOGFOOD_DIR / "run_repair.py")
assert _spec is not None and _spec.loader is not None
run_repair = importlib.util.module_from_spec(_spec)
sys.modules["run_repair"] = run_repair
_spec.loader.exec_module(run_repair)

run_review = sys.modules["run_review"]  # imported as a side effect of loading run_repair


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


TASK_PROMPT_TEXT = "Do the thing.\nFollow the rules.\n"


def make_reviewed_workspace(tmp_path: Path, *, changed_content: str = "changed\n") -> tuple[Path, str, str]:
    """A Git repo whose current state matches what a prior review would have seen.

    Returns (workspace, baseline_commit, workspace_after_hash) where the hash
    is computed with the *real* adapter functions, so run_repair's independent
    recomputation genuinely matches.
    """

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "app.txt").write_text("original\n", encoding="utf-8")
    (ws / "README.md").write_text("readme\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    baseline_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    (ws / "app.txt").write_text(changed_content, encoding="utf-8")
    after_hash = workspace_hash(snapshot_workspace_hashes(ws))
    return ws, baseline_commit, after_hash


def make_linked_reviewed_workspace(tmp_path: Path) -> tuple[Path, str, str]:
    primary, baseline_commit, _ = make_reviewed_workspace(tmp_path)
    linked = tmp_path / "linked-worktree"
    _git(["worktree", "add", "-q", "-b", "linked-test", str(linked), "HEAD"], primary)
    (linked / "app.txt").write_text("changed\n", encoding="utf-8")
    return (
        linked,
        baseline_commit,
        workspace_hash(snapshot_workspace_hashes(linked)),
    )


def make_implementation_evidence(
    tmp_path: Path,
    *,
    workspace: Path,
    after_hash: str,
    run_id: str = "impl-1",
    status: str = "SUCCEEDED",
    success: bool = True,
    unexpected_changed_paths: list[str] | None = None,
    changed_paths: list[str] | None = None,
    attempt: int | None = 1,
    name: str = "impl-run",
    authorized_commands: list[list[str]] | None = None,
    allowed_side_effect_paths: list[str] | None = None,
    legacy_schema_without_side_effect_paths: bool = False,
    legacy_schema_without_fingerprint: bool = False,
) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    changed = changed_paths if changed_paths is not None else ["app.txt"]
    argv_commands = tuple(tuple(entry) for entry in (authorized_commands or []))
    receipt: dict = {
        "task_id": "task-1",
        "run_id": run_id,
        "workspace": str(workspace),
        "status": status,
        "success": success,
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 1234,
        "changed_paths": changed,
        "unexpected_changed_paths": unexpected_changed_paths or [],
        "allowed_changed_paths": changed,
        "error_category": None,
        "error_message": None,
        "adapter_manifest_path": str(run_dir / "result.json"),
        "adapter_patch_path": str(run_dir / "diff.patch"),
    }
    if not legacy_schema_without_fingerprint:
        # Fingerprints only -- matches what run_task.py now actually persists
        # (Codex corrective review, HIGH finding 2). `legacy_schema_without_fingerprint`
        # exists solely to fabricate evidence predating this corrective schema,
        # for the explicit old-evidence-handling tests below.
        receipt["authorized_command_fingerprints"] = [
            compute_authorized_command_fingerprint(argv) for argv in argv_commands
        ]
        receipt["authorized_command_set_fingerprint"] = (
            compute_authorized_command_set_fingerprint(argv_commands) if argv_commands else None
        )
        receipt["authorized_command_count"] = len(argv_commands)
    if not legacy_schema_without_side_effect_paths:
        # The Owner-approved generated/side-effect roots, recorded verbatim
        # exactly as run_task.py now persists them, so Attempt 2 can verify
        # it inherited the identical ordered set.
        receipt["allowed_side_effect_paths"] = list(allowed_side_effect_paths or [])
        receipt["allowed_side_effect_path_count"] = len(allowed_side_effect_paths or [])
    if attempt is not None:
        receipt["attempt"] = attempt
    (run_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    result = {
        "status": status,
        "success": success,
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 1234,
        "changed_paths": changed,
        "unexpected_changed_paths": receipt["unexpected_changed_paths"],
        "error_category": None,
        "error_message": None,
        "stdout_excerpt": "ok",
        "workspace_before_hash": "unused-before-hash",
        "workspace_after_hash": after_hash,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "diff.patch").write_text(
        "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-original\n+changed\n", encoding="utf-8"
    )
    return run_dir


def make_review_prompt_text(task_prompt_text: str, baseline_commit: str) -> str:
    return (
        "# Original implementation task\n\n"
        + task_prompt_text.strip()
        + "\n\n# Baseline\n\nbaseline_ref: HEAD\nbaseline_commit: "
        + baseline_commit
        + "\n\n# Allowed paths\n\n- app.txt\n"
    )


def make_review_evidence(
    tmp_path: Path,
    *,
    workspace: Path,
    baseline_commit: str,
    implementation_run_id: str = "impl-1",
    review_run_id: str = "review-1",
    verdict: str = "REQUEST_CHANGES",
    findings: list[dict] | None = None,
    task_prompt_text: str = TASK_PROMPT_TEXT,
    workspace_override: str | None = None,
    baseline_commit_override: str | None = None,
    implementation_run_id_override: str | None = None,
    implementation_attempt: Any = 1,
    omit_implementation_attempt: bool = False,
    partitioned_identity: bool = False,
    allowed_paths: list[str] | None = None,
    allowed_side_effect_paths: list[str] | None = None,
    name: str = "review-run",
) -> Path:
    review_dir = tmp_path / name
    review_dir.mkdir()
    if findings is None:
        findings = [
            {
                "id": "F1",
                "severity": "MEDIUM",
                "title": "Missing validation",
                "evidence": "app.txt line 1",
                "required_change": "add validation",
            }
        ]
    result = {
        "verdict": verdict,
        "summary": "needs work" if verdict == "REQUEST_CHANGES" else "ok",
        "goal_satisfied": verdict != "REQUEST_CHANGES",
        "scope_compliant": True,
        "checks_satisfied": verdict != "REQUEST_CHANGES",
        "findings": findings,
        "residual_risks": [],
    }
    (review_dir / "review-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    receipt = {
        "review_run_id": review_run_id,
        "implementation_run_id": implementation_run_id_override or implementation_run_id,
        "workspace": workspace_override or str(workspace),
        "baseline_ref": "HEAD",
        "baseline_commit": baseline_commit_override or baseline_commit,
        "verdict": verdict,
        "workspace_unchanged": True,
    }
    if not omit_implementation_attempt:
        receipt["implementation_attempt"] = implementation_attempt
    if partitioned_identity:
        source_paths = tuple(allowed_paths or ["app.txt"])
        side_effect_paths = tuple(allowed_side_effect_paths or [])
        hashes = snapshot_workspace_hashes(workspace)
        product, side_effects, unexpected = run_review.partition_reviewed_workspace_state(
            hashes,
            source_paths,
            side_effect_paths,
        )
        # One real measurement, reused for the hash/entry-hash/projection
        # fields below -- exactly what run_review.py's own receipt-building
        # does (`review_exit_git_metadata` computed once, then hashed and
        # embedded), so the fabricated fixture receipt matches the real
        # binding invariant `_load_trusted_review_projection()` verifies.
        git_metadata_projection = run_review.protected_git_metadata_snapshot(workspace)
        receipt.update(
            {
                "reviewed_head": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "reviewed_index_clean": True,
                "reviewed_index_identity": run_review.git_index_identity(workspace),
                "reviewed_protected_git_metadata_hash": (
                    workspace_hash(git_metadata_projection)
                ),
                "reviewed_protected_git_metadata_projection": git_metadata_projection,
                "reviewed_workspace_git_entry_hash": (
                    run_review.workspace_git_entry_hash_from_snapshot(
                        git_metadata_projection
                    )
                ),
                "reviewed_git_special_state": None,
                "reviewed_baseline_commit": baseline_commit,
                "reviewed_allowed_source_paths": list(source_paths),
                "reviewed_workspace_integrity_hash": (
                    run_review.reviewed_workspace_integrity_hash(
                        hashes,
                        source_paths,
                        side_effect_paths,
                    )
                ),
                "reviewed_product_state_hash": workspace_hash(product),
                "reviewed_authorized_side_effect_state_hash": workspace_hash(side_effects),
                "reviewed_unexpected_state_hash": workspace_hash(unexpected),
                "reviewed_workspace_full_hash": workspace_hash(hashes),
            }
        )
    (review_dir / "review-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    (review_dir / "review-prompt.txt").write_text(
        make_review_prompt_text(task_prompt_text, baseline_commit_override or baseline_commit), encoding="utf-8"
    )
    (review_dir / "checks.json").write_text(json.dumps([]), encoding="utf-8")
    return review_dir


def make_task_prompt(tmp_path: Path, *, text: str = TASK_PROMPT_TEXT, name: str = "task.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def write_fake_claude(
    directory: Path,
    *,
    name: str = "fake-claude",
    write_allowed: str | None = "app.txt",
    write_content: str = "repaired\n",
    write_unexpected: str | None = None,
    exit_code: int = 0,
    sleep_seconds: float = 0,
) -> Path:
    script = directory / name
    body = f'''#!/usr/bin/env python3
import pathlib, time
if {sleep_seconds!r}:
    time.sleep({sleep_seconds!r})
if {write_allowed!r}:
    pathlib.Path({write_allowed!r}).write_text({write_content!r}, encoding="utf-8")
if {write_unexpected!r}:
    pathlib.Path({write_unexpected!r}).write_text("unexpected write\\n", encoding="utf-8")
raise SystemExit({exit_code!r})
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def run_repair_cli(
    tmp_path: Path,
    workspace: Path,
    claude_executable: Path,
    *,
    task_prompt_file: Path | None = None,
    implementation_run_directory: Path,
    review_run_directory: Path,
    allowed_paths: list[str] | None = None,
    check_commands: list[str] | None = None,
    authorized_commands: list[str] | None = None,
    allowed_side_effect_paths: list[str] | None = None,
    timeout_seconds: int = 30,
    repair_run_id: str | None = None,
    evidence_directory: Path | None = None,
    implementation_run_id: str = "impl-1",
    implementation_attempt: int = 1,
    expected_review_run_id: str = "review-1",
) -> int:
    argv = [
        "--workspace",
        str(workspace),
        "--original-task-prompt-file",
        str(task_prompt_file or make_task_prompt(tmp_path)),
        "--previous-implementation-run-directory",
        str(implementation_run_directory),
        "--review-run-directory",
        str(review_run_directory),
        "--expected-review-run-id",
        expected_review_run_id,
        "--timeout-seconds",
        str(timeout_seconds),
        "--claude-executable",
        str(claude_executable),
        "--evidence-directory",
        str(evidence_directory or (tmp_path / "runs")),
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
    ]
    for path in allowed_paths if allowed_paths is not None else ["app.txt"]:
        argv.extend(["--allowed-path", path])
    for command in check_commands or []:
        argv.extend(["--check-command", command])
    for command in authorized_commands or []:
        argv.extend(["--authorized-command", command])
    for side_effect_path in allowed_side_effect_paths or []:
        argv.extend(["--allowed-side-effect-path", side_effect_path])
    if repair_run_id:
        argv.extend(["--repair-run-id", repair_run_id])
    return run_repair.main(argv)


def standard_setup(tmp_path: Path, **review_overrides) -> dict:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit, **review_overrides)
    return {"ws": ws, "baseline_commit": baseline_commit, "impl_dir": impl_dir, "review_dir": review_dir}


# ---------------------------------------------------------------------------
# 1-2. Valid REQUEST_CHANGES scenarios.
# ---------------------------------------------------------------------------


def test_valid_request_changes_with_one_medium_finding(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 0


def test_valid_request_changes_with_high_medium_low_findings(tmp_path: Path) -> None:
    findings = [
        {"id": "F1", "severity": "HIGH", "title": "a", "evidence": "e1", "required_change": "fix a"},
        {"id": "F2", "severity": "MEDIUM", "title": "b", "evidence": "e2", "required_change": "fix b"},
        {"id": "F3", "severity": "LOW", "title": "c", "evidence": "e3", "required_change": "fix c"},
    ]
    ctx = standard_setup(tmp_path, findings=findings)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 0


# ---------------------------------------------------------------------------
# 3-6. Review-verdict/schema rejection.
# ---------------------------------------------------------------------------


def test_approve_verdict_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, verdict="APPROVE", findings=[])
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 2


def test_blocked_verdict_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, verdict="BLOCKED", findings=[])
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 2


def test_request_changes_with_no_findings_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, findings=[])
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 3


def test_malformed_review_json_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    (ctx["review_dir"] / "review-result.json").write_text("{not valid json", encoding="utf-8")
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 7-8. Finding-content validation.
# ---------------------------------------------------------------------------


def test_duplicate_finding_ids_rejected(tmp_path: Path) -> None:
    findings = [
        {"id": "F1", "severity": "MEDIUM", "title": "a", "evidence": "e1", "required_change": "fix a"},
        {"id": "F1", "severity": "LOW", "title": "b", "evidence": "e2", "required_change": "fix b"},
    ]
    ctx = standard_setup(tmp_path, findings=findings)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 3


def test_empty_required_change_rejected(tmp_path: Path) -> None:
    findings = [{"id": "F1", "severity": "MEDIUM", "title": "a", "evidence": "e1", "required_change": "   "}]
    ctx = standard_setup(tmp_path, findings=findings)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 9-10. Task prompt preflight.
# ---------------------------------------------------------------------------


def test_missing_original_task_prompt(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        task_prompt_file=tmp_path / "does-not-exist.md",
    )
    assert exit_code == 2


def test_empty_original_task_prompt(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    empty = make_task_prompt(tmp_path, text="   \n", name="empty-task.md")
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        task_prompt_file=empty,
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 11-13. Previous implementation evidence preflight.
# ---------------------------------------------------------------------------


def test_missing_previous_implementation_evidence(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=tmp_path / "no-such-dir",
        review_run_directory=ctx["review_dir"],
    )
    assert exit_code == 2


def test_previous_implementation_reports_failure(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, status="FAILED", success=False
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ws, claude, implementation_run_directory=impl_dir, review_run_directory=review_dir
    )
    assert exit_code == 2


def test_previous_implementation_reports_unexpected_paths(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, unexpected_changed_paths=["oops.txt"]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ws, claude, implementation_run_directory=impl_dir, review_run_directory=review_dir
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 14. Missing review evidence.
# ---------------------------------------------------------------------------


def test_missing_review_evidence(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=tmp_path / "no-such-review",
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 15-17. Review-binding cross-checks.
# ---------------------------------------------------------------------------


def test_review_bound_to_another_implementation_run(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash, run_id="impl-1")
    review_dir = make_review_evidence(
        tmp_path,
        workspace=ws,
        baseline_commit=baseline_commit,
        implementation_run_id_override="some-other-impl-run",
    )
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ws, claude, implementation_run_directory=impl_dir, review_run_directory=review_dir
    )
    assert exit_code == 3


def test_review_bound_to_another_workspace(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    other_ws = tmp_path / "not-the-same-workspace"
    other_ws.mkdir()
    review_dir = make_review_evidence(
        tmp_path,
        workspace=ctx["ws"],
        baseline_commit=ctx["baseline_commit"],
        workspace_override=str(other_ws),
        name="review-other-ws",
    )
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=review_dir
    )
    assert exit_code == 3


def test_review_bound_to_another_baseline(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    review_dir = make_review_evidence(
        tmp_path,
        workspace=ctx["ws"],
        baseline_commit=ctx["baseline_commit"],
        baseline_commit_override="0" * 40,
        name="review-other-baseline",
    )
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=review_dir
    )
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 18. Current workspace changed after Codex review.
# ---------------------------------------------------------------------------


def test_workspace_changed_after_review_fails_closed(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    (ctx["ws"] / "app.txt").write_text("manually edited after review\n", encoding="utf-8")
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 19. Dirty index.
# ---------------------------------------------------------------------------


def test_dirty_index_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    _git(["add", "app.txt"], ctx["ws"])
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 20-21. Allowed-path validation.
# ---------------------------------------------------------------------------


def test_changed_path_outside_allowed_scope(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["README.md"],
    )
    assert exit_code == 3


def test_invalid_allowed_path_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["../escape.txt"],
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 22. Exact reviewed state accepted (positive control for the binding proof).
# ---------------------------------------------------------------------------


def test_exact_reviewed_state_is_accepted(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 0


# ---------------------------------------------------------------------------
# 23-25. Repair prompt content.
# ---------------------------------------------------------------------------


def test_repair_prompt_preserves_every_finding_id(tmp_path: Path) -> None:
    findings = [
        {"id": "F1", "severity": "HIGH", "title": "a", "evidence": "e1", "required_change": "fix a"},
        {"id": "F2", "severity": "LOW", "title": "b", "evidence": "e2", "required_change": "fix b"},
    ]
    ctx = standard_setup(tmp_path, findings=findings)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    prompt = (run_dir / "repair-prompt.txt").read_text(encoding="utf-8")
    assert "F1" in prompt
    assert "F2" in prompt


def test_repair_prompt_preserves_original_goal_and_constraints(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    prompt = (run_dir / "repair-prompt.txt").read_text(encoding="utf-8")
    assert TASK_PROMPT_TEXT.strip() in prompt
    assert "non-goals" in prompt.lower()


def test_repair_prompt_does_not_introduce_unrelated_work(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    prompt = (run_dir / "repair-prompt.txt").read_text(encoding="utf-8")
    assert "do not broaden scope" in prompt.lower()
    assert "do not redesign unrelated areas" in prompt.lower()


# ---------------------------------------------------------------------------
# 26-29. Claude process outcomes.
# ---------------------------------------------------------------------------


def test_fake_claude_succeeds_and_modifies_only_allowed_file(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path, write_allowed="app.txt")
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 0
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "repaired\n"


def test_fake_claude_nonzero_exit(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path, exit_code=1)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 1


def test_fake_claude_changes_an_unexpected_file(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path, write_unexpected="README.md")
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 1


def test_fake_claude_times_out(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path, sleep_seconds=5)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        timeout_seconds=1,
    )
    assert exit_code == 1


# ---------------------------------------------------------------------------
# 30. Invalid explicit executable.
# ---------------------------------------------------------------------------


def test_invalid_explicit_claude_executable(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    missing = tmp_path / "no-such-claude"
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], missing, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 1  # adapter returns UNAVAILABLE/success=False; Claude "ran" (was attempted), repair fails


# ---------------------------------------------------------------------------
# 31-33. Deterministic checks.
# ---------------------------------------------------------------------------


def test_check_command_passes(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        check_commands=["python3 -c \"print(1)\""],
    )
    assert exit_code == 0


def test_check_command_fails(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        check_commands=["python3 -c \"import sys; sys.exit(1)\""],
    )
    assert exit_code == 1


def test_shell_operator_rejected_in_check_command(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        check_commands=["echo hi && echo bye"],
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 34-35. Evidence directory.
# ---------------------------------------------------------------------------


def test_new_repair_evidence_directory_created(tmp_path: Path) -> None:
    # --evidence-directory, like run_task.py's, names the FINAL run directory
    # directly (it is not a parent that --repair-run-id nests under).
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    run_dir = tmp_path / "runs" / "repair-xyz"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=run_dir,
        repair_run_id="repair-xyz",
    )
    for name in ("result.json", "diff.patch", "receipt.json", "repair-prompt.txt", "checks.json", "repair-context.json"):
        assert (run_dir / name).is_file(), f"missing {name}"


def test_existing_repair_evidence_collision_rejected(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    collision_dir = tmp_path / "runs" / "collide"
    collision_dir.mkdir(parents=True)
    (collision_dir / "stale.txt").write_text("leftover", encoding="utf-8")
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=collision_dir,
        repair_run_id="collide",
    )
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 36-37. Previous evidence immutability.
# ---------------------------------------------------------------------------


def test_previous_implementation_evidence_remains_unchanged(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    before = {
        p.name: p.read_bytes() for p in ctx["impl_dir"].iterdir()
    }
    claude = write_fake_claude(tmp_path)
    run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    after = {p.name: p.read_bytes() for p in ctx["impl_dir"].iterdir()}
    assert before == after


def test_review_evidence_remains_unchanged(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    before = {p.name: p.read_bytes() for p in ctx["review_dir"].iterdir()}
    claude = write_fake_claude(tmp_path)
    run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    after = {p.name: p.read_bytes() for p in ctx["review_dir"].iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# 38. Exactly one Claude invocation.
# ---------------------------------------------------------------------------


def test_exactly_one_claude_invocation(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    counter_path = tmp_path / "invocation_count.txt"
    script = tmp_path / "fake-claude-counter"
    script.write_text(
        f'''#!/usr/bin/env python3
import pathlib
p = pathlib.Path({str(counter_path)!r})
count = int(p.read_text()) if p.exists() else 0
p.write_text(str(count + 1))
pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
raise SystemExit(0)
''',
        encoding="utf-8",
    )
    script.chmod(0o755)
    run_repair_cli(
        tmp_path, ctx["ws"], script, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert counter_path.read_text(encoding="utf-8") == "1"


# ---------------------------------------------------------------------------
# 39. No automatic Codex invocation (structural: no codex-invoking code path).
# ---------------------------------------------------------------------------


def test_no_automatic_codex_invocation_code_path_exists() -> None:
    source = (DOGFOOD_DIR / "run_repair.py").read_text(encoding="utf-8")
    assert "codex" not in source.lower() or "run_review" in source  # only imported for reuse, never invoked
    assert "build_codex_argv" not in source
    assert "execute_bounded" not in source  # run_repair never runs a Codex-shaped subprocess itself


# ---------------------------------------------------------------------------
# 40. Runner never reports findings as independently resolved.
# ---------------------------------------------------------------------------


def test_runner_never_claims_findings_resolved(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "REPAIR_ATTEMPT_COMPLETED"
    assert "FINDINGS_RESOLVED" not in json.dumps(receipt)
    assert "resolved" not in receipt["status"].lower()


# ---------------------------------------------------------------------------
# 41. Credential-safe internal-error output.
# ---------------------------------------------------------------------------


def test_credential_safe_internal_error_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOMTIT_TEST_REPAIR_SECRET", "sk-super-secret-repair-value")
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path, exit_code=1)
    run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    captured = capsys.readouterr()
    assert "sk-super-secret-repair-value" not in captured.out
    assert "sk-super-secret-repair-value" not in captured.err


# ---------------------------------------------------------------------------
# 42. Final changed paths remain bounded.
# ---------------------------------------------------------------------------


def test_final_changed_paths_remain_bounded(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert set(receipt["changed_paths"]).issubset(set(receipt["allowed_changed_paths"]))
    assert receipt["unexpected_changed_paths"] == []


# ---------------------------------------------------------------------------
# 43-44. Attempt-number cap.
# ---------------------------------------------------------------------------


def test_attempt_number_is_fixed_to_two(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    run_dir = evidence_root
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["attempt"] == 2
    context = json.loads((run_dir / "repair-context.json").read_text(encoding="utf-8"))
    assert context["attempt"] == 2


def test_final_receipt_has_canonical_run_id_matching_repair_run_id(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    evidence_root = tmp_path / "runs"
    run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        evidence_directory=evidence_root,
    )
    receipt = json.loads((evidence_root / "receipt.json").read_text(encoding="utf-8"))
    assert "run_id" in receipt
    assert "repair_run_id" in receipt
    assert receipt["run_id"] == receipt["repair_run_id"]
    assert receipt["attempt"] == run_repair.REPAIR_ATTEMPT_NUMBER


def test_second_repair_round_rejected(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    # Simulate: the referenced "implementation" run is itself already a repair (attempt=2).
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash, attempt=2)
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ws, claude, implementation_run_directory=impl_dir, review_run_directory=review_dir
    )
    assert exit_code == 2


# ===========================================================================
# EXACT_COMMAND_AUTHORIZATION corrective task: repair must inherit the exact
# same Bash authorization as the original implementation, never broaden it,
# never drop it, never substitute it. See
# EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md section 9.
# ===========================================================================


def test_repair_preserves_original_authorized_command_set(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, authorized_commands=[["npm", "install", "foo", "--save-exact"]]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        authorized_commands=["npm install foo --save-exact"],
    )

    # What this test actually verifies is that an *exactly matching* set is
    # accepted through preflight and reaches the adapter at all, rather than
    # being rejected as a contract mismatch. With the layered exact-argv
    # capability enabled, that now completes successfully instead of failing
    # closed as UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION.
    assert exit_code == 0
    expected_argv = ("npm", "install", "foo", "--save-exact")
    expected_fingerprint = compute_authorized_command_fingerprint(expected_argv)
    expected_set_fingerprint = compute_authorized_command_set_fingerprint((expected_argv,))
    run_dir = tmp_path / "runs"
    receipt = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["authorized_command_fingerprints"] == [expected_fingerprint]
    assert receipt["authorized_command_set_fingerprint"] == expected_set_fingerprint
    # No longer dormant: the capability must not report itself unsupported,
    # and the inherited set must still persist as fingerprints only.
    assert receipt["error_category"] != "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"
    receipt_json = json.dumps(receipt)
    assert " ".join(expected_argv) not in receipt_json
    assert "--save-exact" not in receipt_json
    assert "authorized_commands" not in receipt
    context = json.loads((run_dir / "repair-context.json").read_text(encoding="utf-8"))
    assert context["authorized_command_fingerprints"] == [expected_fingerprint]
    assert context["authorized_command_set_fingerprint"] == expected_set_fingerprint
    assert "authorized_commands" not in context

    # No file under this repair's evidence directory ever contains the raw
    # argv text or a raw Bash(...) rule (Codex corrective review, HIGH
    # finding 2).
    for path in run_dir.rglob("*"):
        if path.is_file():
            contents = path.read_text(encoding="utf-8", errors="replace")
            assert "npm install foo --save-exact" not in contents, path
            assert "Bash(npm install foo --save-exact)" not in contents, path


def test_repair_cannot_add_a_new_authorized_command(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash, authorized_commands=[])
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        authorized_commands=["npm install a-brand-new-package"],
    )

    assert exit_code == 3  # AUTHORIZED_COMMAND_CONTRACT_MISMATCH -> IntegrityFailure
    assert not (tmp_path / "runs").exists()


def test_repair_cannot_replace_an_authorized_command(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, authorized_commands=[["npm", "install", "foo"]]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        authorized_commands=["npm install bar"],  # a substitution, not a match
    )

    assert exit_code == 3
    assert not (tmp_path / "runs").exists()


def test_repair_findings_cannot_introduce_bash_authorization(tmp_path: Path) -> None:
    """Even with a HIGH-severity finding that might plausibly justify a new
    command, repair must still reject any authorized-command set not
    identical to the original -- findings are never grounds to broaden
    Bash authorization.
    """

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash, authorized_commands=[])
    findings = [
        {
            "id": "F1",
            "severity": "HIGH",
            "title": "Missing dependency",
            "evidence": "import error for `zod`",
            "required_change": "run `npm install zod` to add the missing dependency",
        }
    ]
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit, findings=findings)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        authorized_commands=["npm install zod"],
    )

    assert exit_code == 3
    assert not (tmp_path / "runs").exists()


def test_repair_no_authorized_commands_on_both_sides_is_accepted(tmp_path: Path) -> None:
    """The common case: neither the original implementation nor the repair
    ever requested Bash authorization -- must remain fully unaffected by
    this feature (backward-compatible default).
    """

    ctx = standard_setup(tmp_path)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path, ctx["ws"], claude, implementation_run_directory=ctx["impl_dir"], review_run_directory=ctx["review_dir"]
    )
    assert exit_code == 0
    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["authorized_command_fingerprints"] == []
    assert receipt["authorized_command_set_fingerprint"] is None
    assert receipt["authorized_command_count"] == 0
    assert "authorized_commands" not in receipt


# ---------------------------------------------------------------------------
# Old-evidence handling: implementation evidence predating this corrective
# schema (no `authorized_command_set_fingerprint` key at all) must be
# handled explicitly and safely -- never silently trusted, never silently
# broadened. See run_repair.run_preflight()'s "AUTHORIZED_COMMAND_EVIDENCE_INVALID"
# branch.
# ---------------------------------------------------------------------------


def test_repair_rejects_authorized_command_against_legacy_evidence_without_fingerprint(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, legacy_schema_without_fingerprint=True
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        authorized_commands=["npm install foo"],
    )

    assert exit_code == 3  # AUTHORIZED_COMMAND_EVIDENCE_INVALID -> IntegrityFailure, fails closed
    assert not (tmp_path / "runs").exists()


def test_repair_with_no_authorized_command_against_legacy_evidence_is_accepted(tmp_path: Path) -> None:
    """Legacy evidence with nothing to inherit is fine as long as the repair
    also requests no authorization -- there is nothing to silently trust or
    broaden in that case."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, legacy_schema_without_fingerprint=True
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path, ws, claude, implementation_run_directory=impl_dir, review_run_directory=review_dir
    )

    assert exit_code == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# Authorized generated/side-effect path inheritance
# (WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md).
#
# Governed exactly like authorized_commands: Attempt 2 must present the
# identical ordered set Attempt 1 was approved for. A reviewer finding is
# never grounds to widen filesystem scope.
# ---------------------------------------------------------------------------


def test_repair_inherits_identical_side_effect_authorization(tmp_path: Path) -> None:
    """11. Attempt 2 receives exactly Attempt 1's side-effect authorization."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, allowed_side_effect_paths=["node_modules", ".next"]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=["node_modules", ".next"],
    )

    assert exit_code == 0
    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["allowed_side_effect_paths"] == ["node_modules", ".next"]
    assert receipt["allowed_side_effect_path_count"] == 2


@pytest.mark.parametrize(
    "repair_paths",
    [
        ["node_modules", ".next", "dist"],   # addition
        ["node_modules"],                     # removal
        ["node_modules", "build"],            # substitution
        [".next", "node_modules"],            # reorder
        [],                                   # dropped entirely
    ],
    ids=["addition", "removal", "substitution", "reorder", "dropped"],
)
def test_repair_cannot_change_side_effect_authorization(tmp_path: Path, repair_paths: list[str]) -> None:
    """12. Reviewer findings cannot introduce, broaden, replace, or reorder
    the Owner's side-effect authorization."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, allowed_side_effect_paths=["node_modules", ".next"]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=repair_paths,
    )

    assert exit_code == 3  # SIDE_EFFECT_PATH_CONTRACT_MISMATCH -> IntegrityFailure


def test_repair_cannot_introduce_side_effect_authorization_from_nothing(tmp_path: Path) -> None:
    """12. Attempt 1 had no side-effect authorization; Attempt 2 cannot add
    any."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, allowed_side_effect_paths=[]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=["node_modules"],
    )

    assert exit_code == 3


def test_repair_rejects_side_effect_paths_against_legacy_evidence(tmp_path: Path) -> None:
    """Evidence predating this schema cannot prove Attempt 1's filesystem
    scope, so the only safe inheritance is 'none' -- fail closed."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, legacy_schema_without_side_effect_paths=True
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=["node_modules"],
    )

    assert exit_code == 3  # SIDE_EFFECT_PATH_EVIDENCE_INVALID


def test_repair_with_no_side_effect_paths_against_legacy_evidence_is_accepted(tmp_path: Path) -> None:
    """The legacy path stays usable when nothing is being claimed."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, legacy_schema_without_side_effect_paths=True
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
    )

    assert exit_code == 0


def test_repair_rejects_unsafe_side_effect_path_argument(tmp_path: Path) -> None:
    """CLI-layer validation is fail-closed before any preflight work."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path)

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=[".git"],
    )

    assert exit_code == 2  # PreflightFailure


# ---------------------------------------------------------------------------
# M1 Turn B -- reviewed-state reconciliation for authorized non-ignored side
# effects. Preflight-only helper: exercises run_preflight() directly (no
# fake Claude needed) so classification/identity pass-fail tests stay fast
# and assert the exact WorkspaceFingerprint split.
# ---------------------------------------------------------------------------


def make_reviewed_workspace_multi(
    tmp_path: Path, files: dict[str, str], *, committed: dict[str, str] | None = None
) -> tuple[Path, str, str]:
    """Like make_reviewed_workspace, but with an arbitrary final file layout.

    `committed` paths are seeded with placeholder content in the baseline
    commit so they show up as a *tracked* diff; everything else in `files`
    is a new (untracked) addition. Returns (workspace, baseline_commit,
    workspace_after_hash) where the hash reflects the workspace exactly as
    `files` leaves it -- i.e. "what Attempt 1 was reviewed at".
    """

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "app.txt").write_text("original\n", encoding="utf-8")
    for rel in committed or {}:
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    baseline_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    for rel, content in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    after_hash = workspace_hash(snapshot_workspace_hashes(ws))
    return ws, baseline_commit, after_hash


def preflight_for(
    tmp_path: Path,
    workspace: Path,
    *,
    implementation_run_directory: Path,
    review_run_directory: Path,
    implementation_run_id: str = "impl-1",
    implementation_attempt: int = 1,
    expected_review_run_id: str = "review-1",
    allowed_paths: list[str],
    allowed_side_effect_paths: list[str] | None = None,
    task_prompt_file: Path | None = None,
) -> dict:
    argv = [
        "--workspace",
        str(workspace),
        "--original-task-prompt-file",
        str(task_prompt_file or make_task_prompt(tmp_path)),
        "--previous-implementation-run-directory",
        str(implementation_run_directory),
        "--review-run-directory",
        str(review_run_directory),
        "--expected-review-run-id",
        expected_review_run_id,
        "--timeout-seconds",
        "30",
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
    ]
    for path in allowed_paths:
        argv.extend(["--allowed-path", path])
    for path in allowed_side_effect_paths or []:
        argv.extend(["--allowed-side-effect-path", path])
    args = run_repair.parse_args(argv)
    return run_repair.run_preflight(args)


def test_authorized_nonignored_side_effect_passes_repair_preflight(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    pre = preflight_for(
        tmp_path,
        ws,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    fingerprint = pre["fingerprint"]
    assert fingerprint.source_changed_paths == ("package.json",)
    assert fingerprint.authorized_side_effect_changed_paths == ("dist/bundle.js",)
    assert fingerprint.unexpected_changed_paths == ()
    # The reconciliation compares only the SOURCE portion against the
    # reviewed receipt's (already source-only) changed_paths.
    assert fingerprint.source_changed_paths == tuple(sorted(["package.json"]))


def test_source_portion_must_equal_reviewed_receipt_source_paths(tmp_path: Path) -> None:
    """If the receipt claims a source change that the live workspace does not
    actually have, reconciliation must fail even though nothing UNEXPECTED
    is present."""

    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(tmp_path, {"package.json": '{"a": 1}\n'})
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json", "missing.txt"],
        allowed_side_effect_paths=[],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json", "missing.txt"],
        )
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_unexpected_source_path_fails_repair_reconciliation(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path,
        {
            "package.json": '{"a": 1}\n',
            "dist/bundle.js": "console.log(1);\n",
            "src/unapproved.ts": "export {};\n",
        },
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"
    assert "src/unapproved.ts" in excinfo.value.message


def test_prefix_confusion_dist_evil_fails_repair_reconciliation(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist-evil/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"
    assert "dist-evil/bundle.js" in excinfo.value.message


def test_side_effect_without_authorization_fails_repair_reconciliation(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=[],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
        )
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"
    assert "dist/bundle.js" in excinfo.value.message


def test_source_allowlist_wins_over_overlapping_side_effect_root_in_repair(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["dist/bundle.js"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    pre = preflight_for(
        tmp_path,
        ws,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_paths=["dist/bundle.js"],
        allowed_side_effect_paths=["dist"],
    )
    fingerprint = pre["fingerprint"]
    assert fingerprint.source_changed_paths == ("dist/bundle.js",)
    assert fingerprint.authorized_side_effect_changed_paths == ()
    assert fingerprint.unexpected_changed_paths == ()


def test_wrong_implementation_run_id_fails_before_side_effect_authority_trusted(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
        run_id="impl-1",
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            implementation_run_id="impl-WRONG",
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "IMPLEMENTATION_RUN_ID_MISMATCH"


def test_wrong_implementation_attempt_fails_before_side_effect_authority_trusted(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
        attempt=1,
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            implementation_attempt=2,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_unexpected_mutation_after_review_fails_reconciliation(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    (ctx["ws"] / "unexpected.txt").write_text("added after review\n", encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )
    # The whole-workspace hash (load-bearing, never bypassed) detects this
    # before path classification is even reached.
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_legacy_review_evidence_keeps_strict_whole_workspace_hash(tmp_path: Path) -> None:
    """Historical receipts without partitioned identities retain strict fallback."""

    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "v1\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    # Drift: the reviewed evidence never changes, but the authorized
    # generated file's content silently changes after Review 1.
    (ws / "dist" / "bundle.js").write_text("v2 -- silently different\n", encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"
    assert "hash" in excinfo.value.message


def test_partitioned_review_identity_allows_authorized_side_effect_drift(
    tmp_path: Path,
) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "v1\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(
        tmp_path,
        workspace=ws,
        baseline_commit=baseline_commit,
        partitioned_identity=True,
        allowed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    (ws / "dist" / "bundle.js").write_text("v2 after deterministic review check\n", encoding="utf-8")

    pre = preflight_for(
        tmp_path,
        ws,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )

    assert pre["fingerprint"].authorized_side_effect_drift is True
    assert (
        pre["fingerprint"].reviewed_product_state_hash_actual
        == pre["fingerprint"].reviewed_product_state_hash_expected
    )
    assert (
        pre["fingerprint"].reviewed_unexpected_state_hash_actual
        == pre["fingerprint"].reviewed_unexpected_state_hash_expected
    )


def test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift(
    tmp_path: Path,
) -> None:
    """A benign timestamp-only change to `.git/index` between Review and
    Repair (e.g. an OS-attached extended attribute) must not desynchronize
    the reviewed-vs-actual protected-Git-metadata hash comparison."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    index_path = run_review.git_dir(ctx["ws"]) / "index"
    os.utime(index_path, None)

    pre = preflight_for(
        tmp_path,
        ctx["ws"],
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["app.txt"],
    )

    assert (
        pre["fingerprint"].reviewed_protected_git_metadata_hash_actual
        == pre["fingerprint"].reviewed_protected_git_metadata_hash_expected
    )


def test_partitioned_review_identity_rejects_source_drift(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    (ctx["ws"] / "app.txt").write_text("source changed after review\n", encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_partitioned_review_identity_rejects_ignored_unexpected_drift(
    tmp_path: Path,
) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    git_exclude = ctx["ws"] / ".git" / "info" / "exclude"
    git_exclude.write_text(".private/\n", encoding="utf-8")
    (ctx["ws"] / ".private").mkdir()
    (ctx["ws"] / ".private" / "state").write_text("unexpected\n", encoding="utf-8")
    assert ".private/state" not in run_review.git_changed_paths(
        ctx["ws"], ctx["baseline_commit"]
    )

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_repair_rejects_reviewed_head_and_baseline_ref_drift(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    _git(
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t.invalid",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "move head",
        ],
        ctx["ws"],
    )

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "REVIEW_BASELINE_MISMATCH"


def test_repair_cannot_widen_bound_source_allowlist_with_absent_path(
    tmp_path: Path,
) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt", "future.txt"],
        )

    assert excinfo.value.category == "ALLOWED_PATH_CONTRACT_MISMATCH"


def test_partial_partitioned_review_identity_fails_closed(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_head"] = ctx["baseline_commit"]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewed_workspace_full_hash", "0" * 64),
        ("reviewed_index_clean", True),
        ("reviewed_git_special_state", None),
        ("reviewed_allowed_source_paths", ["app.txt"]),
    ],
)
def test_any_lone_current_review_identity_field_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_malformed_partitioned_review_hash_fails_closed(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_authorized_side_effect_state_hash"] = "not-a-sha256"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_repair_rejects_semantic_index_flag_drift(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    before_identity = run_review.git_index_identity(ctx["ws"])
    _git(["update-index", "--assume-unchanged", "app.txt"], ctx["ws"])

    assert run_review.git_index_clean(ctx["ws"]) is True
    assert run_review.git_index_identity(ctx["ws"]) != before_identity
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_repair_rejects_git_config_drift_after_review(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path, partitioned_identity=True)
    _git(["config", "tomtit.synthetic", "changed"], ctx["ws"])

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_lone_git_metadata_identity_field_cannot_downgrade_to_legacy(
    tmp_path: Path,
) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_protected_git_metadata_hash"] = (
        run_review.protected_git_metadata_hash(ctx["ws"])
    )
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_same_content_receipt_symlink_substitution_is_rejected(
    tmp_path: Path,
) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    external = tmp_path / "external-receipt.json"
    external.write_bytes(receipt_path.read_bytes())
    receipt_path.unlink()
    receipt_path.symlink_to(external)

    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(
            ctx["impl_dir"],
            ctx["ws"],
            "impl-1",
            1,
        )

    assert excinfo.value.category == "INVALID_IMPLEMENTATION_EVIDENCE"


def test_review_loader_parses_captured_bytes_without_path_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = standard_setup(tmp_path)
    result_path = ctx["review_dir"] / "review-result.json"
    external = tmp_path / "later-review-result.json"
    later_result = json.loads(result_path.read_text(encoding="utf-8"))
    later_result["verdict"] = "APPROVE"
    later_result["findings"] = []
    later_result["goal_satisfied"] = True
    later_result["checks_satisfied"] = True
    external.write_text(json.dumps(later_result), encoding="utf-8")
    real_reader = run_review.read_canonical_evidence

    def capture_then_replace(root: Path, names: tuple[str, ...]):
        measured = real_reader(root, names)
        result_path.unlink()
        result_path.symlink_to(external)
        return measured

    monkeypatch.setattr(run_review, "read_canonical_evidence", capture_then_replace)

    loaded = run_repair.load_review_evidence(ctx["review_dir"])

    assert result_path.is_symlink()
    assert loaded["result"]["verdict"] == "REQUEST_CHANGES"


def test_repair_rejects_linked_worktree_gitfile_drift_after_review(
    tmp_path: Path,
) -> None:
    workspace, baseline_commit, after_hash = make_linked_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=workspace,
        after_hash=after_hash,
    )
    review_dir = make_review_evidence(
        tmp_path,
        workspace=workspace,
        baseline_commit=baseline_commit,
        partitioned_identity=True,
    )
    (workspace / ".git").chmod(0o600)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            workspace,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "WORKSPACE_STATE_MISMATCH"


def test_linked_modern_review_missing_gitfile_identity_fails_closed(
    tmp_path: Path,
) -> None:
    workspace, baseline_commit, after_hash = make_linked_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=workspace,
        after_hash=after_hash,
    )
    review_dir = make_review_evidence(
        tmp_path,
        workspace=workspace,
        baseline_commit=baseline_commit,
        partitioned_identity=True,
    )
    receipt_path = review_dir / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["reviewed_workspace_git_entry_hash"]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            workspace,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_linked_legacy_review_without_modern_identity_retains_fallback(
    tmp_path: Path,
) -> None:
    workspace, baseline_commit, _ = make_linked_reviewed_workspace(tmp_path)
    current_hashes = snapshot_workspace_hashes(workspace)
    historical_hashes = dict(current_hashes)
    historical_hashes[".git"] = run_review._sha256((workspace / ".git").read_bytes())
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=workspace,
        after_hash=workspace_hash(historical_hashes),
    )
    review_dir = make_review_evidence(
        tmp_path,
        workspace=workspace,
        baseline_commit=baseline_commit,
        partitioned_identity=False,
    )

    preflight = preflight_for(
        tmp_path,
        workspace,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_paths=["app.txt"],
    )

    assert preflight["fingerprint"].workspace_after_hash_expected == workspace_hash(
        historical_hashes
    )


def test_repair_cannot_add_side_effect_root(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist", "coverage"],
        )
    assert excinfo.value.category == "SIDE_EFFECT_PATH_CONTRACT_MISMATCH"


def test_repair_cannot_drop_side_effect_root(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist", "coverage"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "SIDE_EFFECT_PATH_CONTRACT_MISMATCH"


def test_repair_cannot_reorder_or_substitute_side_effect_roots(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist", "coverage"],
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    # Reordered (same set, different order).
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["coverage", "dist"],
        )
    assert excinfo.value.category == "SIDE_EFFECT_PATH_CONTRACT_MISMATCH"

    # Substituted (same cardinality, different root).
    impl_dir2 = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
        name="impl-run-2",
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo2:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir2,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["other-root"],
        )
    assert excinfo2.value.category == "SIDE_EFFECT_PATH_CONTRACT_MISMATCH"


def test_generated_only_change_does_not_count_as_product_change(tmp_path: Path) -> None:
    """If Attempt 2 only touches an authorized generated path and makes zero
    SOURCE changes, the adapter's own NO_EXPECTED_CHANGE rule (unchanged by
    this task) must still deny it as a successful product implementation."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    (ws / "dist").mkdir(exist_ok=True)
    impl_dir = make_implementation_evidence(
        tmp_path, workspace=ws, after_hash=after_hash, allowed_side_effect_paths=["dist"]
    )
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)
    claude = write_fake_claude(tmp_path, write_allowed="dist/bundle.js", write_content="generated only\n")

    exit_code = run_repair_cli(
        tmp_path,
        ws,
        claude,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        allowed_side_effect_paths=["dist"],
    )

    assert exit_code == 1
    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["success"] is False
    assert receipt["error_category"] == "NO_EXPECTED_CHANGE"


def test_ordinary_source_only_repair_still_passes_preflight(tmp_path: Path) -> None:
    """Existing source-only repair behavior (no side-effect authorization
    involved at all) remains unaffected by the classification fix."""

    ctx = standard_setup(tmp_path)
    pre = preflight_for(
        tmp_path,
        ctx["ws"],
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["app.txt"],
    )
    fingerprint = pre["fingerprint"]
    assert fingerprint.source_changed_paths == ("app.txt",)
    assert fingerprint.authorized_side_effect_changed_paths == ()
    assert fingerprint.unexpected_changed_paths == ()


# ---------------------------------------------------------------------------
# H-1 corrective fix -- shared implementation-evidence loader: workspace
# binding must fail closed unconditionally (never gated by truthiness).
# ---------------------------------------------------------------------------


def test_valid_canonical_workspace_is_accepted_by_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    evidence = run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert evidence["receipt"]["workspace"] == str(ctx["ws"])


def test_missing_workspace_field_fails_closed_in_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["workspace"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert excinfo.value.category in ("INVALID_IMPLEMENTATION_EVIDENCE", "IMPLEMENTATION_WORKSPACE_MISMATCH")


def test_empty_string_workspace_fails_closed_in_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = ""
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_null_workspace_fails_closed_in_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = None
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_malformed_type_workspace_fails_closed_in_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = {"not": "a string"}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_wrong_workspace_fails_closed_in_repair(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_ws, _, _ = make_reviewed_workspace(other_root)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = str(other_ws)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        run_repair.load_previous_implementation_evidence(ctx["impl_dir"], ctx["ws"], "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_falsy_workspace_cannot_authorize_side_effects_in_repair(tmp_path: Path) -> None:
    """A null workspace in the bound implementation evidence must not fall
    through truthiness and let allowed_side_effect_paths authorize
    dist/bundle.js during repair reconciliation."""

    ws, baseline_commit, after_hash = make_reviewed_workspace_multi(
        tmp_path, {"package.json": '{"a": 1}\n', "dist/bundle.js": "console.log(1);\n"}
    )
    impl_dir = make_implementation_evidence(
        tmp_path,
        workspace=ws,
        after_hash=after_hash,
        changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    receipt_path = impl_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = None
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    review_dir = make_review_evidence(tmp_path, workspace=ws, baseline_commit=baseline_commit)

    with pytest.raises(run_repair.PreflightFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["package.json"],
            allowed_side_effect_paths=["dist"],
        )
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


# ---------------------------------------------------------------------------
# M-1 corrective fix -- review evidence must state the exact same canonical
# implementation_attempt as the bound implementation receipt.
# ---------------------------------------------------------------------------


def test_matching_review_implementation_attempt_passes(tmp_path: Path) -> None:
    ctx = standard_setup(tmp_path)  # implementation attempt=1, review implementation_attempt=1 by default
    pre = preflight_for(
        tmp_path,
        ctx["ws"],
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["app.txt"],
    )
    assert pre["fingerprint"].source_changed_paths == ("app.txt",)


def test_missing_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, omit_implementation_attempt=True
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_wrong_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash, attempt=1)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt=2
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_null_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt=None
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_string_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt="1"
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_bool_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt=True
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_zero_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt=0
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_negative_review_implementation_attempt_fails(tmp_path: Path) -> None:
    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path, workspace=ws, baseline_commit=baseline_commit, implementation_attempt=-1
    )
    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=review_dir,
            allowed_paths=["app.txt"],
        )
    assert excinfo.value.category == "REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH"


# ---------------------------------------------------------------------------
# R01_REPAIR_PREFLIGHT_TRUSTED_PROJECTION_MISSING -- repair preflight's
# protected-Git-metadata validation must use the exact Review1 trusted
# projection (never a run-start baseline, never a fresh coherent-bootstrap
# re-establishment, never an unstable current reading trusted on its own).
# ---------------------------------------------------------------------------


def _perturb_stat_identity_ctime_for_inode(
    monkeypatch: pytest.MonkeyPatch, target_ino: int, *, perturb_calls: set[int]
) -> dict[str, int]:
    """Deterministically simulate an OS-level ctime-only touch landing
    between two specific observation points of one specific file during a
    single measurement -- never real OS timing. Mirrors the identical
    helper in test_run_review.py's coherent-bootstrap tests."""

    real_stat_identity = run_review._stat_identity
    state = {"calls": 0}

    def perturbing(metadata: object) -> tuple:
        real = real_stat_identity(metadata)
        if metadata.st_ino == target_ino:  # type: ignore[union-attr]
            state["calls"] += 1
            if state["calls"] in perturb_calls:
                dev, ino, mode, size, nlink, mtime_ns, ctime_ns = real
                return (dev, ino, mode, size, nlink, mtime_ns, ctime_ns + 1)
        return real

    monkeypatch.setattr(run_review, "_stat_identity", perturbing)
    return state


def test_repair_recovers_from_benign_intra_measurement_timestamp_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(Section 7) A protected regular file experiencing a genuine
    same-window timestamp-only instability during repair's OWN measurement
    -- path, dev, inode, mode, size, nlink, and bytes/SHA all unchanged --
    must be tolerated because it is validated against the exact Review1
    trusted projection, and repair preflight must succeed (the point where
    Attempt 2 would be invoked is reached)."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    head_path = run_review.git_dir(ctx["ws"]) / "HEAD"
    target_ino = head_path.stat().st_ino
    state = _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    pre = preflight_for(
        tmp_path,
        ctx["ws"],
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        allowed_paths=["app.txt"],
    )

    assert (
        pre["fingerprint"].reviewed_protected_git_metadata_hash_actual
        == pre["fingerprint"].reviewed_protected_git_metadata_hash_expected
    )
    assert state["calls"] >= 4


def test_repair_rejects_semantic_git_metadata_drift_even_with_matching_stat_fields(
    tmp_path: Path,
) -> None:
    """(Section 8) Even when size/inode/mode/nlink all match, if a protected
    file's content genuinely differs from the exact Review1 trusted
    projection, repair must block -- Attempt 2 must never start."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    description_path = run_review.git_dir(ctx["ws"]) / "description"
    original = description_path.read_bytes()
    mutated = bytearray(original)
    mutated[0] ^= 0xFF
    before_stat = description_path.stat()
    description_path.write_bytes(bytes(mutated))
    after_stat = description_path.stat()
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mode == before_stat.st_mode
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_nlink == before_stat.st_nlink

    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 3
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "changed\n"


def test_repair_blocks_when_reviewed_projection_missing(tmp_path: Path) -> None:
    """(Section 9.A) Missing full projection -> repair blocks. The
    projection is itself a modern-evidence marker field (see B02): removing
    it alone -- while every other modern field stays present -- is caught
    by the modern-schema-completeness check, not by
    `_load_trusted_review_projection()` (which is now unreachable for this
    exact case, but kept as defense in depth)."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["reviewed_protected_git_metadata_projection"]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_repair_blocks_when_reviewed_projection_malformed(tmp_path: Path) -> None:
    """(Section 9.B) Malformed projection (not a string-mapping) -> repair
    blocks at the modern-schema-completeness check (shared shape check with
    `_load_trusted_review_projection()`), not silently downgraded to the
    legacy whole-workspace-hash path."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_protected_git_metadata_projection"] = ["not", "a", "mapping"]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_repair_blocks_when_reviewed_projection_empty(tmp_path: Path) -> None:
    """(Section 9.B) An empty projection is schema-malformed too -- a real
    protected-repository projection is never empty."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_protected_git_metadata_projection"] = {}
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_repair_blocks_when_reviewed_projection_hash_mismatch(tmp_path: Path) -> None:
    """(Section 9.C) Projection content does not hash to the receipt's own
    bound `reviewed_protected_git_metadata_hash` -> repair blocks."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    projection = dict(receipt["reviewed_protected_git_metadata_projection"])
    tampered_key = next(iter(projection))
    projection[tampered_key] = projection[tampered_key] + "-tampered"
    receipt["reviewed_protected_git_metadata_projection"] = projection
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "REVIEWED_PROTECTED_PROJECTION_HASH_MISMATCH"


def test_repair_accepts_valid_exact_review1_projection_with_no_drift(tmp_path: Path) -> None:
    """(Section 9.G) A valid, exact Review1 projection with a live state
    that genuinely matches it -- repair proceeds, Attempt 2 is invoked."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 0
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "repaired\n"


def test_repair_never_calls_coherent_bootstrap_establishment() -> None:
    """(Section 6) Repair must never call
    `establish_protected_git_metadata_baseline()` to recover from a mismatch
    or missing evidence -- no baseline rollover, ever. Only the qualified
    call form (`run_review.establish_protected_git_metadata_baseline`) would
    ever actually invoke it from this module -- a bare mention of the name
    in prose/docstrings (explaining what NOT to do) is not a call."""

    source = (DOGFOOD_DIR / "run_repair.py").read_text(encoding="utf-8")
    assert "run_review.establish_protected_git_metadata_baseline" not in source


def test_target04_repair_lifecycle_benign_churn_reaches_attempt2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(Section 12) Target-04 gate lifecycle regression: Review1
    REQUEST_CHANGES -> repair preflight observes a benign intra-measurement
    timestamp-only race on a protected file -> the exact Review1 trusted
    projection validates it -> the fake Attempt 2 model is actually
    invoked. Proves R01 no longer prevents the repair invocation boundary."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    head_path = run_review.git_dir(ctx["ws"]) / "HEAD"
    target_ino = head_path.stat().st_ino
    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 0
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "repaired\n"


def test_target04_repair_lifecycle_semantic_drift_blocks_attempt2(tmp_path: Path) -> None:
    """(Section 12) Opposite case: a genuine semantic drift on a protected
    file between Review 1 and repair must block preflight, and the fake
    Attempt 2 model must never be invoked."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    description_path = run_review.git_dir(ctx["ws"]) / "description"
    mutated = bytearray(description_path.read_bytes())
    mutated[0] ^= 0xFF
    description_path.write_bytes(bytes(mutated))

    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 3
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "changed\n"


# ---------------------------------------------------------------------------
# B01_EXPECTED_REVIEW1_ID_UNBOUND -- repair must receive and verify the
# parent loop's own authoritative Review1 run id, explicitly, before
# trusting anything else in the review evidence. Never inferred from a
# directory name, the receipt's own contents, the implementation identity,
# or filesystem ordering.
# ---------------------------------------------------------------------------


def test_repair_accepts_matching_expected_review_run_id(tmp_path: Path) -> None:
    """(Section 4.A) The parent loop's expected Review1 id matches the
    receipt's own review_run_id -> the normal trusted path continues."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    review_dir = make_review_evidence(
        tmp_path,
        workspace=ws,
        baseline_commit=baseline_commit,
        review_run_id="review-authoritative-1",
        partitioned_identity=True,
    )

    pre = preflight_for(
        tmp_path,
        ws,
        implementation_run_directory=impl_dir,
        review_run_directory=review_dir,
        expected_review_run_id="review-authoritative-1",
        allowed_paths=["app.txt"],
    )

    assert pre["fingerprint"].workspace == str(ws)


def test_repair_blocks_when_expected_review_run_id_differs(tmp_path: Path) -> None:
    """(Section 4.B / lifecycle CASE 2) receipt review_run_id differs from
    the parent loop's expected Review1 id -> repair blocks before Attempt 2."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            expected_review_run_id="review-1-WRONG",
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "REVIEW_RUN_ID_MISMATCH"


def test_repair_blocks_when_receipt_review_run_id_missing(tmp_path: Path) -> None:
    """(Section 4.C) Modern repair evidence with no review_run_id at all
    -> repair blocks. Every real receipt (legacy or modern) has always set
    this field unconditionally, so its absence can only mean a tampered or
    corrupt receipt -- never a genuine historical evidence shape."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["review_run_id"]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "REVIEW_RUN_ID_MISSING"


def test_repair_blocks_foreign_self_consistent_review_with_wrong_id(
    tmp_path: Path,
) -> None:
    """(Section 4.D / lifecycle CASE 3) A foreign review receipt bound to
    the exact same implementation run/attempt/workspace/baseline, with its
    own internally self-consistent projection and hash, must still be
    rejected -- because it is not the Review1 the parent loop actually
    selected. Repair authority is which review, never merely whether a
    review is internally valid."""

    ws, baseline_commit, after_hash = make_reviewed_workspace(tmp_path)
    impl_dir = make_implementation_evidence(tmp_path, workspace=ws, after_hash=after_hash)
    genuine_review1_dir = make_review_evidence(
        tmp_path,
        workspace=ws,
        baseline_commit=baseline_commit,
        review_run_id="review-1",
        partitioned_identity=True,
        name="review-1-run",
    )
    foreign_review2_dir = make_review_evidence(
        tmp_path,
        workspace=ws,
        baseline_commit=baseline_commit,
        review_run_id="review-2",
        partitioned_identity=True,
        name="review-2-run",
    )
    assert (
        json.loads((genuine_review1_dir / "review-receipt.json").read_text())["reviewed_protected_git_metadata_hash"]
        == json.loads((foreign_review2_dir / "review-receipt.json").read_text())["reviewed_protected_git_metadata_hash"]
    ), "the foreign review's own projection/hash must be internally self-consistent to make this a meaningful test"

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=impl_dir,
            review_run_directory=foreign_review2_dir,
            expected_review_run_id="review-1",
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "REVIEW_RUN_ID_MISMATCH"


def test_target04_repair_lifecycle_wrong_review_id_blocks_attempt2(tmp_path: Path) -> None:
    """(Section 16 CASE 2) Exact parent-loop lifecycle: the parent selected
    review1_id=A, but the review evidence now carries review_run_id=B ->
    the fake Attempt 2 model must NOT run."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["review_run_id"] == "review-1"
    receipt["review_run_id"] = "review-1-REPLACED"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
        expected_review_run_id="review-1",
    )

    assert exit_code == 3
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "changed\n"


# ---------------------------------------------------------------------------
# B02_MODERN_EVIDENCE_CAN_DOWNGRADE_TO_LEGACY -- a receipt carrying the
# modern trusted-projection marker field must always be treated as modern
# evidence, and must never be able to shed the rest of the modern schema to
# fall back to the legacy whole-workspace-hash path.
# ---------------------------------------------------------------------------


def test_repair_blocks_modern_marker_with_all_other_modern_fields_removed(
    tmp_path: Path,
) -> None:
    """(Section 9, exact Codex B02 reproduction) Retain the trusted
    projection (a modern-evidence marker on its own) while removing every
    one of the other 13 partitioned-identity fields, and change the live
    protected state to a same-length but semantically different value.
    Expected: the receipt is recognized as incomplete modern evidence (never
    silently reclassified as legacy), repair blocks, and the legacy
    whole-workspace-hash path is never reached."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    projection = receipt["reviewed_protected_git_metadata_projection"]
    assert isinstance(projection, dict) and projection

    other_modern_only_fields = (
        "reviewed_head",
        "reviewed_index_clean",
        "reviewed_index_identity",
        "reviewed_protected_git_metadata_hash",
        "reviewed_workspace_git_entry_hash",
        "reviewed_git_special_state",
        "reviewed_baseline_commit",
        "reviewed_allowed_source_paths",
        "reviewed_workspace_integrity_hash",
        "reviewed_product_state_hash",
        "reviewed_authorized_side_effect_state_hash",
        "reviewed_unexpected_state_hash",
        "reviewed_workspace_full_hash",
    )
    for field in other_modern_only_fields:
        assert field in receipt
        del receipt[field]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    description_path = run_review.git_dir(ctx["ws"]) / "description"
    mutated = bytearray(description_path.read_bytes())
    mutated[0] ^= 0xFF
    description_path.write_bytes(bytes(mutated))

    with pytest.raises(run_repair.IntegrityFailure) as excinfo:
        preflight_for(
            tmp_path,
            ctx["ws"],
            implementation_run_directory=ctx["impl_dir"],
            review_run_directory=ctx["review_dir"],
            allowed_paths=["app.txt"],
        )

    assert excinfo.value.category == "PREVIOUS_EVIDENCE_INCOMPLETE"


def test_target04_repair_lifecycle_malformed_modern_downgrade_blocks_attempt2(
    tmp_path: Path,
) -> None:
    """(Section 16 CASE 4) Full lifecycle version of the B02 reproduction:
    the trusted projection is retained, every other modern field is
    removed, live protected state differs -- modern classification is
    retained (never downgraded to legacy), repair blocks, and the fake
    Attempt 2 model is never invoked."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["review_dir"] / "review-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    other_modern_only_fields = (
        "reviewed_head",
        "reviewed_index_clean",
        "reviewed_index_identity",
        "reviewed_protected_git_metadata_hash",
        "reviewed_workspace_git_entry_hash",
        "reviewed_git_special_state",
        "reviewed_baseline_commit",
        "reviewed_allowed_source_paths",
        "reviewed_workspace_integrity_hash",
        "reviewed_product_state_hash",
        "reviewed_authorized_side_effect_state_hash",
        "reviewed_unexpected_state_hash",
        "reviewed_workspace_full_hash",
    )
    for field in other_modern_only_fields:
        del receipt[field]
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    description_path = run_review.git_dir(ctx["ws"]) / "description"
    mutated = bytearray(description_path.read_bytes())
    mutated[0] ^= 0xFF
    description_path.write_bytes(bytes(mutated))

    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 3
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "changed\n"


# ---------------------------------------------------------------------------
# F-RUN005 (TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX): exact Run005
# reproduction and the surrounding regression matrix for repair's own
# previous-evidence cross-phase preservation check.
# ---------------------------------------------------------------------------


def _write_fake_claude_touching_paths(directory: Path, touch_paths: list[Path], *, name: str = "fake-claude-touch") -> Path:
    """A fake claude executable that performs the ordinary repair write,
    then touches (mtime/ctime only, via `os.utime`) each of `touch_paths` --
    simulating benign external metadata churn on the trusted prior-evidence
    tree landing strictly between repair's own before/after measurements.
    """

    script = directory / name
    touch_literals = ", ".join(repr(str(p)) for p in touch_paths)
    body = f'''#!/usr/bin/env python3
import os, pathlib, time
pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
now = time.time() + 5
for p in [{touch_literals}]:
    os.utime(p, (now, now))
raise SystemExit(0)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_repair_tolerates_prior_evidence_root_timestamp_only_change(tmp_path: Path) -> None:
    """(F-RUN005/Section 1/7/11) Exact Run005-class reproduction: the
    trusted Review1/Attempt1 evidence tree's ROOT DIRECTORY mtime_ns/
    ctime_ns change during the repair run (e.g. benign external metadata
    churn), with every member file's content, membership, and every other
    identity field completely unchanged. Repair must succeed -- this exact
    mismatch must never surface as PREVIOUS_EVIDENCE_MUTATED."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    claude = _write_fake_claude_touching_paths(tmp_path, [ctx["impl_dir"], ctx["review_dir"]])

    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 0
    assert (ctx["ws"] / "app.txt").read_text(encoding="utf-8") == "repaired\n"

    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["previous_evidence_unchanged"] is True
    assert receipt["error_category"] is None
    # (Section 10) Raw vs stable diagnostics kept strictly separate: the raw
    # representation genuinely changed (real mtime_ns/ctime_ns churn was
    # applied), even though semantic preservation holds.
    assert receipt["previous_evidence_raw_representation_unchanged"] is False


def test_repair_persists_prior_evidence_observability_fields(tmp_path: Path) -> None:
    """(F-RUN005/Section 9) Repair-start and repair-end stable hashes are
    persisted on every run, whether or not a mismatch occurs, so a future
    mismatch has enough bounded evidence to diagnose without needing to
    reproduce it."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    claude = write_fake_claude(tmp_path)
    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        claude,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )
    assert exit_code == 0

    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert isinstance(receipt["prior_evidence_stable_hash_start"], str) and receipt["prior_evidence_stable_hash_start"]
    assert isinstance(receipt["prior_evidence_stable_hash_end"], str) and receipt["prior_evidence_stable_hash_end"]
    assert receipt["prior_evidence_stable_hash_start"] == receipt["prior_evidence_stable_hash_end"]
    assert receipt["prior_evidence_diagnostic"] is None  # nothing changed -- bounded, not unbounded

    repair_context = json.loads((tmp_path / "runs" / "repair-context.json").read_text(encoding="utf-8"))
    assert repair_context["prior_evidence_stable_hash_start"] == receipt["prior_evidence_stable_hash_start"]


def test_repair_diagnostic_populated_on_real_mutation(tmp_path: Path) -> None:
    """(F-RUN005/Section 9) When prior evidence genuinely mutates during
    repair, the bounded diagnostic identifies which path and which field."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    receipt_path = ctx["impl_dir"] / "receipt.json"
    script = tmp_path / "fake-claude-mutate-evidence"
    body = f'''#!/usr/bin/env python3
import pathlib
pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
p = pathlib.Path({str(receipt_path)!r})
p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
raise SystemExit(0)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)

    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        script,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 1
    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["previous_evidence_unchanged"] is False
    assert receipt["error_category"] == "PREVIOUS_EVIDENCE_MUTATED"
    diagnostic = receipt["prior_evidence_diagnostic"]
    assert diagnostic is not None
    assert diagnostic["changed_path_count"] >= 1
    impl_sample = diagnostic["previous_implementation_evidence"]["changed_paths_sample"]
    assert any(
        entry["path"] == "receipt.json" and "content_sha256" in entry["changed_fields"]
        for entry in impl_sample
    )


def test_repair_rejects_prior_evidence_member_disappearance(tmp_path: Path) -> None:
    """(F-RUN005/Section 5/12.G) A required prior-evidence member file
    disappearing during repair must still reject -- directory-timestamp
    tolerance never implies membership tolerance."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    # An extra, non-required member of the trusted evidence directory (the
    # three REPAIR_EVIDENCE_FILES are required to exist -- deleting one of
    # those is caught as a harder EvidenceIntegrityFailure, tested
    # separately below; this exercises graceful membership-change
    # rejection for a member outside that required set).
    extra_path = ctx["impl_dir"] / "extra-note.txt"
    extra_path.write_text("incidental\n", encoding="utf-8")
    script = tmp_path / "fake-claude-delete-evidence-member"
    body = f'''#!/usr/bin/env python3
import pathlib
pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
pathlib.Path({str(extra_path)!r}).unlink()
raise SystemExit(0)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)

    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        script,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 1
    receipt = json.loads((tmp_path / "runs" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["previous_evidence_unchanged"] is False
    assert receipt["error_category"] == "PREVIOUS_EVIDENCE_MUTATED"
    impl_sample = receipt["prior_evidence_diagnostic"]["previous_implementation_evidence"]["changed_paths_sample"]
    assert any(entry["path"] == "extra-note.txt" and entry["disappeared"] is True for entry in impl_sample)


def test_repair_raises_integrity_failure_when_required_evidence_file_disappears(tmp_path: Path) -> None:
    """(F-RUN005/Section 5) A REQUIRED prior-evidence file (one of
    REPAIR_EVIDENCE_FILES) disappearing during repair is caught by the
    existing, stricter `read_canonical_evidence` required-file check
    (a harder failure than a graceful PREVIOUS_EVIDENCE_MUTATED, but still
    correctly fail-closed, not silently tolerated)."""

    ctx = standard_setup(tmp_path, partitioned_identity=True)
    patch_path = ctx["impl_dir"] / "diff.patch"
    script = tmp_path / "fake-claude-delete-required-evidence-member"
    body = f'''#!/usr/bin/env python3
import pathlib
pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
pathlib.Path({str(patch_path)!r}).unlink()
raise SystemExit(0)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)

    exit_code = run_repair_cli(
        tmp_path,
        ctx["ws"],
        script,
        implementation_run_directory=ctx["impl_dir"],
        review_run_directory=ctx["review_dir"],
    )

    assert exit_code == 3
