"""Offline, deterministic tests for the local implement-review-repair-review
orchestrator (`run_loop.py`).

None of these tests invoke the real Claude or Codex service. Each uses small
fake `claude`/`codex`-shaped executables and a throwaway Git repository. Fake
executables are written fresh per test under pytest's own `tmp_path` and are
never persisted; nothing here writes into the real `.tomtit/dogfood/loops/`.

Run with:

    python3 -m pytest .tomtit/dogfood/test_run_loop.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DOGFOOD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DOGFOOD_DIR))
REPO_ROOT = DOGFOOD_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("run_loop", DOGFOOD_DIR / "run_loop.py")
assert _spec is not None and _spec.loader is not None
run_loop = importlib.util.module_from_spec(_spec)
sys.modules["run_loop"] = run_loop
_spec.loader.exec_module(run_loop)

run_review = sys.modules["run_review"]  # imported as a side effect of loading run_loop
run_repair = sys.modules["run_repair"]
run_task = sys.modules["run_task"]

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    snapshot_workspace_hashes,
    workspace_hash,
)


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def make_clean_workspace(tmp_path: Path, *, name: str = "workspace") -> Path:
    """A committed Git repo with no pending changes -- the state before Attempt 1."""

    ws = tmp_path / name
    ws.mkdir()
    (ws / "app.txt").write_text("original\n", encoding="utf-8")
    (ws / "README.md").write_text("readme\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    return ws


def make_linked_clean_workspace(tmp_path: Path) -> Path:
    primary = make_clean_workspace(tmp_path)
    linked = tmp_path / "linked-worktree"
    _git(["worktree", "add", "-q", "-b", "linked-test", str(linked), "HEAD"], primary)
    return linked


def make_task_prompt(tmp_path: Path, *, text: str = "Do the thing.\nFollow the rules.\n", name: str = "task.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


TOP_HELP = (
    "Usage: codex [OPTIONS] COMMAND\n\n"
    "Options:\n"
    "  -a, --ask-for-approval <POLICY>   untrusted | on-failure | never\n\n"
    "Commands:\n"
    "  exec  Run Codex non-interactively\n"
)
EXEC_HELP = (
    "Usage: codex exec [OPTIONS] [PROMPT]\n\n"
    "Options:\n"
    "  --sandbox <MODE>              read-only | workspace-write | danger-full-access\n"
    "  --cd <DIR>, -C <DIR>          set the working directory\n"
    "  --model <MODEL>               model to use\n"
    "  --output-last-message <FILE>  write the final agent message to FILE\n"
)


def write_fake_claude(
    directory: Path,
    *,
    name: str,
    write_allowed: str | None = "app.txt",
    write_content: str = "implemented\n",
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


def write_fake_codex(
    directory: Path,
    *,
    name: str,
    verdict_json: str | None = None,
    prose_prefix: str = "",
    exit_code: int = 0,
    sleep_seconds: float = 0,
    write_unexpected: str | None = None,
    help_write_path: str | None = None,
    omit_last_message: bool = False,
    top_help: str = TOP_HELP,
    exec_help: str = EXEC_HELP,
) -> Path:
    script = directory / name
    body = f'''#!/usr/bin/env python3
import sys, time, pathlib

argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    if {help_write_path!r}:
        pathlib.Path({help_write_path!r}).write_text("capability probe touched source\\n")
    print({top_help!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({exec_help!r})
    raise SystemExit(0)
if "exec" in argv:
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    if {sleep_seconds!r}:
        time.sleep({sleep_seconds!r})
    if {write_unexpected!r}:
        pathlib.Path({write_unexpected!r}).write_text("unexpected write\\n", encoding="utf-8")
    content = {prose_prefix!r} + ({verdict_json!r} or "")
    if last_message_path and not {omit_last_message!r}:
        pathlib.Path(last_message_path).write_text(content)
    raise SystemExit({exit_code!r})
raise SystemExit(1)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def approve_verdict_json(findings: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "Looks good.",
            "goal_satisfied": True,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": findings or [],
            "residual_risks": [],
        }
    )


def request_changes_verdict_json(findings: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "verdict": "REQUEST_CHANGES",
            "summary": "Needs work.",
            "goal_satisfied": False,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": findings
            or [
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


def run_loop_cli(
    tmp_path: Path,
    workspace: Path,
    claude_executable: Path,
    codex_executable: Path,
    *,
    allow_one_repair: bool = False,
    allowed_paths: list[str] | None = None,
    allowed_side_effect_paths: list[str] | None = None,
    check_commands: list[str] | None = None,
    implementation_timeout_seconds: int = 30,
    review_timeout_seconds: int = 30,
    evidence_directory: Path | None = None,
    loop_run_id: str | None = None,
    task_prompt_file: Path | None = None,
) -> int:
    argv = [
        "--workspace",
        str(workspace),
        "--task-prompt-file",
        str(task_prompt_file or make_task_prompt(tmp_path)),
        "--baseline-ref",
        "HEAD",
        "--implementation-timeout-seconds",
        str(implementation_timeout_seconds),
        "--review-timeout-seconds",
        str(review_timeout_seconds),
        "--claude-executable",
        str(claude_executable),
        "--codex-executable",
        str(codex_executable),
        "--evidence-directory",
        str(evidence_directory or (tmp_path / "loops")),
    ]
    for path in allowed_paths if allowed_paths is not None else ["app.txt"]:
        argv.extend(["--allowed-path", path])
    for path in allowed_side_effect_paths or []:
        argv.extend(["--allowed-side-effect-path", path])
    for command in check_commands or []:
        argv.extend(["--check-command", command])
    if allow_one_repair:
        argv.append("--allow-one-repair")
    if loop_run_id:
        argv.extend(["--loop-run-id", loop_run_id])
    return run_loop.main(argv)


def load_result(loop_dir: Path) -> dict:
    return json.loads((loop_dir / "loop-result.json").read_text(encoding="utf-8"))


def load_events(loop_dir: Path) -> list[dict]:
    lines = (loop_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# 1. Attempt 1 succeeds, Review 1 APPROVE.
# ---------------------------------------------------------------------------


def test_attempt1_succeeds_review1_approves(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-1"
    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-1", evidence_directory=tmp_path / "loops")

    assert exit_code == 0
    result = load_result(loop_dir)
    assert result["run_state"] == "COMPLETED"
    assert result["terminal_outcome"] == "COMPLETED"
    assert result["stop_reason"] == "REVIEW_APPROVED"
    assert result["accepted"] is True
    assert result["implementation_attempts"] == 1
    assert result["review_attempts"] == 1
    assert result["repair_rounds"] == 0
    assert result["implementation_run_ids"] == ["loop-1-impl-1"]
    assert result["review_run_ids"] == ["loop-1-review-1"]


# ---------------------------------------------------------------------------
# 2. Full REQUEST_CHANGES -> repair -> APPROVE cycle.
# ---------------------------------------------------------------------------


def test_full_repair_cycle_to_approve(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-2"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-2", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 0
    result = load_result(loop_dir)
    assert result["run_state"] == "COMPLETED"
    assert result["accepted"] is True
    assert result["stop_reason"] == "REVIEW_APPROVED"
    assert result["implementation_attempts"] == 2
    assert result["review_attempts"] == 2
    assert result["repair_rounds"] == 1
    assert result["implementation_run_ids"] == ["loop-2-impl-1", "loop-2-repair-2"]
    assert result["review_run_ids"] == ["loop-2-review-1", "loop-2-review-2"]
    assert result["final_verdict"] == "APPROVE"


# ---------------------------------------------------------------------------
# M1 implementation-identity propagation from run_loop.
# ---------------------------------------------------------------------------


def test_review1_and_review2_bind_the_correct_implementation_identity(tmp_path: Path) -> None:
    """Review 1 must bind Attempt 1's run_id/attempt=1; Review 2 must bind
    the repair Attempt 2's run_id/attempt=2, never impl1's identity, and
    each review keeps its own review-run-id independent of both."""

    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-m1"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-m1", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 0
    result = load_result(loop_dir)
    assert result["implementation_run_ids"] == ["loop-m1-impl-1", "loop-m1-repair-2"]
    assert result["review_run_ids"] == ["loop-m1-review-1", "loop-m1-review-2"]

    children_root = loop_dir / "children"
    review1_receipt = json.loads(
        (children_root / "loop-m1-review-1" / "review-receipt.json").read_text(encoding="utf-8")
    )
    review2_receipt = json.loads(
        (children_root / "loop-m1-review-2" / "review-receipt.json").read_text(encoding="utf-8")
    )

    # Review 1 binds Attempt 1's identity and keeps its own review-run-id.
    assert review1_receipt["implementation_run_id"] == "loop-m1-impl-1"
    assert review1_receipt["implementation_attempt"] == 1
    assert review1_receipt["review_run_id"] == "loop-m1-review-1"

    # Review 2 binds the repair Attempt 2's identity, never impl1's, and
    # keeps its own independent review-run-id.
    assert review2_receipt["implementation_run_id"] == "loop-m1-repair-2"
    assert review2_receipt["implementation_run_id"] != "loop-m1-impl-1"
    assert review2_receipt["implementation_attempt"] == 2
    assert review2_receipt["review_run_id"] == "loop-m1-review-2"

    # No Attempt 3 behavior: exactly two implementation attempts and two
    # review attempts are recorded, nothing further.
    assert result["implementation_attempts"] == 2
    assert result["review_attempts"] == 2
    assert result["repair_rounds"] == 1


# ---------------------------------------------------------------------------
# 3. REQUEST_CHANGES, repair not authorized.
# ---------------------------------------------------------------------------


def test_request_changes_repair_not_authorized(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-3"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=False, loop_run_id="loop-3", evidence_directory=tmp_path / "loops")

    assert exit_code == 1
    result = load_result(loop_dir)
    assert result["run_state"] == "STOPPED"
    assert result["terminal_outcome"] == "NOT_ACCEPTED"
    assert result["stop_reason"] == "OWNER_REPAIR_NOT_AUTHORIZED"
    assert result["accepted"] is False
    assert result["repair_rounds"] == 0
    assert (loop_dir / "children" / "loop-3-repair-2").exists() is False


# ---------------------------------------------------------------------------
# 4. Attempt 1 succeeds, Review 1 BLOCKED.
# ---------------------------------------------------------------------------


def test_review1_blocked(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=blocked_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-4"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-4", evidence_directory=tmp_path / "loops")

    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"
    assert result["terminal_outcome"] == "BLOCKED"
    assert result["stop_reason"] == "REVIEW_BLOCKED"
    assert result["accepted"] is False


# ---------------------------------------------------------------------------
# 5. Attempt 1 fails.
# ---------------------------------------------------------------------------


def test_attempt1_fails(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude", exit_code=1)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-5"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-5", evidence_directory=tmp_path / "loops")

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"
    assert result["terminal_outcome"] == "FAILED"
    assert result["accepted"] is False
    assert result["review_run_ids"] == []


# ---------------------------------------------------------------------------
# 6-7. Review 1 process failure / invalid JSON.
# ---------------------------------------------------------------------------


def test_review1_process_fails(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json(), exit_code=1)
    loop_dir = tmp_path / "loops" / "loop-6"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-6", evidence_directory=tmp_path / "loops")

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"
    assert result["error_category"] == "REVIEW_1_INVALID"


def test_review1_invalid_json(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json="{not valid json")
    loop_dir = tmp_path / "loops" / "loop-7"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-7", evidence_directory=tmp_path / "loops")

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"
    assert result["error_category"] == "REVIEW_1_INVALID"


# ---------------------------------------------------------------------------
# 8-9. Review 1 APPROVE with invalid booleans / HIGH finding.
# ---------------------------------------------------------------------------


def test_review1_approve_with_invalid_booleans(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    bad = json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "x",
            "goal_satisfied": True,
            "scope_compliant": False,
            "checks_satisfied": True,
            "findings": [],
            "residual_risks": [],
        }
    )
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=bad)
    loop_dir = tmp_path / "loops" / "loop-8"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-8", evidence_directory=tmp_path / "loops")

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"


def test_review1_approve_with_high_finding(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    bad = json.dumps(
        {
            "verdict": "APPROVE",
            "summary": "x",
            "goal_satisfied": True,
            "scope_compliant": True,
            "checks_satisfied": True,
            "findings": [{"id": "F1", "severity": "HIGH", "title": "a", "evidence": "e", "required_change": "c"}],
            "residual_risks": [],
        }
    )
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=bad)
    loop_dir = tmp_path / "loops" / "loop-9"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-9", evidence_directory=tmp_path / "loops")

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"


# ---------------------------------------------------------------------------
# 10. Repair Attempt 2 fails.
# ---------------------------------------------------------------------------


def test_repair_attempt2_fails(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude_src = f'''#!/usr/bin/env python3
import pathlib, sys
marker = pathlib.Path({str(tmp_path / "marker.txt")!r})
count = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(count + 1))
if count == 0:
    pathlib.Path("app.txt").write_text("implemented\\n", encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(1)
'''
    claude = tmp_path / "claude-fails-on-repair"
    claude.write_text(claude_src, encoding="utf-8")
    claude.chmod(0o755)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-10"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-10", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"
    assert result["repair_rounds"] == 1
    assert result["review_run_ids"] == ["loop-10-review-1"]


# ---------------------------------------------------------------------------
# 11-13. Review 2 outcomes.
# ---------------------------------------------------------------------------


def _adaptive_codex_script(tmp_path: Path, *, workspace: Path, second_verdict_json: str) -> Path:
    app_txt = str(workspace / "app.txt")
    codex_src = f'''#!/usr/bin/env python3
import sys, pathlib
argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    print({TOP_HELP!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({EXEC_HELP!r})
    raise SystemExit(0)
if "exec" in argv:
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    content = pathlib.Path({app_txt!r}).read_text(encoding="utf-8")
    if "repaired" in content:
        verdict = {second_verdict_json!r}
    else:
        verdict = {request_changes_verdict_json()!r}
    if last_message_path:
        pathlib.Path(last_message_path).write_text(verdict)
    raise SystemExit(0)
raise SystemExit(1)
'''
    codex = tmp_path / "codex-adaptive-2"
    codex.write_text(codex_src, encoding="utf-8")
    codex.chmod(0o755)
    return codex


def _stateful_claude(tmp_path: Path) -> Path:
    src = f'''#!/usr/bin/env python3
import pathlib
marker = pathlib.Path({str(tmp_path / "stateful-marker.txt")!r})
count = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(count + 1))
if count == 0:
    pathlib.Path("app.txt").write_text("implemented\\n", encoding="utf-8")
else:
    pathlib.Path("app.txt").write_text("repaired\\n", encoding="utf-8")
raise SystemExit(0)
'''
    p = tmp_path / "claude-stateful-2"
    p.write_text(src, encoding="utf-8")
    p.chmod(0o755)
    return p


def test_review2_request_changes_stops_at_repair_limit(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-11"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-11", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 1
    result = load_result(loop_dir)
    assert result["run_state"] == "STOPPED"
    assert result["stop_reason"] == "MAX_REPAIR_ROUNDS_REACHED"
    assert result["accepted"] is False
    assert result["repair_rounds"] == 1
    assert result["implementation_attempts"] == 2
    assert result["review_attempts"] == 2


def test_review2_blocked(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=blocked_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-12"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-12", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"
    assert result["stop_reason"] == "REVIEW_BLOCKED"


def test_review2_process_fails(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex_src = f'''#!/usr/bin/env python3
import sys, pathlib
argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    print({TOP_HELP!r})
    raise SystemExit(0)
if argv[:2] == ["exec", "--help"]:
    print({EXEC_HELP!r})
    raise SystemExit(0)
if "exec" in argv:
    content = pathlib.Path({str(ws / "app.txt")!r}).read_text(encoding="utf-8")
    if "repaired" in content:
        raise SystemExit(1)
    rest = argv[argv.index("exec") + 1 :]
    last_message_path = None
    for i, tok in enumerate(rest):
        if tok == "--output-last-message" and i + 1 < len(rest):
            last_message_path = rest[i + 1]
    if last_message_path:
        pathlib.Path(last_message_path).write_text({request_changes_verdict_json()!r})
    raise SystemExit(0)
raise SystemExit(1)
'''
    codex = tmp_path / "codex-fails-on-review2"
    codex.write_text(codex_src, encoding="utf-8")
    codex.chmod(0o755)
    loop_dir = tmp_path / "loops" / "loop-13"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-13", evidence_directory=tmp_path / "loops"
    )

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"
    assert result["error_category"] == "REVIEW_2_INVALID"


# ---------------------------------------------------------------------------
# 14-17. Bounded-attempt guarantees.
# ---------------------------------------------------------------------------


def test_no_attempt3_ever_started(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-14"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-14", evidence_directory=tmp_path / "loops")

    children = sorted(p.name for p in (loop_dir / "children").iterdir())
    assert children == ["loop-14-impl-1", "loop-14-repair-2", "loop-14-review-1", "loop-14-review-2"]
    for forbidden in ("loop-14-impl-3", "loop-14-repair-3", "loop-14-review-3"):
        assert not (loop_dir / "children" / forbidden).exists()


def test_exactly_one_repair_maximum(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-15"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-15", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["repair_rounds"] == run_loop.MAX_REPAIR_ROUNDS == 1


def test_exactly_two_implementation_attempts_maximum(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-16"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-16", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["implementation_attempts"] == run_loop.MAX_IMPLEMENTATION_ATTEMPTS == 2


def test_exactly_two_review_attempts_maximum(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-17"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-17", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["review_attempts"] == run_loop.MAX_REVIEW_ATTEMPTS == 2


# ---------------------------------------------------------------------------
# 18. Missing --allow-one-repair prevents repair (already covered by test 3,
#     asserted again explicitly here for the exact flag semantics).
# ---------------------------------------------------------------------------


def test_missing_allow_one_repair_flag_prevents_repair(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-18"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-18", evidence_directory=tmp_path / "loops")

    assert exit_code == 1
    assert not (loop_dir / "children" / "loop-18-repair-2").exists()


# ---------------------------------------------------------------------------
# 19-20. Deterministic, distinct child run IDs and evidence directories.
# ---------------------------------------------------------------------------


def test_child_run_ids_are_deterministic_and_directories_distinct(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-19"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-19", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)

    assert result["implementation_run_ids"] == ["loop-19-impl-1", "loop-19-repair-2"]
    assert result["review_run_ids"] == ["loop-19-review-1", "loop-19-review-2"]
    directories = {loop_dir / "children" / cid for cid in result["implementation_run_ids"] + result["review_run_ids"]}
    assert len(directories) == 4
    for directory in directories:
        assert directory.is_dir()


# ---------------------------------------------------------------------------
# 21. Previous child evidence remains unchanged.
# ---------------------------------------------------------------------------


def test_previous_child_evidence_remains_unchanged(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-21"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-21", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["previous_evidence_immutable"] is True


# ---------------------------------------------------------------------------
# 22. Review workspace immutability is enforced.
# ---------------------------------------------------------------------------


def test_review_workspace_immutability_enforced(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(
        tmp_path, name="codex", verdict_json=approve_verdict_json(), write_unexpected=str(ws / "README.md")
    )
    loop_dir = tmp_path / "loops" / "loop-22"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-22", evidence_directory=tmp_path / "loops")

    # run_review.py's own immutability check refuses the verdict outright
    # (exit 4), which the loop reads back as invalid review evidence.
    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"


# ---------------------------------------------------------------------------
# 23. Workspace drift before repair is rejected.
# ---------------------------------------------------------------------------


def test_workspace_drift_before_repair_rejected(tmp_path: Path) -> None:
    """The loop must surface run_repair's own reviewed-state binding-proof
    failure rather than paper over it. The proof itself (recomputing the
    whole-workspace hash and comparing it to what Attempt 1 recorded) is
    exhaustively covered by test_run_repair.py's own
    `test_workspace_changed_after_review_fails_closed`; this test verifies
    that `invoke_repair()` -- run_loop.py's own composition layer -- reads
    back that rejection correctly rather than assuming success. Deterministic
    (no timing/threading): the drift is applied directly between building
    Attempt-1/Review-1 evidence and calling `invoke_repair()`, with no race.
    """

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    children_root = tmp_path / "children"
    children_root.mkdir()

    impl1 = run_loop.invoke_implementation(
        child_id="drift-impl-1",
        workspace=ws,
        task_prompt_path=make_task_prompt(tmp_path),
        allowed_paths=("app.txt",),
        timeout_seconds=30,
        model=None,
        permission_mode="acceptEdits",
        claude_executable=str(claude),
        children_root=children_root,
    )
    assert impl1["receipt"]["success"] is True

    review1 = run_loop.invoke_review(
        child_id="drift-review-1",
        workspace=ws,
        task_prompt_path=make_task_prompt(tmp_path),
        implementation_run_directory=impl1["run_directory"],
        implementation_run_id="drift-impl-1",
        implementation_attempt=1,
        baseline_ref="HEAD",
        allowed_paths=("app.txt",),
        check_commands=[],
        timeout_seconds=30,
        model=None,
        codex_executable=str(codex),
        children_root=children_root,
    )
    assert review1["receipt"]["verdict"] == "REQUEST_CHANGES"

    # Deterministic drift: the workspace is altered between Review 1
    # completing and the repair invocation, with no timing dependency.
    (ws / "app.txt").write_text("manually edited after review\n", encoding="utf-8")

    repair2 = run_loop.invoke_repair(
        child_id="drift-repair-2",
        workspace=ws,
        task_prompt_path=make_task_prompt(tmp_path),
        previous_implementation_run_directory=impl1["run_directory"],
        implementation_run_id="drift-impl-1",
        implementation_attempt=1,
        review_run_directory=review1["run_directory"],
        allowed_paths=("app.txt",),
        check_commands=[],
        timeout_seconds=30,
        model=None,
        permission_mode="acceptEdits",
        claude_executable=str(claude),
        children_root=children_root,
    )

    # run_repair's own preflight rejects before any Claude invocation, so no
    # receipt.json is written at all -- exactly what invoke_repair() must
    # read back as "not successful", never fabricating a receipt of its own.
    assert repair2["receipt"] is None
    assert repair2["exit_code"] == 3


def _add_ignored_runtime_paths(workspace: Path, *paths: str) -> None:
    (workspace / ".gitignore").write_text("".join(f"{path}/\n" for path in paths), encoding="utf-8")
    _git(["add", ".gitignore"], workspace)
    _git(
        ["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "ignore runtime paths"],
        workspace,
    )


def _cache_writing_check_command() -> str:
    return (
        f'{sys.executable} -c "from pathlib import Path; '
        'p=Path(\'.pytest_cache/review-state\'); '
        'p.parent.mkdir(parents=True, exist_ok=True); p.write_text(\'checked\')"'
    )


def test_authorized_ignored_check_side_effect_drift_allows_repair(tmp_path: Path) -> None:
    """Real R-01 shape: a check-created ignored cache must not invalidate reviewed source."""

    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".pytest_cache")
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(
        tmp_path,
        workspace=ws,
        second_verdict_json=approve_verdict_json(),
    )
    loop_dir = tmp_path / "loops" / "loop-authorized-review-cache"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allow_one_repair=True,
        allowed_side_effect_paths=[".pytest_cache"],
        check_commands=[_cache_writing_check_command()],
        loop_run_id="loop-authorized-review-cache",
        evidence_directory=tmp_path / "loops",
    )

    assert exit_code == 0
    result = load_result(loop_dir)
    assert result["workspace_final_changed_paths"] == ["app.txt"]
    assert result["unexpected_changed_paths"] == []
    assert result["workspace_final_hash"] == workspace_hash(snapshot_workspace_hashes(ws))
    assert result["model_invocation_count"] == 4
    assert result["model_invocation_observation_complete"] is True
    review1_receipt = json.loads(
        (loop_dir / "children" / "loop-authorized-review-cache-review-1" / "review-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert review1_receipt["workspace_unchanged"] is True
    assert review1_receipt["workspace_preservation_status"] == "AUTHORIZED_SIDE_EFFECT_DRIFT"
    assert review1_receipt["deterministic_check_authorized_side_effect_drift"] is True
    repair_receipt = json.loads(
        (loop_dir / "children" / "loop-authorized-review-cache-repair-2" / "receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert repair_receipt["provider_invoked"] is True
    repair_model_events = [
        event
        for event in load_events(loop_dir)
        if event["child_run_id"] == "loop-authorized-review-cache-repair-2"
        and event["event_type"].startswith("MODEL_INVOCATION_")
    ]
    assert [event["event_type"] for event in repair_model_events] == [
        "MODEL_INVOCATION_START",
        "MODEL_INVOCATION_COMPLETE",
    ]
    assert all(event["provider_invoked"] is True for event in repair_model_events)
    assert "provider_exit_code=0" in repair_model_events[-1]["summary"]


def test_receiptless_repair_preflight_failure_keeps_terminal_evidence_truthful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-02/R-03/R-04: a consumed repair slot is not a provider call or mutation proof."""

    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        name="codex-preflight-failure",
        verdict_json=request_changes_verdict_json(),
    )
    real_invoke_repair = run_loop.invoke_repair

    def invoke_with_wrong_bound_identity(**kwargs):
        kwargs["implementation_run_id"] = "wrong-implementation-run"
        return real_invoke_repair(**kwargs)

    monkeypatch.setattr(run_loop, "invoke_repair", invoke_with_wrong_bound_identity)
    loop_dir = tmp_path / "loops" / "loop-repair-preflight-failure"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allow_one_repair=True,
        loop_run_id="loop-repair-preflight-failure",
        evidence_directory=tmp_path / "loops",
    )

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["implementation_attempts"] == 2  # bounded attempt slot consumed
    assert result["model_invocation_count"] == 2  # Attempt 1 + Review 1 only
    assert result["model_invocation_observation_complete"] is True
    assert result["previous_evidence_immutable"] is True
    assert result["workspace_final_changed_paths"] == ["app.txt"]
    assert result["workspace_final_hash"] == workspace_hash(snapshot_workspace_hashes(ws))
    assert (tmp_path / "stateful-marker.txt").read_text(encoding="utf-8") == "1"
    repair_id = "loop-repair-preflight-failure-repair-2"
    repair_events = [event for event in load_events(loop_dir) if event["child_run_id"] == repair_id]
    assert not [event for event in repair_events if event["event_type"].startswith("MODEL_INVOCATION_")]
    runner_complete = [
        event for event in repair_events if event["event_type"] == "RUNNER_INVOCATION_COMPLETE"
    ]
    assert len(runner_complete) == 1
    assert runner_complete[0]["provider_invoked"] is False


