"""Offline, deterministic tests for the local Codex review runner.

None of these tests invoke the real Codex service. Each uses a small fake
`codex`-shaped executable to control help text and verdict output precisely,
and a throwaway Git repository as the reviewed "workspace".

Run with:

    python3 -m pytest .tomtit/dogfood/test_run_review.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


def make_linked_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A primary repository plus a linked worktree with one source change."""

    primary = make_workspace(tmp_path)
    linked = tmp_path / "linked-worktree"
    _git(["worktree", "add", "-q", "-b", "linked-test", str(linked), "HEAD"], primary)
    (linked / "app.txt").write_text("changed in linked worktree\n", encoding="utf-8")
    return primary, linked


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
    allowed_changed_paths: list[str] | None = None,
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
        "allowed_changed_paths": list(allowed_changed_paths or ["app.txt"]),
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
    assume_unchanged_workspace: str | None = None,
    help_write_path: str | None = None,
    help_assume_unchanged_workspace: str | None = None,
    help_background_launcher: str | None = None,
    omit_last_message: bool = False,
    top_help: str = _FAKE_CODEX_TOP_HELP,
    exec_help: str = _FAKE_CODEX_EXEC_HELP,
) -> Path:
    """A Python stand-in for `codex` that understands --help, exec --help, and exec <prompt>."""

    script = directory / name
    body = f'''#!/usr/bin/env python3
import sys, time, pathlib, subprocess

argv = sys.argv[1:]
if argv[:1] == ["--help"]:
    if {help_write_path!r}:
        pathlib.Path({help_write_path!r}).write_text("capability probe touched source\\n")
    if {help_assume_unchanged_workspace!r}:
        subprocess.run(
            [
                "git",
                "-C",
                {help_assume_unchanged_workspace!r},
                "update-index",
                "--assume-unchanged",
                "app.txt",
            ],
            check=True,
        )
    if {help_background_launcher!r}:
        subprocess.run([{help_background_launcher!r}], check=True)
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
    if {assume_unchanged_workspace!r}:
        subprocess.run(
            [
                "git",
                "-C",
                {assume_unchanged_workspace!r},
                "update-index",
                "--assume-unchanged",
                "app.txt",
            ],
            check=True,
        )
    content = {prose_prefix!r} + ({verdict_json!r} or "")
    if last_message_path and not {omit_last_message!r}:
        pathlib.Path(last_message_path).write_text(content)
    raise SystemExit({exit_code!r})
raise SystemExit(1)
'''
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def write_background_writer_launcher(
    directory: Path,
    *,
    target: Path,
    name: str,
    ignore_term: bool = False,
) -> tuple[Path, Path]:
    launcher = directory / name
    pid_file = directory / f"{name}.child-pid"
    child_code = (
        "import os, pathlib, signal, time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
        + f"time.sleep(30); pathlib.Path({str(target)!r}).write_text('escaped delayed write\\n')"
    )
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, subprocess, sys, time\n"
        f"pid_file = pathlib.Path({str(pid_file)!r})\n"
        "subprocess.Popen([sys.executable, '-c', "
        f"{child_code!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "deadline = time.monotonic() + 2\n"
        "while not pid_file.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "raise SystemExit(0 if pid_file.exists() else 2)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher, pid_file


def assert_process_absent(pid_file: Path) -> None:
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"delayed descendant {pid} is still present")


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


def test_provider_observer_is_false_only_when_codex_process_does_not_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[bool | None] = []

    def fail_start(*args: object, **kwargs: object) -> object:
        raise OSError("synthetic start failure")

    monkeypatch.setattr(run_review.subprocess, "Popen", fail_start)
    with pytest.raises(OSError):
        run_review.execute_bounded(
            ["fake-codex"],
            cwd=tmp_path,
            timeout_seconds=1,
            provider_observer=observations.append,
        )

    assert observations == [False]


def test_provider_observer_stays_true_after_post_start_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[bool | None] = []

    class StartedProcess:
        pid = 12345
        returncode = None

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            raise OSError("synthetic communicate failure")

    monkeypatch.setattr(
        run_review.subprocess,
        "Popen",
        lambda *args, **kwargs: StartedProcess(),
    )
    monkeypatch.setattr(run_review, "_ensure_group_quiescent", lambda pgid: None)
    with pytest.raises(OSError):
        run_review.execute_bounded(
            ["fake-codex"],
            cwd=tmp_path,
            timeout_seconds=1,
            provider_observer=observations.append,
        )

    assert observations == [True]


def test_capability_probe_delayed_descendant_is_quiesced(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    original = (ws / "app.txt").read_text(encoding="utf-8")
    launcher, pid_file = write_background_writer_launcher(
        tmp_path,
        target=ws / "app.txt",
        name="probe-delayed-writer",
    )
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        help_background_launcher=str(launcher),
    )

    assert run_review_cli(tmp_path, ws, codex) == 0
    assert_process_absent(pid_file)
    assert (ws / "app.txt").read_text(encoding="utf-8") == original


def test_deterministic_check_delayed_descendant_is_quiesced(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    original = (ws / "app.txt").read_text(encoding="utf-8")
    launcher, pid_file = write_background_writer_launcher(
        tmp_path,
        target=ws / "app.txt",
        name="check-delayed-writer",
    )
    codex = write_fake_codex(tmp_path, verdict_json=approve_verdict_json())

    assert run_review_cli(tmp_path, ws, codex, check_commands=[str(launcher)]) == 0
    assert_process_absent(pid_file)
    assert (ws / "app.txt").read_text(encoding="utf-8") == original


def test_descendant_ignoring_sigterm_is_killed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "must-not-exist"
    launcher, pid_file = write_background_writer_launcher(
        tmp_path,
        target=target,
        name="ignore-term-writer",
        ignore_term=True,
    )
    monkeypatch.setattr(run_review, "_PROCESS_CLEANUP_GRACE_SECONDS", 0.1)

    result = run_review.run_check_command(str(launcher), tmp_path, 5)

    assert result.exit_code == 0
    assert_process_absent(pid_file)
    assert not target.exists()


def test_quiescence_unknown_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_review, "_group_alive", lambda pgid: None)

    with pytest.raises(run_review.PreflightFailure) as excinfo:
        run_review.run_check_command("/usr/bin/true", tmp_path, 5)

    assert excinfo.value.category == "SUBPROCESS_QUIESCENCE_UNVERIFIED"


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


def test_codex_cannot_write_authorized_ignored_side_effect(tmp_path: Path) -> None:
    """Only deterministic checks may create declared review side effects."""

    ws = make_workspace(tmp_path)
    (ws / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    _git(["add", ".gitignore"], ws)
    _git(
        ["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "ignore cache"],
        ws,
    )
    cache = ws / ".pytest_cache"
    cache.mkdir()
    run_dir = make_implementation_run_directory(
        tmp_path,
        workspace=ws,
        allowed_side_effect_paths=[".pytest_cache"],
    )
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(cache / "provider-state"),
    )
    evidence_root = tmp_path / "reviews"

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        implementation_run_directory=run_dir,
        review_run_id="review-provider-side-effect",
        evidence_directory=evidence_root,
    )

    assert exit_code == 4
    receipt = json.loads(
        (evidence_root / "review-provider-side-effect" / "review-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["provider_invoked"] is True
    assert receipt["workspace_unchanged"] is False
    assert receipt["workspace_preservation_status"] == "UNEXPECTED_DRIFT"
    assert receipt["reviewer_modified_paths"] == [".pytest_cache/provider-state"]


def test_codex_semantic_index_flag_drift_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    before_identity = run_review.git_index_identity(ws)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        assume_unchanged_workspace=str(ws),
    )
    evidence_root = tmp_path / "reviews"

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        review_run_id="review-index-flag-drift",
        evidence_directory=evidence_root,
    )

    assert exit_code == 4
    assert run_review.git_index_clean(ws) is True
    assert run_review.git_index_identity(ws) != before_identity
    receipt = json.loads(
        (evidence_root / "review-index-flag-drift" / "review-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["workspace_unchanged"] is False
    assert receipt["reviewer_repository_identity_preserved"] is False


def test_codex_git_hook_mutation_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    hook = run_review.git_common_dir(ws) / "hooks" / "tomtit-review-hook"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(hook),
    )
    evidence_root = tmp_path / "reviews"

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        review_run_id="review-git-hook-drift",
        evidence_directory=evidence_root,
    )

    assert exit_code == 4
    receipt = json.loads(
        (evidence_root / "review-git-hook-drift" / "review-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["workspace_unchanged"] is False
    assert receipt["protected_git_metadata_preserved"] is False
    assert any(
        path.endswith("hooks/tomtit-review-hook")
        for path in receipt["protected_git_metadata_changed_paths_sample"]
    )


def test_deterministic_check_git_config_mutation_stops_before_reviewer(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
    )

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        check_commands=["git config tomtit.synthetic changed"],
    )

    assert exit_code == 3
    assert not marker.exists()


def test_capability_probe_git_hook_mutation_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    hook = run_review.git_common_dir(ws) / "hooks" / "tomtit-probe-hook"
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        help_write_path=str(hook),
        write_unexpected=str(marker),
    )

    exit_code = run_review_cli(tmp_path, ws, codex)

    assert exit_code == 3
    assert not marker.exists()


