"""Offline, deterministic tests for the local Codex review runner.

None of these tests invoke the real Codex service. Each uses a small fake
`codex`-shaped executable to control help text and verdict output precisely,
and a throwaway Git repository as the reviewed "workspace".

Run with:

    python3 -m pytest .tomtit/dogfood/test_run_review.py -q
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent / "run_review.py"
_spec = importlib.util.spec_from_file_location("run_review", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
run_review = importlib.util.module_from_spec(_spec)
sys.modules["run_review"] = run_review
_spec.loader.exec_module(run_review)


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def make_workspace(tmp_path: Path, *, changed_content: str = "changed\n") -> Path:
    """A tiny Git repo with one committed baseline and one uncommitted change."""

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "app.txt").write_text("original\n", encoding="utf-8")
    (ws / "README.md").write_text("readme\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    (ws / "app.txt").write_text(changed_content, encoding="utf-8")
    return ws


def make_files_workspace(
    tmp_path: Path, files: dict[str, str], *, committed: dict[str, str] | None = None
) -> Path:
    """A tiny Git repo with a committed baseline plus the given untracked files.

    `committed` paths are added to the baseline commit with placeholder
    content and then overwritten with the value in `files`, so they show up
    as a *tracked* diff rather than an untracked addition.
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
    for rel, content in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ws


