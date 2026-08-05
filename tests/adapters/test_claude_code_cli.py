"""Offline, deterministic tests for the Gate 3 Claude Code CLI adapter.

None of these tests call the real Claude service. Each uses a small fake
executable script to control subprocess behavior precisely.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from tomtit.adapters.claude_code_cli import (
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIStatus,
    render_evidence_manifest_json,
)


def write_fake_executable(directory: Path, name: str, body: str) -> Path:
    """A tiny Python script usable as a stand-in `claude` executable."""

    script = directory / name
    script.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "result.txt").write_text("not completed\n", encoding="utf-8")
    (ws / "README.md").write_text("# demo\n", encoding="utf-8")
    return ws


def base_request(workspace: Path, **overrides: object) -> ClaudeCodeCLIRequest:
    values: dict[str, object] = {
        "task_id": "task-1",
        "run_id": "run-1",
        "attempt": 1,
        "goal": "demo goal",
        "prompt": "demo prompt",
        "working_directory": workspace,
        "timeout_seconds": 5,
        "allowed_changed_paths": ("result.txt",),
        "max_stdout_bytes": 4096,
        "max_stderr_bytes": 4096,
    }
    values.update(overrides)
    return ClaudeCodeCLIRequest(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Executable unavailable.
# ---------------------------------------------------------------------------


def test_unavailable_when_executable_not_discovered(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tomtit.adapters.claude_code_cli as module

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace))

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE
    assert result.success is False
    assert result.exit_code is None
    assert result.error_category == "CLI_NOT_FOUND"
    assert result.changed_paths == ()


# ---------------------------------------------------------------------------
# 2. Successful exit and expected file change.
# ---------------------------------------------------------------------------


def test_success_with_expected_file_change(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-success",
        """
import pathlib
pathlib.Path("result.txt").write_text(
    "TOMTIT Claude Code CLI adapter smoke test passed", encoding="utf-8"
)
print("ok")
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.success is True
    assert result.exit_code == 0
    assert result.changed_paths == ("result.txt",)
    assert result.unexpected_changed_paths == ()
    assert result.error_category is None
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# 3. Non-zero exit.
# ---------------------------------------------------------------------------


def test_nonzero_exit_never_succeeds(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(tmp_path, "fake-claude-fail", "raise SystemExit(3)")
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.success is False
    assert result.exit_code == 3
    assert result.error_category == "NONZERO_EXIT"


# ---------------------------------------------------------------------------
# 4. Timeout with process termination.
# ---------------------------------------------------------------------------


def test_timeout_terminates_the_process(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-timeout",
        """
import time
time.sleep(30)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, timeout_seconds=1),
        executable_path=str(script),
    )

    assert result.timed_out is True
    assert result.status is ClaudeCodeCLIStatus.TIMED_OUT
    assert result.success is False
    assert result.error_category == "TIMEOUT"


# ---------------------------------------------------------------------------
# 5. Stdout truncation.
# ---------------------------------------------------------------------------


def test_stdout_is_truncated_but_hash_covers_full_output(
    workspace: Path,
    tmp_path: Path,
) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-stdout-big",
        """
print("A" * 5000)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, max_stdout_bytes=100),
        executable_path=str(script),
    )

    assert result.stdout_truncated is True
    assert len(result.stdout_excerpt.encode("utf-8")) <= 100
    expected_full = ("A" * 5000 + "\n").encode("utf-8")
    assert result.stdout_sha256 == hashlib.sha256(expected_full).hexdigest()


# ---------------------------------------------------------------------------
# 6. Stderr truncation.
# ---------------------------------------------------------------------------


def test_stderr_is_truncated_but_hash_covers_full_output(
    workspace: Path,
    tmp_path: Path,
) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-stderr-big",
        """
import sys
sys.stderr.write("B" * 5000)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, max_stderr_bytes=100),
        executable_path=str(script),
    )

    assert result.stderr_truncated is True
    assert len(result.stderr_excerpt.encode("utf-8")) <= 100
    expected_full = ("B" * 5000).encode("utf-8")
    assert result.stderr_sha256 == hashlib.sha256(expected_full).hexdigest()


# ---------------------------------------------------------------------------
# 7. Partial output retained on timeout.
# ---------------------------------------------------------------------------


def test_partial_output_is_retained_on_timeout(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-partial",
        """