def test_protected_git_metadata_identity_is_worktree_aware(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    linked = tmp_path / "linked-worktree"
    _git(["worktree", "add", "-q", "-b", "linked-test", str(linked), "HEAD"], ws)

    git_directory = run_review.git_dir(linked)
    common_directory = run_review.git_common_dir(linked)
    before = run_review.protected_git_metadata_snapshot(linked)

    assert git_directory is not None
    assert common_directory is not None
    assert git_directory != common_directory
    assert before["@git_dir"] == f"path:{git_directory.resolve()}"
    assert before["@git_common_dir"] == f"path:{common_directory.resolve()}"
    _git(["config", "tomtit.worktree", "changed"], linked)
    assert run_review.protected_git_metadata_snapshot(linked) != before


def test_protected_git_metadata_identity_binds_ordinary_git_directory(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)

    identity = run_review.protected_git_metadata_snapshot(ws)

    assert (ws / ".git").is_dir()
    assert identity["@workspace_git_entry"].startswith("dir:")
    assert run_review.workspace_git_entry_hash_from_snapshot(identity)


def test_linked_worktree_root_gitfile_unchanged_is_bound(tmp_path: Path) -> None:
    _, linked = make_linked_workspace(tmp_path)

    before = run_review.protected_git_metadata_snapshot(linked)
    after = run_review.protected_git_metadata_snapshot(linked)

    assert (linked / ".git").is_file()
    assert before["@workspace_git_entry"].startswith("file:")
    assert before == after
    assert (
        run_review.workspace_git_entry_hash_from_snapshot(before)
        == run_review.workspace_git_entry_hash_from_snapshot(after)
    )


def test_linked_worktree_root_gitfile_content_change_is_detected(
    tmp_path: Path,
) -> None:
    _, linked = make_linked_workspace(tmp_path)
    gitfile = linked / ".git"
    before = run_review.protected_git_metadata_snapshot(linked)
    resolved_git_dir = run_review.git_dir(linked)
    assert resolved_git_dir is not None
    relative_git_dir = os.path.relpath(resolved_git_dir, linked)
    gitfile.write_text(f"gitdir: {relative_git_dir}\n", encoding="utf-8")

    assert run_review.git_dir(linked) == resolved_git_dir
    after = run_review.protected_git_metadata_snapshot(linked)
    assert after != before
    assert (
        run_review.workspace_git_entry_hash_from_snapshot(after)
        != run_review.workspace_git_entry_hash_from_snapshot(before)
    )


def test_linked_worktree_root_gitfile_mode_change_is_detected(tmp_path: Path) -> None:
    _, linked = make_linked_workspace(tmp_path)
    gitfile = linked / ".git"
    before = run_review.protected_git_metadata_snapshot(linked)
    gitfile.chmod(0o600)

    after = run_review.protected_git_metadata_snapshot(linked)

    assert after != before
    assert (
        run_review.workspace_git_entry_hash_from_snapshot(after)
        != run_review.workspace_git_entry_hash_from_snapshot(before)
    )


def test_linked_worktree_root_gitfile_replacement_type_change_is_detected(
    tmp_path: Path,
) -> None:
    _, linked = make_linked_workspace(tmp_path)
    gitfile = linked / ".git"
    before = run_review.protected_git_metadata_snapshot(linked)
    original = gitfile.read_bytes()
    gitfile.unlink()
    gitfile.write_bytes(original)

    replaced = run_review.protected_git_metadata_snapshot(linked)
    assert replaced != before

    gitfile.unlink()
    gitfile.mkdir()

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(linked)


# ---------------------------------------------------------------------------
# Cross-phase-stable protected Git metadata (excludes mtime_ns/ctime_ns only).
# ---------------------------------------------------------------------------


def test_protected_git_metadata_ctime_only_drift_is_cross_phase_stable(
    tmp_path: Path,
) -> None:
    """Benign OS-level timestamp-only churn between two separate calls (e.g.
    an extended attribute the OS attaches asynchronously) must not register
    as a semantic mutation of protected Git metadata."""

    ws = make_workspace(tmp_path)
    before = run_review.protected_git_metadata_snapshot(ws)

    index_path = run_review.git_dir(ws) / "index"
    os.utime(index_path, None)  # bumps mtime/ctime only; content is untouched

    after = run_review.protected_git_metadata_snapshot(ws)
    assert after == before


def test_protected_git_metadata_same_size_content_mutation_is_detected(
    tmp_path: Path,
) -> None:
    """A same-size content mutation must still be detected: the stable
    projection excludes only mtime_ns/ctime_ns, never content identity."""

    ws = make_workspace(tmp_path)
    before = run_review.protected_git_metadata_snapshot(ws)

    # `.git/description` is free-form and never parsed by Git itself, so
    # mutating its bytes cannot break subsequent `git` subprocess calls the
    # way corrupting `.git/config` could.
    description_path = run_review.git_dir(ws) / "description"
    original = bytearray(description_path.read_bytes())
    assert original
    original[-1] ^= 0xFF
    description_path.write_bytes(bytes(original))

    after = run_review.protected_git_metadata_snapshot(ws)
    assert after != before


def test_protected_git_metadata_index_stage_mutation_is_detected(
    tmp_path: Path,
) -> None:
    """Staging a new file changes `.git/index` content and must still be
    detected as a cross-phase Git metadata identity change."""

    ws = make_workspace(tmp_path)
    before = run_review.protected_git_metadata_snapshot(ws)

    (ws / "new_file.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "new_file.txt"], ws)

    after = run_review.protected_git_metadata_snapshot(ws)
    assert after != before


def test_protected_git_metadata_head_ref_mutation_is_detected(tmp_path: Path) -> None:
    """A new commit changes HEAD/ref/log content and must still be detected."""

    ws = make_workspace(tmp_path)
    before = run_review.protected_git_metadata_snapshot(ws)

    _git(
        ["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "--allow-empty", "-q", "-m", "second"],
        ws,
    )

    after = run_review.protected_git_metadata_snapshot(ws)
    assert after != before


def test_protected_git_metadata_root_directory_mode_change_is_detected(
    tmp_path: Path,
) -> None:
    """A root `.git` directory mode change must still be detected (not just
    for the linked-worktree gitfile case already covered above)."""

    ws = make_workspace(tmp_path)
    git_dir_path = ws / ".git"
    before = run_review.protected_git_metadata_snapshot(ws)
    original_mode = git_dir_path.stat().st_mode & 0o777
    git_dir_path.chmod(0o750)
    try:
        after = run_review.protected_git_metadata_snapshot(ws)
        assert after != before
    finally:
        git_dir_path.chmod(original_mode)


# ---------------------------------------------------------------------------
# F-01 repair: the timestamp-stripping projection must be descriptor-type
# aware. A `path:`/`symlink:` descriptor is caller-controlled text (a real
# filesystem path) and must never be regex-rewritten, even when it happens
# to contain a substring that looks like `:mtime_ns=1:ctime_ns=2`.
# ---------------------------------------------------------------------------


def test_strip_volatile_timestamp_fields_preserves_path_descriptor_byte_for_byte() -> None:
    descriptor = "path:/tmp/repo:mtime_ns=1:ctime_ns=2/.git"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_does_not_collide_distinct_paths() -> None:
    colliding_path = "path:/tmp/repo:mtime_ns=1:ctime_ns=2/.git"
    plain_path = "path:/tmp/repo/.git"
    assert colliding_path != plain_path
    assert (
        run_review._strip_volatile_timestamp_fields(colliding_path)
        != run_review._strip_volatile_timestamp_fields(plain_path)
    )


def test_strip_volatile_timestamp_fields_preserves_symlink_descriptor_byte_for_byte() -> None:
    descriptor = "symlink:mode=0777:target=some/target:mtime_ns=1:ctime_ns=2"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_unrecognized_descriptor_grammar() -> None:
    descriptor = "unknown-type:mtime_ns=1:ctime_ns=2"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_dir_descriptor_removes_only_timestamp_fields() -> None:
    descriptor = "dir:dev=1:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=200"
    assert (
        run_review._strip_volatile_timestamp_fields(descriptor)
        == "dir:dev=1:ino=2:mode=0755:size=64:nlink=1"
    )


def test_strip_volatile_timestamp_fields_file_descriptor_retains_sha256() -> None:
    sha256 = "deadbeef" * 8  # a real hashlib.sha256().hexdigest() is 64 hex chars
    descriptor = f"file:dev=1:ino=2:mode=0644:size=64:nlink=1:mtime_ns=100:ctime_ns=200:sha256={sha256}"
    assert (
        run_review._strip_volatile_timestamp_fields(descriptor)
        == f"file:dev=1:ino=2:mode=0644:size=64:nlink=1:sha256={sha256}"
    )


# ---------------------------------------------------------------------------
# F-01 re-repair: prefix-only recognition (anything starting "dir:"/"file:")
# was still too broad. Recognition must require an exact fullmatch against
# the canonical grammar `_directory_descriptor`/`_regular_file_measurement`
# actually emit -- not merely the type prefix.
# ---------------------------------------------------------------------------


def test_strip_volatile_timestamp_fields_preserves_noncanonical_dir_with_timestamp_like_text() -> None:
    descriptor = "dir:opaque=caller:mtime_ns=1:ctime_ns=2:payload=retained"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_noncanonical_file_with_timestamp_like_text() -> None:
    descriptor = "file:opaque=caller:mtime_ns=1:ctime_ns=2:payload=retained"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_truncated_canonical_dir_descriptor() -> None:
    # Missing the trailing ctime_ns field.
    descriptor = "dir:dev=1:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_truncated_canonical_file_descriptor() -> None:
    # Missing the trailing sha256 field.
    descriptor = "file:dev=1:ino=2:mode=0644:size=64:nlink=1:mtime_ns=100:ctime_ns=200"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_reordered_canonical_fields() -> None:
    # mode and ino swapped relative to the canonical dev/ino/mode/size/nlink order.
    descriptor = "dir:dev=1:mode=0755:ino=2:size=64:nlink=1:mtime_ns=100:ctime_ns=200"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_extra_unknown_field() -> None:
    # An otherwise-canonical dir: descriptor with one trailing field that is
    # not part of the canonical grammar the producer functions emit.
    descriptor = "dir:dev=1:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=200:extra=1"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_strips_real_directory_descriptor(
    tmp_path: Path,
) -> None:
    """Positive case, generated through the real producer function: an exact
    canonical `dir:` descriptor has only mtime_ns/ctime_ns removed."""

    directory = tmp_path / "measured-dir"
    directory.mkdir()
    metadata = directory.lstat()
    descriptor = run_review._directory_descriptor(metadata)

    stripped = run_review._strip_volatile_timestamp_fields(descriptor)

    assert "mtime_ns=" not in stripped
    assert "ctime_ns=" not in stripped
    assert stripped == (
        f"dir:dev={metadata.st_dev}:ino={metadata.st_ino}:"
        f"mode={stat.S_IMODE(metadata.st_mode):04o}:size={metadata.st_size}:"
        f"nlink={metadata.st_nlink}"
    )


def test_strip_volatile_timestamp_fields_strips_real_file_descriptor(
    tmp_path: Path,
) -> None:
    """Positive case, generated through the real producer function: an exact
    canonical `file:` descriptor has only mtime_ns/ctime_ns removed, and
    every other field -- including SHA-256 -- survives."""

    file_path = tmp_path / "measured-file"
    content = b"some real file content\n"
    file_path.write_bytes(content)
    metadata = file_path.lstat()
    descriptor, _ = run_review._regular_file_measurement(
        file_path, metadata, reject_hardlinks=False, capture_bytes=False
    )

    stripped = run_review._strip_volatile_timestamp_fields(descriptor)

    assert "mtime_ns=" not in stripped
    assert "ctime_ns=" not in stripped
    expected_sha256 = hashlib.sha256(content).hexdigest()
    assert stripped == (
        f"file:dev={metadata.st_dev}:ino={metadata.st_ino}:"
        f"mode={stat.S_IMODE(metadata.st_mode):04o}:size={metadata.st_size}:"
        f"nlink={metadata.st_nlink}:sha256={expected_sha256}"
    )


# ---------------------------------------------------------------------------
# F-01 final precision correction: integer fields must match exactly
# Python's default `{int}` lexical form ("0", or a non-zero digit sequence
# with no leading zeros, optionally negated). A canonical-*looking*
# descriptor whose integer field is spelled some other way (a leading zero,
# or "-0") is not byte-for-byte what the producer emits, and must be
# preserved unchanged, not accepted as canonical.
# ---------------------------------------------------------------------------


def test_strip_volatile_timestamp_fields_preserves_dir_with_leading_zero_dev() -> None:
    descriptor = "dir:dev=001:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=200"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_dir_with_negative_zero_ino() -> None:
    descriptor = "dir:dev=1:ino=-0:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=200"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_file_with_leading_zero_size() -> None:
    sha256 = "a" * 64
    descriptor = f"file:dev=1:ino=2:mode=0644:size=0003:nlink=1:mtime_ns=100:ctime_ns=200:sha256={sha256}"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_file_with_leading_zero_negative_nlink() -> None:
    sha256 = "a" * 64
    descriptor = f"file:dev=1:ino=2:mode=0644:size=64:nlink=-01:mtime_ns=100:ctime_ns=200:sha256={sha256}"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_leading_zero_mtime_ns() -> None:
    descriptor = "dir:dev=1:ino=2:mode=0755:size=64:nlink=1:mtime_ns=001:ctime_ns=200"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_preserves_negative_zero_ctime_ns() -> None:
    descriptor = "dir:dev=1:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=-0"
    assert run_review._strip_volatile_timestamp_fields(descriptor) == descriptor


def test_strip_volatile_timestamp_fields_strips_canonical_negative_integer_fields() -> None:
    """A canonical-form negative integer (no leading zero, single leading
    "-") is still recognized: only mtime_ns/ctime_ns are removed."""

    descriptor = "dir:dev=-1:ino=-12:mode=0755:size=0:nlink=1:mtime_ns=-100:ctime_ns=-200"
    assert (
        run_review._strip_volatile_timestamp_fields(descriptor)
        == "dir:dev=-1:ino=-12:mode=0755:size=0:nlink=1"
    )


def _make_workspace_with_timestamp_like_name(tmp_path: Path) -> Path:
    """A committed Git repo whose own directory name contains text matching
    the volatile-timestamp-field pattern, to prove path descriptors survive
    projection byte-for-byte even under an adversarial-looking real path."""

    root = tmp_path / "repo:mtime_ns=1:ctime_ns=2"
    root.mkdir()
    (root / "app.txt").write_text("original\n", encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], root)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], root)
    return root