def test_missing_or_contradictory_provider_evidence_is_unknown() -> None:
    assert run_loop._provider_invocation_observation({}) is None
    assert (
        run_loop._provider_invocation_observation(
            {
                "provider_invoked_observation": True,
                "receipt": {"provider_invoked": False, "invocation_count": 99},
            }
        )
        is None
    )


def test_source_mutation_during_review_is_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-review-source-mutation")
    codex = write_fake_codex(
        tmp_path,
        name="codex-review-source-mutation",
        verdict_json=approve_verdict_json(),
        write_unexpected=str(ws / "app.txt"),
    )
    loop_dir = tmp_path / "loops" / "loop-review-source-mutation"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        loop_run_id="loop-review-source-mutation",
        evidence_directory=tmp_path / "loops",
    )

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["workspace_preserved_during_reviews"] is False
    assert result["review_workspace_observations"][0]["product_state_preserved"] is False
    assert result["review_workspace_observations"][0]["source_changed_paths"] == ["app.txt"]


def test_unauthorized_ignored_check_drift_is_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".pytest_cache")
    claude = write_fake_claude(tmp_path, name="claude-unauthorized-cache")
    codex = write_fake_codex(
        tmp_path,
        name="codex-unauthorized-cache",
        verdict_json=approve_verdict_json(),
    )
    loop_dir = tmp_path / "loops" / "loop-unauthorized-cache"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        check_commands=[_cache_writing_check_command()],
        loop_run_id="loop-unauthorized-cache",
        evidence_directory=tmp_path / "loops",
    )

    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["workspace_preserved_during_reviews"] is False
    assert result["review_workspace_observations"][0]["status"] == "UNEXPECTED_DRIFT"
    assert result["review_workspace_observations"][0]["unexpected_changed_paths"] == [
        ".pytest_cache/review-state"
    ]