import sys, time
print("partial-output-marker")
sys.stdout.flush()
time.sleep(30)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, timeout_seconds=1),
        executable_path=str(script),
    )

    assert result.timed_out is True
    assert "partial-output-marker" in result.stdout_excerpt


# ---------------------------------------------------------------------------
# 8. Unexpected file change causes SCOPE_VIOLATION.
# ---------------------------------------------------------------------------


def test_unexpected_changed_path_is_scope_violation(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-scope",
        """
import pathlib
pathlib.Path("README.md").write_text("mutated", encoding="utf-8")
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert result.success is False
    assert result.unexpected_changed_paths == ("README.md",)
    assert result.error_category == "UNEXPECTED_CHANGED_PATHS"


def test_scope_violation_takes_priority_over_nonzero_exit(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """An unsafe write must be surfaced even if the process also failed otherwise."""

    script = write_fake_executable(
        tmp_path,
        "fake-claude-scope-and-fail",
        """
import pathlib
pathlib.Path("README.md").write_text("mutated", encoding="utf-8")
raise SystemExit(1)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 9. Expected file not changed causes failure.
# ---------------------------------------------------------------------------


def test_missing_expected_change_is_failure(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(tmp_path, "fake-claude-noop", "pass")
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.success is False
    assert result.error_category == "NO_EXPECTED_CHANGE"
    assert result.changed_paths == ()


# ---------------------------------------------------------------------------
# 10. Exact changed-path calculation.
# ---------------------------------------------------------------------------


def test_changed_paths_are_calculated_exactly(workspace: Path, tmp_path: Path) -> None:
    (workspace / "extra.txt").write_text("untouched\n", encoding="utf-8")
    script = write_fake_executable(
        tmp_path,
        "fake-claude-two-changes",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
pathlib.Path("extra.txt").write_text("also changed", encoding="utf-8")
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, allowed_changed_paths=("result.txt", "extra.txt")),
        executable_path=str(script),
    )

    assert result.changed_paths == ("extra.txt", "result.txt")
    assert result.unexpected_changed_paths == ()
    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# 11. Environment allowlist.
# ---------------------------------------------------------------------------


def test_environment_allowlist_excludes_unlisted_variables(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOMTIT_TEST_SECRET_TOKEN", "should-not-be-inherited")
    monkeypatch.setenv("TOMTIT_TEST_HARMLESS", "also-not-inherited")
    script = write_fake_executable(
        tmp_path,
        "fake-claude-env",
        """
import os, pathlib
seen = {
    name: os.environ.get(name)
    for name in ("TOMTIT_TEST_SECRET_TOKEN", "TOMTIT_TEST_HARMLESS")
    if name in os.environ
}
pathlib.Path("result.txt").write_text(
    "TOMTIT Claude Code CLI adapter smoke test passed" + ("|" + str(seen) if seen else ""),
    encoding="utf-8",
)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    written = (workspace / "result.txt").read_text(encoding="utf-8")
    assert "TOMTIT_TEST_SECRET_TOKEN" not in written
    assert "TOMTIT_TEST_HARMLESS" not in written


# ---------------------------------------------------------------------------
# 12. Secret redaction.
# ---------------------------------------------------------------------------


def test_secret_values_are_redacted_from_captured_output(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-supersecretvalue123"
    monkeypatch.setenv("TOMTIT_TEST_API_KEY", secret)
    script = write_fake_executable(
        tmp_path,
        "fake-claude-secret",
        """
import os, pathlib
print(os.environ.get("TOMTIT_TEST_API_KEY", ""))
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(
            workspace,
            environment_allowlist=("PATH", "TOMTIT_TEST_API_KEY"),
        ),
        executable_path=str(script),
    )

    assert secret not in result.stdout_excerpt
    assert "[REDACTED]" in result.stdout_excerpt


# ---------------------------------------------------------------------------
# 13. No shell=True.
# ---------------------------------------------------------------------------


def test_subprocess_never_uses_shell_true(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-shell-check",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    recorded: list[dict[str, object]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen:
        recorded.append(kwargs)
        return real_popen(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    adapter = ClaudeCodeCLIAdapter()

    adapter.run(base_request(workspace), executable_path=str(script))

    assert recorded
    assert recorded[0].get("shell") is not True


# ---------------------------------------------------------------------------
# 14. Argv values remain separate (no shell interpretation).
# ---------------------------------------------------------------------------


def test_argv_values_are_not_shell_interpreted(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-argv",
        """
import json, pathlib, sys
pathlib.Path("argv_seen.json").write_text(json.dumps(sys.argv), encoding="utf-8")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    dangerous_prompt = "hello; echo INJECTED > injected.txt"
    adapter = ClaudeCodeCLIAdapter()

    adapter.run(
        base_request(workspace, prompt=dangerous_prompt),
        executable_path=str(script),
    )

    assert not (workspace / "injected.txt").exists()
    argv_seen = json.loads((workspace / "argv_seen.json").read_text(encoding="utf-8"))
    assert argv_seen[-1] == dangerous_prompt


# ---------------------------------------------------------------------------
# 15. Working directory validation.
# ---------------------------------------------------------------------------


def test_nonexistent_working_directory_fails_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess must not be invoked for an invalid workspace")

    monkeypatch.setattr(subprocess, "Popen", forbidden_popen)
    adapter = ClaudeCodeCLIAdapter()
    missing = tmp_path / "does-not-exist"

    result = adapter.run(base_request(missing), executable_path="/usr/bin/true")

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "INVALID_WORKSPACE"
    assert result.exit_code is None


def test_working_directory_that_is_a_file_fails_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess must not be invoked for an invalid workspace")

    monkeypatch.setattr(subprocess, "Popen", forbidden_popen)
    not_a_dir = tmp_path / "plain-file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(not_a_dir), executable_path="/usr/bin/true")

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "INVALID_WORKSPACE"


# ---------------------------------------------------------------------------
# 16. Evidence manifest is deterministic.
# ---------------------------------------------------------------------------


def test_evidence_manifest_rendering_is_deterministic(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-manifest",
        """
import pathlib
pathlib.Path("result.txt").write_text(
    "TOMTIT Claude Code CLI adapter smoke test passed", encoding="utf-8"
)
""",
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))

    first = render_evidence_manifest_json(result)
    second = render_evidence_manifest_json(result)

    assert first == second
    assert json.loads(first) == json.loads(second)


# ---------------------------------------------------------------------------
# 17. Result is immutable.
# ---------------------------------------------------------------------------


def test_result_is_immutable(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(tmp_path, "fake-claude-immutable", "pass")
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = True  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.changed_paths = ("x",)  # type: ignore[misc]


def test_request_is_immutable(workspace: Path) -> None:
    request = base_request(workspace)

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.attempt = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 18. Adapter does not access or mutate Runtime state.
# ---------------------------------------------------------------------------


def test_adapter_module_imports_no_runtime_or_domain_code() -> None:
    """Parse actual import statements (AST), not prose that merely mentions them."""

    import ast

    import tomtit.adapters.claude_code_cli as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = ("tomtit.runtime", "tomtit.domain", "tomtit.reporting", "tomtit.fakes")
    violations = [
        name
        for name in imported_modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
    ]
    assert violations == []


# ---------------------------------------------------------------------------
# 19. No automatic retry.
# ---------------------------------------------------------------------------


def test_adapter_never_retries_internally(workspace: Path, tmp_path: Path) -> None:
    # The counter lives outside the workspace so writing it is never itself a
    # scope violation; this test isolates retry behavior only.
    counter_path = tmp_path / "invocation_count.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-count",
        f"""
import pathlib
counter_path = pathlib.Path({str(counter_path)!r})
count = int(counter_path.read_text()) if counter_path.exists() else 0
counter_path.write_text(str(count + 1))
raise SystemExit(1)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert counter_path.read_text(encoding="utf-8") == "1"


# ---------------------------------------------------------------------------
# 20. No child process remains after timeout.
# ---------------------------------------------------------------------------


def test_no_child_process_survives_a_timeout(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-child",
        """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path("child.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(
        base_request(workspace, timeout_seconds=1),
        executable_path=str(script),
    )

    assert result.timed_out is True
    child_pid_path = workspace / "child.pid"
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    # Give the killed process a brief moment to be reaped by the OS.
    import time

    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"child process {child_pid} was still alive after timeout kill")