def test_protected_git_metadata_git_dir_path_with_timestamp_like_text_is_preserved(
    tmp_path: Path,
) -> None:
    ws = _make_workspace_with_timestamp_like_name(tmp_path)

    identity = run_review.protected_git_metadata_snapshot(ws)

    expected_git_dir = run_review.git_dir(ws)
    assert expected_git_dir is not None
    assert identity["@git_dir"] == f"path:{expected_git_dir.resolve()}"
    assert "mtime_ns=1:ctime_ns=2" in identity["@git_dir"]


def test_protected_git_metadata_git_common_dir_path_with_timestamp_like_text_is_preserved(
    tmp_path: Path,
) -> None:
    primary = _make_workspace_with_timestamp_like_name(tmp_path)
    linked = tmp_path / "linked-worktree"
    _git(["worktree", "add", "-q", "-b", "linked-timestamp-like", str(linked), "HEAD"], primary)

    identity = run_review.protected_git_metadata_snapshot(linked)

    expected_git_dir = run_review.git_dir(linked)
    expected_common_dir = run_review.git_common_dir(linked)
    assert expected_git_dir is not None and expected_common_dir is not None
    assert expected_git_dir != expected_common_dir
    assert identity["@git_common_dir"] == f"path:{expected_common_dir.resolve()}"
    assert "mtime_ns=1:ctime_ns=2" in identity["@git_common_dir"]


def test_protected_git_metadata_workspace_root_git_path_with_timestamp_like_text_is_preserved(
    tmp_path: Path,
) -> None:
    ws = _make_workspace_with_timestamp_like_name(tmp_path)

    identity = run_review.protected_git_metadata_snapshot(ws)

    entry_key = run_review._WORKSPACE_GIT_ENTRY_PATH_KEY
    assert identity[entry_key] == f"path:{(ws / '.git').resolve()}"
    assert "mtime_ns=1:ctime_ns=2" in identity[entry_key]


def _synthetic_git_entry(
    *, path: str, mtime_ns: int, ctime_ns: int, ino: int = 1, size: int = 64
) -> tuple[str, str]:
    return (
        f"path:{path}",
        f"dir:dev=1:ino={ino}:mode=0755:size={size}:nlink=1:"
        f"mtime_ns={mtime_ns}:ctime_ns={ctime_ns}",
    )


def test_protected_git_metadata_bounded_retry_recovers_from_transient_timestamp_only_instability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One same-window timestamp-only instability is retried and may still
    succeed, without exhausting the bounded retry limit."""

    ws = make_workspace(tmp_path)
    entry_path = str((ws / ".git").resolve())
    sequence = iter(
        [
            _synthetic_git_entry(path=entry_path, mtime_ns=100, ctime_ns=100),  # attempt 1 before
            _synthetic_git_entry(path=entry_path, mtime_ns=200, ctime_ns=200),  # attempt 1 after (timestamp-only)
            _synthetic_git_entry(path=entry_path, mtime_ns=300, ctime_ns=300),  # attempt 2 before
            _synthetic_git_entry(path=entry_path, mtime_ns=300, ctime_ns=300),  # attempt 2 after (coherent)
        ]
    )
    monkeypatch.setattr(run_review, "_workspace_git_entry_descriptor", lambda cwd, **kwargs: next(sequence))

    result = run_review.protected_git_metadata_snapshot(ws)

    assert result
    with pytest.raises(StopIteration):
        next(sequence)


def test_protected_git_metadata_persistent_timestamp_instability_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If every bounded attempt sees only timestamp-only instability, the
    measurement fails closed rather than retrying forever."""

    ws = make_workspace(tmp_path)
    entry_path = str((ws / ".git").resolve())
    values = []
    for attempt in range(run_review._PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS):
        base = attempt * 2
        values.append(_synthetic_git_entry(path=entry_path, mtime_ns=base, ctime_ns=base))
        values.append(_synthetic_git_entry(path=entry_path, mtime_ns=base + 1, ctime_ns=base + 1))
    sequence = iter(values)
    monkeypatch.setattr(run_review, "_workspace_git_entry_descriptor", lambda cwd, **kwargs: next(sequence))

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws)

    with pytest.raises(StopIteration):
        next(sequence)