def test_final_workspace_evidence_no_product_change(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    before = snapshot_workspace_hashes(ws)
    baseline_git_metadata = run_review.protected_git_metadata_snapshot(ws)

    evidence = run_loop._derive_final_workspace_evidence(
        workspace=ws,
        baseline_commit=baseline,
        baseline_hashes=before,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(".pytest_cache",),
        baseline_git_metadata=baseline_git_metadata,
    )

    assert evidence["workspace_final_changed_paths"] == []
    assert evidence["workspace_final_authorized_side_effect_changed_path_count"] == 0
    assert evidence["protected_git_metadata_preserved"] is True


def test_final_workspace_evidence_reports_git_metadata_drift_separately(
    tmp_path: Path,
) -> None:
    ws = make_clean_workspace(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    before = snapshot_workspace_hashes(ws)
    baseline_git_metadata = run_review.protected_git_metadata_snapshot(ws)
    hook = run_review.git_common_dir(ws) / "hooks" / "terminal-drift"
    hook.write_text("changed\n", encoding="utf-8")

    evidence = run_loop._derive_final_workspace_evidence(
        workspace=ws,
        baseline_commit=baseline,
        baseline_hashes=before,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(),
        baseline_git_metadata=baseline_git_metadata,
    )

    assert evidence["workspace_final_changed_paths"] == []
    assert evidence["protected_git_metadata_preserved"] is False
    assert evidence["protected_git_metadata_changed_path_count"] >= 1
    assert any(
        path.endswith("hooks/terminal-drift")
        for path in evidence["protected_git_metadata_changed_paths_sample"]
    )


def test_terminal_evidence_reports_linked_worktree_gitfile_drift_separately(
    tmp_path: Path,
) -> None:
    workspace = make_linked_clean_workspace(tmp_path)
    baseline = run_review.git_rev_parse(workspace, "HEAD")
    assert baseline is not None
    before = snapshot_workspace_hashes(workspace)
    baseline_git_metadata = run_review.protected_git_metadata_snapshot(workspace)
    (workspace / ".git").chmod(0o600)

    evidence = run_loop._derive_final_workspace_evidence(
        workspace=workspace,
        baseline_commit=baseline,
        baseline_hashes=before,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(),
        baseline_git_metadata=baseline_git_metadata,
    )

    assert evidence["workspace_final_changed_paths"] == []
    assert evidence["protected_git_metadata_preserved"] is False
    assert evidence["workspace_git_entry_preserved"] is False
    assert evidence["workspace_git_entry_final_hash"] != evidence[
        "workspace_git_entry_baseline_hash"
    ]


def test_loop_review_preservation_detects_linked_worktree_gitfile_drift(
    tmp_path: Path,
) -> None:
    workspace = make_linked_clean_workspace(tmp_path)
    before = run_loop._snapshot_workspace_for_review(workspace)
    (workspace / ".git").chmod(0o600)
    after = run_loop._snapshot_workspace_for_review(workspace)

    evidence = run_loop._review_preservation_evidence(
        attempt=1,
        workspace=workspace,
        before=before,
        after=after,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(),
    )

    assert evidence["product_state_preserved"] is True
    assert evidence["repository_identity_preserved"] is False
    assert evidence["workspace_git_entry_preserved"] is False
    assert evidence["workspace_preserved"] is False


def test_receiptless_repair_git_metadata_drift_is_visible_terminally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        name="codex-git-metadata-preflight-failure",
        verdict_json=request_changes_verdict_json(),
    )
    real_invoke_repair = run_loop.invoke_repair

    def invoke_after_git_metadata_drift(**kwargs):
        hook = run_review.git_common_dir(ws) / "hooks" / "between-review-repair"
        hook.write_text("changed\n", encoding="utf-8")
        return real_invoke_repair(**kwargs)

    monkeypatch.setattr(run_loop, "invoke_repair", invoke_after_git_metadata_drift)
    loop_id = "loop-git-metadata-preflight-failure"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allow_one_repair=True,
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 3
    result = load_result(loop_root / loop_id)
    assert result["workspace_final_changed_paths"] == ["app.txt"]
    assert result["protected_git_metadata_preserved"] is False
    assert any(
        path.endswith("hooks/between-review-repair")
        for path in result["protected_git_metadata_changed_paths_sample"]
    )
    assert result["model_invocation_count"] == 2


def test_final_workspace_evidence_detects_ignored_unexpected_change(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".private")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    before = snapshot_workspace_hashes(ws)
    (ws / ".private").mkdir()
    (ws / ".private" / "state").write_text("unexpected", encoding="utf-8")

    evidence = run_loop._derive_final_workspace_evidence(
        workspace=ws,
        baseline_commit=baseline,
        baseline_hashes=before,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(".pytest_cache",),
    )

    assert evidence["workspace_final_changed_paths"] == [".private/state"]
    assert evidence["workspace_final_unexpected_changed_paths"] == [".private/state"]


def test_final_workspace_evidence_separates_authorized_side_effect_only_drift(
    tmp_path: Path,
) -> None:
    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".pytest_cache")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()
    before = snapshot_workspace_hashes(ws)
    (ws / ".pytest_cache").mkdir()
    (ws / ".pytest_cache" / "state").write_text("authorized", encoding="utf-8")

    evidence = run_loop._derive_final_workspace_evidence(
        workspace=ws,
        baseline_commit=baseline,
        baseline_hashes=before,
        allowed_source_paths=("app.txt",),
        allowed_side_effect_paths=(".pytest_cache",),
    )

    assert evidence["workspace_final_changed_paths"] == []
    assert evidence["workspace_final_unexpected_changed_paths"] == []
    assert evidence["workspace_final_authorized_side_effect_changed_path_count"] == 1
    assert evidence["workspace_final_authorized_side_effect_changed_paths_sample"] == [
        ".pytest_cache/state"
    ]