def make_implementation_run_directory(
    tmp_path: Path,
    *,
    status: str = "SUCCEEDED",
    success: bool = True,
    unexpected_changed_paths: list[str] | None = None,
    changed_paths: list[str] | None = None,
    workspace: Path | None = None,
    run_id: str = "run-1",
    attempt: int = 1,
    dir_name: str = "impl-run",
    allowed_side_effect_paths: list[str] | None = None,
) -> Path:
    run_dir = tmp_path / dir_name
    run_dir.mkdir()
    receipt = {
        "task_id": "task-1",
        "run_id": run_id,
        "attempt": attempt,
        "workspace": str(workspace) if workspace else "/tmp/somewhere",
        "status": status,
        "success": success,
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 1234,
        "changed_paths": changed_paths if changed_paths is not None else ["app.txt"],
        "unexpected_changed_paths": unexpected_changed_paths or [],
        "allowed_changed_paths": ["app.txt"],
        "allowed_side_effect_paths": list(allowed_side_effect_paths or []),
        "error_category": None,
        "error_message": None,
        "adapter_manifest_path": str(run_dir / "result.json"),
        "adapter_patch_path": str(run_dir / "diff.patch"),
    }
    (run_dir / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    result = {
        "status": status,
        "success": success,
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 1234,
        "changed_paths": receipt["changed_paths"],
        "unexpected_changed_paths": receipt["unexpected_changed_paths"],
        "error_category": None,
        "error_message": None,
        "stdout_excerpt": "ok",
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "diff.patch").write_text(
        "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-original\n+changed\n", encoding="utf-8"
    )
    return run_dir


def make_task_prompt(tmp_path: Path, *, text: str = "Do the thing.\n") -> Path:
    p = tmp_path / "task.md"
    p.write_text(text, encoding="utf-8")
    return p


# Matches the real installed Codex CLI's discovered contract: --ask-for-approval
# / -a is documented only as a GLOBAL flag (here, in `codex --help`), not
# repeated in `codex exec --help`. This is deliberately the default fixture
# (rather than the earlier, easier-to-satisfy shape where the flag also
# appeared in exec help) so the whole existing test suite continues to
# exercise the actual reproduced defect scenario, not a scenario that never
# triggered it.
_FAKE_CODEX_TOP_HELP = (
    "Usage: codex [OPTIONS] COMMAND\n\n"
    "Options:\n"
    "  -a, --ask-for-approval <POLICY>   untrusted | on-failure | never\n\n"
    "Commands:\n"
    "  exec  Run Codex non-interactively\n"
)
_FAKE_CODEX_EXEC_HELP = (
    "Usage: codex exec [OPTIONS] [PROMPT]\n\n"
    "Options:\n"
    "  --sandbox <MODE>              read-only | workspace-write | danger-full-access\n"
    "  --cd <DIR>, -C <DIR>          set the working directory\n"
    "  --model <MODEL>               model to use\n"
    "  --output-last-message <FILE>  write the final agent message to FILE\n"
)


def write_fake_codex(
    directory: Path,
    *,
    name: str = "fake-codex",
    verdict_json: str | None = None,
    prose_prefix: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
    write_unexpected: str | None = None,
    write_allowed: str | None = None,
    omit_last_message: bool = False,
    top_help: str = _FAKE_CODEX_TOP_HELP,
    exec_help: str = _FAKE_CODEX_EXEC_HELP,
) -> Path:
    """A Python stand-in for `codex` that understands --help, exec --help, and exec <prompt>."""

    script = directory / name
    body = f'''#!/usr/bin/env python3
import sys, time, pathlib

argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    print({top_help!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({exec_help!r})
    raise SystemExit(0)
if "exec" in argv:
    # "exec" may be preceded by global flags (e.g. -a never exec ...), so
    # locate it by position rather than assuming it is argv[0].
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    if {sleep_seconds!r}:
        time.sleep({sleep_seconds!r})
    if {write_allowed!r}:
        pathlib.Path({write_allowed!r}).write_text("codex touched an allowed path\\n")
    if {write_unexpected!r}:
        pathlib.Path({write_unexpected!r}).write_text("codex touched an unexpected path\\n")
    content = {prose_prefix!r} + ({verdict_json!r} or "")
    if last_message_path and not {omit_last_message!r}:
        pathlib.Path(last_message_path).write_text(content)
    raise SystemExit({exit_code!r})
raise SystemExit(1)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def approve_verdict_json() -> str:
    return json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "Looks good.",
            "goal_satisfied": True,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": [],
            "residual_risks": [],
        }
    )


def request_changes_verdict_json() -> str:
    return json.dumps(
        {
            "verdict": "REQUEST_CHANGES",
            "summary": "Needs work.",
            "goal_satisfied": False,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": [
                {
                    "id": "F1",
                    "severity": "MEDIUM",
                    "title": "Missing case",
                    "evidence": "app.txt line 1",
                    "required_change": "handle it",
                }
            ],
            "residual_risks": [],
        }
    )


def blocked_verdict_json() -> str:
    return json.dumps(
        {
            "verdict": "BLOCKED",
            "summary": "Not enough evidence.",
            "goal_satisfied": False,
            "scope_compliant": False,
            "checks_satisfied": False,
            "findings": [],
            "residual_risks": ["insufficient context"],
        }
    )


def run_review_cli(
    tmp_path: Path,
    workspace: Path,
    codex_executable: Path,
    *,
    allowed_paths: list[str] | None = None,
    check_commands: list[str] | None = None,
    timeout_seconds: int = 30,
    implementation_run_directory: Path | None = None,
    task_prompt_file: Path | None = None,
    review_run_id: str | None = None,
    evidence_directory: Path | None = None,
    implementation_run_id: str = "run-1",
    implementation_attempt: int = 1,
) -> int:
    argv = [
        "--workspace",
        str(workspace),
        "--task-prompt-file",
        str(task_prompt_file or make_task_prompt(tmp_path)),
        "--implementation-run-directory",
        str(implementation_run_directory or make_implementation_run_directory(tmp_path, workspace=workspace)),
        "--baseline-ref",
        "HEAD",
        "--timeout-seconds",
        str(timeout_seconds),
        "--codex-executable",
        str(codex_executable),
        "--evidence-directory",
        str(evidence_directory or (tmp_path / "reviews")),
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
    ]
    for path in allowed_paths if allowed_paths is not None else ["app.txt"]:
        argv.extend(["--allowed-path", path])
    for command in check_commands or []:
        argv.extend(["--check-command", command])
    if review_run_id:
        argv.extend(["--review-run-id", review_run_id])
    return run_review.main(argv)


# ---------------------------------------------------------------------------
# 1-3. Valid verdicts.
# ---------------------------------------------------------------------------


def test_valid_approve_response(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex)
    assert exit_code == 0


def test_valid_request_changes_response(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=request_changes_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex)
    assert exit_code == 1


def test_valid_blocked_response(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=blocked_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex)
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 4-6. Malformed Codex output.
# ---------------------------------------------------------------------------


def test_invalid_json(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json="{not valid json")
    assert run_review_cli(tmp_path, ws, codex) == 4


def test_prose_before_json(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, prose_prefix="Sure, here is my review:\n", verdict_json=approve_verdict_json())
    assert run_review_cli(tmp_path, ws, codex) == 4


def test_exit_zero_with_no_verdict(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json="", omit_last_message=True)
    assert run_review_cli(tmp_path, ws, codex) == 4


# ---------------------------------------------------------------------------
# 7-8. Process-level failures.
# ---------------------------------------------------------------------------


def test_codex_nonzero_exit(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json(), exit_code=1)
    assert run_review_cli(tmp_path, ws, codex) == 4


def test_timeout(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json(), sleep_seconds=5)
    assert run_review_cli(tmp_path, ws, codex, timeout_seconds=1) == 4


# ---------------------------------------------------------------------------
# 9-10. Codex modifies the workspace.
# ---------------------------------------------------------------------------


def test_codex_modifies_an_allowed_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path, verdict_json=approve_verdict_json(), write_allowed=str(ws / "app.txt")
    )
    assert run_review_cli(tmp_path, ws, codex) == 4


def test_codex_modifies_an_unexpected_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path, verdict_json=approve_verdict_json(), write_unexpected=str(ws / "README.md")
    )
    assert run_review_cli(tmp_path, ws, codex) == 4


# ---------------------------------------------------------------------------
# 11-16. Preflight failures.
# ---------------------------------------------------------------------------


def test_dirty_index(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    _git(["add", "app.txt"], ws)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    assert run_review_cli(tmp_path, ws, codex) == 3


def test_no_implementation_diff(tmp_path: Path) -> None:
    ws = tmp_path / "clean-ws"
    ws.mkdir()
    (ws / "app.txt").write_text("original\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    assert run_review_cli(tmp_path, ws, codex) == 3


def test_changed_path_outside_allowed_scope(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    (ws / "README.md").write_text("also changed\n", encoding="utf-8")
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    assert run_review_cli(tmp_path, ws, codex, allowed_paths=["app.txt"]) == 3


def test_invalid_allowed_path(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    assert run_review_cli(tmp_path, ws, codex, allowed_paths=["../escape.txt"]) == 3


def test_missing_task_prompt(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex, task_prompt_file=tmp_path / "does-not-exist.md")
    assert exit_code == 3


def test_empty_task_prompt(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    empty = make_task_prompt(tmp_path, text="   \n")
    assert run_review_cli(tmp_path, ws, codex, task_prompt_file=empty) == 3


# ---------------------------------------------------------------------------
# 17-19. Implementation-evidence preflight failures.
# ---------------------------------------------------------------------------


def test_missing_implementation_evidence(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    missing_dir = tmp_path / "no-such-run-dir"
    assert run_review_cli(tmp_path, ws, codex, implementation_run_directory=missing_dir) == 3


def test_implementation_receipt_reports_failure(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_dir = make_implementation_run_directory(tmp_path, status="FAILED", success=False, workspace=ws)
    assert run_review_cli(tmp_path, ws, codex, implementation_run_directory=run_dir) == 3


def test_implementation_receipt_reports_unexpected_paths(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_dir = make_implementation_run_directory(
        tmp_path, unexpected_changed_paths=["oops.txt"], workspace=ws
    )
    assert run_review_cli(tmp_path, ws, codex, implementation_run_directory=run_dir) == 3


# ---------------------------------------------------------------------------
# 20-21. Deterministic checks.
# ---------------------------------------------------------------------------


def test_deterministic_check_passes(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex, check_commands=["python3 -c \"print(1)\""])
    assert exit_code == 0


def test_deterministic_check_fails(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=request_changes_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex, check_commands=["python3 -c \"import sys; sys.exit(1)\""])
    # The review still proceeds; the *verdict* drives the exit code, not the check.
    assert exit_code == 1


def test_shell_operator_rejected_in_check_command(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    exit_code = run_review_cli(tmp_path, ws, codex, check_commands=["echo hi && echo bye"])
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 23. Evidence-directory collision.
# ---------------------------------------------------------------------------


def test_existing_review_evidence_directory_collision(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    review_run_id = "collide-me"
    evidence_root = tmp_path / "reviews"
    existing = evidence_root / review_run_id
    existing.mkdir(parents=True)
    (existing / "stale.txt").write_text("leftover", encoding="utf-8")
    exit_code = run_review_cli(
        tmp_path, ws, codex, review_run_id=review_run_id, evidence_directory=evidence_root
    )
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 24. No credential leakage on internal error.
# ---------------------------------------------------------------------------


def test_credentials_are_not_printed_on_internal_error(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOMTIT_TEST_REVIEW_SECRET", "sk-super-secret-value")
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json(), exit_code=1)
    run_review_cli(tmp_path, ws, codex)
    captured = capsys.readouterr()
    assert "sk-super-secret-value" not in captured.out
    assert "sk-super-secret-value" not in captured.err


# ---------------------------------------------------------------------------
# 25. Exactly one Codex invocation.
# ---------------------------------------------------------------------------


def test_one_codex_invocation_only(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    counter_path = tmp_path / "invocation_count.txt"
    codex_src = f'''#!/usr/bin/env python3
import sys, pathlib
argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    print({_FAKE_CODEX_TOP_HELP!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({_FAKE_CODEX_EXEC_HELP!r})
    raise SystemExit(0)
if "exec" in argv:
    p = pathlib.Path({str(counter_path)!r})
    count = int(p.read_text()) if p.exists() else 0
    p.write_text(str(count + 1))
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    if last_message_path:
        pathlib.Path(last_message_path).write_text({approve_verdict_json()!r})
    raise SystemExit(0)
raise SystemExit(1)
'''
    codex = tmp_path / "fake-codex-counter"
    codex.write_text(codex_src, encoding="utf-8")
    codex.chmod(0o755)
    run_review_cli(tmp_path, ws, codex)
    assert counter_path.read_text(encoding="utf-8") == "1"


# ---------------------------------------------------------------------------
# 26-27. Verdict-logic rejection.
# ---------------------------------------------------------------------------


def test_approve_rejected_with_medium_or_high_findings(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    bad_approve = json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "ok",
            "goal_satisfied": True,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": [
                {
                    "id": "F1",
                    "severity": "HIGH",
                    "title": "bad",
                    "evidence": "x",
                    "required_change": "y",
                }
            ],
            "residual_risks": [],
        }
    )
    codex = write_fake_codex(tmp_path, verdict_json=bad_approve)
    assert run_review_cli(tmp_path, ws, codex) == 4


def test_approve_rejected_with_false_required_boolean(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    bad_approve = json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "ok",
            "goal_satisfied": True,
            "scope_compliant": False,
            "checks_satisfied": True,
            "findings": [],
            "residual_risks": [],
        }
    )
    codex = write_fake_codex(tmp_path, verdict_json=bad_approve)
    assert run_review_cli(tmp_path, ws, codex) == 4


# ---------------------------------------------------------------------------
# 28. Workspace hashes remain identical after a clean review.
# ---------------------------------------------------------------------------


def test_workspace_hashes_identical_after_review(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    before = (ws / "app.txt").read_bytes(), (ws / "README.md").read_bytes()
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_review_cli(tmp_path, ws, codex)
    after = (ws / "app.txt").read_bytes(), (ws / "README.md").read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# Additional coverage: capability-contract rejection and unit-level helpers.
# ---------------------------------------------------------------------------


def test_unsupported_codex_cli_contract_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        top_help="Usage: codex\n",
        exec_help="Usage: codex exec\n\n(no options documented)\n",
    )
    assert run_review_cli(tmp_path, ws, codex) == 3


def test_normalize_allowed_paths_rejects_traversal(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(run_review.PreflightFailure):
        run_review.normalize_allowed_paths(["../x"], ws)


def test_normalize_allowed_paths_rejects_git_metadata(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(run_review.PreflightFailure):
        run_review.normalize_allowed_paths([".git/config"], ws)


def test_validate_check_command_rejects_redirection() -> None:
    with pytest.raises(run_review.PreflightFailure):
        run_review.validate_check_command_text("cat file.txt > out.txt")


def test_validate_check_command_accepts_plain_command() -> None:
    argv = run_review.validate_check_command_text('python3 -c "print(1)"')
    assert argv == ["python3", "-c", "print(1)"]


def test_enforce_verdict_rules_requires_finding_for_request_changes() -> None:
    obj = {
        "verdict": "REQUEST_CHANGES",
        "summary": "x",
        "goal_satisfied": False,
        "scope_compliant": True,
        "checks_satisfied": True,
        "findings": [],
        "residual_risks": [],
    }
    with pytest.raises(run_review.ReviewFailure):
        run_review.enforce_verdict_rules(obj)


def test_parse_verdict_text_rejects_empty_output() -> None:
    with pytest.raises(run_review.ReviewFailure):
        run_review.parse_verdict_text("   ")


# ===========================================================================
# Corrective regressions: --ask-for-approval discovered as a GLOBAL flag,
# separately from `codex exec --help`. Root cause of the original defect was
# requiring evidence for approval control only in exec-scoped help text.
# ===========================================================================


_EXEC_HELP_NO_SANDBOX_NO_DEFAULT = (
    "Usage: codex exec [OPTIONS] [PROMPT]\n\n"
    "Options:\n"
    "  --cd <DIR>, -C <DIR>          set the working directory\n"
    "  --model <MODEL>               model to use\n"
    "  --output-last-message <FILE>  write the final agent message to FILE\n"
)
_EXEC_HELP_NO_OUTPUT_LAST_MESSAGE = (
    "Usage: codex exec [OPTIONS] [PROMPT]\n\n"
    "Options:\n"
    "  --sandbox <MODE>              read-only | workspace-write | danger-full-access\n"
    "  --cd <DIR>, -C <DIR>          set the working directory\n"
    "  --model <MODEL>               model to use\n"
)
_TOP_HELP_NO_EXEC_COMMAND = "Usage: codex [OPTIONS] COMMAND\n\nOptions:\n  -a, --ask-for-approval <POLICY>   never\n"
_TOP_HELP_ALIAS_ONLY = (
    "Usage: codex [OPTIONS] COMMAND\n\n"
    "Options:\n"
    "  -a <POLICY>   accepted values: untrusted, on-failure, never\n\n"
    "Commands:\n"
    "  exec  Run Codex non-interactively\n"
)
_TOP_HELP_NO_APPROVAL_CONTROL = "Usage: codex [OPTIONS] COMMAND\n\nCommands:\n  exec  Run Codex non-interactively\n"


# ---------------------------------------------------------------------------
# 1. Global help documents --ask-for-approval; exec help does not: discovery
#    still confirms the capability (this is the exact reproduced defect
#    scenario, and it is also now the default fixture for the whole file).
# ---------------------------------------------------------------------------


def test_global_approval_flag_without_exec_help_repetition_is_confirmed() -> None:
    caps = run_review.detect_codex_capabilities(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    assert caps["non_interactive_approval"] is True
    assert run_review.missing_required_capabilities(caps) == []
    approval = run_review.resolve_approval_flag(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    assert approval == ("--ask-for-approval", "global")


# ---------------------------------------------------------------------------
# 2. Short global alias -a with documented never support.
# ---------------------------------------------------------------------------


def test_short_global_alias_with_never_support_is_confirmed() -> None:
    caps = run_review.detect_codex_capabilities(_TOP_HELP_ALIAS_ONLY, _FAKE_CODEX_EXEC_HELP)
    assert caps["non_interactive_approval"] is True
    approval = run_review.resolve_approval_flag(_TOP_HELP_ALIAS_ONLY, _FAKE_CODEX_EXEC_HELP)
    assert approval == ("-a", "global")


# ---------------------------------------------------------------------------
# 3. Neither help output documents approval control: fail closed.
# ---------------------------------------------------------------------------


def test_no_approval_control_anywhere_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        top_help=_TOP_HELP_NO_APPROVAL_CONTROL,
        exec_help=_FAKE_CODEX_EXEC_HELP,
    )
    assert run_review_cli(tmp_path, ws, codex) == 3
    caps = run_review.detect_codex_capabilities(_TOP_HELP_NO_APPROVAL_CONTROL, _FAKE_CODEX_EXEC_HELP)
    assert caps["non_interactive_approval"] is False
    assert "non_interactive_approval" in run_review.missing_required_capabilities(caps)


# ---------------------------------------------------------------------------
# 4. exec subcommand missing: fail closed.
# ---------------------------------------------------------------------------


def test_exec_subcommand_missing_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        top_help=_TOP_HELP_NO_EXEC_COMMAND,
        exec_help=_FAKE_CODEX_EXEC_HELP,
    )
    assert run_review_cli(tmp_path, ws, codex) == 3
    caps = run_review.detect_codex_capabilities(_TOP_HELP_NO_EXEC_COMMAND, _FAKE_CODEX_EXEC_HELP)
    assert caps["exec_subcommand"] is False


# ---------------------------------------------------------------------------
# 5. Read-only sandbox missing, with no trustworthy documented default:
#    fail closed.
# ---------------------------------------------------------------------------


def test_read_only_missing_with_no_default_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        top_help=_FAKE_CODEX_TOP_HELP,
        exec_help=_EXEC_HELP_NO_SANDBOX_NO_DEFAULT,
    )
    assert run_review_cli(tmp_path, ws, codex) == 3
    caps = run_review.detect_codex_capabilities(_FAKE_CODEX_TOP_HELP, _EXEC_HELP_NO_SANDBOX_NO_DEFAULT)
    assert caps["read_only_mode"] is False
    assert "read_only_mode" in run_review.missing_required_capabilities(caps)


# ---------------------------------------------------------------------------
# 6. Output-last-message capability missing: fail closed.
# ---------------------------------------------------------------------------


def test_output_last_message_missing_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        top_help=_FAKE_CODEX_TOP_HELP,
        exec_help=_EXEC_HELP_NO_OUTPUT_LAST_MESSAGE,
    )
    assert run_review_cli(tmp_path, ws, codex) == 3
    caps = run_review.detect_codex_capabilities(_FAKE_CODEX_TOP_HELP, _EXEC_HELP_NO_OUTPUT_LAST_MESSAGE)
    assert caps["isolated_final_message"] is False


# ---------------------------------------------------------------------------
# 7-8. Generated argv places the global approval flag before `exec` and
#      requests explicit read-only sandbox mode.
# ---------------------------------------------------------------------------


def test_generated_argv_places_global_approval_before_exec(tmp_path: Path) -> None:
    caps = run_review.detect_codex_capabilities(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    approval = run_review.resolve_approval_flag(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    assert approval is not None
    argv = run_review.build_codex_argv(
        "/usr/bin/codex",
        workspace=tmp_path,
        model=None,
        last_message_path=tmp_path / "last.txt",
        prompt="review this",
        caps=caps,
        approval=approval,
    )
    assert argv[0] == "/usr/bin/codex"
    exec_position = argv.index("exec")
    approval_position = argv.index("--ask-for-approval")
    assert approval_position < exec_position, f"approval flag must precede exec: {argv}"
    assert argv[approval_position : approval_position + 2] == ["--ask-for-approval", "never"]


def test_generated_argv_requests_read_only_sandbox(tmp_path: Path) -> None:
    caps = run_review.detect_codex_capabilities(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    approval = run_review.resolve_approval_flag(_FAKE_CODEX_TOP_HELP, _FAKE_CODEX_EXEC_HELP)
    assert approval is not None
    argv = run_review.build_codex_argv(
        "/usr/bin/codex",
        workspace=tmp_path,
        model=None,
        last_message_path=tmp_path / "last.txt",
        prompt="review this",
        caps=caps,
        approval=approval,
    )
    sandbox_position = argv.index("--sandbox")
    assert argv[sandbox_position + 1] == "read-only"
    for forbidden in run_review._FORBIDDEN_ARGV_TOKENS:
        assert forbidden not in argv


def test_generated_argv_uses_exec_scope_when_approval_only_documented_there(tmp_path: Path) -> None:
    """Fallback path: some CLI versions might document the flag only at exec level."""

    exec_help_with_approval = _FAKE_CODEX_EXEC_HELP + "  --ask-for-approval <POLICY>   never\n"
    top_help_without_approval = _TOP_HELP_NO_APPROVAL_CONTROL
    caps = run_review.detect_codex_capabilities(top_help_without_approval, exec_help_with_approval)
    approval = run_review.resolve_approval_flag(top_help_without_approval, exec_help_with_approval)
    assert approval == ("--ask-for-approval", "exec")
    argv = run_review.build_codex_argv(
        "/usr/bin/codex",
        workspace=tmp_path,
        model=None,
        last_message_path=tmp_path / "last.txt",
        prompt="review this",
        caps=caps,
        approval=approval,
    )
    exec_position = argv.index("exec")
    approval_position = argv.index("--ask-for-approval")
    assert approval_position > exec_position, f"exec-scoped approval flag must follow exec: {argv}"


# ---------------------------------------------------------------------------
# 9. Exactly one fake Codex invocation under the corrected global-flag
#    contract (dedicated regression, distinct from the generic case).
# ---------------------------------------------------------------------------


def test_exactly_one_invocation_under_global_approval_contract(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    counter_path = tmp_path / "global-invocation-count.txt"
    codex_src = f'''#!/usr/bin/env python3
import sys, pathlib
argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    print({_FAKE_CODEX_TOP_HELP!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({_FAKE_CODEX_EXEC_HELP!r})
    raise SystemExit(0)
if "exec" in argv:
    assert argv.index("exec") > 0, "expected the global approval flag before exec: " + repr(argv)
    p = pathlib.Path({str(counter_path)!r})
    count = int(p.read_text()) if p.exists() else 0
    p.write_text(str(count + 1))
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    if last_message_path:
        pathlib.Path(last_message_path).write_text({approve_verdict_json()!r})
    raise SystemExit(0)
raise SystemExit(1)
'''
    codex = tmp_path / "fake-codex-global-counter"
    codex.write_text(codex_src, encoding="utf-8")
    codex.chmod(0o755)
    exit_code = run_review_cli(tmp_path, ws, codex)
    assert exit_code == 0
    assert counter_path.read_text(encoding="utf-8") == "1"


# ---------------------------------------------------------------------------
# 10. Existing APPROVE/REQUEST_CHANGES/BLOCKED/invalid-JSON/timeout/
#     immutability/credential-safety cases already run under this corrected
#     global-flag-by-default fixture (see _FAKE_CODEX_TOP_HELP /
#     _FAKE_CODEX_EXEC_HELP above): test_valid_approve_response,
#     test_valid_request_changes_response, test_valid_blocked_response,
#     test_invalid_json, test_timeout, test_codex_modifies_an_allowed_file,
#     and test_credentials_are_not_printed_on_internal_error all exercise the
#     corrected contract without any separate duplicate test being needed.


# ---------------------------------------------------------------------------
# M1 implementation-identity binding.
# ---------------------------------------------------------------------------


def test_matching_run_id_and_attempt_is_accepted(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        implementation_run_directory=run_dir,
        implementation_run_id="impl-1",
        implementation_attempt=1,
    )
    assert exit_code == 0


def test_wrong_run_id_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        implementation_run_directory=run_dir,
        implementation_run_id="impl-WRONG",
        implementation_attempt=1,
    )
    assert exit_code == 3


def test_wrong_attempt_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        implementation_run_directory=run_dir,
        implementation_run_id="impl-1",
        implementation_attempt=2,
    )
    assert exit_code == 3


def test_missing_run_id_in_receipt_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["run_id"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_RUN_ID_MISMATCH"


def test_missing_attempt_in_receipt_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["attempt"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_malformed_run_id_in_receipt_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["run_id"] = 12345
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_RUN_ID_MISMATCH"


def test_malformed_attempt_in_receipt_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["attempt"] = "one"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_wrong_run_evidence_cannot_be_trusted_via_side_effect_paths(tmp_path: Path) -> None:
    """Wrong-run evidence must be rejected before any authority-bearing
    field (e.g. allowed_side_effect_paths) in it could be used."""

    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-OTHER", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["allowed_side_effect_paths"] = ["some/generated/root"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_RUN_ID_MISMATCH"


def test_wrong_attempt_evidence_cannot_be_trusted_via_side_effect_paths(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=2)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["allowed_side_effect_paths"] = ["some/generated/root"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_ATTEMPT_MISMATCH"


# ---------------------------------------------------------------------------
# M1 A2 -- changed-path classification for authorized non-ignored side
# effects. Preflight-only helper: exercises run_preflight() directly (no
# fake Codex needed) so pass/fail classification tests stay fast and assert
# the exact (source, side-effect, unexpected) split.
# ---------------------------------------------------------------------------


def preflight_for(
    tmp_path: Path,
    workspace: Path,
    *,
    implementation_run_directory: Path,
    implementation_run_id: str = "impl-1",
    implementation_attempt: int = 1,
    allowed_paths: list[str],
) -> dict:
    argv = [
        "--workspace",
        str(workspace),
        "--task-prompt-file",
        str(make_task_prompt(tmp_path)),
        "--implementation-run-directory",
        str(implementation_run_directory),
        "--baseline-ref",
        "HEAD",
        "--timeout-seconds",
        "30",
        "--implementation-run-id",
        implementation_run_id,
        "--implementation-attempt",
        str(implementation_attempt),
    ]
    for path in allowed_paths:
        argv.extend(["--allowed-path", path])
    args = run_review.parse_args(argv)
    return run_review.run_preflight(args)


def test_authorized_nonignored_side_effect_passes_preflight(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    pre = preflight_for(
        tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"]
    )
    assert pre["source_changed_paths"] == ("package.json",)
    assert pre["authorized_side_effect_changed_paths"] == ("dist/bundle.js",)
    assert pre["unexpected_changed_paths"] == ()
    assert pre["changed_paths"] == ("package.json",)


def test_unexpected_source_fails_closed(tmp_path: Path) -> None:
    ws = make_files_workspace(
        tmp_path,
        {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n", "src/unapproved.ts": "export {};\n"},
    )
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_prefix_confusion_dist_evil_denied(tmp_path: Path) -> None:
    ws = make_files_workspace(
        tmp_path, {"package.json": "{}\n", "dist-evil/bundle.js": "console.log(1);\n"}
    )
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_side_effect_without_authorization_fails_closed(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_source_allowlist_wins_over_overlapping_side_effect_root(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    pre = preflight_for(
        tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["dist/bundle.js"]
    )
    assert pre["source_changed_paths"] == ("dist/bundle.js",)
    assert pre["authorized_side_effect_changed_paths"] == ()
    assert pre["unexpected_changed_paths"] == ()
    assert pre["changed_paths"] == ("dist/bundle.js",)


def test_reviewer_cannot_broaden_authorized_side_effect_roots(tmp_path: Path) -> None:
    """Attempt 1 evidence authorizes only `dist`. A path under an unrelated
    root (`coverage`) must remain unexpected no matter what -- there is no
    CLI flag, receipt field, or Git state that can add a new root outside
    the bound Attempt-1 evidence itself."""

    ws = make_files_workspace(
        tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n", "coverage/report.html": "<html/>\n"}
    )
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"
    assert "coverage/report.html" in excinfo.value.message


def test_wrong_run_evidence_cannot_authorize_side_effects_end_to_end(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=run_dir,
            implementation_run_id="impl-WRONG",
            implementation_attempt=1,
            allowed_paths=["package.json"],
        )
    assert excinfo.value.category == "IMPLEMENTATION_RUN_ID_MISMATCH"


def test_wrong_attempt_evidence_cannot_authorize_side_effects_end_to_end(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(
            tmp_path,
            ws,
            implementation_run_directory=run_dir,
            implementation_run_id="impl-1",
            implementation_attempt=2,
            allowed_paths=["package.json"],
        )
    assert excinfo.value.category == "IMPLEMENTATION_ATTEMPT_MISMATCH"


def test_source_only_reviewer_patch_excludes_authorized_side_effect(tmp_path: Path) -> None:
    ws = make_files_workspace(
        tmp_path,
        {"package.json": '{"name": "changed"}\n', "dist/bundle.js": "console.log(1);\n"},
        committed={"package.json": ""},
    )
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())
    evidence_directory = tmp_path / "reviews"
    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        implementation_run_directory=run_dir,
        implementation_run_id="impl-1",
        implementation_attempt=1,
        allowed_paths=["package.json"],
        evidence_directory=evidence_directory,
        review_run_id="review-1",
    )
    assert exit_code == 0
    prompt_text = (evidence_directory / "review-1" / "review-prompt.txt").read_text(encoding="utf-8")

    # The raw `git status --porcelain` section is deliberately unfiltered
    # (full transparency), so it may still mention dist/bundle.js. The
    # sections that actually drive/represent the reviewer's *patch* --
    # tracked diff and untracked-file list -- must be source-only.
    def _section(marker: str) -> str:
        start = prompt_text.index(marker) + len(marker)
        end = prompt_text.index("\n\n# ", start)
        return prompt_text[start:end]

    diff_section = _section("# Tracked diff from baseline (allowed paths only)\n\n")
    untracked_section = _section("# Untracked files\n\n")

    # package.json is a tracked modification, so it drives the actual patch;
    # dist/bundle.js is an authorized but non-source untracked addition and
    # must appear in neither the patch nor the untracked-file list.
    assert "package.json" in diff_section
    assert "dist/bundle.js" not in diff_section
    assert "console.log(1)" not in diff_section
    assert "dist/bundle.js" not in untracked_section


# ---------------------------------------------------------------------------
# H-1 corrective fix -- implementation receipt workspace must fail closed
# unconditionally (never gated by truthiness). A missing, empty, null, or
# malformed-type workspace value must be rejected exactly like a wrong one,
# before any authority-bearing field (e.g. allowed_side_effect_paths) can be
# consumed.
# ---------------------------------------------------------------------------


def test_valid_canonical_workspace_is_accepted(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    evidence = run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert evidence["receipt"]["workspace"] == str(ws)


def test_missing_workspace_field_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["workspace"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    # Caught by required-field structural validation, which itself precedes
    # workspace/run_id/attempt binding -- still fails closed either way.
    assert excinfo.value.category in ("INVALID_IMPLEMENTATION_EVIDENCE", "IMPLEMENTATION_WORKSPACE_MISMATCH")


def test_empty_string_workspace_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = ""
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_null_workspace_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = None
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_malformed_type_workspace_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    run_dir = make_implementation_run_directory(tmp_path, workspace=ws, run_id="impl-1", attempt=1)
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = ["not", "a", "string"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_wrong_workspace_fails_closed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_ws = make_workspace(other_root)
    run_dir = make_implementation_run_directory(tmp_path, workspace=other_ws, run_id="impl-1", attempt=1)
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.load_implementation_evidence(run_dir, ws, "impl-1", 1)
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


def test_falsy_workspace_cannot_authorize_side_effects(tmp_path: Path) -> None:
    """An empty-string workspace must not fall through truthiness and let
    allowed_side_effect_paths authorize dist/bundle.js."""

    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path, workspace=ws, run_id="impl-1", attempt=1, allowed_side_effect_paths=["dist"]
    )
    receipt_path = run_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["workspace"] = ""
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "IMPLEMENTATION_WORKSPACE_MISMATCH"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