def test_protected_git_metadata_content_changing_race_during_retry_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry attempt that itself detects a real (non-timestamp) change
    must fail closed immediately, without exhausting further retries."""

    ws = make_workspace(tmp_path)
    entry_path = str((ws / ".git").resolve())
    sequence = iter(
        [
            _synthetic_git_entry(path=entry_path, mtime_ns=1, ctime_ns=1),  # attempt 1 before
            _synthetic_git_entry(path=entry_path, mtime_ns=2, ctime_ns=2),  # attempt 1 after (timestamp-only)
            _synthetic_git_entry(path=entry_path, mtime_ns=3, ctime_ns=3, ino=1),  # attempt 2 before
            _synthetic_git_entry(path=entry_path, mtime_ns=3, ctime_ns=3, ino=2),  # attempt 2 after (real change)
        ]
    )
    monkeypatch.setattr(run_review, "_workspace_git_entry_descriptor", lambda cwd, **kwargs: next(sequence))

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws)

    with pytest.raises(StopIteration):
        next(sequence)


# ---------------------------------------------------------------------------
# Intra-measurement timestamp-only instability for a protected regular-file
# *member* (HEAD, a ref, a loose object, ...), not just the root .git entry.
# See TimestampOnlyRegularFileInstability. All of these perturb only the
# opaque `_stat_identity()` tuple for one targeted inode -- never real OS
# timing -- so they are fully deterministic.
# ---------------------------------------------------------------------------


def _perturb_stat_identity_ctime_for_inode(
    monkeypatch: pytest.MonkeyPatch, target_ino: int, *, perturb_calls: set[int]
) -> dict[str, int]:
    """Return every real `_stat_identity()` observation unperturbed, except
    that any call numbered in `perturb_calls` (1-indexed, counting only
    calls whose `st_ino` matches `target_ino`) has its `ctime_ns` bumped by
    one. This simulates an OS-level ctime-only touch landing between two
    specific observation points of one specific file, deterministically."""

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


def _open_count_content_swap(
    monkeypatch: pytest.MonkeyPatch,
    target_path: Path,
    *,
    swap_on_open_number: int,
    new_content: bytes,
) -> dict[str, int]:
    """Rewrite `target_path`'s bytes in place immediately before its Nth
    `os.open()` call, simulating a real mutation landing between two
    coherent-measurement retry attempts (as opposed to a same-window
    ctime-only touch, which `_perturb_stat_identity_ctime_for_inode`
    simulates instead)."""

    real_open = os.open
    state = {"opens": 0}
    target = str(target_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            if state["opens"] == swap_on_open_number:
                target_path.write_bytes(new_content)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)
    return state


def test_protected_git_metadata_original_counterexample_mutation_before_first_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(A) Codex's exact deterministic reproduction: content A exists, a
    same-size A->B mutation happens immediately before the very first real
    read (so the measurement's own before/opened comparison is genuinely
    confined to mtime_ns/ctime_ns -- same dev/ino/mode/size/nlink -- but
    the bytes actually read are B, not A). Trusted evidence from an
    earlier, separate, real coherent snapshot proves SHA(A); SHA(B) !=
    SHA(A) must fail the whole protected snapshot closed on this very
    first unstable attempt -- never merely retried and then accepted just
    because a later reading agrees with itself."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)  # coherent: content A

    head_path = run_review.git_dir(ws) / "HEAD"
    original = head_path.read_bytes()
    mutated = bytearray(original)
    mutated[0] ^= 0xFF
    mutated_bytes = bytes(mutated)
    assert len(mutated_bytes) == len(original)
    assert mutated_bytes != original

    # Mutate HEAD's bytes in place immediately before the very first real
    # open of the second call -- no _stat_identity perturbation needed:
    # the in-place same-size rewrite naturally produces a before/opened
    # mismatch confined to mtime_ns/ctime_ns on its own.
    _open_count_content_swap(monkeypatch, head_path, swap_on_open_number=1, new_content=mutated_bytes)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)


def test_protected_git_metadata_benign_ctime_only_with_trusted_baseline_retries_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(B) Trusted BEFORE snapshot has HEAD's real SHA. The AFTER
    measurement experiences a genuine ctime-only instability but the bytes
    read are unchanged, so the unstable reading's SHA matches the trusted
    evidence exactly: the whole snapshot is retry-eligible and a later
    coherent reading of the same bytes is accepted."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    state = _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    result = run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)

    assert result
    # HEAD was measured at least twice (an unstable first attempt, a
    # coherent second one): 4 raw-identity observations per measurement.
    assert state["calls"] >= 8


def test_protected_git_metadata_initial_snapshot_timestamp_instability_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(C) No trusted prior coherent protected snapshot exists at all
    (`trusted_baseline` defaults to `None`). A member's timestamp-only
    instability must fail closed immediately: there is no trusted
    pre-instability content to prove the unstable reading against, so it
    can never establish its own baseline (no self-baselining)."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws)


def test_protected_git_metadata_persistent_ctime_instability_fails_closed_despite_matching_trusted_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trusted baseline that matches on every attempt still does not grant
    unbounded retries: if HEAD's measurement is timestamp-only unstable on
    every one of the 3 bounded attempts, the whole snapshot fails closed
    once the bound is exhausted."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    # Perturb the second observation of every attempt (calls 2, 6, 10 for
    # three complete 4-observation measurements of HEAD).
    state = _perturb_stat_identity_ctime_for_inode(
        monkeypatch, target_ino, perturb_calls={2, 6, 10}
    )

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)

    assert state["calls"] == 4 * run_review._PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS


# ---------------------------------------------------------------------------
# Coherent-bootstrap INITIAL baseline establishment
# (`establish_protected_git_metadata_baseline`). This supersedes the old
# "initial protected measurement + timestamp instability + no prior trusted
# content -> block" invariant for the *bootstrap* case only: the explicit,
# bounded, two-consecutive-match algorithm below is the only place Runtime
# may ever trust a protected observation without external evidence.
# `protected_git_metadata_snapshot`'s own strict-validation behavior (tested
# above) is completely unchanged and is not reopened by any of this.
# ---------------------------------------------------------------------------


def test_establish_baseline_clean_bootstrap_two_matching_observations(
    tmp_path: Path,
) -> None:
    """(A) A clean repository establishes its baseline on the second
    independently rebuilt observation, since nothing changes between them."""

    ws = make_workspace(tmp_path)

    result = run_review.establish_protected_git_metadata_baseline(ws)

    assert result.observations_used == 2
    assert result.timestamp_churn_observed is False
    assert result.required_third_observation is False
    # The established baseline round-trips as valid trusted evidence for
    # strict post-baseline validation.
    validated = run_review.protected_git_metadata_snapshot(
        ws, trusted_baseline=result.stable_projection
    )
    assert validated == result.stable_projection


def test_establish_baseline_benign_timestamp_churn_during_first_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(B) A genuine same-window ctime-only touch on HEAD during the first
    provisional observation is tolerated unconditionally; the semantic
    content is unchanged, so the second independent observation still
    agrees and establishes the baseline, with the churn recorded only as a
    diagnostic."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    # Perturb only the "opened" reading (the 2nd _stat_identity call) of
    # HEAD's very first measurement -- confined entirely within S1. S2 is
    # completely unperturbed.
    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    result = run_review.establish_protected_git_metadata_baseline(ws)

    assert result.observations_used == 2
    assert result.timestamp_churn_observed is True
    assert result.required_third_observation is False


def test_establish_baseline_content_mutated_before_first_read_adopts_new_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(C) If content changes from A to B before the bootstrap sequence's
    very first read of a file, Runtime never claimed to have observed A:
    both S1 and S2 independently observe B, and B legitimately becomes the
    baseline -- this is now EXPECTED, not a failure."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    original = head_path.read_bytes()
    mutated = bytearray(original)
    # Flip the byte just before the trailing newline (inside the branch
    # name), not the "ref: " prefix -- HEAD must remain a parseable
    # symbolic-ref file so `git rev-parse --git-dir` keeps working.
    mutated[-2] ^= 0xFF
    mutated_bytes = bytes(mutated)
    assert len(mutated_bytes) == len(original)
    assert mutated_bytes != original

    _open_count_content_swap(
        monkeypatch, head_path, swap_on_open_number=1, new_content=mutated_bytes
    )

    result = run_review.establish_protected_git_metadata_baseline(ws)

    assert result.observations_used == 2
    match = run_review._STABLE_FILE_DESCRIPTOR_RE.fullmatch(
        result.stable_projection["git_dir/HEAD"]
    )
    assert match is not None
    assert match.group("sha256") == hashlib.sha256(mutated_bytes).hexdigest()


def test_establish_baseline_disagree_then_agree_establishes_on_third_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(D) S1 observes A, S2 observes B (a real, non-timestamp content
    change), S3 observes B again: S2 and S3 are the first *consecutive*
    agreement, so B is established on the third observation -- S1 is never
    compared directly against S3, only against S2."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    original = head_path.read_bytes()
    mutated = bytearray(original)
    # See test C: flip a byte inside the branch name, not the "ref: "
    # prefix, so HEAD stays a parseable symbolic-ref file throughout.
    mutated[-2] ^= 0xFF
    mutated_bytes = bytes(mutated)
    assert len(mutated_bytes) == len(original)

    # HEAD is visited twice per observation (once via the git_dir role, once
    # via the git_common_dir role, since both resolve to the same directory
    # for an ordinary non-linked-worktree repository): opens 1-2 are S1,
    # opens 3-4 are S2, opens 5-6 are S3. Mutate in place immediately before
    # the third open (S2's first read) so S1 sees A throughout and S2/S3
    # both see B.
    _open_count_content_swap(
        monkeypatch, head_path, swap_on_open_number=3, new_content=mutated_bytes
    )

    result = run_review.establish_protected_git_metadata_baseline(ws)

    assert result.observations_used == 3
    assert result.required_third_observation is True
    match = run_review._STABLE_FILE_DESCRIPTOR_RE.fullmatch(
        result.stable_projection["git_dir/HEAD"]
    )
    assert match is not None
    assert match.group("sha256") == hashlib.sha256(mutated_bytes).hexdigest()


def test_establish_baseline_three_disagreeing_observations_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(E) S1 observes A, S2 observes B, S3 observes C: no two consecutive
    observations ever agree, so establishment fails closed once the global
    bound is exhausted -- it must never fall back to comparing S1 against
    S3 directly."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    original = head_path.read_bytes()

    def _flip(content: bytes, salt: int) -> bytes:
        # See test C: mutate a byte inside the branch name, not the "ref: "
        # prefix, so HEAD stays a parseable symbolic-ref file.
        mutated = bytearray(content)
        mutated[-2] = (mutated[-2] + salt) % 256
        return bytes(mutated)

    content_b = _flip(original, 1)
    content_c = _flip(original, 2)
    assert content_b != original
    assert content_c != original
    assert content_c != content_b

    real_open = os.open
    state = {"opens": 0}
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            # Mutate immediately before S2's first open (3) to B, and
            # immediately before S3's first open (5) to C.
            if state["opens"] == 3:
                head_path.write_bytes(content_b)
            elif state["opens"] == 5:
                head_path.write_bytes(content_c)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.establish_protected_git_metadata_baseline(ws)


def test_establish_baseline_intra_observation_inode_replacement_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F) A real (non-timestamp) identity change observed mid-measurement
    -- here, HEAD's inode changing via unlink+rewrite immediately before the
    first open -- is never tolerated, even during bootstrap: establishment
    fails closed immediately rather than continuing to another observation."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    content = head_path.read_bytes()
    real_open = os.open
    state = {"opens": 0}
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            if state["opens"] == 1:
                head_path.unlink()
                head_path.write_bytes(content)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.establish_protected_git_metadata_baseline(ws)

    assert state["opens"] == 1


def test_establish_baseline_unreadable_member_prevents_establishment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(G) An observation that cannot produce a complete trustworthy
    projection -- here, HEAD becoming unreadable mid-observation -- must be
    discarded outright; it can never participate in establishing a baseline."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    real_open = os.open
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            raise OSError("simulated unreadable")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.establish_protected_git_metadata_baseline(ws)


def test_establish_baseline_persistent_disagreement_blocks_after_exactly_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(L) If no two consecutive observations ever agree, establishment
    fails closed after exactly the global bound of independently rebuilt
    observations -- never fewer, never more, and never a nested retry
    multiplying that count."""

    ws = make_workspace(tmp_path)
    head_path = run_review.git_dir(ws) / "HEAD"
    original = head_path.read_bytes()

    call_counter = {"n": 0}
    real_build = run_review._build_provisional_protected_git_metadata_observation

    def counting_build(cwd: Path, **kwargs: Any) -> dict[str, str]:
        call_counter["n"] += 1
        # See test C: mutate a byte inside the branch name, not the "ref: "
        # prefix, so HEAD stays a parseable symbolic-ref file.
        mutated = bytearray(original)
        mutated[-2] = (mutated[-2] + call_counter["n"]) % 256
        head_path.write_bytes(bytes(mutated))
        return real_build(cwd, **kwargs)

    monkeypatch.setattr(
        run_review,
        "_build_provisional_protected_git_metadata_observation",
        counting_build,
    )

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.establish_protected_git_metadata_baseline(ws)

    assert call_counter["n"] == run_review._MAX_INITIAL_BASELINE_OBSERVATIONS


def test_establish_baseline_rebuilds_whole_tree_each_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(M) Each observation independently rebuilds the complete protected
    tree from scratch -- a file added to the tree strictly between two
    observation attempts is detected as a fresh disagreement, not silently
    missed by reusing a cached prior subtree measurement."""

    ws = make_workspace(tmp_path)
    extra_file = run_review.git_dir(ws) / "extra-loose-like-file"

    call_counter = {"n": 0}
    real_build = run_review._build_provisional_protected_git_metadata_observation

    def adding_file_before_second_attempt(cwd: Path, **kwargs: Any) -> dict[str, str]:
        call_counter["n"] += 1
        if call_counter["n"] == 2:
            extra_file.write_bytes(b"synthetic content for tree-rebuild proof\n")
        return real_build(cwd, **kwargs)

    monkeypatch.setattr(
        run_review,
        "_build_provisional_protected_git_metadata_observation",
        adding_file_before_second_attempt,
    )

    result = run_review.establish_protected_git_metadata_baseline(ws)

    assert result.observations_used == 3
    assert "git_dir/extra-loose-like-file" in result.stable_projection


def test_protected_git_metadata_same_size_content_mutation_between_retries_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(D) Attempt 1 is genuinely timestamp-only unstable (content still A,
    matching trusted evidence) and is retry-eligible. Before the retry's
    read completes, the file is mutated in place to same-size content B.
    The retry must NOT accept B as coherent merely because it read
    consistently within itself -- it fails closed against the trusted
    evidence."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    original = head_path.read_bytes()
    mutated = bytearray(original)
    mutated[0] ^= 0xFF
    mutated_bytes = bytes(mutated)
    assert len(mutated_bytes) == len(original)

    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})
    _open_count_content_swap(
        monkeypatch, head_path, swap_on_open_number=2, new_content=mutated_bytes
    )

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)


def test_protected_git_metadata_inode_replacement_between_retries_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(E) Attempt 1 is genuinely timestamp-only unstable and retry-eligible.
    Before the retry's read completes, the file is replaced (unlink +
    recreate, a new inode) with byte-identical content. Must still fail
    closed: inode identity, not just content, is load-bearing."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    original = head_path.read_bytes()

    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    real_open = os.open
    state = {"opens": 0}
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            if state["opens"] == 2:
                head_path.unlink()
                head_path.write_bytes(original)  # byte-identical, new inode
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)


def test_protected_git_metadata_mode_mutation_between_retries_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F) Attempt 1 is genuinely timestamp-only unstable and retry-eligible.
    Before the retry's read completes, the file's mode changes (same
    dev/ino/size/nlink/content). Must fail closed -- mode is load-bearing,
    caught at the intra-measurement coherence gate itself."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino
    original_mode = head_path.stat().st_mode & 0o777
    replacement_mode = 0o600 if original_mode != 0o600 else 0o644

    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    real_open = os.open
    state = {"opens": 0}
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            if state["opens"] == 2:
                head_path.chmod(replacement_mode)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    try:
        with pytest.raises(run_review.EvidenceIntegrityFailure):
            run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)
    finally:
        head_path.chmod(original_mode)


def test_protected_git_metadata_symlink_replacement_between_retries_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F) Same as above but the replacement is a symlink, not a mode
    change. Still fails closed (via the existing O_NOFOLLOW protection,
    unaffected by the trusted-baseline mechanism)."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino

    _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={2})

    real_open = os.open
    state = {"opens": 0}
    target = str(head_path)

    def wrapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == target:
            state["opens"] += 1
            if state["opens"] == 2:
                head_path.unlink()
                head_path.symlink_to(ws / "app.txt")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", wrapped_open)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)


def test_protected_git_metadata_multiple_unstable_paths_each_validated_against_own_trusted_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(G) Two distinct member paths (HEAD and refs/heads/main) each become
    timestamp-only unstable on their own first measurement. Each is
    validated against its own entry in the same constant trusted baseline,
    independent of the other, and the whole snapshot still succeeds within
    the shared 3-attempt budget."""

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)

    head_path = run_review.git_dir(ws) / "HEAD"
    ref_path = run_review.git_dir(ws) / "refs" / "heads" / "main"
    head_ino = head_path.stat().st_ino
    ref_ino = ref_path.stat().st_ino

    real_stat_identity = run_review._stat_identity
    state = {"head_calls": 0, "ref_calls": 0}

    def perturbing(metadata: object) -> tuple:
        real = real_stat_identity(metadata)
        if metadata.st_ino == head_ino:  # type: ignore[union-attr]
            state["head_calls"] += 1
            if state["head_calls"] == 2:
                dev, ino, mode, size, nlink, mtime_ns, ctime_ns = real
                return (dev, ino, mode, size, nlink, mtime_ns, ctime_ns + 1)
        elif metadata.st_ino == ref_ino:  # type: ignore[union-attr]
            state["ref_calls"] += 1
            if state["ref_calls"] == 2:
                dev, ino, mode, size, nlink, mtime_ns, ctime_ns = real
                return (dev, ino, mode, size, nlink, mtime_ns, ctime_ns + 1)
        return real

    monkeypatch.setattr(run_review, "_stat_identity", perturbing)

    result = run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)

    assert result
    assert state["head_calls"] >= 2
    assert state["ref_calls"] >= 2


