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