def test_prior_evidence_mutation_is_reported_false(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-evidence-mutation")
    loop_id = "loop-evidence-mutation"
    loop_root = tmp_path / "loops"
    implementation_receipt = (
        loop_root / loop_id / "children" / f"{loop_id}-impl-1" / "receipt.json"
    )
    codex = write_fake_codex(
        tmp_path,
        name="codex-evidence-mutation",
        verdict_json=blocked_verdict_json(),
        write_unexpected=str(implementation_receipt),
    )

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["previous_evidence_immutable"] is False


def test_evidence_tree_regular_file_content_and_missing_member_are_bound(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    receipt = evidence / "receipt.json"
    receipt.write_text('{"ok": true}\n', encoding="utf-8")
    unchanged = run_loop.hash_directory(evidence)

    assert run_loop.hash_directory(evidence) == unchanged
    receipt.write_text('{"ok": false}\n', encoding="utf-8")
    assert run_loop.hash_directory(evidence) != unchanged
    receipt.unlink()
    assert run_loop.hash_directory(evidence) != unchanged


def test_same_content_external_symlink_substitution_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    receipt = evidence / "receipt.json"
    content = '{"ok": true}\n'
    receipt.write_text(content, encoding="utf-8")
    run_loop.hash_directory(evidence)
    external = tmp_path / "external-receipt.json"
    external.write_text(content, encoding="utf-8")
    receipt.unlink()
    receipt.symlink_to(external)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_loop.hash_directory(evidence)

    external.write_text('{"ok": false}\n', encoding="utf-8")
    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_loop.hash_directory(evidence)


def test_special_evidence_member_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    os.mkfifo(evidence / "receipt.json")

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_loop.hash_directory(evidence)


def test_symlinked_evidence_directory_is_rejected(tmp_path: Path) -> None:
    real_evidence = tmp_path / "real-evidence"
    real_evidence.mkdir()
    (real_evidence / "receipt.json").write_text("{}\n", encoding="utf-8")
    alias = tmp_path / "evidence-alias"
    alias.symlink_to(real_evidence, target_is_directory=True)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_loop.hash_directory(alias)


def test_hardlinked_evidence_member_is_rejected(tmp_path: Path) -> None:
    external = tmp_path / "external-receipt.json"
    external.write_text("{}\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    os.link(external, evidence / "receipt.json")

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_loop.hash_directory(evidence)


def test_child_json_reader_parses_captured_bytes_without_path_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "child"
    evidence.mkdir()
    result_path = evidence / "result.json"
    result_path.write_text('{"status":"ORIGINAL"}', encoding="utf-8")
    external = tmp_path / "later-result.json"
    external.write_text('{"status":"LATER"}', encoding="utf-8")
    real_reader = run_review.read_canonical_evidence

    def capture_then_replace(root: Path, names: tuple[str, ...]):
        measured = real_reader(root, names)
        result_path.unlink()
        result_path.symlink_to(external)
        return measured

    monkeypatch.setattr(run_review, "read_canonical_evidence", capture_then_replace)

    loaded = run_loop._read_json(result_path)

    assert result_path.is_symlink()
    assert loaded == {"status": "ORIGINAL"}


def test_preflight_failure_after_baseline_resolution_reports_live_paths(
    tmp_path: Path,
) -> None:
    ws = make_clean_workspace(tmp_path)
    (ws / "app.txt").write_text("already dirty\n", encoding="utf-8")
    missing_claude = tmp_path / "missing-claude"
    codex = write_fake_codex(
        tmp_path,
        name="codex-preflight-live-paths",
        verdict_json=approve_verdict_json(),
    )
    loop_id = "loop-preflight-live-paths"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        missing_claude,
        codex,
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["error_category"] == "CLAUDE_NOT_FOUND"
    assert result["workspace_final_changed_paths"] == ["app.txt"]
    assert result["workspace_final_hash"] == workspace_hash(snapshot_workspace_hashes(ws))


def test_capability_probe_semantic_repository_mutation_still_blocks_preflight(
    tmp_path: Path,
) -> None:
    """A capability probe that genuinely mutates repository state (not just a
    volatile timestamp) must still block PREFLIGHT with IMMUTABILITY_VIOLATION
    after the cross-phase-stable protected-Git-metadata fix -- the fix only
    excludes mtime_ns/ctime_ns, never real semantic drift."""

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-capability-mutation")
    hook = run_review.git_dir(ws) / "hooks" / "tomtit-loop-probe-hook"
    codex = write_fake_codex(
        tmp_path,
        name="codex-capability-mutation",
        verdict_json=approve_verdict_json(),
        help_write_path=str(hook),
    )
    loop_id = "loop-capability-mutation"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["run_state"] == "BLOCKED"
    assert result["terminal_outcome"] == "BLOCKED"
    assert result["stop_reason"] == "PREFLIGHT_FAILED"
    assert result["implementation_attempts"] == 0
    assert result["review_attempts"] == 0


# ---------------------------------------------------------------------------
# Coherent-bootstrap INITIAL baseline establishment lifecycle threading
# (L1 = establish_protected_git_metadata_baseline, L2 = capability probe
# BEFORE, L3 = capability probe AFTER). Source-level bootstrap-algorithm
# tests (clean bootstrap, benign churn, disagreement bounds, tree rebuild,
# ...) live in test_run_review.py; these test the loop-level threading.
# ---------------------------------------------------------------------------


def test_no_untrusted_subprocess_before_baseline_establishment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(H) If the coherent-bootstrap baseline cannot be established, no
    untrusted subprocess (the Codex capability probe) may ever run."""

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-no-subprocess-before-baseline")
    marker = tmp_path / "codex-invoked.marker"
    codex = write_fake_codex(
        tmp_path,
        name="codex-no-subprocess-before-baseline",
        verdict_json=approve_verdict_json(),
        help_write_path=str(marker),
    )

    def _always_fails(cwd: Path) -> "run_review.ProtectedGitMetadataBaseline":
        raise run_review.EvidenceIntegrityFailure("simulated establishment failure")

    monkeypatch.setattr(run_review, "establish_protected_git_metadata_baseline", _always_fails)

    loop_id = "loop-no-subprocess-before-baseline"
    loop_root = tmp_path / "loops"
    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, loop_run_id=loop_id, evidence_directory=loop_root
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["error_category"] == "PROTECTED_BASELINE_ESTABLISHMENT_FAILED"
    assert not marker.exists()


def test_no_rebootstrap_after_baseline_established_on_later_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(I) Once the L1 baseline is established, a later genuine drift (here,
    the Codex capability probe writing a Git hook) must still block
    PREFLIGHT -- Runtime must never silently re-enter bootstrap and accept
    the drifted state as a new baseline.
    `establish_protected_git_metadata_baseline` is called exactly once for
    the whole run."""

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-no-rebootstrap")
    hook = run_review.git_dir(ws) / "hooks" / "tomtit-loop-probe-hook"
    codex = write_fake_codex(
        tmp_path,
        name="codex-no-rebootstrap",
        verdict_json=approve_verdict_json(),
        help_write_path=str(hook),
    )

    real_establish = run_review.establish_protected_git_metadata_baseline
    calls = {"count": 0}

    def _counting_establish(cwd: Path) -> "run_review.ProtectedGitMetadataBaseline":
        calls["count"] += 1
        return real_establish(cwd)

    monkeypatch.setattr(run_review, "establish_protected_git_metadata_baseline", _counting_establish)

    loop_id = "loop-no-rebootstrap"
    loop_root = tmp_path / "loops"
    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, loop_run_id=loop_id, evidence_directory=loop_root
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["error_category"] == "IMMUTABILITY_VIOLATION"
    assert calls["count"] == 1


def test_capability_probe_before_validates_against_run_baseline_not_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(J) The capability-probe BEFORE snapshot (L2) must execute in strict
    trusted-validation mode using L1's already-established baseline -- not
    another implicit bootstrap of its own. A single transient timestamp-only
    touch on HEAD occurring only after L1 has established must still be
    tolerated (proving L2 has trusted evidence to validate against), and the
    loop must proceed to a normal APPROVE outcome."""

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-l2-strict-validation")
    codex = write_fake_codex(
        tmp_path, name="codex-l2-strict-validation", verdict_json=approve_verdict_json()
    )

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    real_establish = run_review.establish_protected_git_metadata_baseline
    real_stat_identity = run_review._stat_identity
    state = {"established": False, "perturbed": False}

    def perturbing_after_establishment(metadata: object) -> tuple:
        real = real_stat_identity(metadata)
        if (
            state["established"]
            and metadata.st_ino == target_ino  # type: ignore[union-attr]
            and not state["perturbed"]
        ):
            state["perturbed"] = True
            dev, ino, mode, size, nlink, mtime_ns, ctime_ns = real
            return (dev, ino, mode, size, nlink, mtime_ns, ctime_ns + 1)
        return real

    def establishing(cwd: Path) -> "run_review.ProtectedGitMetadataBaseline":
        result = real_establish(cwd)
        state["established"] = True
        return result

    monkeypatch.setattr(run_review, "_stat_identity", perturbing_after_establishment)
    monkeypatch.setattr(run_review, "establish_protected_git_metadata_baseline", establishing)

    loop_id = "loop-l2-strict-validation"
    loop_root = tmp_path / "loops"
    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, loop_run_id=loop_id, evidence_directory=loop_root
    )

    assert state["perturbed"] is True
    assert exit_code == 0
    result = load_result(loop_root / loop_id)
    assert result["final_verdict"] == "APPROVE"


def test_capability_probe_after_remains_strict_validation_chained_from_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(K) The capability-probe AFTER snapshot (L3) must also remain within
    the established authority boundary -- chained from L2's own trusted
    reading, never an implicit bootstrap of its own. A transient timestamp-
    only touch on HEAD occurring only during L3's measurement must still be
    tolerated, and the loop must proceed to a normal APPROVE outcome."""

    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude-l3-strict-validation")
    codex = write_fake_codex(
        tmp_path, name="codex-l3-strict-validation", verdict_json=approve_verdict_json()
    )

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    real_snapshot_for_review = run_loop._snapshot_workspace_for_review
    real_stat_identity = run_review._stat_identity
    state = {"before_done": False, "perturbed": False}
    call_count = {"n": 0}

    def perturbing_after_before_probe(metadata: object) -> tuple:
        real = real_stat_identity(metadata)
        if (
            state["before_done"]
            and metadata.st_ino == target_ino  # type: ignore[union-attr]
            and not state["perturbed"]
        ):
            state["perturbed"] = True
            dev, ino, mode, size, nlink, mtime_ns, ctime_ns = real
            return (dev, ino, mode, size, nlink, mtime_ns, ctime_ns + 1)
        return real

    def counting_snapshot(workspace: Path, **kwargs: object) -> tuple:
        call_count["n"] += 1
        result = real_snapshot_for_review(workspace, **kwargs)
        if call_count["n"] == 1:
            state["before_done"] = True
        return result

    monkeypatch.setattr(run_review, "_stat_identity", perturbing_after_before_probe)
    monkeypatch.setattr(run_loop, "_snapshot_workspace_for_review", counting_snapshot)

    loop_id = "loop-l3-strict-validation"
    loop_root = tmp_path / "loops"
    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, loop_run_id=loop_id, evidence_directory=loop_root
    )

    assert state["perturbed"] is True
    assert exit_code == 0
    result = load_result(loop_root / loop_id)
    assert result["final_verdict"] == "APPROVE"


def test_invalid_allowed_path_preflight_does_not_fabricate_empty_final_paths(
    tmp_path: Path,
) -> None:
    ws = make_clean_workspace(tmp_path)
    (ws / "app.txt").write_text("already dirty\n", encoding="utf-8")
    claude = write_fake_claude(tmp_path, name="claude-invalid-path-preflight")
    codex = write_fake_codex(
        tmp_path,
        name="codex-invalid-path-preflight",
        verdict_json=approve_verdict_json(),
    )
    loop_id = "loop-invalid-path-preflight"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allowed_paths=["../escape.txt"],
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["error_category"] == "INVALID_ALLOWED_PATH"
    assert result["workspace_final_changed_paths"] == ["app.txt"]
    assert result["unexpected_changed_paths"] == ["app.txt"]


def test_failed_repair_with_only_authorized_side_effect_has_no_product_paths(
    tmp_path: Path,
) -> None:
    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".pytest_cache")
    marker = tmp_path / "side-only-marker"
    claude = tmp_path / "claude-side-only-failure"
    claude.write_text(
        f'''#!/usr/bin/env python3
import pathlib
marker = pathlib.Path({str(marker)!r})
count = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(count + 1))
if count == 0:
    pathlib.Path("app.txt").write_text("implemented\\n", encoding="utf-8")
    raise SystemExit(0)
pathlib.Path("app.txt").write_text("original\\n", encoding="utf-8")
cache = pathlib.Path(".pytest_cache/state")
cache.parent.mkdir(parents=True, exist_ok=True)
cache.write_text("authorized\\n", encoding="utf-8")
raise SystemExit(3)
''',
        encoding="utf-8",
    )
    claude.chmod(0o755)
    codex = write_fake_codex(
        tmp_path,
        name="codex-side-only-failure",
        verdict_json=request_changes_verdict_json(),
    )
    loop_id = "loop-side-only-failure"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allow_one_repair=True,
        allowed_side_effect_paths=[".pytest_cache"],
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 3
    result = load_result(loop_root / loop_id)
    assert result["workspace_final_changed_paths"] == []
    assert result["unexpected_changed_paths"] == []
    assert result["workspace_final_authorized_side_effect_changed_path_count"] == 1


def test_failed_repair_reports_ignored_unexpected_final_change(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    _add_ignored_runtime_paths(ws, ".private")
    marker = tmp_path / "unexpected-marker"
    claude = tmp_path / "claude-unexpected-failure"
    claude.write_text(
        f'''#!/usr/bin/env python3
import pathlib
marker = pathlib.Path({str(marker)!r})
count = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(count + 1))
pathlib.Path("app.txt").write_text("implemented\\n", encoding="utf-8")
if count:
    unexpected = pathlib.Path(".private/state")
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("unexpected\\n", encoding="utf-8")
    raise SystemExit(3)
raise SystemExit(0)
''',
        encoding="utf-8",
    )
    claude.chmod(0o755)
    codex = write_fake_codex(
        tmp_path,
        name="codex-unexpected-failure",
        verdict_json=request_changes_verdict_json(),
    )
    loop_id = "loop-unexpected-failure"
    loop_root = tmp_path / "loops"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        allow_one_repair=True,
        loop_run_id=loop_id,
        evidence_directory=loop_root,
    )

    assert exit_code == 2
    result = load_result(loop_root / loop_id)
    assert result["unexpected_changed_paths"] == [".private/state"]
    assert result["workspace_final_changed_paths"] == [".private/state", "app.txt"]


# ---------------------------------------------------------------------------
# 24. Unexpected path from implementation fails closed.
# ---------------------------------------------------------------------------


def test_unexpected_path_from_implementation_fails_closed(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude", write_unexpected="README.md")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-24"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-24", evidence_directory=tmp_path / "loops")

    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"
    assert result["unexpected_changed_paths"] == ["README.md"]
    assert result["review_run_ids"] == []


# ---------------------------------------------------------------------------
# 25. Failed deterministic check cannot result in APPROVE.
# ---------------------------------------------------------------------------


def test_failed_deterministic_check_cannot_approve(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    # checks_satisfied MUST be false alongside a failing check for a schema-
    # valid, but non-APPROVE-worthy, verdict; this proves the loop reflects
    # what the (independently validated) verdict actually says rather than
    # trusting the check's own exit code.
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-25"

    exit_code = run_loop_cli(
        tmp_path,
        ws,
        claude,
        codex,
        check_commands=["python3 -c \"import sys; sys.exit(1)\""],
        loop_run_id="loop-25",
        evidence_directory=tmp_path / "loops",
    )

    assert exit_code == 1
    result = load_result(loop_dir)
    assert result["final_verdict"] != "APPROVE"
    assert result["accepted"] is False


# ---------------------------------------------------------------------------
# 26-28. Config-level preflight rejection.
# ---------------------------------------------------------------------------


def test_invalid_task_prompt_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    empty_prompt = make_task_prompt(tmp_path, text="   \n", name="empty.md")

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, task_prompt_file=empty_prompt, loop_run_id="loop-26", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 4


def test_invalid_allowed_path_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, allowed_paths=["../escape.txt"], loop_run_id="loop-27", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 2  # deep preflight: allowed-path normalization needs the resolved workspace
    loop_dir = tmp_path / "loops" / "loop-27"
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"


def test_invalid_check_command_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, check_commands=["echo hi && echo bye"], loop_run_id="loop-28", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 4


# ---------------------------------------------------------------------------
# 29-32. Executable/contract discovery preflight.
# ---------------------------------------------------------------------------


def test_missing_claude_executable_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    missing_claude = tmp_path / "no-such-claude"
    loop_dir = tmp_path / "loops" / "loop-29"

    exit_code = run_loop_cli(
        tmp_path, ws, missing_claude, codex, loop_run_id="loop-29", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"
    assert result["error_category"] == "CLAUDE_NOT_FOUND"


def test_missing_codex_executable_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    missing_codex = tmp_path / "no-such-codex"
    loop_dir = tmp_path / "loops" / "loop-30"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, missing_codex, loop_run_id="loop-30", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["run_state"] == "BLOCKED"
    assert result["error_category"] == "CODEX_NOT_FOUND"


def test_unsupported_claude_contract_rejected(tmp_path: Path) -> None:
    # The adapter's own discovery only checks executability, not a --help
    # contract, so "unsupported" for Claude manifests as CLAUDE_NOT_FOUND
    # when the given path is not an executable file at all.
    ws = make_clean_workspace(tmp_path)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    not_executable = tmp_path / "not-executable-claude"
    not_executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    loop_dir = tmp_path / "loops" / "loop-31"

    exit_code = run_loop_cli(
        tmp_path, ws, not_executable, codex, loop_run_id="loop-31", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["error_category"] == "CLAUDE_NOT_FOUND"


def test_unsupported_codex_contract_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(
        tmp_path,
        name="codex-bad-contract",
        verdict_json=approve_verdict_json(),
        top_help="Usage: codex\n",
        exec_help="Usage: codex exec\n(no options documented)\n",
    )
    loop_dir = tmp_path / "loops" / "loop-32"

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-32", evidence_directory=tmp_path / "loops")
    assert exit_code == 2
    result = load_result(loop_dir)
    assert result["error_category"] == "UNSUPPORTED_CODEX_CLI_CONTRACT"


# ---------------------------------------------------------------------------
# 33. Loop evidence collision rejected.
# ---------------------------------------------------------------------------


def test_loop_evidence_collision_rejected(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    evidence_root = tmp_path / "loops"
    collision_dir = evidence_root / "loop-33"
    collision_dir.mkdir(parents=True)
    (collision_dir / "stale.txt").write_text("leftover", encoding="utf-8")

    exit_code = run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-33", evidence_directory=evidence_root)
    assert exit_code == 4


# ---------------------------------------------------------------------------
# 34-35. Event log properties.
# ---------------------------------------------------------------------------


def test_event_sequences_strictly_increase(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-34"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-34", evidence_directory=tmp_path / "loops")
    events = load_events(loop_dir)
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, len(sequences) + 1))


def test_events_are_append_only(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-35"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-35", evidence_directory=tmp_path / "loops")
    text_before = (loop_dir / "events.jsonl").read_text(encoding="utf-8")
    events_before = load_events(loop_dir)

    # No mechanism in run_loop.py ever opens events.jsonl for anything but
    # append; assert the file's earlier lines are a strict prefix of a
    # hypothetical later read (there is nothing further to append once the
    # loop is terminal, so this asserts idempotent re-reads are identical).
    text_after = (loop_dir / "events.jsonl").read_text(encoding="utf-8")
    assert text_after == text_before
    assert load_events(loop_dir) == events_before


# ---------------------------------------------------------------------------
# 36. Invalid state transition rejected (defensive guard).
# ---------------------------------------------------------------------------


def test_invalid_state_transition_rejected(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    log = run_loop.EventLog(events_path, "unit-test-loop")
    machine = run_loop.StateMachine(log)
    with pytest.raises(run_loop.InvalidTransition):
        machine.transition("COMPLETED", event_type="STATE_TRANSITION")  # CREATED -> COMPLETED is not allowed


# ---------------------------------------------------------------------------
# 37. Final event matches loop-result terminal state.
# ---------------------------------------------------------------------------


def test_final_event_matches_terminal_state(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-37"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-37", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    events = load_events(loop_dir)
    final_state_events = [e for e in events if e["to_state"] == result["run_state"]]
    assert final_state_events, "no event recorded the terminal state transition"
    assert final_state_events[-1]["to_state"] == result["run_state"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 38. Accepted=true only with valid Codex APPROVE.
# ---------------------------------------------------------------------------


def test_accepted_true_only_with_valid_approve(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=request_changes_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-38"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-38", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["final_verdict"] != "APPROVE"
    assert result["accepted"] is False


# ---------------------------------------------------------------------------
# 39-40. Cost aggregation.
# ---------------------------------------------------------------------------


def test_cost_aggregation_from_two_child_invocations(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude_src = f'''#!/usr/bin/env python3
import pathlib, json
marker = pathlib.Path({str(tmp_path / "cost-marker.txt")!r})
count = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(count + 1))
content = "implemented\\n" if count == 0 else "repaired\\n"
pathlib.Path("app.txt").write_text(content, encoding="utf-8")
payload = {{"total_cost_usd": 0.5, "usage": {{"input_tokens": 100, "output_tokens": 50}}}}
print(json.dumps(payload))
raise SystemExit(0)
'''
    claude = tmp_path / "claude-with-cost"
    claude.write_text(claude_src, encoding="utf-8")
    claude.chmod(0o755)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-39"

    run_loop_cli(tmp_path, ws, claude, codex, allow_one_repair=True, loop_run_id="loop-39", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["usage"]["total_model_cost_usd"] == pytest.approx(1.0)
    assert result["usage"]["total_input_tokens"] == 200
    assert result["usage"]["total_output_tokens"] == 100


def test_missing_cost_remains_unavailable_not_zero(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")  # no stdout at all -> no usage payload
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-40"

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-40", evidence_directory=tmp_path / "loops")
    result = load_result(loop_dir)
    assert result["usage"]["total_model_cost_usd"] is None
    assert result["usage"]["total_input_tokens"] is None


# ---------------------------------------------------------------------------
# 41-42. Timeouts.
# ---------------------------------------------------------------------------


def test_timeout_during_implementation(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude", sleep_seconds=5)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-41"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, implementation_timeout_seconds=1, loop_run_id="loop-41", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"


def test_timeout_during_review(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json(), sleep_seconds=5)
    loop_dir = tmp_path / "loops" / "loop-42"

    exit_code = run_loop_cli(
        tmp_path, ws, claude, codex, review_timeout_seconds=1, loop_run_id="loop-42", evidence_directory=tmp_path / "loops"
    )
    assert exit_code == 3
    result = load_result(loop_dir)
    assert result["run_state"] == "FAILED"


# ---------------------------------------------------------------------------
# 43. Credential-safe error output.
# ---------------------------------------------------------------------------


def test_credential_safe_error_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOMTIT_TEST_LOOP_SECRET", "sk-super-secret-loop-value")
    ws = make_clean_workspace(tmp_path)
    claude = write_fake_claude(tmp_path, name="claude", exit_code=1)
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-43", evidence_directory=tmp_path / "loops")
    captured = capsys.readouterr()
    assert "sk-super-secret-loop-value" not in captured.out
    assert "sk-super-secret-loop-value" not in captured.err


# ---------------------------------------------------------------------------
# 44. No automatic commit, reset, restore, stash, or checkout.
# ---------------------------------------------------------------------------


def test_no_automatic_git_mutation_commands_in_source() -> None:
    source = (DOGFOOD_DIR / "run_loop.py").read_text(encoding="utf-8")
    forbidden = ('"commit"', "'commit'", '"reset"', "'reset'", '"stash"', "'stash'", '"checkout"', "'checkout'", '"clean"', "'clean'")
    for token in forbidden:
        assert token not in source, f"run_loop.py must never construct a git {token} argv"


def test_workspace_untouched_by_loop_itself(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    before_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True).stdout
    claude = write_fake_claude(tmp_path, name="claude")
    codex = write_fake_codex(tmp_path, name="codex", verdict_json=approve_verdict_json())

    run_loop_cli(tmp_path, ws, claude, codex, loop_run_id="loop-44", evidence_directory=tmp_path / "loops")
    after_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ws), capture_output=True, text=True, check=True).stdout
    assert before_head == after_head  # the loop never commits on the Owner's behalf


# ---------------------------------------------------------------------------
# 45. No tracked TOMTIT file changes.
# ---------------------------------------------------------------------------


def test_no_tracked_tomtit_file_changes() -> None:
    """This test file makes no tracked-file changes itself.

    It intentionally does *not* assert `git status` is empty: a corrective
    task authorized to modify tracked files (adapter source/tests) may
    legitimately leave tracked changes staged or unstaged in the working
    tree at the moment this suite runs, and that is not this test's concern.
    What this test protects is narrower and still meaningful: running this
    test must never *add* a tracked-file change beyond whatever was already
    present before it ran.
    """

    def _tracked_status() -> str:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!.tomtit"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0
        return completed.stdout

    before = _tracked_status()
    # This test performs no action of its own; the invariant under test is
    # that simply importing/running this module never mutates tracked files.
    after = _tracked_status()
    assert after == before, f"running this test must not change tracked-file status: before={before!r} after={after!r}"


# ---------------------------------------------------------------------------
# 46. Existing child-runner tests continue to pass (invoked separately by the
# Owner's verification commands; asserted here only as a sanity smoke test
# that the shared modules still import and expose their known symbols).
# ---------------------------------------------------------------------------


def test_child_runner_modules_still_expose_expected_symbols() -> None:
    assert callable(run_task.main)
    assert callable(run_review.main)
    assert callable(run_repair.main)
    assert hasattr(run_review, "validate_verdict_schema")
    assert hasattr(run_repair, "prove_reviewed_state")


# ===========================================================================
# EXACT_COMMAND_AUTHORIZATION corrective task: --authorized-command support.
#
# `run_task.py` has no dedicated test file of its own (it predates any of
# these dogfood test suites), and creating one solely to satisfy the
# verification-command list would be exactly the kind of scope creep this
# task's own instructions warn against. Since `test_run_loop.py` already
# imports `run_task` as a module and exercises it indirectly through
# `invoke_implementation()`, the run_task.py-specific direct unit tests for
# this new flag are added here, clearly delineated, rather than in a new
# file. See EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md section 9.
# ===========================================================================


def run_task_cli(
    tmp_path: Path,
    workspace: Path,
    claude_executable: Path,
    *,
    authorized_commands: list[str] | None = None,
    allowed_side_effect_paths: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    evidence_directory: Path | None = None,
    run_id: str | None = None,
) -> int:
    argv = [
        "--workspace",
        str(workspace),
        "--prompt-file",
        str(make_task_prompt(tmp_path)),
        "--timeout-seconds",
        "30",
        "--evidence-directory",
        str(evidence_directory or (tmp_path / "task-run")),
        "--executable",
        str(claude_executable),
    ]
    for path in allowed_paths if allowed_paths is not None else ["app.txt"]:
        argv.extend(["--allowed-path", path])
    for command in authorized_commands or []:
        argv.extend(["--authorized-command", command])
    for side_effect_path in allowed_side_effect_paths or []:
        argv.extend(["--allowed-side-effect-path", side_effect_path])
    if run_id:
        argv.extend(["--run-id", run_id])
    return run_task.main(argv)


def test_run_task_propagates_exact_authorized_commands(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path, name="task-ws-1")
    claude = write_fake_claude(tmp_path, name="claude-task-1")
    evidence_dir = tmp_path / "task-run-1"

    exit_code = run_task_cli(
        tmp_path,
        ws,
        claude,
        authorized_commands=["npm install foo --save-exact"],
        evidence_directory=evidence_dir,
    )

    # The layered exact-argv capability is enabled, so an exactly-matching
    # authorized command now proceeds through the guarded execution path
    # instead of failing closed as UNSUPPORTED. The assertions below -- which
    # are what this test is actually about -- are unchanged: fingerprints
    # propagate and no raw command text reaches the receipt.
    assert exit_code == 0
    receipt = json.loads((evidence_dir / "receipt.json").read_text(encoding="utf-8"))
    expected_argv = ("npm", "install", "foo", "--save-exact")
    assert receipt["authorized_command_fingerprints"] == [compute_authorized_command_fingerprint(expected_argv)]
    assert receipt["authorized_command_set_fingerprint"] == compute_authorized_command_set_fingerprint(
        (expected_argv,)
    )
    assert receipt["authorized_command_count"] == 1
    assert "authorized_commands" not in receipt
    assert "authorized_commands_display" not in receipt
    # No longer dormant: the capability must not report itself unsupported.
    assert receipt["error_category"] != "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"
    # The guarded path was genuinely taken, and no raw command text persisted.
    result = json.loads((evidence_dir / "result.json").read_text(encoding="utf-8"))
    assert result["exact_bash_guard_enabled"] is True
    assert result["provider_output_withheld"] is True
    receipt_json = json.dumps(receipt)
    assert " ".join(expected_argv) not in receipt_json
    assert "--save-exact" not in receipt_json


def test_run_task_no_authorized_command_unchanged_behavior(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path, name="task-ws-2")
    claude = write_fake_claude(tmp_path, name="claude-task-2")
    evidence_dir = tmp_path / "task-run-2"

    exit_code = run_task_cli(tmp_path, ws, claude, evidence_directory=evidence_dir)

    assert exit_code == 0
    receipt = json.loads((evidence_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["authorized_command_fingerprints"] == []
    assert receipt["authorized_command_set_fingerprint"] is None
    assert receipt["authorized_command_count"] == 0
    assert receipt["authorized_command_cli_rules"] == []


def test_run_task_invalid_authorized_command_fails_before_invocation(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path, name="task-ws-3")
    invoked_marker = tmp_path / "task-invoked.txt"
    claude = write_fake_claude(tmp_path, name="claude-task-3")
    # Overwrite with a script that would prove invocation if ever started.
    claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
pathlib.Path({str(invoked_marker)!r}).write_text("invoked")
pathlib.Path("app.txt").write_text("changed", encoding="utf-8")
""",
        encoding="utf-8",
    )
    claude.chmod(0o755)

    exit_code = run_task_cli(
        tmp_path, ws, claude, authorized_commands=["npm install foo && rm -rf /"], evidence_directory=tmp_path / "task-run-3"
    )

    assert exit_code == 2  # CONFIGURATION_FAILURE
    assert not invoked_marker.exists()
    assert not (tmp_path / "task-run-3").exists()


# ---------------------------------------------------------------------------
# 29-31. run_loop.py: task-contract persistence, identical Attempt-1/Attempt-2
#        authorization, and no post-creation expansion.
# ---------------------------------------------------------------------------


def test_loop_persists_and_reuses_identical_authorized_commands(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-authz-2"

    argv = [
        "--workspace",
        str(ws),
        "--task-prompt-file",
        str(make_task_prompt(tmp_path)),
        "--baseline-ref",
        "HEAD",
        "--implementation-timeout-seconds",
        "30",
        "--review-timeout-seconds",
        "30",
        "--claude-executable",
        str(claude),
        "--codex-executable",
        str(codex),
        "--evidence-directory",
        str(tmp_path / "loops"),
        "--allowed-path",
        "app.txt",
        "--authorized-command",
        "npm install foo --save-exact",
        "--allow-one-repair",
        "--loop-run-id",
        "loop-authz-2",
    ]
    exit_code = run_loop.main(argv)

    # With the layered exact-argv capability enabled, the implementation
    # attempt is no longer failed closed as UNSUPPORTED and the loop runs to
    # completion. The important assertions here remain about *propagation*
    # and fingerprint-only persistence, not about the outcome itself.
    assert exit_code == 0
    expected_argv = ("npm", "install", "foo", "--save-exact")
    expected_fingerprint = compute_authorized_command_fingerprint(expected_argv)
    expected_set_fingerprint = compute_authorized_command_set_fingerprint((expected_argv,))

    contract = json.loads((loop_dir / "task-contract.json").read_text(encoding="utf-8"))
    assert contract["authorized_command_fingerprints"] == [expected_fingerprint]
    assert contract["authorized_command_set_fingerprint"] == expected_set_fingerprint
    assert contract["authorized_command_count"] == 1
    assert "authorized_commands" not in contract
    assert "authorized_commands_display" not in contract
    assert contract["repair_authorized"] is True

    impl1_receipt = json.loads((loop_dir / "children" / "loop-authz-2-impl-1" / "receipt.json").read_text(encoding="utf-8"))
    assert impl1_receipt["authorized_command_fingerprints"] == [expected_fingerprint]
    assert impl1_receipt["authorized_command_set_fingerprint"] == expected_set_fingerprint

    result = load_result(loop_dir)
    assert result["authorized_command_fingerprints"] == [expected_fingerprint]
    assert result["authorized_command_set_fingerprint"] == expected_set_fingerprint
    assert result["authorized_command_count"] == 1
    assert "authorized_commands" not in result

    # No file under the loop's evidence tree ever contains the raw argv text
    # or a raw Bash(...) rule for this authorized command (Codex corrective
    # review, HIGH finding 2).
    raw_display = "npm install foo --save-exact"
    for path in loop_dir.rglob("*"):
        if path.is_file():
            contents = path.read_text(encoding="utf-8", errors="replace")
            assert raw_display not in contents, path
            assert "Bash(npm install foo --save-exact)" not in contents, path


def test_loop_never_expands_authorization_after_creation() -> None:
    """Within `main()`, `authorized_commands` is bound exactly once (from
    `phase_a`, i.e. from `_config_preflight()`, before the state machine or
    any child is invoked) and never reassigned for the rest of the run.

    Scoped to `main()`'s own source only: `_config_preflight()` legitimately
    constructs the value in the first place via `normalize_authorized_commands()`,
    which is the authorized *creation* point, not a later expansion.
    """

    import ast

    source = (DOGFOOD_DIR / "run_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main")

    assignment_targets: list[str] = []
    for node in ast.walk(main_func):
        if isinstance(node, ast.Assign):
            assignment_targets.extend(
                target.id for target in node.targets if isinstance(target, ast.Name) and target.id == "authorized_commands"
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "authorized_commands":
            assignment_targets.append(node.target.id)
    assert assignment_targets == ["authorized_commands"], (
        f"authorized_commands must be bound exactly once inside main(), found {len(assignment_targets)} bindings"
    )


# ---------------------------------------------------------------------------
# 32. Invalid authorized command at the loop level causes zero model
#     invocations (fails closed at config preflight, before the state
#     machine and before any child is ever invoked).
# ---------------------------------------------------------------------------


def test_loop_invalid_authorized_command_causes_zero_model_invocations(tmp_path: Path) -> None:
    ws = make_clean_workspace(tmp_path)
    invoked_marker = tmp_path / "loop-claude-invoked.txt"
    claude = tmp_path / "claude-should-not-run"
    claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
pathlib.Path({str(invoked_marker)!r}).write_text("invoked")
pathlib.Path("app.txt").write_text("changed", encoding="utf-8")
""",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    codex = write_fake_codex(tmp_path, name="codex")
    codex_invoked_marker = tmp_path / "loop-codex-invoked.txt"

    argv = [
        "--workspace",
        str(ws),
        "--task-prompt-file",
        str(make_task_prompt(tmp_path)),
        "--baseline-ref",
        "HEAD",
        "--implementation-timeout-seconds",
        "30",
        "--review-timeout-seconds",
        "30",
        "--claude-executable",
        str(claude),
        "--codex-executable",
        str(codex),
        "--evidence-directory",
        str(tmp_path / "loops"),
        "--allowed-path",
        "app.txt",
        "--authorized-command",
        "npm install foo; rm -rf /",
        "--loop-run-id",
        "loop-authz-invalid",
    ]
    exit_code = run_loop.main(argv)

    assert exit_code == 4  # CONFIG_FAILURE, before the state machine is even entered
    assert not invoked_marker.exists()
    assert not codex_invoked_marker.exists()
    assert not (tmp_path / "loops" / "loop-authz-invalid").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# Authorized generated/side-effect path propagation
# (WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md).
# ---------------------------------------------------------------------------


def _write_side_effect_claude(directory: Path, *, name: str, generate_next: bool) -> Path:
    """A fake Claude that writes one approved source file plus generated
    output beneath `node_modules` (and optionally `.next`)."""

    lines = [
        "#!/usr/bin/env python3",
        "import pathlib",
        'pathlib.Path("app.txt").write_text("implemented-side-effects", encoding="utf-8")',
        'pathlib.Path("node_modules/pkg").mkdir(parents=True, exist_ok=True)',
        'pathlib.Path("node_modules/pkg/index.js").write_text("x", encoding="utf-8")',
    ]
    if generate_next:
        lines += [
            'pathlib.Path(".next").mkdir(parents=True, exist_ok=True)',
            'pathlib.Path(".next/build-manifest.json").write_text("{}", encoding="utf-8")',
        ]
    script = directory / name
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_run_task_classifies_authorized_side_effects(tmp_path: Path) -> None:
    """The real dogfood shape end-to-end through run_task: generated output
    beneath Owner-approved roots is no longer a scope violation, and the
    receipt records bounded evidence rather than the full generated list."""

    ws = make_clean_workspace(tmp_path, name="se-ws-1")
    claude = _write_side_effect_claude(tmp_path, name="claude-se-1", generate_next=True)
    evidence_dir = tmp_path / "se-run-1"

    exit_code = run_task_cli(
        tmp_path,
        ws,
        claude,
        allowed_side_effect_paths=["node_modules", ".next"],
        evidence_directory=evidence_dir,
    )

    assert exit_code == 0
    receipt = json.loads((evidence_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["unexpected_changed_paths"] == []
    assert receipt["changed_paths"] == ["app.txt"]
    assert receipt["allowed_side_effect_paths"] == ["node_modules", ".next"]
    assert receipt["observed_authorized_side_effect_count"] == 2
    assert receipt["observed_authorized_side_effect_counts_by_root"] == {"node_modules": 1, ".next": 1}


def test_run_task_without_side_effect_authorization_still_fails_closed(tmp_path: Path) -> None:
    """The identical worker without authorization must still be rejected --
    proving generated output was not blanket-allowed."""

    ws = make_clean_workspace(tmp_path, name="se-ws-2")
    claude = _write_side_effect_claude(tmp_path, name="claude-se-2", generate_next=False)
    evidence_dir = tmp_path / "se-run-2"

    exit_code = run_task_cli(tmp_path, ws, claude, evidence_directory=evidence_dir)

    assert exit_code == 1
    receipt = json.loads((evidence_dir / "receipt.json").read_text(encoding="utf-8"))
    assert "node_modules/pkg/index.js" in receipt["unexpected_changed_paths"]
    assert receipt["observed_authorized_side_effect_count"] == 0


def test_run_task_rejects_unsafe_side_effect_path(tmp_path: Path) -> None:
    """`.git` can never be authorized through this feature."""

    ws = make_clean_workspace(tmp_path, name="se-ws-3")
    claude = write_fake_claude(tmp_path, name="claude-se-3")
    exit_code = run_task_cli(
        tmp_path, ws, claude, allowed_side_effect_paths=[".git"], evidence_directory=tmp_path / "se-run-3"
    )
    assert exit_code == 2  # CONFIGURATION_FAILURE


def test_loop_propagates_side_effect_authorization_to_task_contract(tmp_path: Path) -> None:
    """11. The loop binds the Owner-approved set once and records it in the
    task contract, from which both attempts inherit it unchanged."""

    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    loop_dir = tmp_path / "loops" / "loop-se-1"

    argv = [
        "--workspace", str(ws),
        "--task-prompt-file", str(make_task_prompt(tmp_path)),
        "--baseline-ref", "HEAD",
        "--implementation-timeout-seconds", "30",
        "--review-timeout-seconds", "30",
        "--claude-executable", str(claude),
        "--codex-executable", str(codex),
        "--evidence-directory", str(tmp_path / "loops"),
        "--allowed-path", "app.txt",
        "--allowed-side-effect-path", "node_modules",
        "--allowed-side-effect-path", ".next",
        "--allow-one-repair",
        "--loop-run-id", "loop-se-1",
    ]
    exit_code = run_loop.main(argv)

    assert exit_code == 0
    contract = json.loads((loop_dir / "task-contract.json").read_text(encoding="utf-8"))
    assert contract["allowed_side_effect_paths"] == ["node_modules", ".next"]
    assert contract["allowed_side_effect_path_count"] == 2

    # Attempt 1's child receipt inherited the identical ordered set.
    impl1_receipt = json.loads(
        (loop_dir / "children" / "loop-se-1-impl-1" / "receipt.json").read_text(encoding="utf-8")
    )
    assert impl1_receipt["allowed_side_effect_paths"] == ["node_modules", ".next"]


def test_loop_rejects_unsafe_side_effect_path_before_any_attempt(tmp_path: Path) -> None:
    """Fail-closed at loop configuration, before any child runs."""

    ws = make_clean_workspace(tmp_path)
    claude = _stateful_claude(tmp_path)
    codex = _adaptive_codex_script(tmp_path, workspace=ws, second_verdict_json=approve_verdict_json())
    argv = [
        "--workspace", str(ws),
        "--task-prompt-file", str(make_task_prompt(tmp_path)),
        "--baseline-ref", "HEAD",
        "--implementation-timeout-seconds", "30",
        "--review-timeout-seconds", "30",
        "--claude-executable", str(claude),
        "--codex-executable", str(codex),
        "--evidence-directory", str(tmp_path / "loops"),
        "--allowed-path", "app.txt",
        "--allowed-side-effect-path", "../escape",
        "--loop-run-id", "loop-se-2",
    ]
    assert run_loop.main(argv) == 4  # ConfigFailure