def test_protected_git_metadata_linked_worktree_gitfile_instability_requires_trusted_evidence_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(H) The root `.git` entry's before/after comparison mechanism is
    architecturally unaffected when the entry is a directory (see the
    dedicated root-gitfile-transient-retry test below), but when it is a
    linked-worktree *regular* indirection file, its own intra-measurement
    instability must go through the exact same trusted-evidence proof as
    any other member -- it does not get to bypass it. Without a trusted
    baseline, timestamp-only instability at the root gitfile still fails
    closed; with a matching one, it is still retry-eligible."""

    _, linked = make_linked_workspace(tmp_path)
    gitfile = linked / ".git"
    assert gitfile.is_file()
    target_ino = gitfile.stat().st_ino

    # No trusted baseline at all: must fail closed (same as requirement C,
    # but for the root entry specifically).
    state_no_trust = _perturb_stat_identity_ctime_for_inode(
        monkeypatch, target_ino, perturb_calls={2}
    )
    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.protected_git_metadata_snapshot(linked)
    assert state_no_trust["calls"] >= 1


def test_protected_git_metadata_root_gitfile_transient_retry_shares_global_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-existing root-.git-entry transient-retry regression, rerun
    to confirm it still passes unchanged under the new shared attempt
    budget (this is the same scenario as
    test_protected_git_metadata_bounded_retry_recovers_from_transient_timestamp_only_instability,
    kept here to anchor requirement F explicitly)."""

    ws = make_workspace(tmp_path)
    entry_path = str((ws / ".git").resolve())
    sequence = iter(
        [
            _synthetic_git_entry(path=entry_path, mtime_ns=100, ctime_ns=100),
            _synthetic_git_entry(path=entry_path, mtime_ns=200, ctime_ns=200),
            _synthetic_git_entry(path=entry_path, mtime_ns=300, ctime_ns=300),
            _synthetic_git_entry(path=entry_path, mtime_ns=300, ctime_ns=300),
        ]
    )
    monkeypatch.setattr(run_review, "_workspace_git_entry_descriptor", lambda cwd, **kwargs: next(sequence))

    result = run_review.protected_git_metadata_snapshot(ws)

    assert result


def test_protected_git_metadata_mixed_root_and_member_instability_shares_one_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-entry instability that resolves within attempt 1, followed by
    member-file (HEAD) instability during attempt 2, must still consume
    from the same shared 3-attempt total budget -- not 3 root retries
    times 3 member retries -- and succeed by the 3rd attempt.

    `ws` is an ordinary (non-linked-worktree) repository, so `git_dir` and
    `git_common_dir` are the same physical directory and a fully coherent
    attempt walks it twice (once per role) -- HEAD is therefore measured
    twice per attempt when nothing aborts early. Root instability is
    forced for exactly one attempt via the same synthetic-sequence
    technique as the pre-existing root-entry tests (both the "before" and
    "after" calls of attempt 1 are satisfied from the synthetic sequence,
    so attempt 1's tree walk runs for real and completes coherently).
    Member instability is then targeted at the *second* attempt's tree
    walk specifically, so it lands after root has already resolved.
    """

    ws = make_workspace(tmp_path)
    trusted = run_review.protected_git_metadata_snapshot(ws)
    head_path = run_review.git_dir(ws) / "HEAD"
    target_ino = head_path.stat().st_ino

    entry_path = str((ws / ".git").resolve())
    real_entry_descriptor = run_review._workspace_git_entry_descriptor
    root_sequence = iter(
        [
            _synthetic_git_entry(path=entry_path, mtime_ns=1, ctime_ns=1),  # attempt 1 before
            _synthetic_git_entry(path=entry_path, mtime_ns=2, ctime_ns=2),  # attempt 1 after (timestamp-only)
        ]
    )

    def entry_descriptor_stub(cwd: Path, **kwargs: object) -> tuple[str, str]:
        try:
            return next(root_sequence)
        except StopIteration:
            return real_entry_descriptor(cwd, **kwargs)

    monkeypatch.setattr(run_review, "_workspace_git_entry_descriptor", entry_descriptor_stub)

    tree_walk_calls = {"n": 0}
    real_snapshot_tree = run_review.snapshot_type_aware_tree

    def counting_snapshot_tree(root: Path, **kwargs: object) -> dict[str, str]:
        tree_walk_calls["n"] += 1
        return real_snapshot_tree(root, **kwargs)

    monkeypatch.setattr(run_review, "snapshot_type_aware_tree", counting_snapshot_tree)

    # Attempt 1's tree walk measures HEAD coherently twice (calls 1-4 and
    # 5-8, one per role). Attempt 2's first (git_dir) measurement of HEAD
    # begins at call 9; its "opened" observation is call 10.
    state = _perturb_stat_identity_ctime_for_inode(monkeypatch, target_ino, perturb_calls={10})

    result = run_review.protected_git_metadata_snapshot(ws, trusted_baseline=trusted)

    assert result
    assert state["calls"] > 8  # member instability was actually exercised
    # Two tree-walk roles per attempt, three attempts total: never more
    # than a flat 2x3 budget, regardless of mixing root- and member-level
    # instability sources (never a nested 3x3 multiplication).
    assert tree_walk_calls["n"] <= 2 * run_review._PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS


def test_protected_git_metadata_cross_phase_timestamp_stability_still_passes(
    tmp_path: Path,
) -> None:
    """The original cross-phase-projection regression, rerun to confirm it
    remains unaffected by the intra-measurement retry mechanism added
    above (requirement H)."""

    ws = make_workspace(tmp_path)
    before = run_review.protected_git_metadata_snapshot(ws)
    index_path = run_review.git_dir(ws) / "index"
    os.utime(index_path, None)
    after = run_review.protected_git_metadata_snapshot(ws)
    assert after == before


def test_capability_probe_linked_worktree_gitfile_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    _, linked = make_linked_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        help_write_path=str(linked / ".git"),
        write_unexpected=str(marker),
    )

    exit_code = run_review_cli(tmp_path, linked, codex)

    assert exit_code == 3
    assert not marker.exists()


def test_reviewer_linked_worktree_gitfile_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    _, linked = make_linked_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(linked / ".git"),
    )

    exit_code = run_review_cli(tmp_path, linked, codex)

    assert exit_code == 4


def test_linked_worktree_review_receipt_binds_gitfile_identity(
    tmp_path: Path,
) -> None:
    _, linked = make_linked_workspace(tmp_path)
    codex = write_fake_codex(
        tmp_path,
        verdict_json=request_changes_verdict_json(),
    )
    evidence_root = tmp_path / "reviews"

    exit_code = run_review_cli(
        tmp_path,
        linked,
        codex,
        review_run_id="linked-review",
        evidence_directory=evidence_root,
    )

    receipt = json.loads(
        (evidence_root / "linked-review" / "review-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    current = run_review.protected_git_metadata_snapshot(linked)
    assert exit_code == 1
    assert receipt["workspace_git_entry_preserved"] is True
    assert receipt["reviewed_workspace_git_entry_hash"] == (
        run_review.workspace_git_entry_hash_from_snapshot(current)
    )


def test_deterministic_check_repository_drift_stops_before_reviewer(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
    )

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        check_commands=["git add app.txt"],
    )

    assert exit_code == 3
    assert not marker.exists()
    assert run_review.git_index_clean(ws) is False


def test_deterministic_check_semantic_index_flag_drift_stops_before_reviewer(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    before_identity = run_review.git_index_identity(ws)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
    )

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        check_commands=["git update-index --assume-unchanged app.txt"],
    )

    assert exit_code == 3
    assert not marker.exists()
    assert run_review.git_index_clean(ws) is True
    assert run_review.git_index_identity(ws) != before_identity


def test_codex_capability_probe_source_drift_stops_before_model_execution(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
        help_write_path=str(ws / "app.txt"),
    )

    exit_code = run_review_cli(tmp_path, ws, codex)

    assert exit_code == 3
    assert not marker.exists()
    assert (ws / "app.txt").read_text(encoding="utf-8") == (
        "capability probe touched source\n"
    )


def test_failed_codex_capability_probe_still_reports_source_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = make_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
        help_write_path=str(ws / "app.txt"),
        top_help="Usage: unsupported-provider\n",
        exec_help="Usage: unsupported-provider exec\n",
    )

    exit_code = run_review_cli(tmp_path, ws, codex)

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "STATUS=PREFLIGHT_FAILURE:IMMUTABILITY_VIOLATION" in output
    assert "UNSUPPORTED_CODEX_CLI_CONTRACT" not in output
    assert not marker.exists()


def test_codex_capability_probe_index_flag_drift_stops_before_model_execution(
    tmp_path: Path,
) -> None:
    ws = make_workspace(tmp_path)
    before_identity = run_review.git_index_identity(ws)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
        help_assume_unchanged_workspace=str(ws),
    )

    exit_code = run_review_cli(tmp_path, ws, codex)

    assert exit_code == 3
    assert not marker.exists()
    assert run_review.git_index_clean(ws) is True
    assert run_review.git_index_identity(ws) != before_identity


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


def test_review_evidence_directory_inside_workspace_is_rejected(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    marker = tmp_path / "reviewer-started"
    codex = write_fake_codex(
        tmp_path,
        verdict_json=approve_verdict_json(),
        write_unexpected=str(marker),
    )
    evidence_root = ws / ".review-evidence"

    exit_code = run_review_cli(
        tmp_path,
        ws,
        codex,
        evidence_directory=evidence_root,
        review_run_id="inside-workspace",
    )

    assert exit_code == 3
    assert not marker.exists()
    assert not (evidence_root / "inside-workspace").exists()


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


def test_read_canonical_evidence_accepts_unchanged_regular_artifact(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    artifact = evidence / "receipt.json"
    artifact.write_bytes(b'{"status":"ok"}')

    measured = run_review.read_canonical_evidence(evidence, ("receipt.json",))

    assert measured.file_bytes["receipt.json"] == b'{"status":"ok"}'
    assert measured.identity["receipt.json"].startswith("file:")


def test_read_canonical_evidence_rejects_same_size_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    artifact = evidence / "receipt.json"
    original = b'{"value":"AAAA"}'
    replacement = b'{"value":"BBBB"}'
    assert len(original) == len(replacement)
    artifact.write_bytes(original)
    target_inode = artifact.lstat().st_ino
    real_read = os.read
    mutated = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        data = real_read(descriptor, count)
        if data and not mutated and os.fstat(descriptor).st_ino == target_inode:
            mutated = True
            artifact.write_bytes(replacement)
            current = artifact.stat()
            os.utime(
                artifact,
                ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000),
            )
        return data

    monkeypatch.setattr(os, "read", racing_read)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.read_canonical_evidence(evidence, ("receipt.json",))


def test_read_canonical_evidence_rejects_pathname_replacement_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    artifact = evidence / "receipt.json"
    artifact.write_bytes(b'{"value":"original"}')
    target_inode = artifact.lstat().st_ino
    held_original = tmp_path / "held-original.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b'{"value":"replaced"}')
    real_read = os.read
    replaced = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        data = real_read(descriptor, count)
        if data and not replaced and os.fstat(descriptor).st_ino == target_inode:
            replaced = True
            artifact.replace(held_original)
            replacement.replace(artifact)
        return data

    monkeypatch.setattr(os, "read", racing_read)

    with pytest.raises(run_review.EvidenceIntegrityFailure):
        run_review.read_canonical_evidence(evidence, ("receipt.json",))
    assert held_original.exists(), "the originally opened inode remains alive by another name"


def test_implementation_loader_parses_captured_bytes_without_path_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    run_directory = make_implementation_run_directory(tmp_path, workspace=workspace)
    receipt_path = run_directory / "receipt.json"
    external = tmp_path / "later-receipt.json"
    later_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    later_receipt["run_id"] = "unrelated-later-run"
    external.write_text(json.dumps(later_receipt), encoding="utf-8")
    real_reader = run_review.read_canonical_evidence

    def capture_then_replace(root: Path, names: tuple[str, ...]):
        measured = real_reader(root, names)
        receipt_path.unlink()
        receipt_path.symlink_to(external)
        return measured

    monkeypatch.setattr(run_review, "read_canonical_evidence", capture_then_replace)

    loaded = run_review.load_implementation_evidence(
        run_directory,
        workspace,
        "run-1",
        1,
    )

    assert receipt_path.is_symlink()
    assert loaded["receipt"]["run_id"] == "run-1"


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
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
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
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_prefix_confusion_dist_evil_denied(tmp_path: Path) -> None:
    ws = make_files_workspace(
        tmp_path, {"package.json": "{}\n", "dist-evil/bundle.js": "console.log(1);\n"}
    )
    run_dir = make_implementation_run_directory(
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_side_effect_without_authorization_fails_closed(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"


def test_source_allowlist_wins_over_overlapping_side_effect_root(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["dist/bundle.js"],
        allowed_side_effect_paths=["dist"],
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
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
    )
    with pytest.raises(run_review.PreflightFailure) as excinfo:
        preflight_for(tmp_path, ws, implementation_run_directory=run_dir, allowed_paths=["package.json"])
    assert excinfo.value.category == "CHANGED_PATH_OUT_OF_SCOPE"
    assert "coverage/report.html" in excinfo.value.message


def test_wrong_run_evidence_cannot_authorize_side_effects_end_to_end(tmp_path: Path) -> None:
    ws = make_files_workspace(tmp_path, {"package.json": "{}\n", "dist/bundle.js": "console.log(1);\n"})
    run_dir = make_implementation_run_directory(
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
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
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
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
        tmp_path,
        workspace=ws,
        run_id="impl-1",
        attempt=1,
        allowed_changed_paths=["package.json"],
        allowed_side_effect_paths=["dist"],
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


# ---------------------------------------------------------------------------
# Canonical effective-index identity (TARGET04_CANONICAL_INDEX_IDENTITY_FIX):
# `canonical_index_projection`, `git_index_identity`,
# `protected_git_metadata_preserved`, `describe_index_representation_diff`.
# ---------------------------------------------------------------------------


def make_committed_workspace(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A tiny Git repo with everything committed -- a clean, matching index/HEAD/worktree."""

    ws = tmp_path / "index-workspace"
    ws.mkdir()
    payload = files if files is not None else {"a.txt": "content-a\n", "b.txt": "content-b\n"}
    for rel, content in payload.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "baseline"], ws)
    return ws


