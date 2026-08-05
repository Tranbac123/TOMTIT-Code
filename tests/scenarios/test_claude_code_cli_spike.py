"""Offline rehearsal of the full Gate 3 spike flow: request -> adapter -> evidence.

This exercises the exact code path `scripts/run_claude_code_cli_spike.py` uses,
with a fake executable standing in for the real `claude` CLI, so the flow is
fully deterministic and costs nothing to run.
"""

from __future__ import annotations

import json
from pathlib import Path

from tomtit.adapters.claude_code_cli import (
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIStatus,
    snapshot_workspace_contents,
    write_evidence,
)

REQUIRED_RESULT_CONTENT = "TOMTIT Claude Code CLI adapter smoke test passed"


def write_fake_claude(directory: Path) -> Path:
    script = directory / "fake-claude-e2e"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        f"pathlib.Path('result.txt').write_text({REQUIRED_RESULT_CONTENT!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_offline_end_to_end_spike_flow_produces_valid_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# TOMTIT Gate 3 spike fixture\n", encoding="utf-8")
    (workspace / "result.txt").write_text("not completed\n", encoding="utf-8")

    script = write_fake_claude(tmp_path)
    before_contents = snapshot_workspace_contents(workspace)

    request = ClaudeCodeCLIRequest(
        task_id="gate3-offline-spike",
        run_id="run-offline-spike",
        attempt=1,
        goal="Replace result.txt with the required smoke-test string",
        prompt=(
            "Modify only result.txt. Replace its full contents with exactly:\n\n"
            f"{REQUIRED_RESULT_CONTENT}\n\n"
            "Do not modify, create, rename or delete any other file."
        ),
        working_directory=workspace,
        timeout_seconds=5,
        allowed_changed_paths=("result.txt",),
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(request, executable_path=str(script))
    after_contents = snapshot_workspace_contents(workspace)

    # ---- generic adapter-level acceptance ----
    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.success is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.changed_paths == ("result.txt",)
    assert result.unexpected_changed_paths == ()
    assert result.workspace_before_hash is not None
    assert result.workspace_after_hash is not None
    assert result.workspace_before_hash != result.workspace_after_hash

    # ---- task-specific acceptance (mirrors the real spike's acceptance rule) ----
    written = (workspace / "result.txt").read_text(encoding="utf-8")
    assert written == REQUIRED_RESULT_CONTENT or written == REQUIRED_RESULT_CONTENT + "\n"

    output_dir = tmp_path / "evidence"
    finalized = write_evidence(
        result,
        before_contents=before_contents,
        after_contents=after_contents,
        output_dir=output_dir,
    )

    manifest_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_RESULT.json"
    patch_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_DIFF.patch"
    assert manifest_path.is_file()
    assert patch_path.is_file()
    assert finalized.evidence_manifest_path == str(manifest_path)
    assert finalized.patch_path == str(patch_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "SUCCEEDED"
    assert manifest["success"] is True
    assert manifest["changed_paths"] == ["result.txt"]
    assert manifest["unexpected_changed_paths"] == []
    assert manifest["evidence_manifest_path"] == str(manifest_path)
    assert manifest["patch_path"] == str(patch_path)

    patch_text = patch_path.read_text(encoding="utf-8")
    assert "a/result.txt" in patch_text
    assert "b/result.txt" in patch_text
    assert REQUIRED_RESULT_CONTENT in patch_text
    assert "not completed" in patch_text


def test_offline_scope_violation_flow_produces_evidence_without_success(
    tmp_path: Path,
) -> None:
    """A misbehaving worker is caught before evidence claims success."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# TOMTIT Gate 3 spike fixture\n", encoding="utf-8")
    (workspace / "result.txt").write_text("not completed\n", encoding="utf-8")

    script = tmp_path / "fake-claude-overreach"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "pathlib.Path('result.txt').write_text('done', encoding='utf-8')\n"
        "pathlib.Path('README.md').write_text('unexpected edit', encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    before_contents = snapshot_workspace_contents(workspace)
    request = ClaudeCodeCLIRequest(
        task_id="gate3-offline-scope",
        run_id="run-offline-scope",
        attempt=1,
        goal="Replace result.txt only",
        prompt="Modify only result.txt.",
        working_directory=workspace,
        timeout_seconds=5,
        allowed_changed_paths=("result.txt",),
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(request, executable_path=str(script))
    after_contents = snapshot_workspace_contents(workspace)

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert result.success is False
    assert result.unexpected_changed_paths == ("README.md",)

    output_dir = tmp_path / "evidence"
    finalized = write_evidence(
        result,
        before_contents=before_contents,
        after_contents=after_contents,
        output_dir=output_dir,
    )
    manifest = json.loads(Path(finalized.evidence_manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "SCOPE_VIOLATION"
    assert manifest["success"] is False
    assert manifest["unexpected_changed_paths"] == ["README.md"]


def test_offline_spike_evidence_is_not_invalidated_by_a_delayed_descendant(
    tmp_path: Path,
) -> None:
    """G3-SOL-01 at the evidence layer: persisted proof must stay true.

    A worker exits 0 after the expected change but leaves a descendant that
    would rewrite an unexpected file moments later. The persisted manifest and
    patch must still describe the workspace accurately after that delay.
    """

    import os
    import time

    workspace = tmp_path / "ws"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("# TOMTIT Gate 3 spike fixture\n", encoding="utf-8")
    (workspace / "result.txt").write_text("not completed\n", encoding="utf-8")

    pid_file = tmp_path / "descendant.pid"
    descendant_source = (
        "import time, pathlib; "
        "time.sleep(3); "
        f"pathlib.Path({str(readme)!r}).write_text('late unexpected write')"
    )
    script = tmp_path / "fake-claude-delayed"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, subprocess, sys\n"
        f"pathlib.Path('result.txt').write_text({REQUIRED_RESULT_CONTENT!r}, encoding='utf-8')\n"
        f"child = subprocess.Popen([sys.executable, '-c', {descendant_source!r}])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    before_contents = snapshot_workspace_contents(workspace)
    request = ClaudeCodeCLIRequest(
        task_id="gate3-offline-delayed",
        run_id="run-offline-delayed",
        attempt=1,
        goal="Replace result.txt with the required smoke-test string",
        prompt="Modify only result.txt.",
        working_directory=workspace,
        timeout_seconds=30,
        allowed_changed_paths=("result.txt",),
    )

    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))
    after_contents = snapshot_workspace_contents(workspace)

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
        assert result.changed_paths == ("result.txt",)
        assert result.unexpected_changed_paths == ()

        output_dir = tmp_path / "evidence"
        finalized = write_evidence(
            result,
            before_contents=before_contents,
            after_contents=after_contents,
            output_dir=output_dir,
        )
        manifest = json.loads(Path(finalized.evidence_manifest_path).read_text(encoding="utf-8"))
        patch_text = Path(finalized.patch_path).read_text(encoding="utf-8")

        # Wait past the descendant's intended write window, then re-verify that
        # the persisted evidence still matches the real workspace.
        time.sleep(4)
        assert readme.read_text(encoding="utf-8") == "# TOMTIT Gate 3 spike fixture\n"
        assert manifest["changed_paths"] == ["result.txt"]
        assert manifest["unexpected_changed_paths"] == []
        assert "README.md" not in patch_text
    finally:
        try:
            os.kill(descendant_pid, 9)
        except OSError:
            pass


def test_persisted_evidence_patch_is_valid_and_applies_cleanly(tmp_path: Path) -> None:
    """GATE_3_PATCH_RENDERING regression at the evidence-persistence layer.

    The originally shipped `CLAUDE_CODE_CLI_SPIKE_DIFF.patch` was produced by
    exactly this `write_evidence` call and failed `git apply --check`. This
    rehearses the same real-spike shape end to end and validates the
    persisted patch through a real Git parser in a throwaway repository, not
    only by string inspection.
    """

    import subprocess

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# TOMTIT Gate 3 spike fixture\n", encoding="utf-8")
    (workspace / "result.txt").write_text("not completed\n", encoding="utf-8")

    script = write_fake_claude(tmp_path)
    before_contents = snapshot_workspace_contents(workspace)

    request = ClaudeCodeCLIRequest(
        task_id="gate3-patch-validity",
        run_id="run-patch-validity",
        attempt=1,
        goal="Replace result.txt with the required smoke-test string",
        prompt="Modify only result.txt.",
        working_directory=workspace,
        timeout_seconds=5,
        allowed_changed_paths=("result.txt",),
    )
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))
    after_contents = snapshot_workspace_contents(workspace)

    output_dir = tmp_path / "evidence"
    finalized = write_evidence(
        result,
        before_contents=before_contents,
        after_contents=after_contents,
        output_dir=output_dir,
    )
    patch_path = Path(finalized.patch_path)

    validation_repo = tmp_path / "validation-repo"
    validation_repo.mkdir()
    (validation_repo / "result.txt").write_text("not completed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(validation_repo), check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"],
        cwd=str(validation_repo),
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "seed"],
        cwd=str(validation_repo),
        check=True,
    )

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=str(validation_repo),
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, f"persisted patch is not a valid unified diff: {check.stderr}"

    apply = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=str(validation_repo),
        capture_output=True,
        text=True,
    )
    assert apply.returncode == 0, f"git apply failed: {apply.stderr}"
    assert (validation_repo / "result.txt").read_text(encoding="utf-8") == REQUIRED_RESULT_CONTENT