def _rev_parse(ws: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=str(ws), capture_output=True, text=True, check=True
    ).stdout.strip()


def test_canonical_index_identity_tolerates_git_stat_cache_refresh(tmp_path: Path) -> None:
    """(Section 10, positive) Reproduces the exact Run-004 class: a benign
    Git stat-cache refresh (the first `git` command to inspect a tree whose
    tracked file mtime changed since the index was last written) rewrites
    `.git/index`'s raw physical bytes, with every repository-relevant index
    semantic unchanged. Canonical identity must be equal even though the
    raw descriptor differs."""

    ws = make_committed_workspace(tmp_path)
    before_projection = run_review.canonical_index_projection(ws)
    before_identity = run_review.git_index_identity(ws)
    index_path = run_review.git_dir(ws) / "index"
    before_raw_index_bytes = index_path.read_bytes()

    os.utime(ws / "a.txt", (time.time() + 5, time.time() + 5))
    subprocess.run(["git", "status"], cwd=str(ws), capture_output=True, check=True)

    after_projection = run_review.canonical_index_projection(ws)
    after_identity = run_review.git_index_identity(ws)
    after_raw_index_bytes = index_path.read_bytes()

    assert before_raw_index_bytes != after_raw_index_bytes, (
        "test setup must actually trigger a raw index rewrite"
    )
    assert before_projection is not None
    assert after_projection == before_projection
    assert after_identity is not None
    assert after_identity == before_identity


def test_protected_git_metadata_preserved_tolerates_index_only_representation_change(
    tmp_path: Path,
) -> None:
    """(Section 10, end-to-end) CANONICAL_INDEX_EQUAL=YES,
    RAW_INDEX_DESCRIPTOR_EQUAL=NO, REPOSITORY_IDENTITY_PRESERVED=YES. No
    source/product change should be attributed to this."""

    ws = make_committed_workspace(tmp_path)
    before_metadata = run_review.protected_git_metadata_snapshot(ws)
    before_index_identity = run_review.git_index_identity(ws)

    os.utime(ws / "a.txt", (time.time() + 5, time.time() + 5))
    subprocess.run(["git", "status"], cwd=str(ws), capture_output=True, check=True)

    after_metadata = run_review.protected_git_metadata_snapshot(
        ws, trusted_baseline=before_metadata
    )
    after_index_identity = run_review.git_index_identity(ws)

    assert before_metadata["git_dir/index"] != after_metadata["git_dir/index"]
    assert before_index_identity == after_index_identity
    assert (
        run_review.protected_git_metadata_preserved(
            before_metadata,
            after_metadata,
            canonical_index_identity_before=before_index_identity,
            canonical_index_identity_after=after_index_identity,
        )
        is True
    )


def test_canonical_index_identity_changes_when_path_added(tmp_path: Path) -> None:
    """(Section 11.A) staged path added."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    (ws / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "new.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_when_path_removed(tmp_path: Path) -> None:
    """(Section 11.B) staged path removed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    _git(["rm", "-q", "a.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_when_staged_object_id_changes(tmp_path: Path) -> None:
    """(Section 11.C) staged object id changed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    (ws / "a.txt").write_text("different content\n", encoding="utf-8")
    _git(["add", "a.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_when_mode_changes(tmp_path: Path) -> None:
    """(Section 11.D) staged mode changed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    (ws / "a.txt").chmod(0o755)
    _git(["add", "a.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_with_unmerged_stages(tmp_path: Path) -> None:
    """(Section 11.E) stage number changed (an unresolved merge conflict
    introduces stage 1/2/3 entries in place of the single stage-0 entry)."""

    ws = tmp_path / "conflict-ws"
    ws.mkdir()
    (ws / "f.txt").write_text("base\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "base"], ws)
    base_commit = _rev_parse(ws)
    before = run_review.git_index_identity(ws)

    _git(["checkout", "-q", "-b", "branch-a", base_commit], ws)
    (ws / "f.txt").write_text("a-version\n", encoding="utf-8")
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-am", "a"], ws)
    _git(["checkout", "-q", "-b", "branch-b", base_commit], ws)
    (ws / "f.txt").write_text("b-version\n", encoding="utf-8")
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-am", "b"], ws)
    subprocess.run(["git", "merge", "branch-a", "-q"], cwd=str(ws), capture_output=True)

    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after
    projection = run_review.canonical_index_projection(ws)
    assert projection is not None
    assert any(
        key.startswith("entry:") and key.rsplit(":", 1)[-1] in ("1", "2", "3")
        for key in projection
    )


def test_canonical_index_identity_changes_with_assume_unchanged_toggle(tmp_path: Path) -> None:
    """(Section 11.F) assume-unchanged semantic state changed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    _git(["update-index", "--assume-unchanged", "a.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_with_skip_worktree_toggle(tmp_path: Path) -> None:
    """(Section 11.G) skip-worktree semantic state changed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    _git(["update-index", "--skip-worktree", "a.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_identity_changes_with_intent_to_add(tmp_path: Path) -> None:
    """(Section 11.H) intent-to-add state changed."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    (ws / "new.txt").write_text("new\n", encoding="utf-8")
    _git(["add", "-N", "new.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after
    projection = run_review.canonical_index_projection(ws)
    assert projection is not None
    assert any(
        key.startswith("entry:") and "intent_to_add=1" in value
        for key, value in projection.items()
    )


def test_canonical_index_identity_changes_with_resolve_undo_state(tmp_path: Path) -> None:
    """(Section 11.I) resolve-undo canonical state changed: resolving a
    merge conflict (which records resolve-undo information) changes the
    identity relative to the still-unmerged state, and the resolve-undo
    record is present in the projection."""

    ws = tmp_path / "resolve-undo-ws"
    ws.mkdir()
    (ws / "f.txt").write_text("base\n", encoding="utf-8")
    _git(["init", "-q"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"], ws)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "base"], ws)
    base_commit = _rev_parse(ws)

    _git(["checkout", "-q", "-b", "branch-a", base_commit], ws)
    (ws / "f.txt").write_text("a-version\n", encoding="utf-8")
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-am", "a"], ws)
    _git(["checkout", "-q", "-b", "branch-b", base_commit], ws)
    (ws / "f.txt").write_text("b-version\n", encoding="utf-8")
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-am", "b"], ws)
    subprocess.run(["git", "merge", "branch-a", "-q"], cwd=str(ws), capture_output=True)
    before = run_review.git_index_identity(ws)

    subprocess.run(["git", "checkout", "--ours", "--", "f.txt"], cwd=str(ws), capture_output=True, check=True)
    _git(["add", "f.txt"], ws)

    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after
    projection = run_review.canonical_index_projection(ws)
    assert projection is not None
    assert any(key.startswith("resolve_undo:") for key in projection)


def test_canonical_index_identity_changes_when_path_bytes_change(tmp_path: Path) -> None:
    """(Section 11.J) path bytes changed (a rename)."""

    ws = make_committed_workspace(tmp_path)
    before = run_review.git_index_identity(ws)
    _git(["mv", "a.txt", "a-renamed.txt"], ws)
    after = run_review.git_index_identity(ws)
    assert before is not None and after is not None and before != after


def test_canonical_index_projection_blocks_on_unclassified_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(Section 11.K) An unrecognized/unclassified index state -- here, a
    tag letter `ls-files --stage -t -v -z` would only ever emit for
    -d/-m/-o/-k/--resolve-undo listing modes this query never requests --
    must block (return None), never be silently accepted."""

    ws = make_committed_workspace(tmp_path)
    real_run_git_bytes = run_review._run_git_bytes

    def faking(args: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess:
        if args[:2] == ["ls-files", "--stage"]:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=0,
                stdout=b"? 100644 " + b"0" * 40 + b" 0\tweird.txt\x00",
                stderr=b"",
            )
        return real_run_git_bytes(args, cwd, **kwargs)

    monkeypatch.setattr(run_review, "_run_git_bytes", faking)

    assert run_review.canonical_index_projection(ws) is None
    assert run_review.git_index_identity(ws) is None


def test_canonical_index_projection_blocks_on_duplicate_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(Section 11.K) A duplicate (path, stage) pair is an internally
    inconsistent observation and must block."""

    ws = make_committed_workspace(tmp_path)
    real_run_git_bytes = run_review._run_git_bytes
    duplicate_record = b"H 100644 " + b"a" * 40 + b" 0\ta.txt\x00"

    def faking(args: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess:
        if args[:2] == ["ls-files", "--stage"]:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=0,
                stdout=duplicate_record + duplicate_record,
                stderr=b"",
            )
        return real_run_git_bytes(args, cwd, **kwargs)

    monkeypatch.setattr(run_review, "_run_git_bytes", faking)

    assert run_review.canonical_index_projection(ws) is None


def test_canonical_index_projection_is_byte_safe_for_non_utf8_paths() -> None:
    """Byte-safe path handling: a raw path byte sequence that is not valid
    UTF-8 parses without loss, and different raw bytes never collapse to
    the same canonical identity."""

    record_a = b"H 100644 " + b"a" * 40 + b" 0\tweird-\xff-name.txt"
    record_b = b"H 100644 " + b"a" * 40 + b" 0\tweird-\xfe-name.txt"
    parsed_a = run_review._parse_canonical_index_stage_record(record_a)
    parsed_b = run_review._parse_canonical_index_stage_record(record_b)
    assert parsed_a is not None and parsed_b is not None
    assert parsed_a[0] == b"weird-\xff-name.txt"
    assert parsed_a[0].hex() != parsed_b[0].hex()


def test_protected_git_metadata_preserved_rejects_non_index_key_change() -> None:
    """(Section 7) A change to any NON-index protected key must still
    block, even when canonical index identity matches -- this correction
    only tolerates the index representation issue, nothing else."""

    before = {
        "git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64,
        "git_dir/HEAD": "file:dev=1:ino=2:mode=0644:size=1:nlink=1:sha256=" + "b" * 64,
    }
    after = dict(before)
    after["git_dir/HEAD"] = "file:dev=1:ino=2:mode=0644:size=1:nlink=1:sha256=" + "c" * 64
    assert (
        run_review.protected_git_metadata_preserved(
            before, after, canonical_index_identity_before="same", canonical_index_identity_after="same"
        )
        is False
    )


def test_protected_git_metadata_preserved_rejects_when_canonical_index_identity_differs() -> None:
    """(Section 6) A raw index descriptor difference is only tolerated when
    the canonical index identity itself is proven unchanged."""

    before = {"git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    after = {"git_dir/index": "file:dev=1:ino=2:mode=0644:size=1:nlink=1:sha256=" + "b" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            before, after, canonical_index_identity_before="X", canonical_index_identity_after="Y"
        )
        is False
    )
    assert (
        run_review.protected_git_metadata_preserved(
            before, after, canonical_index_identity_before=None, canonical_index_identity_after=None
        )
        is False
    )


def test_protected_git_metadata_preserved_true_when_nothing_changed() -> None:
    same = {"git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            same, dict(same), canonical_index_identity_before="X", canonical_index_identity_after="X"
        )
        is True
    )


def test_protected_git_metadata_preserved_false_on_empty_snapshot() -> None:
    before = {"git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            before, {}, canonical_index_identity_before="X", canonical_index_identity_after="X"
        )
        is False
    )


def test_describe_index_representation_diff_reports_bounded_fields() -> None:
    """(Section 15) Bounded diagnostic: reports exactly which stable fields
    differ, never an unbounded dump."""

    before = "file:dev=1:ino=100:mode=0644:size=10:nlink=1:sha256=" + "a" * 64
    after = "file:dev=1:ino=200:mode=0644:size=10:nlink=1:sha256=" + "b" * 64
    diff = run_review.describe_index_representation_diff(before, after)
    assert diff == {
        "dev_changed": False,
        "ino_changed": True,
        "mode_changed": False,
        "size_changed": False,
        "nlink_changed": False,
        "sha256_changed": True,
    }


def test_describe_index_representation_diff_none_on_non_matching_descriptor() -> None:
    assert run_review.describe_index_representation_diff(None, "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64) is None
    assert run_review.describe_index_representation_diff("not-a-descriptor", "also-not-one") is None


# ---------------------------------------------------------------------------
# F01 (Section 4/21): bounded coherent multi-observation canonical index
# projection -- two *consecutive* identical complete observations required,
# up to `_MAX_CANONICAL_INDEX_OBSERVATIONS`, flat and non-nested.
# ---------------------------------------------------------------------------


def _sequence_of_projections(
    monkeypatch: pytest.MonkeyPatch, sequence: list[dict[str, str] | None]
) -> dict[str, int]:
    calls = {"n": 0}

    def fake(cwd: Path) -> dict[str, str] | None:
        calls["n"] += 1
        value = sequence[calls["n"] - 1]
        return dict(value) if value is not None else None

    monkeypatch.setattr(run_review, "_build_canonical_index_projection_once", fake)
    return calls


def test_canonical_index_projection_two_matching_observations_establish_in_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(F01.A) observation 1 = A, observation 2 = A -> establish A in
    exactly 2 complete observations."""

    calls = _sequence_of_projections(monkeypatch, [{"a": "1"}, {"a": "1"}, {"a": "999"}])
    result = run_review.canonical_index_projection(Path("/unused"))
    assert result == {"a": "1"}
    assert calls["n"] == 2


def test_canonical_index_projection_disagree_then_agree_establishes_on_third(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(F01.B) observation 1 = A, observation 2 = B, observation 3 = B ->
    establish B."""

    calls = _sequence_of_projections(monkeypatch, [{"a": "1"}, {"a": "2"}, {"a": "2"}])
    result = run_review.canonical_index_projection(Path("/unused"))
    assert result == {"a": "2"}
    assert calls["n"] == 3


def test_canonical_index_projection_three_disagreeing_observations_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(F01.C) A -> B -> C -> canonical state unavailable after the bound."""

    calls = _sequence_of_projections(monkeypatch, [{"a": "1"}, {"a": "2"}, {"a": "3"}])
    result = run_review.canonical_index_projection(Path("/unused"))
    assert result is None
    assert calls["n"] == 3


def test_canonical_index_projection_incomplete_first_observation_not_authoritative_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(F01.D) One complete projection attempt cannot be built (returns
    `None`) -- that incomplete result cannot become authoritative by
    itself, but two later consecutive complete matches still can."""

    calls = _sequence_of_projections(monkeypatch, [None, {"a": "1"}, {"a": "1"}])
    result = run_review.canonical_index_projection(Path("/unused"))
    assert result == {"a": "1"}
    assert calls["n"] == 3


def test_canonical_index_projection_all_incomplete_observations_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(F01.D) Every observation incomplete -> unavailable, never an
    empty/default projection."""

    calls = _sequence_of_projections(monkeypatch, [None, None, None])
    result = run_review.canonical_index_projection(Path("/unused"))
    assert result is None
    assert calls["n"] == 3


def test_canonical_index_projection_bound_is_flat_never_nested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly `_MAX_CANONICAL_INDEX_OBSERVATIONS` calls to the single-shot
    builder in the worst (all-disagreeing) case -- one whole-projection
    attempt per loop iteration, never a retry loop nested inside another."""

    calls = _sequence_of_projections(monkeypatch, [{"a": "1"}, {"a": "2"}, {"a": "3"}])
    run_review.canonical_index_projection(Path("/unused"))
    assert calls["n"] == run_review._MAX_CANONICAL_INDEX_OBSERVATIONS


def test_build_canonical_index_projection_once_rejects_intent_to_add_hybrid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F01.E) Query outputs within ONE provisional observation that
    correspond to inconsistent logical states -- here, `status` reports a
    path as intent-to-add while `stage` simultaneously holds a real,
    non-empty staged object for that same path -- must not be enough by
    itself to establish authority: this exact single observation is
    rejected outright (`None`), so it can never even participate as a
    "previous" value in the bounded coherence comparison. This is the
    original Codex-discovered "impossible hybrid" reproduction."""

    ws = make_committed_workspace(tmp_path)
    real_run_git_bytes = run_review._run_git_bytes

    def faking(args: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess:
        if args[:2] == ["ls-files", "--stage"]:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=0,
                stdout=b"H 100644 " + b"a" * 40 + b" 0\ta.txt\x00",
                stderr=b"",
            )
        if args[:2] == ["status", "--porcelain=v2"]:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=0,
                stdout=(
                    b"1 .A N... 000000 000000 100644 "
                    + b"0" * 40
                    + b" "
                    + b"0" * 40
                    + b" a.txt\x00"
                ),
                stderr=b"",
            )
        return real_run_git_bytes(args, cwd, **kwargs)

    monkeypatch.setattr(run_review, "_run_git_bytes", faking)

    assert run_review._build_canonical_index_projection_once(ws) is None
    assert run_review.canonical_index_projection(ws) is None


# ---------------------------------------------------------------------------
# F02 (Section 5/21): canonical index identity must be non-`None` and equal
# on BOTH sides before ANY preservation success path -- even when the raw
# protected-metadata projections are already byte-for-byte identical.
# ---------------------------------------------------------------------------


def test_protected_git_metadata_preserved_raw_equal_canonical_differs_rejected() -> None:
    """(F02) Raw metadata identical, but canonical index identity differs
    between observations -- must still block."""

    same = {"git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            same, dict(same), canonical_index_identity_before="X", canonical_index_identity_after="Y"
        )
        is False
    )


def test_protected_git_metadata_preserved_raw_equal_canonical_unavailable_rejected() -> None:
    """(F02) Raw equality never substitutes for an unavailable canonical
    index identity -- an unavailable (`None`) reading must never compare
    equal to another unavailable reading."""

    same = {"git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            same, dict(same), canonical_index_identity_before=None, canonical_index_identity_after=None
        )
        is False
    )


# ---------------------------------------------------------------------------
# F03 (Section 8/9/21): role-aware index handling -- `git_dir/index` (the
# current worktree's own index) may always use the canonical exception;
# `git_common_dir/index` may use it only when it aliases the same physical
# file (ordinary repository), never in a linked worktree.
# ---------------------------------------------------------------------------


def test_index_metadata_exception_keys_ordinary_repository_both_aliases() -> None:
    """(F03.C) git_dir == git_common_dir -> both alias keys behave
    consistently."""

    metadata = {"@git_dir": "path:/repo/.git", "@git_common_dir": "path:/repo/.git"}
    assert run_review._index_metadata_exception_keys(metadata) == frozenset(
        {"git_dir/index", "git_common_dir/index"}
    )


def test_index_metadata_exception_keys_linked_worktree_current_only() -> None:
    """(F03) git_dir != git_common_dir (linked worktree) -> the current
    worktree's own git_dir/index is eligible, along with the second key
    under which the common-dir tree walk re-discovers that exact same
    physical file (Git's own nested `<common>/worktrees/<name>` layout) --
    but never a genuinely different `git_common_dir/index`."""

    metadata = {
        "@git_dir": "path:/repo/.git/worktrees/linked",
        "@git_common_dir": "path:/repo/.git",
    }
    assert run_review._index_metadata_exception_keys(metadata) == frozenset(
        {"git_dir/index", "git_common_dir/worktrees/linked/index"}
    )


def test_index_metadata_exception_keys_missing_role_info_applies_nothing() -> None:
    """(F03.D) Role info unavailable/inconsistent -> do not apply the
    exception to anything (fails closed)."""

    assert run_review._index_metadata_exception_keys({}) == frozenset()
    assert run_review._index_metadata_exception_keys({"@git_dir": "path:/x"}) == frozenset()


def test_protected_git_metadata_preserved_linked_worktree_current_index_change_tolerated() -> None:
    """(F03.A, synthetic) A raw representation-only change confined to the
    CURRENT linked worktree's own index may still be tolerated."""

    linked_roles = {"@git_dir": "path:/repo/.git/worktrees/linked", "@git_common_dir": "path:/repo/.git"}
    before = {**linked_roles, "git_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    after = {**linked_roles, "git_dir/index": "file:dev=1:ino=2:mode=0644:size=1:nlink=1:sha256=" + "b" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            before, after, canonical_index_identity_before="X", canonical_index_identity_after="X"
        )
        is True
    )


def test_protected_git_metadata_preserved_linked_worktree_common_index_change_rejected() -> None:
    """(F03.B, synthetic) A difference confined to git_common_dir/index in a
    linked worktree must NOT be tolerated, even though the current
    worktree's own canonical index identity is unchanged -- it belongs to a
    different worktree's index and must remain strict."""

    linked_roles = {"@git_dir": "path:/repo/.git/worktrees/linked", "@git_common_dir": "path:/repo/.git"}
    before = {**linked_roles, "git_common_dir/index": "file:dev=1:ino=1:mode=0644:size=1:nlink=1:sha256=" + "a" * 64}
    after = {**linked_roles, "git_common_dir/index": "file:dev=1:ino=2:mode=0644:size=1:nlink=1:sha256=" + "b" * 64}
    assert (
        run_review.protected_git_metadata_preserved(
            before, after, canonical_index_identity_before="X", canonical_index_identity_after="X"
        )
        is False
    )


def test_canonical_index_identity_real_linked_worktree_tolerates_own_representation_refresh(
    tmp_path: Path,
) -> None:
    """(F03.A, real linked worktree) The linked worktree's own canonical
    index identity tolerates a benign stat-cache refresh confined to ITS OWN
    (git_dir/index) representation, exactly like the ordinary-repository
    case, with git_dir != git_common_dir genuinely observed."""

    primary, linked = make_linked_workspace(tmp_path)
    _git(["add", "-A"], linked)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "linked"], linked)

    metadata_before = run_review.protected_git_metadata_snapshot(linked)
    assert metadata_before["@git_dir"] != metadata_before["@git_common_dir"]
    index_identity_before = run_review.git_index_identity(linked)
    assert index_identity_before is not None

    os.utime(linked / "app.txt", (time.time() + 5, time.time() + 5))
    subprocess.run(["git", "status"], cwd=str(linked), capture_output=True, check=True)

    metadata_after = run_review.protected_git_metadata_snapshot(linked, trusted_baseline=metadata_before)
    index_identity_after = run_review.git_index_identity(linked)

    assert metadata_before["git_dir/index"] != metadata_after["git_dir/index"]
    assert index_identity_after is not None
    assert index_identity_after == index_identity_before
    assert (
        run_review.protected_git_metadata_preserved(
            metadata_before,
            metadata_after,
            canonical_index_identity_before=index_identity_before,
            canonical_index_identity_after=index_identity_after,
        )
        is True
    )


def test_canonical_index_identity_real_linked_worktree_common_dir_change_rejected(
    tmp_path: Path,
) -> None:
    """(F03.B, real linked worktree) A raw representation-only change to
    the PRIMARY worktree's own index -- which is exactly what
    git_common_dir/index represents from the linked worktree's perspective
    -- must still block: it is a different worktree's index, never eligible
    for the current worktree's canonical-identity exception, restoring the
    previously-intended strict behavior Codex flagged as regressed."""

    primary, linked = make_linked_workspace(tmp_path)
    _git(["add", "-A"], linked)
    _git(["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "linked"], linked)

    metadata_before = run_review.protected_git_metadata_snapshot(linked)
    assert metadata_before["@git_dir"] != metadata_before["@git_common_dir"]

    # Refresh the PRIMARY worktree's own index (== linked's
    # git_common_dir/index) -- never the linked worktree's own git_dir/index.
    os.utime(primary / "app.txt", (time.time() + 5, time.time() + 5))
    subprocess.run(["git", "status"], cwd=str(primary), capture_output=True, check=True)

    metadata_after = run_review.protected_git_metadata_snapshot(linked, trusted_baseline=metadata_before)
    assert metadata_before["git_common_dir/index"] != metadata_after["git_common_dir/index"]
    assert metadata_before.get("git_dir/index") == metadata_after.get("git_dir/index")

    index_identity_before = run_review.git_index_identity(linked)
    index_identity_after = run_review.git_index_identity(linked)
    assert index_identity_before is not None
    assert index_identity_before == index_identity_after

    assert (
        run_review.protected_git_metadata_preserved(
            metadata_before,
            metadata_after,
            canonical_index_identity_before=index_identity_before,
            canonical_index_identity_after=index_identity_after,
        )
        is False
    )


# ---------------------------------------------------------------------------
# F04 (Section 10-15/21): strict, complete validation of every canonical
# index query source -- no record is ever silently skipped; anything not
# fully recognized fails the whole observation closed.
# ---------------------------------------------------------------------------


def _patch_canonical_index_queries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage_stdout: bytes | None = None,
    status_stdout: bytes | None = None,
    undo_stdout: bytes | None = None,
) -> None:
    """Override one or more of the three canonical-index git queries'
    stdout with fixed bytes; anything not overridden runs for real against
    the fixture workspace."""

    real_run_git_bytes = run_review._run_git_bytes

    def faking(args: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess:
        if args[:2] == ["ls-files", "--stage"] and stage_stdout is not None:
            return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=stage_stdout, stderr=b"")
        if args[:2] == ["status", "--porcelain=v2"] and status_stdout is not None:
            return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=status_stdout, stderr=b"")
        if args[:2] == ["ls-files", "--resolve-undo"] and undo_stdout is not None:
            return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=undo_stdout, stderr=b"")
        return real_run_git_bytes(args, cwd, **kwargs)

    monkeypatch.setattr(run_review, "_run_git_bytes", faking)


def test_build_canonical_index_projection_rejects_unexpected_status_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) An unrecognized porcelain-v2 record type (e.g. "?" untracked,
    never expected under `--untracked-files=no`) must never be silently
    skipped."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, status_stdout=b"? a.txt\x00")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_incomplete_status_framing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) Status output not ending with the terminating NUL is
    truncated/incomplete framing."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(
        monkeypatch,
        status_stdout=b"1 .A N... 000000 000000 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" a.txt",
    )
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_noncanonical_stage_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) A noncanonical stage spelling such as "00" must not become a
    canonical record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, stage_stdout=b"H 100644 " + b"a" * 40 + b" 00\ta.txt\x00")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_invalid_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) An invalid Git mode must not become a canonical record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, stage_stdout=b"H 999999 " + b"a" * 40 + b" 0\ta.txt\x00")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_invalid_object_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) An invalid full object id must not become a canonical
    record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, stage_stdout=b"H 100644 not-a-valid-object-id 0\ta.txt\x00")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_empty_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) An empty path must not become a canonical record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, stage_stdout=b"H 100644 " + b"a" * 40 + b" 0\t\x00")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_incomplete_stage_framing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) Stage output not ending with the terminating NUL is a
    truncated/incomplete record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, stage_stdout=b"H 100644 " + b"a" * 40 + b" 0\ta.txt")
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_intent_to_add_missing_stage_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04/Section 13) status indicates intent-to-add for a path with no
    matching effective stage-0 entry at all -> unavailable."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(
        monkeypatch,
        status_stdout=(
            b"1 .A N... 000000 000000 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" nonexistent.txt\x00"
        ),
    )
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_intent_to_add_incompatible_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04/Section 13) status indicates intent-to-add for a path but its
    matching stage-0 entry has a real, non-empty-blob object id ->
    unavailable. The exact "impossible hybrid" Codex originally found."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(
        monkeypatch,
        stage_stdout=b"H 100644 " + b"a" * 40 + b" 0\ta.txt\x00",
        status_stdout=(
            b"1 .A N... 000000 000000 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" a.txt\x00"
        ),
    )
    assert run_review._build_canonical_index_projection_once(ws) is None


def test_build_canonical_index_projection_rejects_incomplete_resolve_undo_framing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(F04) Resolve-undo output not ending with the terminating NUL is a
    truncated/incomplete record."""

    ws = make_committed_workspace(tmp_path)
    _patch_canonical_index_queries(monkeypatch, undo_stdout=b"100644 " + b"a" * 40 + b" 1\ta.txt")
    assert run_review._build_canonical_index_projection_once(ws) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
