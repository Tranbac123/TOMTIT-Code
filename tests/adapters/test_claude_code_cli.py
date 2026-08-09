"""Offline, deterministic tests for the Gate 3 Claude Code CLI adapter.

None of these tests call the real Claude service. Each uses a small fake
executable script to control subprocess behavior precisely.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from tomtit.adapters.claude_code_cli import (
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIResult,
    ClaudeCodeCLIStatus,
    render_evidence_manifest_json,
    render_unified_diff,
)


def write_fake_executable(directory: Path, name: str, body: str) -> Path:
    """A tiny Python script usable as a stand-in `claude` executable."""

    script = directory / name
    script.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def await_process_absence(pid: int, *, timeout: float = 5.0) -> bool:
    """Poll until `pid` is gone, bounded. True if it disappeared in time."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def force_kill(pid: int) -> None:
    """Best-effort cleanup so a test can never leak a process it created."""

    import signal as _signal

    try:
        os.kill(pid, _signal.SIGKILL)
    except OSError:
        pass


def live_reader_threads() -> list[threading.Thread]:
    """Any adapter stream-reader thread still running right now."""

    from tomtit.adapters.claude_code_cli import _StreamReader

    return [t for t in threading.enumerate() if isinstance(t, _StreamReader) and t.is_alive()]


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
    # The prompt occupies the fixed positional slot immediately after `-p`
    # (see CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md), not merely "last" --
    # asserting the exact position also catches a regression back to
    # appending it after a variadic option.
    assert argv_seen[argv_seen.index("-p") + 1] == dangerous_prompt


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


# ===========================================================================
# Gate 3 corrective regressions: G3-SOL-01 (process quiescence) and
# G3-SOL-02 (total result normalization).
#
# Every test below is offline and deterministic. Descendant processes are
# always either proven dead by the adapter or force-killed by the test, so no
# test can leak a process.
# ===========================================================================


# ---------------------------------------------------------------------------
# 21-23. Explicit executable overrides are validated, not trusted.
# ---------------------------------------------------------------------------


def test_invalid_explicit_path_returns_unavailable_result(workspace: Path, tmp_path: Path) -> None:
    """G3-SOL-02: a nonexistent override must not escape as FileNotFoundError."""

    adapter = ClaudeCodeCLIAdapter()
    missing = tmp_path / "no-such-executable"

    result = adapter.run(base_request(workspace), executable_path=str(missing))

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE
    assert result.success is False
    assert result.exit_code is None
    assert result.timed_out is False
    assert result.error_category == "EXECUTABLE_UNAVAILABLE"


def test_explicit_path_that_is_a_directory_returns_unavailable(
    workspace: Path,
    tmp_path: Path,
) -> None:
    adapter = ClaudeCodeCLIAdapter()
    directory = tmp_path / "a-directory"
    directory.mkdir()

    result = adapter.run(base_request(workspace), executable_path=str(directory))

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE
    assert result.success is False
    assert result.error_category == "EXECUTABLE_UNAVAILABLE"


def test_explicit_non_executable_file_returns_unavailable(
    workspace: Path,
    tmp_path: Path,
) -> None:
    adapter = ClaudeCodeCLIAdapter()
    plain = tmp_path / "not-executable.txt"
    plain.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    plain.chmod(0o644)

    result = adapter.run(base_request(workspace), executable_path=str(plain))

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE
    assert result.success is False
    assert result.error_category == "EXECUTABLE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 24-26. Subprocess-start failures normalize into immutable results.
# ---------------------------------------------------------------------------


def _run_with_failing_popen(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> object:
    script = write_fake_executable(tmp_path, "fake-claude-start-fail", "pass")

    def failing_popen(*args: object, **kwargs: object) -> subprocess.Popen:
        raise exception

    monkeypatch.setattr(subprocess, "Popen", failing_popen)
    return ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))


def test_popen_file_not_found_is_normalized(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_failing_popen(
        workspace, tmp_path, monkeypatch, FileNotFoundError(2, "No such file or directory")
    )

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE  # type: ignore[attr-defined]
    assert result.success is False  # type: ignore[attr-defined]
    assert result.error_category == "EXECUTABLE_UNAVAILABLE"  # type: ignore[attr-defined]
    assert result.exit_code is None  # type: ignore[attr-defined]
    # Evidence stays well formed even though nothing ran.
    assert result.stdout_excerpt == ""  # type: ignore[attr-defined]
    assert result.stderr_excerpt == ""  # type: ignore[attr-defined]
    empty = hashlib.sha256(b"").hexdigest()
    assert result.stdout_sha256 == empty  # type: ignore[attr-defined]
    assert result.stderr_sha256 == empty  # type: ignore[attr-defined]
    assert result.duration_ms >= 0  # type: ignore[attr-defined]
    assert result.completed_at >= result.started_at  # type: ignore[attr-defined]
    assert result.workspace_before_hash is not None  # type: ignore[attr-defined]
    assert result.workspace_after_hash is not None  # type: ignore[attr-defined]


def test_popen_permission_error_is_normalized(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_failing_popen(
        workspace, tmp_path, monkeypatch, PermissionError(13, "Permission denied")
    )

    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE  # type: ignore[attr-defined]
    assert result.success is False  # type: ignore[attr-defined]
    assert result.error_category == "EXECUTABLE_NOT_EXECUTABLE"  # type: ignore[attr-defined]


def test_generic_popen_oserror_returns_failed(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_failing_popen(
        workspace, tmp_path, monkeypatch, OSError(8, "Exec format error")
    )

    assert result.status is ClaudeCodeCLIStatus.FAILED  # type: ignore[attr-defined]
    assert result.success is False  # type: ignore[attr-defined]
    assert result.error_category == "PROCESS_START_FAILED"  # type: ignore[attr-defined]
    assert result.exit_code is None  # type: ignore[attr-defined]


def test_start_failure_result_is_immutable_and_leaks_no_environment(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-must-never-appear-in-an-error"
    monkeypatch.setenv("TOMTIT_TEST_API_KEY", secret)
    result = _run_with_failing_popen(
        workspace, tmp_path, monkeypatch, OSError(8, f"Exec format error {secret}")
    )

    assert secret not in (result.error_message or "")  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 27. No supported discovery/start failure escapes run() as an exception.
# ---------------------------------------------------------------------------


def test_no_executable_or_start_exception_escapes_run(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The result boundary is total for every supported failure mode."""

    from tomtit.adapters.claude_code_cli import ClaudeCodeCLIResult

    adapter = ClaudeCodeCLIAdapter()
    directory = tmp_path / "dir-override"
    directory.mkdir()

    # Overrides that must be rejected by validation, never reaching Popen.
    for override in (str(tmp_path / "missing"), str(directory), "relative-not-found"):
        result = adapter.run(base_request(workspace), executable_path=override)
        assert isinstance(result, ClaudeCodeCLIResult)
        assert result.success is False

    # Start failures that must be normalized rather than raised.
    for exception in (
        FileNotFoundError(2, "No such file or directory"),
        PermissionError(13, "Permission denied"),
        OSError(8, "Exec format error"),
        BlockingIOError(35, "Resource temporarily unavailable"),
    ):
        with monkeypatch.context() as patch:
            script = write_fake_executable(tmp_path, "fake-claude-sweep", "pass")

            def failing_popen(*args: object, __exc: BaseException = exception, **kwargs: object) -> subprocess.Popen:
                raise __exc

            patch.setattr(subprocess, "Popen", failing_popen)
            result = adapter.run(base_request(workspace), executable_path=str(script))
        assert isinstance(result, ClaudeCodeCLIResult)
        assert result.success is False
        assert result.status in (ClaudeCodeCLIStatus.UNAVAILABLE, ClaudeCodeCLIStatus.FAILED)


# ---------------------------------------------------------------------------
# 28. A normally exiting parent may not leave a descendant behind.
# ---------------------------------------------------------------------------


def test_normal_exit_with_surviving_descendant_kills_descendant(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """G3-SOL-01 core: the old code cleaned the group only on timeout."""

    pid_file = tmp_path / "descendant.pid"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-survivor",
        f"""
import pathlib, subprocess, sys
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
raise SystemExit(0)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert await_process_absence(descendant_pid), (
            f"descendant {descendant_pid} survived a normal parent exit"
        )
        # The parent's own successful exit code is preserved.
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
        assert live_reader_threads() == []
    finally:
        force_kill(descendant_pid)


# ---------------------------------------------------------------------------
# 29. A pipe-holding descendant must not keep the readers alive.
# ---------------------------------------------------------------------------


def test_normal_exit_with_pipe_holding_descendant_terminates_readers(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """Distinct from the timeout test: the parent exits 0 immediately."""

    pid_file = tmp_path / "pipe-holder.pid"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-pipe-holder",
        f"""
import pathlib, subprocess, sys
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
# Inherits this process's stdout/stderr, so the pipes stay open after exit.
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
raise SystemExit(0)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    started = time.monotonic()
    result = adapter.run(base_request(workspace, timeout_seconds=30), executable_path=str(script))
    elapsed = time.monotonic() - started

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert await_process_absence(descendant_pid)
        assert live_reader_threads() == []
        # Bounded return: it must not sit on the reader-join timeouts.
        assert elapsed < 20
        assert result.timed_out is False
        assert result.exit_code == 0
    finally:
        force_kill(descendant_pid)


# ---------------------------------------------------------------------------
# 30. A delayed descendant write cannot land after the final snapshot.
# ---------------------------------------------------------------------------


def test_delayed_descendant_write_is_prevented_before_final_snapshot(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """The exact scenario Sol reproduced: success certified, README mutated later."""

    pid_file = tmp_path / "delayed.pid"
    readme = workspace / "README.md"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-delayed-write",
        f"""
import pathlib, subprocess, sys
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
child = subprocess.Popen([
    sys.executable, "-c",
    "import time, pathlib; time.sleep(3); "
    "pathlib.Path({str(readme)!r}).write_text('late unexpected write')",
])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
raise SystemExit(0)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace, timeout_seconds=30), executable_path=str(script))

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert await_process_absence(descendant_pid)
        assert result.changed_paths == ("result.txt",)
        assert result.unexpected_changed_paths == ()
        assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
        assert live_reader_threads() == []

        # The decisive assertion: the delayed write never happens at all,
        # so the certified snapshot cannot be invalidated after the fact.
        time.sleep(4)
        assert readme.read_text(encoding="utf-8") == "# demo\n"
    finally:
        force_kill(descendant_pid)


# ---------------------------------------------------------------------------
# 31. An unexpected write that lands in time is still a scope violation.
# ---------------------------------------------------------------------------


def test_early_descendant_unexpected_write_is_scope_violation(
    workspace: Path,
    tmp_path: Path,
) -> None:
    """Cleanup must not mask a real violation, and must not revert the file."""

    readme = workspace / "README.md"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-early-write",
        f"""
import pathlib, subprocess, sys
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
child = subprocess.Popen([
    sys.executable, "-c",
    "import pathlib; pathlib.Path({str(readme)!r}).write_text('early unexpected write')",
])
child.wait()
raise SystemExit(0)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace, timeout_seconds=30), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert result.success is False
    assert result.unexpected_changed_paths == ("README.md",)
    # The adapter reports; it never silently repairs the workspace.
    assert readme.read_text(encoding="utf-8") == "early unexpected write"


# ---------------------------------------------------------------------------
# 32. A SIGTERM-resistant descendant is escalated to SIGKILL.
# ---------------------------------------------------------------------------


def test_sigterm_resistant_descendant_receives_sigkill(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import signal as signal_module

    import tomtit.adapters.claude_code_cli as module

    monkeypatch.setattr(module, "_PROCESS_CLEANUP_GRACE_SECONDS", 0.75)
    signals_sent: list[int] = []
    real_signal_group = module._signal_process_group

    def recording_signal_group(pgid: int, sig: int) -> bool:
        signals_sent.append(int(sig))
        return real_signal_group(pgid, sig)

    monkeypatch.setattr(module, "_signal_process_group", recording_signal_group)

    pid_file = tmp_path / "stubborn.pid"
    ready_file = tmp_path / "stubborn.ready"
    # The descendant signals readiness only after SIG_IGN is installed, and the
    # parent waits for it. Without this handshake the test races interpreter
    # startup and SIGTERM can land before the handler exists.
    descendant_source = (
        "import signal, time, pathlib; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready_file)!r}).write_text('ready'); "
        "time.sleep(60)"
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-stubborn",
        f"""
import pathlib, subprocess, sys, time
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
child = subprocess.Popen([sys.executable, "-c", {descendant_source!r}])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
ready = pathlib.Path({str(ready_file)!r})
deadline = time.monotonic() + 20
while not ready.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
raise SystemExit(0)
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace, timeout_seconds=30), executable_path=str(script))

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert int(signal_module.SIGTERM) in signals_sent
        assert int(signal_module.SIGKILL) in signals_sent, "SIGKILL fallback was not applied"
        assert await_process_absence(descendant_pid), "SIGTERM-resistant descendant survived"
        assert live_reader_threads() == []
        assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    finally:
        force_kill(descendant_pid)


# ---------------------------------------------------------------------------
# 33. Unprovable cleanup fails closed.
# ---------------------------------------------------------------------------


def test_cleanup_failure_returns_process_cleanup_failed(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tomtit.adapters.claude_code_cli as module

    script = write_fake_executable(
        tmp_path,
        "fake-claude-cleanup-fail",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    monkeypatch.setattr(
        module,
        "ensure_process_group_quiescent",
        lambda pgid, **kwargs: module._ProcessGroupCleanup.FAILED,
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    # The worker exited 0 and made exactly the expected change, yet an
    # unprovable cleanup must still deny the success claim.
    assert result.exit_code == 0
    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.success is False
    assert result.error_category == "PROCESS_CLEANUP_FAILED"


def test_indeterminate_process_group_probe_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group that exists but cannot be signalled is never called quiescent."""

    import tomtit.adapters.claude_code_cli as module

    monkeypatch.setattr(module, "_process_group_alive", lambda pgid: None)

    assert module.ensure_process_group_quiescent(999999) is module._ProcessGroupCleanup.FAILED


def test_process_group_cleanup_never_targets_own_group() -> None:
    """A guard against ever signalling the TOMTIT process group itself."""

    import tomtit.adapters.claude_code_cli as module

    assert module.ensure_process_group_quiescent(os.getpgrp()) is module._ProcessGroupCleanup.FAILED
    assert module.ensure_process_group_quiescent(0) is module._ProcessGroupCleanup.FAILED
    assert module.ensure_process_group_quiescent(1) is module._ProcessGroupCleanup.FAILED


# ---------------------------------------------------------------------------
# 34. A still-live reader fails closed.
# ---------------------------------------------------------------------------


def test_live_reader_returns_process_cleanup_failed(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate cleanup claiming success while a reader is genuinely stuck."""

    import tomtit.adapters.claude_code_cli as module

    pid_file = tmp_path / "reader-holder.pid"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-reader-holder",
        f"""
import pathlib, subprocess, sys
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
raise SystemExit(0)
""",
    )
    # Cleanup reports quiescence without actually killing anything, so the
    # pipe-holding descendant keeps both readers blocked on read().
    monkeypatch.setattr(
        module,
        "ensure_process_group_quiescent",
        lambda pgid, **kwargs: module._ProcessGroupCleanup.QUIESCENT,
    )
    monkeypatch.setattr(module, "_READER_JOIN_TIMEOUT_SECONDS", 0.5)
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace, timeout_seconds=30), executable_path=str(script))

    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert result.exit_code == 0
        assert result.status is ClaudeCodeCLIStatus.FAILED
        assert result.success is False
        assert result.error_category == "PROCESS_CLEANUP_FAILED"
    finally:
        # This test intentionally defeated cleanup; undo the leak itself.
        force_kill(descendant_pid)
        assert await_process_absence(descendant_pid)


# ---------------------------------------------------------------------------
# 35. The final workspace snapshot happens only after cleanup.
# ---------------------------------------------------------------------------


def test_final_snapshot_occurs_only_after_cleanup(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tomtit.adapters.claude_code_cli as module

    events: list[str] = []
    real_snapshot = module.snapshot_workspace_hashes
    real_cleanup = module.ensure_process_group_quiescent

    def recording_snapshot(root: Path) -> dict[str, str]:
        events.append("snapshot")
        return real_snapshot(root)

    def recording_cleanup(pgid: int, **kwargs: object) -> object:
        events.append("cleanup")
        return real_cleanup(pgid, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "snapshot_workspace_hashes", recording_snapshot)
    monkeypatch.setattr(module, "ensure_process_group_quiescent", recording_cleanup)

    script = write_fake_executable(
        tmp_path,
        "fake-claude-ordering",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert events == ["snapshot", "cleanup", "snapshot"], (
        f"required order is before-snapshot -> cleanup -> after-snapshot, got {events}"
    )


# ---------------------------------------------------------------------------
# 36. No output buffer can mutate after run() returns.
# ---------------------------------------------------------------------------


def test_no_output_buffer_mutation_after_run_returns(
    workspace: Path,
    tmp_path: Path,
) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-frozen-output",
        """
import pathlib
print("frozen-output-marker")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(base_request(workspace), executable_path=str(script))

    # No reader survives, so nothing is able to append to a captured buffer.
    assert live_reader_threads() == []
    # And the captured evidence is an immutable str on a frozen result.
    assert "frozen-output-marker" in result.stdout_excerpt
    assert isinstance(result.stdout_excerpt, str)
    snapshot = result.stdout_excerpt
    time.sleep(0.2)
    assert result.stdout_excerpt == snapshot
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.stdout_excerpt = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 39. Committed real-invocation evidence is untouched by this correction.
# ---------------------------------------------------------------------------


def test_committed_real_evidence_files_are_unchanged() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        pytest.skip("not a git checkout")

    completed = subprocess.run(
        ["git", "status", "--porcelain", "--", "evidence/gate3"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "", (
        f"Gate 3 evidence must not be modified by this patch: {completed.stdout!r}"
    )


# ---------------------------------------------------------------------------
# 40. Exactly one Popen per run(), still no internal retry.
# ---------------------------------------------------------------------------


def test_exactly_one_popen_per_run(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-one-popen",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
raise SystemExit(1)
""",
    )
    calls: list[object] = []
    real_popen = subprocess.Popen

    def counting_popen(*args: object, **kwargs: object) -> subprocess.Popen:
        calls.append(args)
        return real_popen(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "Popen", counting_popen)

    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert len(calls) == 1
    assert result.status is ClaudeCodeCLIStatus.FAILED


# ===========================================================================
# GATE_3_PATCH_RENDERING corrective regressions.
#
# Root cause: `_decode_lines` used `splitlines(keepends=True)`, so every
# content line already carried its own trailing "\n". `render_unified_diff`
# then joined lines with an *additional* "\n" via `"\n".join(diff)`, so any
# line ending in a real newline produced a second, unprefixed blank line —
# an invalid unified diff. The fix drops embedded newlines entirely
# (`str.splitlines()` without keepends) and reconstructs "no newline at end
# of file" markers explicitly, from true end-of-file index tracking rather
# than from data embedded in the line content.
#
# Validity is checked through a real `git apply --check`/`git apply` in a
# throwaway temporary repository, never only by string inspection, and never
# against this project's own working tree.
# ===========================================================================

_GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _GIT_AVAILABLE, reason="git executable not available")


def git_apply_check(tmp_path: Path, *, before: bytes | None, patch_text: str, rel_path: str = "result.txt") -> Path:
    """A throwaway git repo seeded with `before`, used only to validate `patch_text`.

    Never touches this project's own working tree: `repo` is a fresh
    directory under pytest's `tmp_path`.
    """

    repo = tmp_path / "git-validation-repo"
    repo.mkdir()
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        target.write_bytes(before)

    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "add", "-A"],
        cwd=str(repo),
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "seed", "--allow-empty"],
        cwd=str(repo),
        check=True,
    )

    patch_path = tmp_path / "generated.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    return repo, patch_path  # type: ignore[return-value]


def assert_patch_applies_cleanly(
    tmp_path: Path,
    *,
    before: bytes | None,
    after: bytes | None,
    patch_text: str,
    rel_path: str = "result.txt",
) -> None:
    """`git apply --check` then `git apply`, verifying exact resulting content."""

    repo, patch_path = git_apply_check(tmp_path, before=before, patch_text=patch_text, rel_path=rel_path)

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, f"git apply --check failed: {check.stderr}"

    apply = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert apply.returncode == 0, f"git apply failed: {apply.stderr}"

    result_path = repo / rel_path
    if after is None:
        assert not result_path.exists()
    else:
        assert result_path.read_bytes() == after


# ---------------------------------------------------------------------------
# 1. Reproduction of the exact malformed result.txt case.
# ---------------------------------------------------------------------------


def test_patch_rendering_reproduces_gate3_result_txt_case_validly(tmp_path: Path) -> None:
    before = {"result.txt": b"not completed\n"}
    after_bytes = b"TOMTIT Claude Code CLI adapter smoke test passed"
    after = {"result.txt": after_bytes}

    patch = render_unified_diff(before, after, ("result.txt",))

    # The exact defect: an unprefixed blank line anywhere in the patch body.
    hunk_lines = patch.splitlines()
    header_lines = {hunk_lines[0], hunk_lines[1]}
    for line in hunk_lines:
        if line in header_lines or line.startswith("@@"):
            continue
        assert line[:1] in (" ", "+", "-", "\\"), f"malformed unprefixed line: {line!r}"

    assert_patch_applies_cleanly(
        tmp_path,
        before=before["result.txt"],
        after=after_bytes,
        patch_text=patch,
    )


# ---------------------------------------------------------------------------
# 2. Generated patch passes git apply --check inside a temp repo.
# ---------------------------------------------------------------------------


@requires_git
def test_generated_patch_passes_git_apply_check(tmp_path: Path) -> None:
    before = {"result.txt": b"line one\nline two\n"}
    after_bytes = b"line one\nline TWO\n"
    after = {"result.txt": after_bytes}

    patch = render_unified_diff(before, after, ("result.txt",))

    assert_patch_applies_cleanly(tmp_path, before=before["result.txt"], after=after_bytes, patch_text=patch)


# ---------------------------------------------------------------------------
# 3-6. Newline-at-end-of-file edge cases.
# ---------------------------------------------------------------------------


@requires_git
def test_both_sides_end_with_newline(tmp_path: Path) -> None:
    before_bytes = b"alpha\n"
    after_bytes = b"beta\n"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert "\\ No newline at end of file" not in patch
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


@requires_git
def test_original_has_newline_replacement_has_none(tmp_path: Path) -> None:
    before_bytes = b"alpha\n"
    after_bytes = b"beta"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert patch.count("\\ No newline at end of file") == 1
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


@requires_git
def test_original_has_no_newline_replacement_has_one(tmp_path: Path) -> None:
    before_bytes = b"alpha"
    after_bytes = b"beta\n"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert patch.count("\\ No newline at end of file") == 1
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


@requires_git
def test_both_sides_have_no_final_newline(tmp_path: Path) -> None:
    before_bytes = b"alpha"
    after_bytes = b"beta"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert patch.count("\\ No newline at end of file") == 2
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


# ---------------------------------------------------------------------------
# 7. Empty line inside file content.
# ---------------------------------------------------------------------------


@requires_git
def test_empty_line_inside_file_content(tmp_path: Path) -> None:
    before_bytes = b"first\n\nthird\n"
    after_bytes = b"first\n\nCHANGED\n"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


# ---------------------------------------------------------------------------
# 8. File creation.
# ---------------------------------------------------------------------------


@requires_git
def test_file_creation_produces_valid_patch(tmp_path: Path) -> None:
    after_bytes = b"brand new content\n"
    patch = render_unified_diff({}, {"new_file.txt": after_bytes}, ("new_file.txt",))
    assert "/dev/null" in patch
    assert_patch_applies_cleanly(
        tmp_path,
        before=None,
        after=after_bytes,
        patch_text=patch,
        rel_path="new_file.txt",
    )


# ---------------------------------------------------------------------------
# 9. File deletion.
# ---------------------------------------------------------------------------


@requires_git
def test_file_deletion_produces_valid_patch(tmp_path: Path) -> None:
    before_bytes = b"soon to be gone\n"
    patch = render_unified_diff({"gone.txt": before_bytes}, {}, ("gone.txt",))
    assert "/dev/null" in patch
    assert_patch_applies_cleanly(
        tmp_path,
        before=before_bytes,
        after=None,
        patch_text=patch,
        rel_path="gone.txt",
    )


# ---------------------------------------------------------------------------
# 10. Multiple changed lines.
# ---------------------------------------------------------------------------


@requires_git
def test_multiple_changed_lines_produce_valid_patch(tmp_path: Path) -> None:
    before_bytes = b"one\ntwo\nthree\nfour\nfive\n"
    after_bytes = b"ONE\ntwo\nTHREE\nfour\nFIVE\n"
    patch = render_unified_diff({"result.txt": before_bytes}, {"result.txt": after_bytes}, ("result.txt",))
    assert_patch_applies_cleanly(tmp_path, before=before_bytes, after=after_bytes, patch_text=patch)


# ---------------------------------------------------------------------------
# 11. Patch remains bounded (omitted-content path unaffected by this fix).
# ---------------------------------------------------------------------------


def test_oversized_file_content_remains_omitted_not_malformed() -> None:
    # `snapshot_workspace_contents` represents an over-bound or unreadable file
    # as `None`; this is the contract `render_unified_diff` must still honor.
    patch = render_unified_diff({"big.txt": None}, {"big.txt": None}, ("big.txt",))
    assert "content omitted" in patch
    assert "\\ No newline at end of file" not in patch


# ---------------------------------------------------------------------------
# 12. Patch touches only the expected path.
# ---------------------------------------------------------------------------


def test_patch_touches_only_the_expected_path() -> None:
    before = {"result.txt": b"a\n", "README.md": b"unchanged\n"}
    after = {"result.txt": b"b\n", "README.md": b"unchanged\n"}
    patch = render_unified_diff(before, after, ("result.txt",))
    assert "result.txt" in patch
    assert "README.md" not in patch


# ---------------------------------------------------------------------------
# 13. Existing real Gate 3 evidence files remain byte-for-byte unchanged.
# ---------------------------------------------------------------------------


def test_committed_gate3_evidence_files_unchanged_by_this_fix() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        pytest.skip("not a git checkout")

    completed = subprocess.run(
        ["git", "status", "--porcelain", "--", "evidence/gate3"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "", (
        f"Gate 3 evidence must not be modified by this patch: {completed.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Determinism of the renderer itself.
# ---------------------------------------------------------------------------


def test_patch_rendering_is_deterministic() -> None:
    before = {"result.txt": b"not completed\n"}
    after = {"result.txt": b"TOMTIT Claude Code CLI adapter smoke test passed"}
    first = render_unified_diff(before, after, ("result.txt",))
    second = render_unified_diff(before, after, ("result.txt",))
    assert first == second


# ===========================================================================
# Bounded exact-command authorization (EXACT_COMMAND_AUTHORIZATION corrective
# task). See EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md and
# EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md for the reproduced defect,
# the two HIGH corrective findings, and the full design rationale.
# ===========================================================================

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    AuthorizedCommandError,
    ClaudeCLIAuthorizationContract,
    build_evidence_manifest,
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    normalize_authorized_command,
    normalize_authorized_commands,
    render_authorized_command_cli_rule,
    render_authorized_command_display,
    render_authorized_command_evidence_rule,
    resolve_claude_cli_authorization_contract,
    validate_authorized_command_argv,
)
import tomtit.adapters.claude_code_cli as claude_code_cli_module  # noqa: E402

SYNTHETIC_SECRET = "SYNTHETIC_TEST_SECRET_DO_NOT_USE"


# ---------------------------------------------------------------------------
# 1. No authorized command preserves existing behavior.
# ---------------------------------------------------------------------------


def test_no_authorized_command_preserves_existing_behavior(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path,
        "fake-claude-no-auth-cmd",
        """
import pathlib
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.authorized_command_fingerprints == ()
    assert result.authorized_command_set_fingerprint is None
    assert result.authorized_command_count == 0
    assert result.authorized_command_cli_rules == ()
    assert "--allowedTools" not in result.sanitized_argv


# ---------------------------------------------------------------------------
# 2-5. normalize_authorized_command(s) unit behavior.
# ---------------------------------------------------------------------------


def test_one_exact_command_is_accepted() -> None:
    argv = normalize_authorized_command("npm install foo --save-exact")
    assert argv == ("npm", "install", "foo", "--save-exact")


def test_multiple_exact_commands_are_accepted() -> None:
    commands = normalize_authorized_commands(["npm install foo", "npm run build"])
    assert commands == (("npm", "install", "foo"), ("npm", "run", "build"))


def test_duplicate_commands_are_deterministically_deduplicated() -> None:
    commands = normalize_authorized_commands(["npm install foo", "npm install foo", "npm run build"])
    assert commands == (("npm", "install", "foo"), ("npm", "run", "build"))


def test_argument_order_is_preserved() -> None:
    argv = normalize_authorized_command("npm install next-mdx-remote gray-matter tsx --save-exact")
    assert argv == ("npm", "install", "next-mdx-remote", "gray-matter", "tsx", "--save-exact")


# ---------------------------------------------------------------------------
# 6-7. Empty / whitespace-only rejected.
# ---------------------------------------------------------------------------


def test_empty_command_rejected() -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command("")
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


def test_whitespace_only_command_rejected() -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command("   \t  ")
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# 8-16. Shell syntax rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo && rm -rf /",
        "npm install foo || echo fallback",
        "npm install foo; echo done",
        "npm install foo | tee log.txt",
        "npm install foo > out.txt",
        "npm install foo >> out.txt",
        "npm install foo < in.txt",
        "npm install foo\necho done",
        "npm install `whoami`",
        "npm install $(whoami)",
    ],
    ids=["and", "or", "semicolon", "pipe", "redirect-out", "append-out", "redirect-in", "newline", "backtick", "dollar-paren"],
)
def test_shell_syntax_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# 17-18. Wildcard-only / broad Bash rule rejected, including a wildcard
# merely *embedded* in a token (Codex corrective review, HIGH finding 1:
# the prior validator only rejected wildcard-ONLY tokens).
# ---------------------------------------------------------------------------


def test_wildcard_only_rule_rejected() -> None:
    with pytest.raises(AuthorizedCommandError):
        normalize_authorized_command("*")


def test_broad_bash_rule_rejected() -> None:
    # These are exactly the broad forms the task explicitly forbids; none may
    # ever be constructed by this module regardless of caller input.
    for broad in ("npm *", "npm install *", "*"):
        with pytest.raises(AuthorizedCommandError):
            normalize_authorized_command(broad)


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo*",
        "npm install *.txt",
        "npm install foo?bar",
        "npm install foo[1]",
        "npm install foo]bar",
        "npm run:*",
        "npm run *",
    ],
    ids=["trailing-star", "leading-star", "question-mark", "bracket-open", "bracket-close", "colon-star", "wildcard-space"],
)
def test_wildcard_or_glob_embedded_in_token_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# Nested shell / interpreter / env-wrapper / environment-assignment forms
# rejected (Codex corrective review, HIGH finding 1, required correction 1
# categories 7-10).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_command",
    [
        "bash -c 'echo hi'",
        "/bin/bash -c 'echo hi'",
        "sh -c 'echo hi'",
        "zsh -c 'echo hi'",
        "npm install foo -c",
        "env FOO=bar npm install foo",
        "/usr/bin/env npm install foo",
        "FOO=bar npm install foo",
        "npm FOO=bar install foo",
    ],
    ids=[
        "bash-c",
        "abs-bash-c",
        "sh-c",
        "zsh-c",
        "trailing-c-flag",
        "env-wrapper",
        "abs-env-wrapper",
        "leading-env-assignment",
        "mid-env-assignment",
    ],
)
def test_shell_interpreter_and_env_forms_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# Control characters and ambiguous/non-ASCII whitespace rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo\x00bar",
        "npm install foo\x07bar",
        "npm install foo\rbar",
        "npm install foo\tbar",
        "npm install foo bar",
        "npm install foo​bar",
        "npm install foo bar",
    ],
    ids=["nul", "bell", "carriage-return", "tab", "nbsp", "zero-width-space", "line-separator"],
)
def test_control_and_ambiguous_whitespace_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


def test_empty_argv_token_rejected_via_direct_construction(workspace: Path) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        validate_authorized_command_argv(("npm", "", "install"))
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# Credential-bearing forms rejected (Codex corrective review, HIGH finding 2
# required correction: never persist -- but the strongest possible mitigation
# is to reject the credential-bearing command outright).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_command",
    [
        f"curl --token {SYNTHETIC_SECRET}",
        f"curl --api-key={SYNTHETIC_SECRET}",
        f"curl --api_key={SYNTHETIC_SECRET}",
        f"curl --password {SYNTHETIC_SECRET}",
        f"curl --private-key {SYNTHETIC_SECRET}",
        f'curl -H "Authorization: Bearer {SYNTHETIC_SECRET}"',
        f"curl https://user:{SYNTHETIC_SECRET}@example.com/data",
    ],
    ids=["token-flag", "api-key-equals", "api_key-equals", "password-flag", "private-key-flag", "auth-header", "url-userinfo"],
)
def test_credential_bearing_command_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "CREDENTIAL_BEARING_AUTHORIZED_COMMAND"
    assert SYNTHETIC_SECRET not in str(excinfo.value)
    assert SYNTHETIC_SECRET not in excinfo.value.message


def test_valid_harmless_exact_argv_still_accepted() -> None:
    argv = normalize_authorized_command("npm install next-mdx-remote gray-matter tsx --save-exact")
    assert argv == ("npm", "install", "next-mdx-remote", "gray-matter", "tsx", "--save-exact")
    validate_authorized_command_argv(argv)


# ===========================================================================
# Validator fix2 (Codex validator-fix2 re-review): mixed-case / path-qualified
# shell basenames, compound interpreter command-mode flags, non-ASCII and
# Unicode-lookalike tokens, and credential-key material split across
# adjacent argv tokens must all be rejected -- through the same canonical
# validator used by raw CLI input and direct `ClaudeCodeCLIRequest`
# construction alike.
# ===========================================================================


@pytest.mark.parametrize(
    "raw_command",
    [
        "Bash -c echo",
        "/bin/BaSh -c echo",
        "./ZsH -c echo",
        "/usr/bin/ENV npm install foo",
        r"C:\tools\BaSh -c echo",
    ],
    ids=["bash-titlecase", "abs-path-mixedcase-bash", "relative-mixedcase-zsh", "abs-path-uppercase-env", "windows-path-mixedcase-bash"],
)
def test_mixed_case_and_path_qualified_shell_names(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


def test_basename_match_is_exact_not_substring() -> None:
    # A token that merely *contains* a forbidden shell name as a substring
    # (not as its own exact, case-folded basename) must not be rejected --
    # this check is basename-equality-scoped, not a blanket substring ban.
    argv = normalize_authorized_command("npm install myzshrc-plugin")
    assert argv == ("npm", "install", "myzshrc-plugin")


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo -lc",
        "npm install foo -ic",
        "npm install foo -lci",
        "npm install foo -cl",
        "npm install foo -abc",
        "npm install foo --command",
    ],
    ids=["lc", "ic", "lci", "cl", "abc", "long-form-command"],
)
def test_compound_interpreter_command_flags_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


def test_unrelated_short_flags_without_c_still_accepted() -> None:
    # A conservative "-c" rule must not reject ordinary flags that merely
    # look similar but contain no "c".
    argv = normalize_authorized_command("npm install foo -p -x")
    assert argv == ("npm", "install", "foo", "-p", "-x")


def test_long_form_flag_containing_c_is_not_rejected() -> None:
    # Only the literal "--command" long-form flag is rejected; other
    # long-form flags that happen to contain "c" (e.g. "--cache") must not
    # be swept up by the compound-short-flag rule.
    argv = normalize_authorized_command("npm install foo --cache")
    assert argv == ("npm", "install", "foo", "--cache")


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install \u0410\u0432\u0442\u043e",  # Cyrillic lookalike token
        "npm install \uff41\uff50\uff50",  # full-width Latin lookalike ("app")
        "npm install caf\u00e9",  # ordinary non-ASCII character
    ],
    ids=["cyrillic-lookalike", "fullwidth-lookalike", "ordinary-non-ascii"],
)
def test_non_ascii_tokens_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


@pytest.mark.parametrize(
    "raw_command",
    [
        f"curl --api key {SYNTHETIC_SECRET}",
        f"curl --private key {SYNTHETIC_SECRET}",
        f"curl --access token {SYNTHETIC_SECRET}",
        f"curl --client secret {SYNTHETIC_SECRET}",
        f"curl Authorization Bearer {SYNTHETIC_SECRET}",
        f"curl Authorization Header {SYNTHETIC_SECRET}",
    ],
    ids=["split-api-key", "split-private-key", "split-access-token", "split-client-secret", "split-authorization-bearer", "split-authorization-header"],
)
def test_split_credential_key_forms_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "CREDENTIAL_BEARING_AUTHORIZED_COMMAND"
    assert SYNTHETIC_SECRET not in str(excinfo.value)
    assert SYNTHETIC_SECRET not in excinfo.value.message


def test_validator_fix2_error_messages_never_contain_synthetic_values() -> None:
    cases = [
        "Bash -c echo",
        "/bin/BaSh -c echo",
        "npm install foo -lci",
        "npm install \u0410\u0432\u0442\u043e",
        f"curl --api key {SYNTHETIC_SECRET}",
        f"curl Authorization Bearer {SYNTHETIC_SECRET}",
    ]
    for raw_command in cases:
        with pytest.raises(AuthorizedCommandError) as excinfo:
            normalize_authorized_command(raw_command)
        assert SYNTHETIC_SECRET not in str(excinfo.value)
        assert raw_command not in str(excinfo.value)


# ===========================================================================
# Validator fix3 (Codex validator-fix3 re-review): the prior fix2 patch's
# long-command-flag check relied on exact raw-string equality with
# "--command" and the split-credential scan only recognized a fixed
# `_`/`-`/`.`/`:` separator set. Both were bypassed by case variants,
# repeated dashes, and `=`/`,` punctuation. This section covers the
# canonical option-key normalizer (`_normalize_option_words` /
# `_is_long_form_command_flag`) and the canonical ASCII credential-word
# tokenizer (`_credential_scan_words`) that replace them.
# ===========================================================================


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo -command",
        "npm install foo --COMMAND",
        "npm install foo ---command",
        f"npm install foo --command={SYNTHETIC_SECRET}",
        f"npm install foo --COMMAND:{SYNTHETIC_SECRET}",
        "npm install foo --command,anything",
        "npm install foo --command.anything",
        "npm install foo --command_anything",
    ],
    ids=[
        "single-dash-command",
        "uppercase-command",
        "triple-dash-command",
        "command-equals-value",
        "uppercase-command-colon-value",
        "command-comma-suffix",
        "command-dot-suffix",
        "command-underscore-suffix",
    ],
)
def test_long_command_flag_equivalence_forms_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"
    assert SYNTHETIC_SECRET not in str(excinfo.value)


@pytest.mark.parametrize(
    "raw_command",
    [
        "npm install foo --cache",
        "npm install foo --commander",
        "npm install foo --commandment",
    ],
    ids=["cache", "commander", "commandment"],
)
def test_long_command_flag_check_does_not_reject_unrelated_words(raw_command: str) -> None:
    argv = normalize_authorized_command(raw_command)
    assert argv == tuple(raw_command.split())


@pytest.mark.parametrize(
    "raw_command",
    [
        f"curl --api={SYNTHETIC_SECRET}",
        f"curl --api,{SYNTHETIC_SECRET}",
        "curl --api :key",  # "--api" then bare "key"-shaped punctuation token
        "curl --api:key",
        "curl --api.key",
        "curl --api_key",
        "curl --private=key",
        "curl --access.token",
        "curl Authorization=Bearer",
        "curl --client:secret",
        "curl --API=KEY",
        "curl --PRIVATE.KEY",
        "curl --Access:Token",
    ],
    ids=[
        "api-equals-key-value",
        "api-comma-value",
        "api-space-colon-key",
        "api-colon-key",
        "api-dot-key",
        "api-underscore-key",
        "private-equals-key",
        "access-dot-token",
        "authorization-equals-bearer",
        "client-colon-secret",
        "mixed-case-api-equals-key",
        "mixed-case-private-dot-key",
        "mixed-case-access-colon-token",
    ],
)
def test_split_or_punctuation_joined_credential_forms_rejected(raw_command: str) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(raw_command)
    assert excinfo.value.category in ("CREDENTIAL_BEARING_AUTHORIZED_COMMAND", "INVALID_AUTHORIZED_COMMAND")
    assert SYNTHETIC_SECRET not in str(excinfo.value)


def test_api_key_as_adjacent_argv_tokens_rejected() -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(f"curl --api, key {SYNTHETIC_SECRET}")
    assert excinfo.value.category == "CREDENTIAL_BEARING_AUTHORIZED_COMMAND"
    assert SYNTHETIC_SECRET not in str(excinfo.value)


def test_authorization_comma_bearer_as_adjacent_argv_tokens_rejected() -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        normalize_authorized_command(f"curl Authorization, Bearer {SYNTHETIC_SECRET}")
    assert excinfo.value.category == "CREDENTIAL_BEARING_AUTHORIZED_COMMAND"
    assert SYNTHETIC_SECRET not in str(excinfo.value)


def test_validator_fix3_error_messages_never_contain_synthetic_values() -> None:
    cases = [
        "npm install foo --COMMAND",
        "npm install foo ---command",
        f"npm install foo --command={SYNTHETIC_SECRET}",
        f"curl --api={SYNTHETIC_SECRET}",
        f"curl --api,{SYNTHETIC_SECRET}",
        "curl --API=KEY",
    ]
    for raw_command in cases:
        with pytest.raises(AuthorizedCommandError) as excinfo:
            normalize_authorized_command(raw_command)
        assert SYNTHETIC_SECRET not in str(excinfo.value)
        assert raw_command not in str(excinfo.value)


def test_compact_short_option_clusters_still_rejected_after_fix3() -> None:
    # Non-regression: the fix2 compact-short-flag rule must remain intact
    # after fix3's long-command-flag rewrite.
    for raw_command in ("npm install foo -lc", "npm install foo -ic", "npm install foo -abc"):
        with pytest.raises(AuthorizedCommandError) as excinfo:
            normalize_authorized_command(raw_command)
        assert excinfo.value.category == "INVALID_AUTHORIZED_COMMAND"


# ---------------------------------------------------------------------------
# The direct-construction trust boundary cannot bypass any rejected syntax
# class above -- `ClaudeCodeCLIRequest.__post_init__` calls the exact same
# canonical validator (Codex corrective review, HIGH finding 1).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_argv",
    [
        ("npm", "install", "foo*"),
        ("npm", "install", "foo?"),
        ("bash", "-c", "echo hi"),
        ("env", "FOO=bar", "npm", "install"),
        ("FOO=bar", "npm", "install"),
        ("npm", "install", "-c"),
        ("npm", "install", "foo\x00bar"),
        ("npm", "install", "foo bar"),
        ("curl", "--token", SYNTHETIC_SECRET),
        ("curl", f"https://user:{SYNTHETIC_SECRET}@example.com"),
        ("npm", "", "install"),
        (";",),
        ("npm", "install", "foo;"),
        ("Bash", "-c", "echo"),
        ("/bin/BaSh", "-c", "echo"),
        ("./ZsH", "-c", "echo"),
        ("/usr/bin/ENV", "npm", "install"),
        ("npm", "install", "foo", "-lc"),
        ("npm", "install", "foo", "-ic"),
        ("npm", "install", "foo", "-lci"),
        ("npm", "install", "foo", "--command"),
        ("npm", "install", "Авто"),
        ("npm", "install", "café"),
        ("curl", "--api", "key", SYNTHETIC_SECRET),
        ("curl", "--private", "key", SYNTHETIC_SECRET),
        ("curl", "--access", "token", SYNTHETIC_SECRET),
        ("curl", "--client", "secret", SYNTHETIC_SECRET),
        ("curl", "Authorization", "Bearer", SYNTHETIC_SECRET),
        ("npm", "install", "foo", "--COMMAND"),
        ("npm", "install", "foo", "---command"),
        ("npm", "install", "foo", "-command"),
        ("npm", "install", "foo", f"--command={SYNTHETIC_SECRET}"),
        ("npm", "install", "foo", f"--COMMAND:{SYNTHETIC_SECRET}"),
        ("npm", "install", "foo", "--command,anything"),
        ("npm", "install", "foo", "--command.anything"),
        ("npm", "install", "foo", "--command_anything"),
        ("curl", f"--api={SYNTHETIC_SECRET}"),
        ("curl", f"--api,{SYNTHETIC_SECRET}"),
        ("curl", "--api,", "key"),
        ("curl", "--api:key"),
        ("curl", "--api.key"),
        ("curl", "--private=key"),
        ("curl", "--access.token"),
        ("curl", "Authorization=Bearer"),
        ("curl", "Authorization,", "Bearer"),
        ("curl", "--client:secret"),
        ("curl", "--API=KEY"),
    ],
)
def test_direct_request_construction_cannot_bypass_canonical_validator(
    workspace: Path, bad_argv: tuple[str, ...]
) -> None:
    with pytest.raises(AuthorizedCommandError) as excinfo:
        base_request(workspace, authorized_commands=(bad_argv,))
    assert SYNTHETIC_SECRET not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 19. shell=True is never used (source-level guarantee, mirrors the existing
#     adapter-wide guarantee).
# ---------------------------------------------------------------------------


def test_authorized_command_source_never_uses_shell_true() -> None:
    import ast

    source = Path(claude_code_cli_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert not (isinstance(node.value, ast.Constant) and node.value.value is True), (
                "shell=True must never appear in a real keyword argument"
            )


# ---------------------------------------------------------------------------
# 20. Adapter request is deeply immutable.
# ---------------------------------------------------------------------------


def test_request_with_authorized_commands_is_deeply_immutable(workspace: Path) -> None:
    request = base_request(workspace, authorized_commands=(("npm", "install", "foo"),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.authorized_commands = (("npm", "install", "bar"),)  # type: ignore[misc]
    # The tuple-of-tuples itself is immutable too (tuples cannot be mutated).
    assert isinstance(request.authorized_commands, tuple)
    assert isinstance(request.authorized_commands[0], tuple)


def test_result_authorized_fields_are_immutable(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(tmp_path, "fake-claude-immutable-auth", "pass")
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.authorized_command_fingerprints = ("deadbeef",)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.authorized_command_cli_rules = ("Bash(x)",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 21. Empty authorization emits no allow-rule CLI arguments.
# ---------------------------------------------------------------------------


def test_empty_authorization_emits_no_allow_rule_argv(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path, "fake-claude-empty-auth", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    adapter = ClaudeCodeCLIAdapter()
    argv = adapter._build_argv(str(script), base_request(workspace))
    assert "--allowedTools" not in argv


# ---------------------------------------------------------------------------
# 22. Exact supported command emits only the expected bounded CLI rule
#     (verified via monkeypatch, since production always fails closed).
# ---------------------------------------------------------------------------


def test_exact_supported_command_emits_only_expected_bounded_rule(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    contract, _ = resolve_claude_cli_authorization_contract()
    assert contract is ClaudeCLIAuthorizationContract.EXACT_SUPPORTED

    script = write_fake_executable(
        tmp_path,
        "fake-claude-exact-supported",
        """
import sys, pathlib
pathlib.Path("argv_seen.json").write_text(repr(sys.argv), encoding="utf-8")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    argv_command = ("npm", "install", "foo", "--save-exact")
    request = base_request(workspace, authorized_commands=(argv_command,))
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    expected_placeholder = render_authorized_command_evidence_rule(argv_command)
    raw_rule = render_authorized_command_cli_rule(argv_command)

    # Evidence (sanitized_argv, result fields) carries only the safe
    # fingerprint placeholder -- never the raw Bash(...) rule (Codex
    # corrective review, HIGH finding 2).
    assert result.authorized_command_cli_rules == (expected_placeholder,)
    assert raw_rule not in result.authorized_command_cli_rules
    assert "--allowedTools" in result.sanitized_argv
    idx = result.sanitized_argv.index("--allowedTools")
    assert result.sanitized_argv[idx + 1] == expected_placeholder
    assert raw_rule not in result.sanitized_argv
    # Exactly one rule argv element per authorized command -- no extra
    # broadened or wildcarded rule sneaks in alongside it, and (since the
    # task prompt now occupies its own positional slot immediately after
    # `-p`, before any variadic option -- see
    # CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md) nothing, prompt included,
    # follows the single rule.
    assert len(result.sanitized_argv) == idx + 2

    assert result.authorized_command_fingerprints == (compute_authorized_command_fingerprint(argv_command),)
    assert result.authorized_command_set_fingerprint == compute_authorized_command_set_fingerprint(
        (argv_command,)
    )
    assert result.authorized_command_count == 1

    # The real subprocess argv (what actually reached the fake CLI) does
    # carry the raw rule -- that is the one place it is allowed to exist,
    # transiently, while constructing the real invocation.
    argv_seen_repr = (workspace / "argv_seen.json").read_text(encoding="utf-8")
    assert raw_rule in argv_seen_repr


def test_render_authorized_command_cli_rule_is_bounded() -> None:
    rule = render_authorized_command_cli_rule(("npm", "install", "foo bar", "--save-exact"))
    assert rule == "Bash(npm install 'foo bar' --save-exact)"
    assert "*" not in rule
    assert ":*" not in rule


def test_render_authorized_command_display_round_trips_via_shlex() -> None:
    import shlex

    argv = ("npm", "install", "next-mdx-remote", "gray-matter", "tsx", "--save-exact")
    display = render_authorized_command_display(argv)
    assert tuple(shlex.split(display)) == argv


# ---------------------------------------------------------------------------
# 23-24. Unsupported CLI capability fails closed before model invocation.
#
# The layered exact-argv capability is now ENABLED by default, so these two
# tests pin the *disabled* direction: turning the single layered flag off must
# still fail closed before any process starts. They deliberately toggle
# `_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE`, never the
# bare-Bash constant, which no longer gates anything.
# ---------------------------------------------------------------------------


def test_unsupported_cli_contract_fails_closed_when_layered_capability_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", False
    )
    contract, rationale = resolve_claude_cli_authorization_contract()
    assert contract is ClaudeCLIAuthorizationContract.UNSUPPORTED
    assert rationale


def test_unsupported_cli_contract_fails_before_model_invocation(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", False
    )
    invocation_marker = tmp_path / "invoked.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-should-not-run",
        f"""
import pathlib
pathlib.Path({str(invocation_marker)!r}).write_text("invoked")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, authorized_commands=(("npm", "install", "foo"),))
    adapter = ClaudeCodeCLIAdapter()

    result = adapter.run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.success is False
    assert result.error_category == "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"
    assert result.exit_code is None
    assert not invocation_marker.exists(), "the worker process must never start when the contract is unsupported"
    assert result.authorized_command_fingerprints == (compute_authorized_command_fingerprint(("npm", "install", "foo")),)
    assert result.authorized_command_count == 1
    assert result.authorized_command_cli_rules == ()


# ---------------------------------------------------------------------------
# Evidence manifest includes the new fields, credential-safely.
# ---------------------------------------------------------------------------


def test_evidence_manifest_includes_authorized_command_fields(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path, "fake-claude-manifest-auth", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))
    manifest = build_evidence_manifest(result)
    assert manifest["authorized_command_fingerprints"] == []
    assert manifest["authorized_command_set_fingerprint"] is None
    assert manifest["authorized_command_count"] == 0
    assert manifest["authorized_command_cli_rules"] == []
    assert "authorized_commands" not in manifest


def test_evidence_credential_safe_for_authorized_commands(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    secret = "sk-authorized-cmd-secret"
    monkeypatch.setenv("TOMTIT_TEST_API_KEY", secret)
    script = write_fake_executable(
        tmp_path, "fake-claude-secret-auth", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    request = base_request(
        workspace,
        authorized_commands=(("npm", "install", "foo"),),
        environment_allowlist=("PATH", "TOMTIT_TEST_API_KEY"),
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))
    manifest_json = render_evidence_manifest_json(result)
    assert secret not in manifest_json


# ---------------------------------------------------------------------------
# Evidence safety group (#19-30): no raw authorized-command argv or rendered
# Bash rule ever reaches the adapter result, the evidence manifest, the
# sanitized argv, or an on-disk evidence file -- and fingerprints are
# deterministic, order-sensitive, and never expose the raw command.
# ---------------------------------------------------------------------------


def test_adapter_result_has_no_raw_authorized_command_attribute(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(tmp_path, "fake-claude-no-raw-attr", "pass")
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(
        base_request(workspace, authorized_commands=(("npm", "install", "foo"),)), executable_path=str(script)
    )
    assert not hasattr(result, "authorized_commands")


# A non-credential-shaped placeholder value: distinct from SYNTHETIC_SECRET
# (which deliberately matches the credential-term rejection pattern above)
# so this test can prove the *general* raw-value-never-persisted guarantee
# for an accepted command, independent of the separate credential rejection.
SYNTHETIC_ARGUMENT_VALUE = "argvalue-should-never-leak-9f3c2a"


def test_command_argument_value_never_persisted_in_authorized_command_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted authorized command's raw *argument* text must never reach
    `sanitized_argv`, `authorized_command_cli_rules`, the evidence manifest,
    or any on-disk evidence file -- only its fingerprint may. Env-value
    redaction alone cannot catch this (Codex corrective review, HIGH finding
    2: "authorized command values can persist credentials")."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path, "fake-claude-arg-value", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    argv_command = ("npm", "install", f"pkg-{SYNTHETIC_ARGUMENT_VALUE}")
    request = base_request(workspace, authorized_commands=(argv_command,))
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    manifest_json = render_evidence_manifest_json(result)
    assert SYNTHETIC_ARGUMENT_VALUE not in manifest_json
    assert SYNTHETIC_ARGUMENT_VALUE not in " ".join(result.sanitized_argv)
    assert SYNTHETIC_ARGUMENT_VALUE not in "".join(result.authorized_command_cli_rules)

    output_dir = tmp_path / "evidence"
    from tomtit.adapters.claude_code_cli import write_evidence

    write_evidence(result, before_contents={}, after_contents={}, output_dir=output_dir)
    for path in output_dir.rglob("*"):
        if path.is_file():
            assert SYNTHETIC_ARGUMENT_VALUE not in path.read_text(encoding="utf-8", errors="replace"), path


def test_fingerprint_is_deterministic() -> None:
    argv = ("npm", "install", "foo", "--save-exact")
    assert compute_authorized_command_fingerprint(argv) == compute_authorized_command_fingerprint(argv)


def test_fingerprint_changes_with_argument_order() -> None:
    forward = compute_authorized_command_fingerprint(("npm", "install", "foo"))
    reversed_argv = compute_authorized_command_fingerprint(("foo", "install", "npm"))
    assert forward != reversed_argv


def test_set_fingerprint_changes_with_authorization_order() -> None:
    a = (("npm", "install", "foo"), ("npm", "run", "build"))
    b = (("npm", "run", "build"), ("npm", "install", "foo"))
    assert compute_authorized_command_set_fingerprint(a) != compute_authorized_command_set_fingerprint(b)


def test_set_fingerprint_changes_with_membership() -> None:
    a = (("npm", "install", "foo"),)
    b = (("npm", "install", "foo"), ("npm", "run", "build"))
    assert compute_authorized_command_set_fingerprint(a) != compute_authorized_command_set_fingerprint(b)


def test_evidence_rule_placeholder_never_contains_raw_argv_text() -> None:
    argv = ("npm", "install", "next-mdx-remote", "--save-exact")
    placeholder = render_authorized_command_evidence_rule(argv)
    for token in argv:
        assert token not in placeholder
    assert placeholder.startswith("Bash(<AUTHORIZED_COMMAND_SHA256:")
    assert placeholder.endswith(">)")


# ---------------------------------------------------------------------------
# Regression proof: the validator-fix2 patch touches only validation, not the
# fingerprint/evidence design Codex already accepted as HIGH-2 RESOLVED.
# ---------------------------------------------------------------------------


def test_validator_fix2_preserves_fingerprint_only_result_schema() -> None:
    field_names = {f.name for f in dataclasses.fields(ClaudeCodeCLIRequest)}
    assert "authorized_commands" in field_names  # the in-memory request field is unchanged

    from tomtit.adapters.claude_code_cli import ClaudeCodeCLIResult as _Result

    result_field_names = {f.name for f in dataclasses.fields(_Result)}
    assert "authorized_commands" not in result_field_names
    assert {
        "authorized_command_fingerprints",
        "authorized_command_set_fingerprint",
        "authorized_command_count",
        "authorized_command_cli_rules",
    }.issubset(result_field_names)


def test_validator_fix2_preserves_no_raw_evidence_for_accepted_command(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path, "fake-claude-fix2-regression", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    argv_command = ("npm", "install", f"pkg-{SYNTHETIC_ARGUMENT_VALUE}")
    request = base_request(workspace, authorized_commands=(argv_command,))
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    raw_rule = render_authorized_command_cli_rule(argv_command)
    expected_placeholder = render_authorized_command_evidence_rule(argv_command)
    assert result.authorized_command_cli_rules == (expected_placeholder,)
    assert raw_rule not in result.sanitized_argv
    assert SYNTHETIC_ARGUMENT_VALUE not in " ".join(result.sanitized_argv)
    assert SYNTHETIC_ARGUMENT_VALUE not in render_evidence_manifest_json(result)


# ===========================================================================
# TOMTIT-owned exact-argv PreToolUse Bash guard (defense-in-depth layer in
# front of Claude's native `--allowedTools` matching). An isolated real-CLI
# probe established that a bare `Bash(<command>)` rule is matched *after*
# Claude strips a fixed set of process wrappers (`command`, `builtin`,
# `timeout`, `time`, `nice`, `nohup`, `stdbuf`, zsh `noglob`, certain
# env-assignment prefixes, bare `xargs`), so it is provider-normalized, not
# TOMTIT Owner-approved-exact. See
# EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md and
# tests/adapters/test_exact_bash_guard.py for the hook module's own
# self-contained unit/subprocess tests; this section covers only the
# adapter-side plumbing (settings/fingerprints construction, argv wiring,
# cleanup, evidence fields, provider-JSON redaction).
# ===========================================================================

from tomtit.adapters.exact_bash_guard import DENIAL_MESSAGE as _GUARD_DENIAL_MESSAGE  # noqa: E402

_GUARD_ARGV_COMMAND = ("python3", "writer.py", "exact.txt")

# The end-to-end fake-claude harness (`_fake_claude_invoking_real_hook`)
# writes these marker files alongside "result.txt" so the test can inspect
# what the real hook subprocess did; they must be declared as expected
# changed paths or the adapter's own out-of-scope-write detection would
# (correctly, but irrelevantly to what these tests check) report a scope
# violation.
_GUARD_HOOK_HARNESS_ALLOWED_PATHS = (
    "result.txt",
    "settings_path_seen.txt",
    "hook_exit_code.txt",
    "hook_stderr.txt",
    "hook_stdout.txt",
)


def _pretool_hook_by_matcher(settings: dict, matcher: str) -> dict:
    """Select a PreToolUse hook entry by its `matcher`, never by position.

    Hook ordering is not part of the contract (the file-write guard entries
    are generated before the Bash entry); hook *identity* is. Requires
    exactly one match -- both "missing" and "duplicated" are bugs a test
    should fail loudly on, not silently paper over by taking `[0]`.
    """

    matches = [entry for entry in settings["hooks"]["PreToolUse"] if entry["matcher"] == matcher]
    assert matches, f"no PreToolUse hook registered for matcher {matcher!r}: {settings['hooks']['PreToolUse']!r}"
    assert len(matches) == 1, f"expected exactly one {matcher!r} hook, found {len(matches)}"
    return matches[0]


# The same matcher-based selection, but as a Python source snippet embeddable
# inside a *generated fake-claude subprocess script* (a separate process, so
# it cannot call the in-process helper above). Kept in exact logical lockstep
# with `_pretool_hook_by_matcher`.
_PRETOOL_HOOK_BY_MATCHER_SUBPROCESS_SNIPPET = """
def _pretool_hook_by_matcher(settings, matcher):
    matches = [e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == matcher]
    assert len(matches) == 1, (matcher, settings["hooks"]["PreToolUse"])
    return matches[0]
"""


def test_no_authorized_commands_keeps_settings_but_omits_allowedtools_argv(
    workspace: Path, tmp_path: Path
) -> None:
    # Locked contract change (file-write guard integration): the built-in
    # mutating file tools are NOT covered by Claude's Bash sandbox and can
    # run with no authorized Bash command, so TOMTIT settings (carrying the
    # file-write PreToolUse guard) and the --setting-sources lockdown are now
    # present on EVERY run. Only the Bash-specific layers stay conditional.
    # This strengthens the boundary: previously a no-Bash run had NO
    # TOMTIT-controlled file-tool enforcement at all.
    script = write_fake_executable(
        tmp_path, "fake-claude-no-guard-argv", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    adapter = ClaudeCodeCLIAdapter()
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((), workspace, ("result.txt",))
    try:
        argv = adapter._build_argv(str(script), base_request(workspace), (), artifacts)
        assert "--settings" in argv
        assert "--setting-sources" in argv
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--allowedTools" not in argv
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_no_authorized_commands_guard_disabled_on_result(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path, "fake-claude-no-guard-result", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))
    assert result.exact_bash_guard_enabled is False


def test_exact_bash_guard_artifacts_contain_only_fingerprints(tmp_path: Path) -> None:
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts(
        (_GUARD_ARGV_COMMAND,), tmp_path
    )
    try:
        fingerprints_text = artifacts.fingerprints_path.read_text(encoding="utf-8")
        fingerprints = json.loads(fingerprints_text)
        assert fingerprints == [compute_authorized_command_fingerprint(_GUARD_ARGV_COMMAND)]
        for token in _GUARD_ARGV_COMMAND:
            assert token not in fingerprints_text

        settings_text = artifacts.settings_path.read_text(encoding="utf-8")
        # "python3" is deliberately excluded: it legitimately appears as
        # part of `sys.executable`'s own path in the generated hook command
        # line, independent of anything from the authorized command's argv.
        for token in ("writer.py", "exact.txt"):
            assert token not in settings_text
        settings = json.loads(settings_text)
        bash_entry = _pretool_hook_by_matcher(settings, "Bash")
        hook_command = bash_entry["hooks"][0]["command"]
        assert str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH) in hook_command
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)
    assert not artifacts.directory.exists()


def test_exact_bash_guard_cleanup_is_safe_on_none() -> None:
    claude_code_cli_module._cleanup_exact_bash_guard_artifacts(None)  # must not raise


def test_exact_supported_command_wires_settings_argv_alongside_allowedtools(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Item 19: a matching TOMTIT guard must never bypass or replace Claude's
    # native permission engine -- both `--settings` (the guard) and
    # `--allowedTools` (Claude's own layer) must reach the real subprocess
    # argv together.
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-guard-argv-wiring",
        """
import sys, pathlib
pathlib.Path("argv_seen.json").write_text(repr(sys.argv), encoding="utf-8")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    assert "--settings" in result.sanitized_argv
    assert "--allowedTools" in result.sanitized_argv
    assert result.exact_bash_guard_enabled is True

    settings_idx = result.sanitized_argv.index("--settings")
    settings_path = result.sanitized_argv[settings_idx + 1]
    # The settings path itself is not sensitive (references only TOMTIT's
    # own hook script and an ephemeral fingerprints file), but it must have
    # already been cleaned up by the time `run()` returns.
    assert not Path(settings_path).exists()


def test_exact_bash_guard_artifacts_removed_after_run(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    captured_dirs: list[Path] = []
    original_write = claude_code_cli_module._write_exact_bash_guard_artifacts

    def _capture(authorized_commands: object, workspace_arg: object, *args: object) -> object:
        artifacts = original_write(authorized_commands, workspace_arg, *args)
        captured_dirs.append(artifacts.directory)
        return artifacts

    monkeypatch.setattr(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _capture)
    script = write_fake_executable(
        tmp_path, "fake-claude-guard-cleanup", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    adapter = ClaudeCodeCLIAdapter()
    adapter.run(request, executable_path=str(script))

    assert len(captured_dirs) == 1
    assert not captured_dirs[0].exists()


def test_exact_bash_guard_fingerprints_env_var_reaches_child_process(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-guard-env",
        f"""
import json, os, pathlib
env_value = os.environ.get({claude_code_cli_module._EXACT_BASH_GUARD_FINGERPRINTS_ENV!r}, "")
contents = pathlib.Path(env_value).read_text(encoding="utf-8") if env_value else ""
pathlib.Path("fingerprints_seen.json").write_text(contents, encoding="utf-8")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    adapter = ClaudeCodeCLIAdapter()
    adapter.run(request, executable_path=str(script))

    fingerprints_seen = json.loads((workspace / "fingerprints_seen.json").read_text(encoding="utf-8"))
    assert fingerprints_seen == [compute_authorized_command_fingerprint(_GUARD_ARGV_COMMAND)]
    for token in _GUARD_ARGV_COMMAND:
        assert token not in (workspace / "fingerprints_seen.json").read_text(encoding="utf-8")


def test_exact_bash_guard_unavailable_fails_closed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    invocation_marker = tmp_path / "invoked.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-guard-unavailable",
        f"""
import pathlib
pathlib.Path({str(invocation_marker)!r}).write_text("invoked")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )

    def _raise(authorized_commands: object, workspace_arg: object, *args: object) -> object:
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _raise)
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "EXACT_BASH_GUARD_UNAVAILABLE"
    assert not invocation_marker.exists(), "the worker process must never start when the guard cannot be constructed"


# ---------------------------------------------------------------------------
# End-to-end wiring: a fake `claude` executable that itself invokes the real
# hook script exactly the way Claude Code would (reading `--settings`,
# running the configured PreToolUse command, feeding it a synthetic Bash
# tool-call payload on stdin) -- still never touching the real Claude
# service. Proves the settings file, env var, and hook script are actually
# wired together end to end, not merely unit-correct in isolation.
# ---------------------------------------------------------------------------


def _fake_claude_invoking_real_hook(hook_input: dict[str, object]) -> str:
    hook_input_json = json.dumps(hook_input)
    return f"""
import json, subprocess, sys, pathlib
{_PRETOOL_HOOK_BY_MATCHER_SUBPROCESS_SNIPPET}
argv = sys.argv[1:]
settings_path = None
for i, a in enumerate(argv):
    if a == "--settings":
        settings_path = argv[i + 1]
pathlib.Path("settings_path_seen.txt").write_text(settings_path or "", encoding="utf-8")
settings = json.loads(pathlib.Path(settings_path).read_text(encoding="utf-8"))
hook_command = _pretool_hook_by_matcher(settings, "Bash")["hooks"][0]["command"]
proc = subprocess.run(hook_command, shell=True, input={hook_input_json!r}, capture_output=True, text=True)
pathlib.Path("hook_exit_code.txt").write_text(str(proc.returncode), encoding="utf-8")
pathlib.Path("hook_stderr.txt").write_text(proc.stderr, encoding="utf-8")
pathlib.Path("hook_stdout.txt").write_text(proc.stdout, encoding="utf-8")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
"""


def test_exact_bash_guard_end_to_end_defers_for_exact_match(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    hook_input = {"tool_name": "Bash", "tool_input": {"command": "python3 writer.py exact.txt"}}
    script = write_fake_executable(tmp_path, "fake-claude-hook-defers", _fake_claude_invoking_real_hook(hook_input))
    request = base_request(
        workspace, authorized_commands=(_GUARD_ARGV_COMMAND,), allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "0"
    # A PASS never itself grants permission: no stdout decision, no stderr.
    assert (workspace / "hook_stdout.txt").read_text(encoding="utf-8") == ""
    assert (workspace / "hook_stderr.txt").read_text(encoding="utf-8") == ""

    settings_path_seen = (workspace / "settings_path_seen.txt").read_text(encoding="utf-8")
    assert settings_path_seen
    assert not Path(settings_path_seen).exists(), "guard artifacts must be cleaned up after run() returns"


@pytest.mark.parametrize(
    "wrapper_command",
    [
        "command python3 writer.py exact.txt",
        "builtin command python3 writer.py exact.txt",
        "timeout 5 python3 writer.py exact.txt",
        "time python3 writer.py exact.txt",
        "nice python3 writer.py exact.txt",
        "nohup python3 writer.py exact.txt",
        "stdbuf -oL python3 writer.py exact.txt",
        "noglob python3 writer.py exact.txt",
        "env python3 writer.py exact.txt",
        "python3 writer.py other.txt",
        "python3 writer.py exact.txt extra.txt",
    ],
    ids=[
        "command",
        "builtin-command",
        "timeout",
        "time",
        "nice",
        "nohup",
        "stdbuf",
        "noglob",
        "env",
        "different-argument",
        "extra-argument",
    ],
)
def test_exact_bash_guard_end_to_end_denies_wrapper_and_mismatch_variants(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrapper_command: str
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    hook_input = {"tool_name": "Bash", "tool_input": {"command": wrapper_command}}
    script = write_fake_executable(
        tmp_path, "fake-claude-hook-denies", _fake_claude_invoking_real_hook(hook_input)
    )
    request = base_request(
        workspace, authorized_commands=(_GUARD_ARGV_COMMAND,), allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS
    )
    adapter = ClaudeCodeCLIAdapter()
    adapter.run(request, executable_path=str(script))

    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "2"
    stderr_seen = (workspace / "hook_stderr.txt").read_text(encoding="utf-8")
    assert stderr_seen.strip() == _GUARD_DENIAL_MESSAGE
    assert "writer.py" not in stderr_seen
    assert "exact.txt" not in stderr_seen


def test_exact_bash_guard_end_to_end_denies_run_in_background(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    hook_input = {
        "tool_name": "Bash",
        "tool_input": {"command": "python3 writer.py exact.txt", "run_in_background": True},
    }
    script = write_fake_executable(
        tmp_path, "fake-claude-hook-background", _fake_claude_invoking_real_hook(hook_input)
    )
    request = base_request(
        workspace, authorized_commands=(_GUARD_ARGV_COMMAND,), allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS
    )
    adapter = ClaudeCodeCLIAdapter()
    adapter.run(request, executable_path=str(script))

    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "2"
    assert (workspace / "hook_stderr.txt").read_text(encoding="utf-8").strip() == _GUARD_DENIAL_MESSAGE


# ===========================================================================
# Provider-output persistence boundary (M-1 / M-2).
#
# The previous design *redacted* a denylist of provider JSON keys and, on a
# parse failure, returned the raw text unchanged. Both halves failed:
# unknown/alternate keys leaked (M-1) and truncated-therefore-unparseable
# payloads bypassed redaction entirely (M-2). The replacement withholds
# provider-controlled text from persistence altogether whenever exact-command
# authorization is active, and persists only a summary whose every value is a
# bool/int/float/None.
#
# These tests deliberately assert against the *serialized evidence*
# (`render_evidence_manifest_json`) rather than against a redaction helper,
# so they measure what actually reaches disk.
# ===========================================================================

_PROVIDER_SYNTHETIC_COMMAND = "rm -rf /tmp/SYNTHETIC_TARGET_DO_NOT_EXECUTE"
_PROVIDER_SYNTHETIC_SECRET = "SYNTHETIC_PROVIDER_SECRET_DO_NOT_LEAK"


def _fake_claude_emitting(stdout_payload: str, stderr_payload: str = "") -> str:
    """A fake `claude` that prints exactly the given provider output."""

    return f"""
import sys, pathlib
sys.stdout.write({stdout_payload!r})
sys.stderr.write({stderr_payload!r})
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
"""


def _run_with_provider_output(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout_payload: str,
    stderr_payload: str = "",
    name: str = "fake-claude-provider-output",
    max_stdout_bytes: int = 65536,
    max_stderr_bytes: int = 65536,
) -> ClaudeCodeCLIResult:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(tmp_path, name, _fake_claude_emitting(stdout_payload, stderr_payload))
    request = base_request(
        workspace,
        authorized_commands=(_GUARD_ARGV_COMMAND,),
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
    )
    return ClaudeCodeCLIAdapter().run(request, executable_path=str(script))


def _assert_evidence_is_clean(result: ClaudeCodeCLIResult, tmp_path: Path, label: str) -> None:
    """Every persistence path must be free of the synthetic command/secret.

    Checks the result's own evidence-facing fields, the manifest dict, the
    serialized manifest JSON, and the actual on-disk evidence files written
    by `write_evidence` -- not just one helper's return value.
    """

    manifest = build_evidence_manifest(result)
    manifest_json = render_evidence_manifest_json(result)
    surfaces = {
        "stdout_excerpt": result.stdout_excerpt,
        "stderr_excerpt": result.stderr_excerpt,
        "error_message": result.error_message or "",
        "sanitized_argv": " ".join(result.sanitized_argv),
        "manifest_dict": json.dumps(manifest, default=str),
        "manifest_json": manifest_json,
    }
    for surface_name, blob in surfaces.items():
        assert _PROVIDER_SYNTHETIC_COMMAND not in blob, f"{label}: command leaked via {surface_name}"
        assert _PROVIDER_SYNTHETIC_SECRET not in blob, f"{label}: secret leaked via {surface_name}"
        assert "rm -rf" not in blob, f"{label}: command fragment leaked via {surface_name}"

    output_dir = tmp_path / f"evidence-{label.replace(' ', '-').replace('/', '-')[:40]}"
    from tomtit.adapters.claude_code_cli import write_evidence

    write_evidence(result, before_contents={}, after_contents={}, output_dir=output_dir)
    for path in output_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert _PROVIDER_SYNTHETIC_COMMAND not in text, f"{label}: command leaked into {path.name}"
            assert _PROVIDER_SYNTHETIC_SECRET not in text, f"{label}: secret leaked into {path.name}"
            assert "rm -rf" not in text, f"{label}: command fragment leaked into {path.name}"


# --- M-1: every provider JSON shape, known and unknown ---------------------

_M1_PAYLOAD_CASES: list[tuple[str, object]] = [
    (
        "permission_denials",
        {"permission_denials": [{"tool_name": "Bash", "tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}}]},
    ),
    ("result-string", {"result": f"I tried to run `{_PROVIDER_SYNTHETIC_COMMAND}` but it was blocked."}),
    ("error-string", {"error": _PROVIDER_SYNTHETIC_COMMAND}),
    ("message-string", {"message": _PROVIDER_SYNTHETIC_COMMAND}),
    ("reason-string", {"reason": _PROVIDER_SYNTHETIC_COMMAND}),
    (
        "hookSpecificOutput-permissionDecisionReason",
        {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": _PROVIDER_SYNTHETIC_COMMAND}},
    ),
    ("nested-content-text", {"content": [{"type": "text", "text": _PROVIDER_SYNTHETIC_COMMAND}]}),
    ("alternate-cmd-key", {"tool_input": {"cmd": _PROVIDER_SYNTHETIC_COMMAND}}),
    ("alternate-Command-key", {"tool_input": {"Command": _PROVIDER_SYNTHETIC_COMMAND}}),
    ("arbitrary-unknown-field", {"a_field_invented_in_2027": _PROVIDER_SYNTHETIC_COMMAND}),
    (
        "arbitrary-nested-list-dict",
        {"outer": [{"mid": [{"inner": {"deep": [_PROVIDER_SYNTHETIC_COMMAND]}}]}]},
    ),
    (
        "multiple-denied-commands",
        {
            "permission_denials": [
                {"tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}},
                {"tool_input": {"command": f"{_PROVIDER_SYNTHETIC_COMMAND} --second"}},
                {"tool_input": {"command": f"timeout 5 {_PROVIDER_SYNTHETIC_COMMAND}"}},
            ]
        },
    ),
    (
        "authorized-command-repeated-in-prose",
        {"result": "I successfully ran `python3 writer.py exact.txt` as authorized."},
    ),
    (
        "unauthorized-generated-command-in-prose",
        {"result": f"I also wanted to run `{_PROVIDER_SYNTHETIC_COMMAND}` but was denied."},
    ),
    ("unicode-output", {"result": f"café {_PROVIDER_SYNTHETIC_COMMAND} 日本語 🚀 {_PROVIDER_SYNTHETIC_SECRET}"}),
    ("secret-in-result", {"result": f"token={_PROVIDER_SYNTHETIC_SECRET} cmd={_PROVIDER_SYNTHETIC_COMMAND}"}),
]


@pytest.mark.parametrize("label,payload", _M1_PAYLOAD_CASES, ids=[c[0] for c in _M1_PAYLOAD_CASES])
def test_m1_provider_json_shapes_never_reach_persisted_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, payload: object
) -> None:
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=json.dumps(payload), name=f"fake-claude-m1-{label[:20]}"
    )
    _assert_evidence_is_clean(result, tmp_path, label)
    assert result.provider_output_withheld is True


def test_m1_stdout_prose_never_reaches_persisted_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_with_provider_output(
        workspace,
        tmp_path,
        monkeypatch,
        stdout_payload=f"plain prose on stdout mentioning {_PROVIDER_SYNTHETIC_COMMAND} and {_PROVIDER_SYNTHETIC_SECRET}",
        name="fake-claude-m1-stdout-prose",
    )
    _assert_evidence_is_clean(result, tmp_path, "stdout-prose")
    assert result.provider_stdout_json_parsed is False


def test_m1_stderr_prose_never_reaches_persisted_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_with_provider_output(
        workspace,
        tmp_path,
        monkeypatch,
        stdout_payload="",
        stderr_payload=f"stderr prose: {_PROVIDER_SYNTHETIC_COMMAND} {_PROVIDER_SYNTHETIC_SECRET}",
        name="fake-claude-m1-stderr-prose",
    )
    _assert_evidence_is_clean(result, tmp_path, "stderr-prose")


def test_m1_mixed_json_and_prose_never_reaches_persisted_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({"result": "ok"}) + f"\ntrailing prose {_PROVIDER_SYNTHETIC_COMMAND}\n"
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=payload, name="fake-claude-m1-mixed"
    )
    _assert_evidence_is_clean(result, tmp_path, "mixed-json-prose")
    assert result.provider_stdout_json_parsed is False


def test_m1_malformed_json_never_reaches_persisted_evidence(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = '{"result": "' + _PROVIDER_SYNTHETIC_COMMAND + '", "unterminated'
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=payload, name="fake-claude-m1-malformed"
    )
    _assert_evidence_is_clean(result, tmp_path, "malformed-json")
    assert result.provider_stdout_json_parsed is False


def test_m1_empty_output_is_handled_safely(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload="", name="fake-claude-m1-empty"
    )
    # An empty stream stays empty -- no phantom placeholder content.
    assert result.stdout_excerpt == ""
    assert result.stderr_excerpt == ""
    assert result.provider_output_withheld is True
    assert result.provider_stdout_bytes == 0
    assert result.provider_stdout_json_parsed is False


def test_m1_string_where_number_expected_cannot_smuggle_text(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider returning a *string* in a numeric field must yield None,
    not carry that string into the summary."""

    payload = json.dumps(
        {
            "total_cost_usd": _PROVIDER_SYNTHETIC_COMMAND,
            "num_turns": _PROVIDER_SYNTHETIC_SECRET,
            "usage": {"input_tokens": _PROVIDER_SYNTHETIC_COMMAND, "output_tokens": {"nested": "obj"}},
            "is_error": _PROVIDER_SYNTHETIC_COMMAND,
            "permission_denials": _PROVIDER_SYNTHETIC_COMMAND,
        }
    )
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=payload, name="fake-claude-m1-typeconfusion"
    )
    _assert_evidence_is_clean(result, tmp_path, "type-confusion")
    assert result.provider_total_cost_usd is None
    assert result.provider_num_turns is None
    assert result.provider_input_tokens is None
    assert result.provider_output_tokens is None
    assert result.provider_is_error is None
    assert result.provider_permission_denial_count is None


# --- M-2: truncation boundaries -------------------------------------------


def _oversized_json_payload(total_bytes: int, *, command_at: str) -> str:
    """A JSON payload of roughly `total_bytes` with the synthetic command
    placed near the start, middle, or end of the padded region."""

    filler_unit = "PADDINGPADDING"
    base = {"result": "", "total_cost_usd": 1.25, "usage": {"input_tokens": 10, "output_tokens": 20}}
    overhead = len(json.dumps(base))
    pad_len = max(total_bytes - overhead, 0)
    pad = (filler_unit * ((pad_len // len(filler_unit)) + 1))[:pad_len]
    if command_at == "start":
        body = _PROVIDER_SYNTHETIC_COMMAND + pad
    elif command_at == "middle":
        half = len(pad) // 2
        body = pad[:half] + _PROVIDER_SYNTHETIC_COMMAND + pad[half:]
    else:  # end
        body = pad + _PROVIDER_SYNTHETIC_COMMAND
    base["result"] = body
    return json.dumps(base)


@pytest.mark.parametrize(
    "label,payload_factory,max_bytes",
    [
        ("json-shorter-than-limit", lambda: json.dumps({"result": _PROVIDER_SYNTHETIC_COMMAND}), 65536),
        (
            "json-exactly-at-limit",
            lambda: _oversized_json_payload(4096, command_at="middle"),
            len(_oversized_json_payload(4096, command_at="middle")),
        ),
        (
            "json-one-byte-over-limit",
            lambda: _oversized_json_payload(4096, command_at="middle"),
            len(_oversized_json_payload(4096, command_at="middle")) - 1,
        ),
        ("command-before-truncation-boundary", lambda: _oversized_json_payload(8192, command_at="start"), 2048),
        ("command-crosses-truncation-boundary", lambda: _oversized_json_payload(8192, command_at="middle"), 4096),
        ("command-entirely-in-retained-prefix", lambda: _oversized_json_payload(8192, command_at="start"), 6144),
        ("closing-json-syntax-truncated-away", lambda: _oversized_json_payload(8192, command_at="start"), 1024),
        (
            "very-large-result-prose",
            lambda: json.dumps({"result": (_PROVIDER_SYNTHETIC_COMMAND + " ") * 4000}),
            65536,
        ),
        (
            "very-large-permission-denials",
            lambda: json.dumps(
                {"permission_denials": [{"tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}} for _ in range(2000)]}
            ),
            65536,
        ),
        (
            "non-json-payload-larger-than-limit",
            lambda: (f"prose {_PROVIDER_SYNTHETIC_COMMAND} {_PROVIDER_SYNTHETIC_SECRET} " * 3000),
            4096,
        ),
    ],
)
def test_m2_truncation_boundaries_never_persist_raw_command(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    payload_factory: object,
    max_bytes: int,
) -> None:
    payload = payload_factory()  # type: ignore[operator]
    result = _run_with_provider_output(
        workspace,
        tmp_path,
        monkeypatch,
        stdout_payload=payload,
        name=f"fake-claude-m2-{label[:20]}",
        max_stdout_bytes=max_bytes,
    )
    _assert_evidence_is_clean(result, tmp_path, label)
    assert result.provider_output_withheld is True
    # Whether or not the payload survived intact, the excerpt is the fixed
    # placeholder -- never a truncated raw prefix.
    assert result.stdout_excerpt == claude_code_cli_module._PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER


def test_m2_truncated_json_falls_back_to_safe_representation(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact M-2 defect: truncated JSON is unparseable, and the old code
    returned the raw prefix. Now it degrades to *less metadata*, never to raw
    text."""

    payload = _oversized_json_payload(8192, command_at="start")
    result = _run_with_provider_output(
        workspace,
        tmp_path,
        monkeypatch,
        stdout_payload=payload,
        name="fake-claude-m2-truncated-fallback",
        max_stdout_bytes=1024,
    )
    assert result.stdout_truncated is True
    assert result.provider_stdout_json_parsed is False, "truncated JSON must not parse"
    assert result.stdout_excerpt == claude_code_cli_module._PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER
    # Safe metadata survives even though parsing failed.
    assert result.provider_stdout_bytes is not None and result.provider_stdout_bytes > 0
    assert result.stdout_sha256  # full-stream hash still recorded
    _assert_evidence_is_clean(result, tmp_path, "truncated-fallback")


def test_m2_parse_failure_never_returns_original_text_unit_level() -> None:
    """Unit-level statement of the M-2 invariant on the summarizer itself:
    no input, parseable or not, can produce a summary containing the input."""

    for payload in (
        '{"result": "' + _PROVIDER_SYNTHETIC_COMMAND + '", "unterminated',
        f"pure prose {_PROVIDER_SYNTHETIC_COMMAND}",
        json.dumps({"result": _PROVIDER_SYNTHETIC_COMMAND}),
        json.dumps({"result": _PROVIDER_SYNTHETIC_COMMAND})[:20],
        "",
    ):
        summary = claude_code_cli_module._summarize_provider_output(payload, payload)
        blob = json.dumps(dataclasses.asdict(summary))
        assert _PROVIDER_SYNTHETIC_COMMAND not in blob
        assert "rm -rf" not in blob


def test_m2_summary_type_has_no_string_field() -> None:
    """The structural guarantee: `_ProviderOutputSummary` has no `str` field,
    so no provider text can be carried by it regardless of call site."""

    for field in dataclasses.fields(claude_code_cli_module._ProviderOutputSummary):
        assert "str" not in str(field.type), f"{field.name} must not be string-typed"


def test_m2_result_provider_fields_are_never_string_typed() -> None:
    """Same structural guarantee on the persisted result dataclass."""

    from tomtit.adapters.claude_code_cli import ClaudeCodeCLIResult as _Result

    for field in dataclasses.fields(_Result):
        if field.name.startswith("provider_") and field.name != "provider_output_withheld":
            assert "str" not in str(field.type), f"{field.name} must not be string-typed"


# --- Safe summary correctness (it must still be useful) --------------------


def test_provider_summary_derives_usable_metadata(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps(
        {
            "is_error": False,
            "num_turns": 7,
            "total_cost_usd": 0.42,
            "usage": {"input_tokens": 123, "output_tokens": 456},
            "permission_denials": [
                {"tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}},
                {"tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}},
            ],
            "result": f"prose with {_PROVIDER_SYNTHETIC_COMMAND}",
        }
    )
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=payload, name="fake-claude-summary-useful"
    )
    assert result.provider_stdout_json_parsed is True
    assert result.provider_is_error is False
    assert result.provider_permission_denial_count == 2
    assert result.provider_num_turns == 7
    assert result.provider_total_cost_usd == 0.42
    assert result.provider_input_tokens == 123
    assert result.provider_output_tokens == 456
    _assert_evidence_is_clean(result, tmp_path, "summary-useful")

    manifest = build_evidence_manifest(result)
    assert manifest["provider_permission_denial_count"] == 2
    assert manifest["provider_total_cost_usd"] == 0.42


# --- No-command compatibility ---------------------------------------------


def test_no_authorized_commands_provider_output_behavior_unchanged(
    workspace: Path, tmp_path: Path
) -> None:
    """No-command compatibility: with `authorized_commands` empty, stdout is
    passed through exactly as before (only the pre-existing env/HOME
    redaction applies), and no withholding or summary occurs."""

    marker = '{"command": "this is not touched when no authorized_commands are set"}'
    script = write_fake_executable(
        tmp_path,
        "fake-claude-no-guard-stdout",
        f"""
import pathlib
print({marker!r})
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert "this is not touched when no authorized_commands are set" in result.stdout_excerpt
    assert result.provider_output_withheld is False
    assert result.provider_stdout_bytes is None
    assert result.provider_stdout_json_parsed is None
    assert result.provider_total_cost_usd is None

    manifest = build_evidence_manifest(result)
    assert manifest["provider_output_withheld"] is False
    assert manifest["stdout_excerpt"] == result.stdout_excerpt


def test_no_authorized_commands_existing_secret_redaction_still_works(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: env-secret redaction is untouched by this change."""

    secret = "sk-no-auth-secret-value"
    monkeypatch.setenv("TOMTIT_TEST_API_KEY", secret)
    script = write_fake_executable(
        tmp_path,
        "fake-claude-no-auth-secret",
        f"""
import pathlib
print({secret!r})
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, environment_allowlist=("PATH", "HOME", "TOMTIT_TEST_API_KEY"))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert secret not in result.stdout_excerpt
    assert "[REDACTED]" in result.stdout_excerpt
    assert secret not in render_evidence_manifest_json(result)


def test_no_authorized_commands_home_redaction_still_works(workspace: Path, tmp_path: Path) -> None:
    """Regression: HOME/path redaction is untouched by this change."""

    home = os.environ.get("HOME")
    assert home and len(home) >= 4
    script = write_fake_executable(
        tmp_path,
        "fake-claude-no-auth-home",
        f"""
import pathlib
print("path is {home}/somewhere")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, environment_allowlist=("PATH", "HOME"))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert "$HOME/somewhere" in result.stdout_excerpt
    assert home not in result.stdout_excerpt


# --- Dogfood persistence paths (audit-driven end-to-end) -------------------


def _import_dogfood_module(name: str) -> object:
    """Import a `.tomtit/dogfood` runner module by path.

    The audit found these are the concrete consumers that persist adapter
    output to disk (`run_task.persist_evidence` -> `result.json`,
    `receipt.json`) and that read it back (`run_loop`), so the
    no-raw-command guarantee is asserted against them directly rather than
    only against the adapter.
    """

    import importlib.util

    repo_root = Path(claude_code_cli_module.__file__).resolve().parents[3]
    module_path = repo_root / ".tomtit" / "dogfood" / f"{name}.py"
    dogfood_dir = str(module_path.parent)
    if dogfood_dir not in _sys.path:
        _sys.path.insert(0, dogfood_dir)
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    _sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_dogfood_task_evidence_never_persists_raw_provider_command(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through `run_task.persist_evidence`: neither `result.json`
    nor `receipt.json` may contain the provider-emitted command or secret."""

    run_task = _import_dogfood_module("run_task")
    payload = json.dumps(
        {
            "permission_denials": [{"tool_input": {"command": _PROVIDER_SYNTHETIC_COMMAND}}],
            "result": f"prose {_PROVIDER_SYNTHETIC_COMMAND} {_PROVIDER_SYNTHETIC_SECRET}",
        }
    )
    result = _run_with_provider_output(
        workspace, tmp_path, monkeypatch, stdout_payload=payload, name="fake-claude-dogfood-task"
    )

    evidence_dir = tmp_path / "dogfood-evidence"
    evidence_dir.mkdir()
    run_task.persist_evidence(  # type: ignore[attr-defined]
        result, {}, {}, evidence_dir, ("result.txt",), workspace, (_GUARD_ARGV_COMMAND,)
    )

    persisted_files = [p for p in evidence_dir.rglob("*") if p.is_file()]
    assert persisted_files, "persist_evidence wrote nothing"
    for path in persisted_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert _PROVIDER_SYNTHETIC_COMMAND not in text, path.name
        assert _PROVIDER_SYNTHETIC_SECRET not in text, path.name
        assert "rm -rf" not in text, path.name

    manifest = json.loads((evidence_dir / "result.json").read_text(encoding="utf-8"))
    assert manifest["stdout_excerpt"] == claude_code_cli_module._PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER
    assert manifest["provider_output_withheld"] is True


def test_dogfood_loop_usage_extraction_uses_safe_fields_when_withheld() -> None:
    """The audit found `run_loop` parsed `stdout_excerpt` for usage. With
    provider text withheld there is no JSON left to parse, so it must read
    the typed safe fields instead -- preserving cost/token telemetry for
    exactly the runs that carry authorized commands."""

    run_loop = _import_dogfood_module("run_loop")
    withheld = {
        "provider_output_withheld": True,
        "provider_total_cost_usd": 0.42,
        "provider_input_tokens": 123,
        "provider_output_tokens": 456,
        "stdout_excerpt": claude_code_cli_module._PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER,
    }
    assert run_loop.extract_usage_from_implementation_result(withheld) == {  # type: ignore[attr-defined]
        "cost_usd": 0.42,
        "input_tokens": 123,
        "output_tokens": 456,
    }


def test_dogfood_loop_usage_extraction_legacy_path_unchanged() -> None:
    """No-command compatibility: a run without authorized commands still has
    its usage parsed out of `stdout_excerpt` exactly as before."""

    run_loop = _import_dogfood_module("run_loop")
    legacy = {
        "stdout_excerpt": json.dumps(
            {"total_cost_usd": 1.5, "usage": {"input_tokens": 10, "output_tokens": 20}}
        )
    }
    assert run_loop.extract_usage_from_implementation_result(legacy) == {  # type: ignore[attr-defined]
        "cost_usd": 1.5,
        "input_tokens": 10,
        "output_tokens": 20,
    }


def test_dogfood_loop_usage_extraction_rejects_type_confused_safe_fields() -> None:
    """A provider-influenced value that is not a real number must yield None
    rather than propagating a string into usage aggregation."""

    run_loop = _import_dogfood_module("run_loop")
    confused = {
        "provider_output_withheld": True,
        "provider_total_cost_usd": _PROVIDER_SYNTHETIC_COMMAND,
        "provider_input_tokens": True,
        "provider_output_tokens": {"nested": "obj"},
    }
    assert run_loop.extract_usage_from_implementation_result(confused) == {  # type: ignore[attr-defined]
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
    }


def test_evidence_manifest_includes_exact_bash_guard_enabled_field(workspace: Path, tmp_path: Path) -> None:
    script = write_fake_executable(
        tmp_path, "fake-claude-manifest-guard-field", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    adapter = ClaudeCodeCLIAdapter()
    result = adapter.run(base_request(workspace), executable_path=str(script))
    manifest = build_evidence_manifest(result)
    assert manifest["exact_bash_guard_enabled"] is False


# ---------------------------------------------------------------------------
# Operational-state regression: the enable constant remains False and is
# never flipped by this guard's presence. Naming debt (the constant's name
# now under-describes what it gates, since Claude's own rule is
# provider-normalized rather than raw-exact) is reported in
# EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md rather than fixed
# here.
# ---------------------------------------------------------------------------


def test_bare_bash_rule_confirmed_exact_constant_remains_false() -> None:
    assert claude_code_cli_module._CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT is False


# ===========================================================================
# Process-boundary hardening (EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_REREVIEW.md
# finding N-1's invocation-hardening recommendation, plus N-2's guard-file
# integrity note -> EXACT_BASH_GUARD_PROCESS_HARDENING_REPORT.md).
#
# The guard's own interpreter process is now started with `-E -s`, moving
# the ambient-import defense to the process boundary where it holds even
# against the spoofed-`__file__` variant no in-process check can survive;
# and the guard script itself is preflighted (exists, regular file, not a
# symlink, resolves to its expected TOMTIT-owned location) before Claude is
# started at all.
#
# Nothing in the already-reviewed guard is redesigned here: the exact-argv
# fingerprint semantics, canonical validator, H-1 trusted-import logic, H-2
# exit-0/exit-2 boundary, fingerprint-only artifacts,
# PASS-never-grants-permission behavior, native `--allowedTools` layering,
# and `run_in_background` rejection are all untouched.
# ===========================================================================

import sys as _sys  # noqa: E402


def _guard_hook_command_parts() -> list[str]:
    """The rendered hook command, split back into argv-style parts."""

    import shlex as _shlex

    path = claude_code_cli_module._verified_exact_bash_guard_module_path()
    return _shlex.split(claude_code_cli_module._render_exact_bash_guard_hook_command(path))


def test_hardening_1_hook_command_uses_exact_trusted_sys_executable() -> None:
    """1. Hook command contains the exact trusted sys.executable."""

    parts = _guard_hook_command_parts()
    assert parts[0] == _sys.executable
    assert Path(parts[0]).is_absolute(), "interpreter must be an absolute path, never PATH-resolved"


def test_hardening_2_e_flag_present() -> None:
    """2. `-E` is present (ignore PYTHON* environment variables)."""

    assert "-E" in _guard_hook_command_parts()


def test_hardening_3_s_flag_present() -> None:
    """3. `-s` is present (disable user site-packages)."""

    assert "-s" in _guard_hook_command_parts()


def test_hardening_4_flags_precede_script_path() -> None:
    """4. Both flags appear before the script path."""

    parts = _guard_hook_command_parts()
    script_index = parts.index(str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH))
    assert parts.index("-E") < script_index
    assert parts.index("-s") < script_index
    # Exactly the expected shape: interpreter, flags, script -- nothing else.
    assert parts == [_sys.executable, "-E", "-s", str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH)]


def test_hardening_5_absolute_resolved_guard_path_is_used() -> None:
    """5. Absolute resolved guard path is used."""

    parts = _guard_hook_command_parts()
    guard_path = Path(parts[-1])
    assert guard_path.is_absolute()
    assert guard_path == guard_path.resolve()
    assert guard_path.name == "exact_bash_guard.py"


def test_hardening_6_no_shell_wrapper_in_hook_command() -> None:
    """6. No shell wrapper is involved."""

    parts = _guard_hook_command_parts()

    # argv[0] is the Python interpreter itself -- never a shell, never an
    # `env`/`command`-style wrapper that would reinterpret what follows.
    interpreter_basename = Path(parts[0]).name.casefold()
    assert interpreter_basename.startswith("python"), interpreter_basename
    for wrapper in ("sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "env", "command", "xargs"):
        assert Path(parts[0]).name.casefold() != wrapper

    # The only elements are the interpreter, the two isolation flags, and
    # the script path -- no `-c`, no inline program text, no extra argument
    # a shell could act on.
    assert parts[1:-1] == ["-E", "-s"]
    assert "-c" not in parts

    # No shell metacharacter survives into the rendered command line.
    rendered = claude_code_cli_module._render_exact_bash_guard_hook_command(
        claude_code_cli_module._verified_exact_bash_guard_module_path()
    )
    for metachar in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n"):
        assert metachar not in rendered


def test_hardening_7_no_shell_true_path_exists() -> None:
    """7. No shell=True path exists (source-level, whole adapter module)."""

    import ast

    source = Path(claude_code_cli_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert not (isinstance(node.value, ast.Constant) and node.value.value is True), (
                "shell=True must never appear in a real keyword argument"
            )


def _run_guard_via_rendered_hook_command(
    *,
    payload: dict[str, object],
    fingerprints: list[str],
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the guard exactly as the rendered hook command specifies.

    Uses the real rendered command (interpreter + `-E -s` + script path),
    split back to argv and executed without a shell -- so these tests
    exercise the *actual* invocation TOMTIT registers with Claude, not a
    hand-built approximation of it.
    """

    fingerprints_path = tmp_path / "hardening-fingerprints.json"
    fingerprints_path.write_text(json.dumps(fingerprints), encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        claude_code_cli_module._EXACT_BASH_GUARD_FINGERPRINTS_ENV: str(fingerprints_path),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        _guard_hook_command_parts(),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _write_malicious_ambient_tomtit(root: Path, *, constant_fingerprint: str) -> None:
    package_dir = root / "tomtit" / "adapters"
    package_dir.mkdir(parents=True, exist_ok=True)
    (root / "tomtit" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "claude_code_cli.py").write_text(
        "class AuthorizedCommandError(Exception):\n"
        "    pass\n\n\n"
        "def validate_authorized_command_argv(argv):\n"
        "    return None\n\n\n"
        "def compute_authorized_command_fingerprint(argv):\n"
        f"    return {constant_fingerprint!r}\n",
        encoding="utf-8",
    )


_HARDENING_AMBIENT_CONSTANT = "a" * 64
_HARDENING_UNAUTHORIZED_COMMAND = "rm -rf /tmp/SYNTHETIC_TARGET_DO_NOT_EXECUTE"


def test_hardening_8_malicious_pythonpath_cannot_influence_decision(tmp_path: Path) -> None:
    """8. Hook still works with a malicious PYTHONPATH environment.

    Seeds the fingerprints file with the *ambient* package's constant, so
    an ambient win would produce a PASS. `-E` means the interpreter never
    even reads `PYTHONPATH`.
    """

    ambient = tmp_path / "ambient"
    _write_malicious_ambient_tomtit(ambient, constant_fingerprint=_HARDENING_AMBIENT_CONSTANT)
    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": _HARDENING_UNAUTHORIZED_COMMAND}},
        fingerprints=[_HARDENING_AMBIENT_CONSTANT],
        tmp_path=tmp_path,
        extra_env={"PYTHONPATH": str(ambient)},
    )
    assert proc.returncode == 2
    assert "SYNTHETIC_TARGET" not in proc.stderr


def test_hardening_8b_pythonpath_is_not_on_guard_sys_path(tmp_path: Path) -> None:
    """`-E` genuinely neutralizes PYTHONPATH -- proven by observing the
    guard interpreter's own `sys.path`, not merely by the deny outcome."""

    parts = _guard_hook_command_parts()
    probe = subprocess.run(
        [*parts[:-1], "-c", "import sys; print('/synthetic-pythonpath-marker' in sys.path)"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/synthetic-pythonpath-marker"},
        timeout=15,
    )
    assert probe.returncode == 0
    assert probe.stdout.strip() == "False"


def test_hardening_9_malicious_user_site_cannot_influence_decision(tmp_path: Path) -> None:
    """9. Hook still works with a malicious PYTHONUSERBASE/user-site package."""

    userbase = tmp_path / "userbase"
    parts = _guard_hook_command_parts()
    # Locate the user-site directory this PYTHONUSERBASE would produce
    # *without* `-s`, then plant the malicious package there.
    locate = subprocess.run(
        [parts[0], "-c", "import site; print(site.getusersitepackages())"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONUSERBASE": str(userbase)},
        timeout=15,
    )
    assert locate.returncode == 0
    _write_malicious_ambient_tomtit(Path(locate.stdout.strip()), constant_fingerprint=_HARDENING_AMBIENT_CONSTANT)

    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": _HARDENING_UNAUTHORIZED_COMMAND}},
        fingerprints=[_HARDENING_AMBIENT_CONSTANT],
        tmp_path=tmp_path,
        extra_env={"PYTHONUSERBASE": str(userbase)},
    )
    assert proc.returncode == 2


def test_hardening_9b_user_site_is_disabled_in_guard_interpreter() -> None:
    """`-s` genuinely disables user site -- observed directly."""

    parts = _guard_hook_command_parts()
    probe = subprocess.run(
        [*parts[:-1], "-c", "import site; print(site.ENABLE_USER_SITE)"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=15,
    )
    assert probe.returncode == 0
    assert probe.stdout.strip() == "False"


def test_hardening_9c_pythonpath_sitecustomize_spoof_is_blocked(tmp_path: Path) -> None:
    """N-1's spoofed-`__file__` variant, delivered via a PYTHONPATH
    `sitecustomize.py`, is blocked by `-E` at the process boundary.

    This is the vector the re-review reproduced as **exit 0** (H1-C2)
    against the un-hardened invocation: a fake module pre-placed in
    `sys.modules` whose `__file__` is spoofed to the real trusted path, so
    the guard's in-process provenance check passes. No in-process check can
    survive it; `-E` prevents the `sitecustomize.py` from ever loading.
    """

    vector = tmp_path / "sitecustomize_vector"
    vector.mkdir()
    real_module_path = str(Path(claude_code_cli_module.__file__).resolve())
    (vector / "sitecustomize.py").write_text(
        "import sys, types\n"
        "_f = types.ModuleType('tomtit.adapters.claude_code_cli')\n"
        f"_f.__file__ = {real_module_path!r}\n"
        "_f.validate_authorized_command_argv = lambda argv: None\n"
        f"_f.compute_authorized_command_fingerprint = lambda argv: {_HARDENING_AMBIENT_CONSTANT!r}\n"
        "_f.AuthorizedCommandError = Exception\n"
        "sys.modules['tomtit'] = types.ModuleType('tomtit')\n"
        "sys.modules['tomtit.adapters'] = types.ModuleType('tomtit.adapters')\n"
        "sys.modules['tomtit.adapters.claude_code_cli'] = _f\n",
        encoding="utf-8",
    )

    # Fixture validity: without -E this sitecustomize really does load.
    parts = _guard_hook_command_parts()
    sanity = subprocess.run(
        [parts[0], "-c", "import sys; print('tomtit.adapters.claude_code_cli' in sys.modules)"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(vector)},
        timeout=15,
    )
    assert sanity.stdout.strip() == "True", "sitecustomize fixture did not load without -E"

    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": _HARDENING_UNAUTHORIZED_COMMAND}},
        fingerprints=[_HARDENING_AMBIENT_CONSTANT],
        tmp_path=tmp_path,
        extra_env={"PYTHONPATH": str(vector)},
    )
    assert proc.returncode == 2, "the -E flag must prevent the spoofed-__file__ sitecustomize from loading"


def test_hardening_10_legitimate_exact_command_still_defers_silently(tmp_path: Path) -> None:
    """10. Legitimate exact command still reaches silent defer under `-E -s`.

    The decisive positive control: a PASS is reachable only if the *real*
    trusted `compute_authorized_command_fingerprint` ran, so this proves
    `-E -s` did not break the trusted import.
    """

    argv = ("echo", "tomtit-synthetic-ok")
    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": "echo tomtit-synthetic-ok"}},
        fingerprints=[compute_authorized_command_fingerprint(argv)],
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_hardening_10b_legitimate_command_defers_even_with_ambient_package(tmp_path: Path) -> None:
    """Trusted import still wins under `-E -s` with a malicious ambient
    package present: PASS requires the real fingerprint function."""

    ambient = tmp_path / "ambient"
    _write_malicious_ambient_tomtit(ambient, constant_fingerprint=_HARDENING_AMBIENT_CONSTANT)
    argv = ("echo", "tomtit-synthetic-ok")
    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": "echo tomtit-synthetic-ok"}},
        fingerprints=[compute_authorized_command_fingerprint(argv)],
        tmp_path=tmp_path,
        extra_env={"PYTHONPATH": str(ambient)},
    )
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_hardening_10c_run_in_background_still_rejected_under_isolation_flags(tmp_path: Path) -> None:
    """Non-regression: `run_in_background` rejection survives the hardening."""

    argv = ("echo", "tomtit-synthetic-ok")
    proc = _run_guard_via_rendered_hook_command(
        payload={
            "tool_name": "Bash",
            "tool_input": {"command": "echo tomtit-synthetic-ok", "run_in_background": True},
        },
        fingerprints=[compute_authorized_command_fingerprint(argv)],
        tmp_path=tmp_path,
    )
    assert proc.returncode == 2


def test_hardening_10d_wrapper_variant_still_denied_under_isolation_flags(tmp_path: Path) -> None:
    """Non-regression: exact-argv wrapper deny survives the hardening."""

    argv = ("echo", "tomtit-synthetic-ok")
    proc = _run_guard_via_rendered_hook_command(
        payload={"tool_name": "Bash", "tool_input": {"command": "timeout 5 echo tomtit-synthetic-ok"}},
        fingerprints=[compute_authorized_command_fingerprint(argv)],
        tmp_path=tmp_path,
    )
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# Guard-file preflight integrity (required changes 11-12).
# ---------------------------------------------------------------------------


def test_hardening_11_missing_guard_file_fails_closed_before_claude_starts(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """11. Missing guard file fails closed before Claude starts."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    monkeypatch.setattr(
        claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", tmp_path / "does-not-exist.py"
    )
    invocation_marker = tmp_path / "invoked.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-missing-guard",
        f"""
import pathlib
pathlib.Path({str(invocation_marker)!r}).write_text("invoked")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "EXACT_BASH_GUARD_UNAVAILABLE"
    assert result.exact_bash_guard_enabled is False
    assert not invocation_marker.exists(), "Claude must never start when the guard file is missing"


def test_hardening_12_symlinked_guard_path_fails_closed_before_claude_starts(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12a. A symlink substituted for the guard script fails closed."""

    real_target = tmp_path / "attacker_owned_guard.py"
    real_target.write_text("# attacker-substituted guard\n", encoding="utf-8")
    symlink_path = tmp_path / "exact_bash_guard.py"
    symlink_path.symlink_to(real_target)

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    monkeypatch.setattr(claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", symlink_path)
    invocation_marker = tmp_path / "invoked.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-symlink-guard",
        f"""
import pathlib
pathlib.Path({str(invocation_marker)!r}).write_text("invoked")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "EXACT_BASH_GUARD_UNAVAILABLE"
    assert not invocation_marker.exists(), "Claude must never start when the guard path is a symlink"


def test_hardening_12b_directory_guard_path_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """12b. A directory (non-regular file) at the guard path fails closed."""

    directory_path = tmp_path / "exact_bash_guard.py"
    directory_path.mkdir()
    monkeypatch.setattr(claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", directory_path)
    with pytest.raises(claude_code_cli_module._ExactBashGuardIntegrityError):
        claude_code_cli_module._verified_exact_bash_guard_module_path()


def test_hardening_12c_integrity_error_is_an_oserror_subclass() -> None:
    """The preflight failure routes through the already-reviewed
    `except OSError` fail-closed path -- a new *reason*, not a new outcome."""

    assert issubclass(claude_code_cli_module._ExactBashGuardIntegrityError, OSError)


def test_hardening_12d_preflight_error_messages_carry_no_dynamic_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight messages stay a fixed shape -- no errno text, no path."""

    missing = tmp_path / "definitely-not-here.py"
    monkeypatch.setattr(claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", missing)
    with pytest.raises(claude_code_cli_module._ExactBashGuardIntegrityError) as excinfo:
        claude_code_cli_module._verified_exact_bash_guard_module_path()
    assert str(missing) not in str(excinfo.value)
    assert "definitely-not-here" not in str(excinfo.value)


def test_hardening_12e_preflight_accepts_the_real_guard_file() -> None:
    """Positive control: the real, unmodified guard file passes preflight."""

    path = claude_code_cli_module._verified_exact_bash_guard_module_path()
    assert path == claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH
    assert path.is_file()
    assert not path.is_symlink()


def test_hardening_13_no_authorized_command_behavior_unchanged(workspace: Path, tmp_path: Path) -> None:
    """13. No authorized command => existing behavior unchanged.

    No preflight is required, no guard artifacts are written, no
    `--settings` argv is emitted, and a broken guard path is irrelevant.
    """

    script = write_fake_executable(
        tmp_path,
        "fake-claude-hardening-no-auth",
        'import pathlib\npathlib.Path("result.txt").write_text("changed", encoding="utf-8")',
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.exact_bash_guard_enabled is False
    # Settings now always present (file-write guard); only Bash layers conditional.
    assert "--settings" in result.sanitized_argv
    assert "--allowedTools" not in result.sanitized_argv


def test_hardening_13b_no_authorized_command_unaffected_by_broken_guard_path(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preflight must not run at all when there is no authorized
    command -- an existing no-command workflow cannot be broken by guard
    file state it never uses."""

    monkeypatch.setattr(
        claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", tmp_path / "does-not-exist.py"
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-hardening-no-auth-broken-guard",
        'import pathlib\npathlib.Path("result.txt").write_text("changed", encoding="utf-8")',
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.exact_bash_guard_enabled is False


def test_hardening_settings_file_records_the_hardened_hook_command(tmp_path: Path) -> None:
    """The persisted settings file carries the `-E -s` command and still
    contains no raw authorized-command text."""

    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), tmp_path)
    try:
        settings_text = artifacts.settings_path.read_text(encoding="utf-8")
        settings = json.loads(settings_text)
        hook_command = _pretool_hook_by_matcher(settings, "Bash")["hooks"][0]["command"]
        assert " -E " in f" {hook_command} "
        assert " -s " in f" {hook_command} "
        assert str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH) in hook_command
        # Fingerprint-only artifacts: unchanged by this hardening.
        for token in ("writer.py", "exact.txt"):
            assert token not in settings_text
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


# ===========================================================================
# Layered exact-argv authorization ENABLEMENT
# (EXACT_BASH_AUTHORIZATION_ENABLEMENT_REPORT.md)
#
# Every test above that exercises the capability monkeypatches the layered
# flag on, which proves the code path but not the shipped posture. This block
# deliberately does NOT monkeypatch: it pins that the capability is enabled
# *by default*, and that enabling it did not weaken any layer.
# ===========================================================================


def test_enablement_1_layered_capability_resolves_exact_supported_by_default() -> None:
    contract, rationale = resolve_claude_cli_authorization_contract()
    assert contract is ClaudeCLIAuthorizationContract.EXACT_SUPPORTED
    assert "layered" in rationale.lower()


def test_enablement_2_native_bare_bash_exactness_is_not_claimed() -> None:
    # The old constant records a provider behavior that was measured to be
    # NON-exact. It must stay False, and -- critically -- the resolver must
    # not consult it, so native `--allowedTools` alone can never satisfy the
    # contract.
    assert claude_code_cli_module._CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT is False

    import inspect

    source = inspect.getsource(claude_code_cli_module.resolve_claude_cli_authorization_contract)
    body = source.split('"""')[-1]  # ignore the docstring, which names it explanatorily
    assert "_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT" not in body


def test_enablement_2b_disabling_the_layered_flag_restores_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", False
    )
    contract, _ = resolve_claude_cli_authorization_contract()
    assert contract is ClaudeCLIAuthorizationContract.UNSUPPORTED


def _enabled_authorized_run(
    workspace: Path,
    tmp_path: Path,
    *,
    body: str,
    name: str,
    allowed_changed_paths: tuple[str, ...] = ("result.txt",),
) -> ClaudeCodeCLIResult:
    """An authorized run using the shipped default posture (no monkeypatch)."""

    script = write_fake_executable(tmp_path, name, body)
    request = base_request(
        workspace,
        authorized_commands=(_GUARD_ARGV_COMMAND,),
        allowed_changed_paths=allowed_changed_paths,
    )
    return ClaudeCodeCLIAdapter().run(request, executable_path=str(script))


_ARGV_CAPTURING_FAKE_CLAUDE = """
import json, pathlib, sys
pathlib.Path("argv_seen.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
settings_path = None
argv = sys.argv[1:]
for i, a in enumerate(argv):
    if a == "--settings":
        settings_path = argv[i + 1]
pathlib.Path("settings_copy.json").write_text(
    pathlib.Path(settings_path).read_text(encoding="utf-8") if settings_path else "", encoding="utf-8"
)
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
"""

_ARGV_CAPTURE_ALLOWED_PATHS = ("result.txt", "argv_seen.json", "settings_copy.json")


def test_enablement_3_to_7_authorized_run_wires_every_layer_by_default(
    workspace: Path, tmp_path: Path
) -> None:
    result = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=_ARGV_CAPTURING_FAKE_CLAUDE,
        name="fake-claude-enablement-layers",
        allowed_changed_paths=_ARGV_CAPTURE_ALLOWED_PATHS,
    )

    # 3 + 7: the request reached guarded execution, not the dormant early return.
    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.error_category != "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"
    assert result.exact_bash_guard_enabled is True

    argv = json.loads((workspace / "argv_seen.json").read_text(encoding="utf-8"))

    # 4: PreToolUse guard settings present.
    assert "--settings" in argv
    settings = json.loads((workspace / "settings_copy.json").read_text(encoding="utf-8"))
    pretooluse = _pretool_hook_by_matcher(settings, "Bash")

    # 5: Claude's native layer still present alongside, never instead of.
    assert "--allowedTools" in argv
    assert render_authorized_command_cli_rule(_GUARD_ARGV_COMMAND) in argv

    # 6: hardened hook invocation retained.
    import shlex as _shlex
    import sys as _sys

    hook_command = pretooluse["hooks"][0]["command"]
    hook_argv = _shlex.split(hook_command)
    assert hook_argv == [
        _sys.executable,
        "-E",
        "-s",
        str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH),
    ]

    # 17: the permission mode is the request's own; never bypassPermissions.
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == base_request(workspace).permission_mode
    assert "bypassPermissions" not in argv


def test_enablement_8_provider_output_withheld_on_enabled_authorized_run(
    workspace: Path, tmp_path: Path
) -> None:
    result = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=(
            "import pathlib, sys\n"
            f"sys.stdout.write({json.dumps({'result': _PROVIDER_SYNTHETIC_COMMAND})!r})\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
        ),
        name="fake-claude-enablement-withheld",
    )
    assert result.provider_output_withheld is True
    assert _PROVIDER_SYNTHETIC_COMMAND not in result.stdout_excerpt


def test_enablement_9_unknown_provider_shape_still_cannot_persist_raw_text(
    workspace: Path, tmp_path: Path
) -> None:
    payload = {
        "a_key_invented_after_enablement": _PROVIDER_SYNTHETIC_COMMAND,
        "nested": [{"deep": {"deeper": [_PROVIDER_SYNTHETIC_SECRET]}}],
    }
    result = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=(
            "import pathlib, sys\n"
            f"sys.stdout.write({json.dumps(payload)!r})\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
        ),
        name="fake-claude-enablement-unknown-shape",
    )
    _assert_evidence_is_clean(result, tmp_path, "enablement-unknown-shape")

    # Malformed output must degrade to less metadata, never to raw text.
    result2 = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=(
            "import pathlib, sys\n"
            f"sys.stdout.write({('{broken json ' + _PROVIDER_SYNTHETIC_COMMAND)!r})\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
        ),
        name="fake-claude-enablement-malformed",
    )
    _assert_evidence_is_clean(result2, tmp_path, "enablement-malformed")
    assert result2.provider_stdout_json_parsed is False


_ENABLEMENT_DENY_CASES = [
    ("wrapper-variant", "command python3 writer.py exact.txt"),
    ("timeout-wrapper", "timeout 5 python3 writer.py exact.txt"),
    ("extra-arg", "python3 writer.py exact.txt --extra"),
    ("compound-command", "python3 writer.py exact.txt; whoami"),
]


@pytest.mark.parametrize("label,command", _ENABLEMENT_DENY_CASES, ids=[c[0] for c in _ENABLEMENT_DENY_CASES])
def test_enablement_11_to_13_guard_denies_variants_in_enabled_configuration(
    workspace: Path, tmp_path: Path, label: str, command: str
) -> None:
    """The variants the native layer would have allowed are denied by the guard.

    Runs the *real* hook subprocess against the settings/fingerprints the
    enabled run actually generated -- no monkeypatch, no hand-built argv.
    """

    hook_input = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=_fake_claude_invoking_real_hook(hook_input),
        name=f"fake-claude-enablement-deny-{label}",
        allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS,
    )
    assert result.exact_bash_guard_enabled is True
    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "2"
    assert (workspace / "hook_stderr.txt").read_text(encoding="utf-8").strip() == _GUARD_DENIAL_MESSAGE


def test_enablement_10_exact_authorized_argv_defers_in_enabled_configuration(
    workspace: Path, tmp_path: Path
) -> None:
    hook_input = {"tool_name": "Bash", "tool_input": {"command": "python3 writer.py exact.txt"}}
    result = _enabled_authorized_run(
        workspace,
        tmp_path,
        body=_fake_claude_invoking_real_hook(hook_input),
        name="fake-claude-enablement-exact",
        allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS,
    )
    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "0"
    # A PASS is a silent defer, never itself a grant.
    assert (workspace / "hook_stdout.txt").read_text(encoding="utf-8") == ""
    assert (workspace / "hook_stderr.txt").read_text(encoding="utf-8") == ""


def test_enablement_14_run_in_background_denied_in_enabled_configuration(
    workspace: Path, tmp_path: Path
) -> None:
    hook_input = {
        "tool_name": "Bash",
        "tool_input": {"command": "python3 writer.py exact.txt", "run_in_background": True},
    }
    _enabled_authorized_run(
        workspace,
        tmp_path,
        body=_fake_claude_invoking_real_hook(hook_input),
        name="fake-claude-enablement-background",
        allowed_changed_paths=_GUARD_HOOK_HARNESS_ALLOWED_PATHS,
    )
    assert (workspace / "hook_exit_code.txt").read_text(encoding="utf-8").strip() == "2"


def test_enablement_15_broken_guard_preflight_fails_closed_when_enabled(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enablement must not weaken the preflight: a substituted guard path
    still fails closed *before* the worker process starts."""

    invocation_marker = tmp_path / "invoked.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-enablement-preflight",
        f"""
import pathlib
pathlib.Path({str(invocation_marker)!r}).write_text("invoked")
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
""",
    )
    monkeypatch.setattr(
        claude_code_cli_module, "_EXACT_BASH_GUARD_MODULE_PATH", tmp_path / "definitely-missing-guard.py"
    )
    request = base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "EXACT_BASH_GUARD_UNAVAILABLE"
    assert result.exact_bash_guard_enabled is False
    assert not invocation_marker.exists(), "the worker must never start when the guard preflight fails"


def test_enablement_16_no_authorized_commands_behavior_unchanged_after_enablement(
    workspace: Path, tmp_path: Path
) -> None:
    payload = json.dumps({"result": "plain run", "total_cost_usd": 1.5})
    script = write_fake_executable(
        tmp_path,
        "fake-claude-enablement-no-command",
        (
            "import pathlib, sys\n"
            f"sys.stdout.write({payload!r})\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
        ),
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.exact_bash_guard_enabled is False
    assert result.provider_output_withheld is False
    assert result.stdout_excerpt == payload  # unchanged passthrough
    # `_build_argv` without artifacts cannot emit --settings; the real run()
    # always supplies them (see the file-write guard integration tests).
    argv = ClaudeCodeCLIAdapter()._build_argv(str(script), base_request(workspace))
    assert "--settings" not in argv
    assert "--settings" in result.sanitized_argv  # the real invocation does carry them


# ===========================================================================
# Claude CLI prompt argv-ordering regression
# (CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md).
#
# Root cause: `--allowedTools` accepts an arbitrary number of positional
# rule values with no closing delimiter. The task prompt used to be appended
# to argv *after* `--allowedTools <rules...>`, so Claude silently consumed
# it as one more allowedTools rule instead of receiving it as the `-p`
# argument -- Claude then saw no prompt at all and exited 1. Reproduced
# against a real personal-web dogfood run. Fixed by placing the prompt in
# its own positional slot immediately after `-p`, before any variadic
# option.
# ===========================================================================


def test_prompt_argv_order_no_authorized_commands_prompt_present_once(
    workspace: Path, tmp_path: Path
) -> None:
    """1. With no authorized_commands: prompt is present exactly once, and
    existing (no-command) behavior remains valid."""

    script = write_fake_executable(
        tmp_path, "fake-claude-argv-order-no-auth", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    prompt = "demo prompt for the no-authorized-commands case"
    argv = ClaudeCodeCLIAdapter()._build_argv(str(script), base_request(workspace, prompt=prompt))

    assert argv.count(prompt) == 1
    assert argv[argv.index("-p") + 1] == prompt
    assert "--allowedTools" not in argv
    # No artifacts passed to this _build_argv call, so no --settings here; the
    # real run() always attaches them (file-write guard integration tests).
    assert "--settings" not in argv


def test_prompt_argv_order_authorized_commands_prompt_precedes_allowedtools(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2. With authorized_commands: `-p` is present, the task prompt appears
    before `--allowedTools`, the prompt appears exactly once, every
    allowedTools rule remains present, settings remain present, and
    permission mode is unchanged."""

    monkeypatch.setattr(claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True)
    script = write_fake_executable(
        tmp_path, "fake-claude-argv-order-auth", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    prompt = "demo prompt for the authorized-commands case"
    two_commands = (_GUARD_ARGV_COMMAND, ("npm", "install", "foo", "--save-exact"))
    request = base_request(workspace, prompt=prompt, authorized_commands=two_commands, permission_mode="acceptEdits")
    adapter = ClaudeCodeCLIAdapter()
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts(two_commands, workspace)
    try:
        rules = tuple(claude_code_cli_module.render_authorized_command_cli_rule(argv) for argv in two_commands)
        argv = adapter._build_argv(str(script), request, rules, artifacts)

        assert "-p" in argv
        assert argv.count(prompt) == 1

        prompt_index = argv.index("-p") + 1
        allowedtools_index = argv.index("--allowedTools")
        assert argv[prompt_index] == prompt
        assert prompt_index < allowedtools_index  # the required invariant

        for rule in rules:
            assert rule in argv
        assert "--settings" in argv
        assert argv[argv.index("--settings") + 1] == str(artifacts.settings_path)
        assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def _fake_claude_cli_argv_parser_source() -> str:
    """A fake `claude` that reproduces the specific real-CLI behavior that
    caused the defect: `--allowedTools` greedily consumes every following
    positional value up to the next `--`-prefixed option (there is no
    closing delimiter), and print mode (`-p`) fails if no positional prompt
    value was captured for it. Writes its verdict to `verdict.json` instead
    of exiting non-zero, so the *old* (buggy) ordering can be exercised
    without the adapter's own timeout/retry machinery getting involved.
    """

    return '''
import json, pathlib, sys

argv = sys.argv[1:]
i = 0
prompt = None
allowed_tools: list[str] = []
while i < len(argv):
    token = argv[i]
    if token == "-p":
        # Real Claude: the *next* token, if present and not itself an
        # option, is the positional prompt argument.
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            prompt = argv[i + 1]
            i += 2
            continue
        i += 1
        continue
    if token == "--allowedTools":
        i += 1
        while i < len(argv) and not argv[i].startswith("--"):
            allowed_tools.append(argv[i])
            i += 1
        continue
    if token in ("--output-format", "--permission-mode", "--settings", "--model"):
        i += 2
        continue
    i += 1

if prompt is None:
    sys.stderr.write(
        "Error: Input must be provided either through stdin or as a prompt "
        "argument when using --print"
    )
    sys.exit(1)

pathlib.Path("verdict.json").write_text(
    json.dumps({"prompt": prompt, "allowed_tools": allowed_tools}), encoding="utf-8"
)
pathlib.Path("result.txt").write_text("changed", encoding="utf-8")
'''


def test_prompt_argv_order_fake_cli_reproduces_old_ordering_failure_and_new_ordering_success(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3. A fake Claude executable/parser reproducing the real CLI's
    `--allowedTools`-is-variadic-and-unterminated behavior: prove the *old*
    (prompt-after-allowedTools) ordering fails exactly as the real dogfood
    run did, and the *new* (fixed) ordering, produced by the current
    `_build_argv`, succeeds."""

    monkeypatch.setattr(claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True)
    parser_source = _fake_claude_cli_argv_parser_source()
    prompt = "the real task prompt"

    # --- Old (buggy) ordering: prompt appended after --allowedTools. ---
    old_script = write_fake_executable(tmp_path, "fake-claude-old-ordering", parser_source)
    old_argv = [
        str(old_script),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash(<AUTHORIZED_COMMAND_SHA256:deadbeef>)",
        prompt,
    ]
    old_proc = subprocess.run(old_argv, cwd=workspace, capture_output=True, text=True, timeout=10)
    assert old_proc.returncode == 1
    assert "Input must be provided either through stdin or as a prompt argument" in old_proc.stderr
    assert not (workspace / "verdict.json").exists()

    # --- New (fixed) ordering: produced by the adapter's real _build_argv. ---
    new_script = write_fake_executable(tmp_path, "fake-claude-new-ordering", parser_source)
    request = base_request(
        workspace, prompt=prompt, authorized_commands=(_GUARD_ARGV_COMMAND,), permission_mode="acceptEdits"
    )
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), workspace)
    try:
        rules = (claude_code_cli_module.render_authorized_command_cli_rule(_GUARD_ARGV_COMMAND),)
        new_argv = ClaudeCodeCLIAdapter()._build_argv(str(new_script), request, rules, artifacts)
        new_proc = subprocess.run(new_argv, cwd=workspace, capture_output=True, text=True, timeout=10)
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)

    assert new_proc.returncode == 0, new_proc.stderr
    verdict = json.loads((workspace / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["prompt"] == prompt
    assert verdict["allowed_tools"] == list(rules)


def test_prompt_argv_order_full_run_succeeds_with_authorized_commands(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the real `run()`: an authorized-command request no
    longer fails with NONZERO_EXIT because the prompt was swallowed by
    `--allowedTools` -- the exact failure mode of the real dogfood run this
    fix addresses."""

    monkeypatch.setattr(claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True)
    script = write_fake_executable(tmp_path, "fake-claude-argv-order-e2e", _fake_claude_cli_argv_parser_source())
    prompt = "end-to-end argv ordering prompt"
    request = base_request(
        workspace,
        prompt=prompt,
        authorized_commands=(_GUARD_ARGV_COMMAND,),
        allowed_changed_paths=("result.txt", "verdict.json"),
    )
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.error_category is None
    verdict = json.loads((workspace / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["prompt"] == prompt


def test_prompt_argv_order_preserves_exact_authorization_security_layers(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4-5. Moving the prompt does not weaken any exact-authorization layer:
    the guard, fingerprints, -E -s hardening, guard preflight, provider-output
    withholding, run_in_background rejection, and the native/layered
    capability flags are all unaffected -- and sanitized argv / persisted
    evidence still behave correctly with the prompt in its new position."""

    monkeypatch.setattr(claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True)
    secret_command_argument = "argvalue-must-not-leak-prompt-order-fix"
    argv_command = ("npm", "install", f"pkg-{secret_command_argument}")
    prompt = "prompt containing no secrets, moved earlier in argv"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-argv-order-security",
        (
            'import json, pathlib, sys\n'
            'payload = {"result": "ok", "permission_denials": []}\n'
            'sys.stdout.write(json.dumps(payload))\n'
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
        ),
    )
    request = base_request(workspace, prompt=prompt, authorized_commands=(argv_command,))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    # Native bare-Bash flag stays False and is not what enabled this.
    assert claude_code_cli_module._CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT is False
    # Layered capability is what governed the run.
    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED

    # Guard/fingerprint/hardening layers still all applied.
    assert result.exact_bash_guard_enabled is True
    assert "--settings" in result.sanitized_argv
    assert "--allowedTools" in result.sanitized_argv
    settings_path = result.sanitized_argv[result.sanitized_argv.index("--settings") + 1]
    assert not Path(settings_path).exists()  # cleaned up after run(), as before

    # Provider-output withholding still applies.
    assert result.provider_output_withheld is True
    assert result.stdout_excerpt == claude_code_cli_module._PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER

    # No raw authorized-command argument value leaks into sanitized argv,
    # the evidence manifest, or the prompt's own position.
    raw_rule = claude_code_cli_module.render_authorized_command_cli_rule(argv_command)
    assert raw_rule not in result.sanitized_argv
    manifest_json = render_evidence_manifest_json(result)
    assert secret_command_argument not in manifest_json
    assert secret_command_argument not in " ".join(result.sanitized_argv)

    # The prompt itself is present, exactly once, before --allowedTools, and
    # unredacted (it carries no secret and is not authorized-command text).
    assert result.sanitized_argv.count(prompt) == 1
    prompt_index = result.sanitized_argv.index(prompt)
    allowedtools_index = result.sanitized_argv.index("--allowedTools")
    assert prompt_index < allowedtools_index


def test_prompt_argv_order_run_in_background_still_rejected(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-regression: the exact-argv guard's `run_in_background` rejection
    is unaffected by the prompt moving in the *adapter's* argv (the guard
    reasons about the Bash tool call's own JSON payload, not the Claude CLI
    invocation argv)."""

    monkeypatch.setattr(claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True)
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), workspace)
    try:
        fingerprints_path = artifacts.fingerprints_path
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "python3 writer.py exact.txt", "run_in_background": True},
        }
        proc = subprocess.run(
            [
                claude_code_cli_module.sys.executable,
                *claude_code_cli_module._EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS,
                str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH),
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", claude_code_cli_module._EXACT_BASH_GUARD_FINGERPRINTS_ENV: str(fingerprints_path)},
            timeout=10,
        )
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)
    assert proc.returncode == 2


# ===========================================================================
# Workspace side-effect scope accounting
# (WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md).
#
# A real dogfood run (personal-web-content-exactauth-rerun-002) was rejected
# with unexpected_count=2709 -- entirely `node_modules/**` (2517) and
# `.next/**` (192) generated by the Owner-approved `npm install` /
# `npm run build`. The real product change set was 17 paths, all inside the
# approved source allowlist. TOMTIT was classifying authorized commands'
# generated output as unauthorized source changes.
#
# The fix is a second, INDEPENDENT Owner contract -- not a relaxation of the
# first, and explicitly not derived from `.gitignore`.
# ===========================================================================

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    AllowedSideEffectPathError,
    classify_workspace_changes,
    normalize_allowed_side_effect_path,
    normalize_allowed_side_effect_paths,
    summarize_side_effect_paths,
)

# The real failure shape, scaled down but structurally identical.
_REAL_SHAPE_ALLOWED_SOURCE = ("package.json", "lib/content/projects.ts")
_REAL_SHAPE_OBSERVED = (
    "package.json",
    "lib/content/projects.ts",
    "node_modules/pkg/index.js",
    "node_modules/pkg/package.json",
    ".next/build-manifest.json",
    ".next/cache/file",
)
_REAL_SHAPE_SIDE_EFFECT_ROOTS = ("node_modules", ".next")


# --- 18/19. The real-shape fixture, both directions ------------------------


def test_side_effect_real_shape_fixture_with_authorization() -> None:
    """18. The reproduced real shape: with `node_modules` and `.next`
    authorized, generated output is classified as authorized side effects and
    nothing is unexpected."""

    source, side_effects, unexpected = classify_workspace_changes(
        _REAL_SHAPE_OBSERVED, _REAL_SHAPE_ALLOWED_SOURCE, _REAL_SHAPE_SIDE_EFFECT_ROOTS
    )
    assert len(source) == 2
    assert len(side_effects) == 4
    assert len(unexpected) == 0
    assert set(source) == set(_REAL_SHAPE_ALLOWED_SOURCE)

    by_root, sample = summarize_side_effect_paths(side_effects, _REAL_SHAPE_SIDE_EFFECT_ROOTS)
    assert dict(by_root) == {"node_modules": 2, ".next": 2}
    assert len(sample) <= claude_code_cli_module._MAX_SIDE_EFFECT_SAMPLE_PATHS


def test_side_effect_real_shape_fixture_without_authorization_fails_closed() -> None:
    """19. The same fixture with no side-effect authorization must fail
    closed -- the pre-fix behavior is preserved exactly when the Owner has
    not approved anything."""

    source, side_effects, unexpected = classify_workspace_changes(
        _REAL_SHAPE_OBSERVED, _REAL_SHAPE_ALLOWED_SOURCE, ()
    )
    assert len(source) == 2
    assert len(side_effects) == 0
    assert len(unexpected) == 4


# --- 1/2/3. Baseline behavior, fail-closed default, and authorization ------


def test_side_effect_existing_allowed_source_paths_unchanged() -> None:
    """1. Existing allowed source paths still classify as source changes."""

    source, side_effects, unexpected = classify_workspace_changes(
        ("a.txt", "dir/b.txt"), ("a.txt", "dir/b.txt"), ()
    )
    assert source == ("a.txt", "dir/b.txt")
    assert side_effects == ()
    assert unexpected == ()


def test_side_effect_none_authorized_generated_output_still_fails_closed() -> None:
    """2. With no allowed-side-effect paths, generated out-of-scope mutations
    remain unexpected."""

    _, side_effects, unexpected = classify_workspace_changes(
        ("node_modules/pkg/index.js", ".next/x"), ("a.txt",), ()
    )
    assert side_effects == ()
    assert unexpected == ("node_modules/pkg/index.js", ".next/x")


def test_side_effect_authorized_roots_reclassify_generated_output() -> None:
    """3. Once the Owner authorizes the roots, mutations beneath them become
    authorized side effects rather than unexpected source changes."""

    _, side_effects, unexpected = classify_workspace_changes(
        ("node_modules/pkg/index.js", ".next/x"), ("a.txt",), ("node_modules", ".next")
    )
    assert side_effects == ("node_modules/pkg/index.js", ".next/x")
    assert unexpected == ()


# --- 4/5. Component-aware containment -------------------------------------


@pytest.mark.parametrize(
    "path,expected_side_effect",
    [
        ("node_modules/foo", True),
        ("node_modules/a/b", True),
        ("node_modules", True),
        ("node_modules-evil/foo", False),
        ("node_modules_evil/foo", False),
        ("xnode_modules/foo", False),
        ("a/node_modules/foo", False),
    ],
    ids=[
        "direct-child",
        "nested-child",
        "root-itself",
        "hyphen-sibling",
        "underscore-sibling",
        "prefixed-sibling",
        "nested-elsewhere",
    ],
)
def test_side_effect_containment_is_component_aware(path: str, expected_side_effect: bool) -> None:
    """4. `node_modules-evil/foo` shares a textual prefix but is a different
    directory -- naive string-prefix matching would wrongly authorize it."""

    _, side_effects, unexpected = classify_workspace_changes((path,), (), ("node_modules",))
    assert bool(side_effects) is expected_side_effect
    assert bool(unexpected) is (not expected_side_effect)


def test_side_effect_next_requires_explicit_authorization() -> None:
    """5. `.next/**` is only authorized when `.next` itself is approved."""

    _, only_node, unexpected_node = classify_workspace_changes(
        (".next/build-manifest.json",), (), ("node_modules",)
    )
    assert only_node == ()
    assert unexpected_node == (".next/build-manifest.json",)

    _, with_next, unexpected_next = classify_workspace_changes(
        (".next/build-manifest.json",), (), ("node_modules", ".next")
    )
    assert with_next == (".next/build-manifest.json",)
    assert unexpected_next == ()


# --- 6/7. Gitignored-but-unauthorized files still fail closed -------------


@pytest.mark.parametrize(
    "ignored_path",
    [".env", ".env.local", "secret.txt", "credentials.json", "dist/bundle.js", "coverage/lcov.info"],
)
def test_side_effect_ignored_but_unauthorized_files_remain_unexpected(ignored_path: str) -> None:
    """6/7. Files a `.gitignore` would typically hide are NOT authorized by
    that fact. Authorization comes only from the explicit Owner contract --
    blanket gitignore filtering would silently widen the trust boundary to
    include `.env`, credentials, and private configuration."""

    _, side_effects, unexpected = classify_workspace_changes(
        (ignored_path,), ("package.json",), ("node_modules", ".next")
    )
    assert side_effects == ()
    assert unexpected == (ignored_path,)


# --- 8/9. Validation: .git, absolute, traversal, root, aliases ------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        ".",
        "./",
        "/",
        "/abs",
        "/abs/path",
        "..",
        "../x",
        "a/../..",
        "./x",
        "a//b",
        "a/./b",
        "foo/",
        ".git",
        ".git/hooks",
        ".git/objects/ab/cd",
        "a\\b",
        "node_modules/../.git",
    ],
    ids=[
        "empty",
        "dot",
        "dot-slash",
        "root-slash",
        "absolute",
        "absolute-nested",
        "parent",
        "parent-traversal",
        "embedded-traversal",
        "dot-component",
        "double-slash",
        "dot-in-middle",
        "trailing-slash",
        "git-root",
        "git-hooks",
        "git-objects",
        "backslash-alias",
        "traversal-into-git",
    ],
)
def test_side_effect_path_validation_rejects_unsafe_values(bad_path: str) -> None:
    """8/9. `.git` at any depth, absolute paths, `..` traversal, the
    workspace root, and non-normalized aliases are all rejected."""

    with pytest.raises(AllowedSideEffectPathError) as excinfo:
        normalize_allowed_side_effect_path(bad_path)
    assert excinfo.value.category == "INVALID_SIDE_EFFECT_PATH"


def test_side_effect_git_can_never_be_authorized_through_the_request() -> None:
    """8. The trust boundary is re-enforced on direct request construction,
    not only in the CLI layer."""

    for git_path in (".git", ".git/hooks", ".git/config"):
        with pytest.raises(AllowedSideEffectPathError):
            normalize_allowed_side_effect_paths([git_path])


def test_side_effect_path_validation_accepts_and_dedupes_canonical_roots() -> None:
    assert normalize_allowed_side_effect_paths(["node_modules", ".next", "node_modules"]) == (
        "node_modules",
        ".next",
    )
    assert normalize_allowed_side_effect_path("a/b/c") == "a/b/c"


# --- 10. Source scope still enforced when side-effect roots exist ---------


def test_side_effect_unapproved_source_file_still_violates_scope() -> None:
    """10. Authorizing generated roots must not weaken source-scope
    enforcement for anything outside them."""

    _, side_effects, unexpected = classify_workspace_changes(
        ("package.json", "src/unapproved.ts", "node_modules/pkg/x"),
        ("package.json",),
        ("node_modules", ".next"),
    )
    assert side_effects == ("node_modules/pkg/x",)
    assert unexpected == ("src/unapproved.ts",)


def test_side_effect_source_allowlist_takes_precedence_over_side_effect_root() -> None:
    """Ordering: a path that is both source-allowed and under an authorized
    root classifies as SOURCE, never silently as a side effect."""

    source, side_effects, _ = classify_workspace_changes(
        ("node_modules/.keep",), ("node_modules/.keep",), ("node_modules",)
    )
    assert source == ("node_modules/.keep",)
    assert side_effects == ()


# --- Adapter integration: request validation, run(), evidence -------------


def test_side_effect_request_rejects_unsafe_paths_at_trust_boundary(workspace: Path) -> None:
    """Direct request construction cannot bypass validation."""

    for bad in ("/abs", "..", ".git", "a/../.git", "."):
        with pytest.raises(AllowedSideEffectPathError):
            base_request(workspace, allowed_side_effect_paths=(bad,))


def test_side_effect_run_classifies_generated_output_end_to_end(
    workspace: Path, tmp_path: Path
) -> None:
    """End-to-end through the real `run()`: a fake worker that writes both an
    approved source file and generated output beneath authorized roots must
    succeed with zero unexpected paths, and `changed_paths` must remain the
    product change set rather than swelling with generated files."""

    script = write_fake_executable(
        tmp_path,
        "fake-claude-side-effects",
        (
            "import pathlib\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
            'pathlib.Path("node_modules/pkg").mkdir(parents=True, exist_ok=True)\n'
            'pathlib.Path("node_modules/pkg/index.js").write_text("x", encoding="utf-8")\n'
            'pathlib.Path("node_modules/pkg/package.json").write_text("{}", encoding="utf-8")\n'
            'pathlib.Path(".next/cache").mkdir(parents=True, exist_ok=True)\n'
            'pathlib.Path(".next/build-manifest.json").write_text("{}", encoding="utf-8")\n'
            'pathlib.Path(".next/cache/file").write_text("y", encoding="utf-8")\n'
        ),
    )
    request = base_request(
        workspace,
        allowed_changed_paths=("result.txt",),
        allowed_side_effect_paths=("node_modules", ".next"),
    )
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.unexpected_changed_paths == ()
    assert result.changed_paths == ("result.txt",)  # product change set only
    assert result.side_effect_changed_path_count == 4
    assert dict(result.side_effect_changed_path_counts_by_root) == {"node_modules": 2, ".next": 2}
    assert result.allowed_side_effect_paths == ("node_modules", ".next")


def test_side_effect_run_without_authorization_reports_scope_violation(
    workspace: Path, tmp_path: Path
) -> None:
    """The same worker, with no side-effect authorization, must still be a
    scope violation -- proving the fix did not blanket-allow generated
    output."""

    script = write_fake_executable(
        tmp_path,
        "fake-claude-side-effects-unauth",
        (
            "import pathlib\n"
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
            'pathlib.Path("node_modules/pkg").mkdir(parents=True, exist_ok=True)\n'
            'pathlib.Path("node_modules/pkg/index.js").write_text("x", encoding="utf-8")\n'
        ),
    )
    result = ClaudeCodeCLIAdapter().run(
        base_request(workspace, allowed_changed_paths=("result.txt",)), executable_path=str(script)
    )

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert "node_modules/pkg/index.js" in result.unexpected_changed_paths
    assert result.side_effect_changed_path_count == 0


def test_side_effect_evidence_is_bounded_and_never_dumps_generated_paths(
    workspace: Path, tmp_path: Path
) -> None:
    """Evidence keeps visibility (counts, per-root totals, small sample) but
    must never materialize thousands of generated paths."""

    many = 40
    lines = ["import pathlib", 'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")',
             'pathlib.Path("node_modules/pkg").mkdir(parents=True, exist_ok=True)']
    lines += [f'pathlib.Path("node_modules/pkg/f{i}.js").write_text("{i}", encoding="utf-8")' for i in range(many)]
    script = write_fake_executable(tmp_path, "fake-claude-many-side-effects", "\n".join(lines))

    request = base_request(
        workspace, allowed_changed_paths=("result.txt",), allowed_side_effect_paths=("node_modules",)
    )
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SUCCEEDED
    assert result.side_effect_changed_path_count == many
    assert len(result.side_effect_changed_paths_sample) == claude_code_cli_module._MAX_SIDE_EFFECT_SAMPLE_PATHS

    manifest = build_evidence_manifest(result)
    assert manifest["observed_authorized_side_effect_count"] == many
    assert manifest["observed_authorized_side_effect_counts_by_root"] == {"node_modules": many}
    assert len(manifest["observed_authorized_side_effect_sample"]) == (
        claude_code_cli_module._MAX_SIDE_EFFECT_SAMPLE_PATHS
    )
    # The full generated list never reaches evidence.
    manifest_json = render_evidence_manifest_json(result)
    assert manifest_json.count("node_modules/pkg/f") <= claude_code_cli_module._MAX_SIDE_EFFECT_SAMPLE_PATHS


# --- 13/15/16. Independence from the exact-Bash contracts -----------------


def test_side_effect_authorization_does_not_grant_bash_execution(workspace: Path, tmp_path: Path) -> None:
    """13/16. Approving a side-effect root grants no Bash authorization: no
    guard artifacts, no `--allowedTools`, no fingerprints."""

    script = write_fake_executable(
        tmp_path, "fake-claude-se-no-bash", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    request = base_request(workspace, allowed_side_effect_paths=("node_modules",))
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.exact_bash_guard_enabled is False
    assert result.authorized_command_count == 0
    assert result.authorized_command_fingerprints == ()
    assert "--allowedTools" not in result.sanitized_argv
    # Settings ARE present now (they carry the always-on file-write guard);
    # what side-effect authorization must not create is any *Bash* layer.
    assert "--settings" in result.sanitized_argv
    # And provider-output withholding is still keyed off authorized_commands.
    assert result.provider_output_withheld is False


def test_side_effect_bash_authorization_does_not_grant_filesystem_scope(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """13/15. Approving `npm run build` grants no filesystem scope: its
    generated output is still unexpected unless the roots are separately
    authorized. Provider-output withholding remains unchanged."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-bash-no-fs",
        (
            "import pathlib, sys\n"
            'sys.stdout.write("{\\"result\\": \\"ok\\"}")\n'
            'pathlib.Path("result.txt").write_text("changed", encoding="utf-8")\n'
            'pathlib.Path(".next").mkdir(parents=True, exist_ok=True)\n'
            'pathlib.Path(".next/out.json").write_text("{}", encoding="utf-8")\n'
        ),
    )
    request = base_request(
        workspace,
        allowed_changed_paths=("result.txt",),
        authorized_commands=(("npm", "run", "build"),),
        # deliberately NO allowed_side_effect_paths
    )
    result = ClaudeCodeCLIAdapter().run(request, executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.SCOPE_VIOLATION
    assert ".next/out.json" in result.unexpected_changed_paths
    assert result.exact_bash_guard_enabled is True  # Bash gate independently active
    assert result.provider_output_withheld is True  # unchanged by this feature


def test_side_effect_no_authorization_leaves_result_fields_at_defaults(
    workspace: Path, tmp_path: Path
) -> None:
    """16. No-command / no-side-effect compatibility: the new fields stay at
    their inert defaults and existing semantics are untouched."""

    script = write_fake_executable(
        tmp_path, "fake-claude-se-defaults", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert result.allowed_side_effect_paths == ()
    assert result.side_effect_changed_path_count == 0
    assert result.side_effect_changed_path_counts_by_root == ()
    assert result.side_effect_changed_paths_sample == ()
    manifest = build_evidence_manifest(result)
    assert manifest["allowed_side_effect_path_count"] == 0
    assert manifest["observed_authorized_side_effect_count"] == 0


# ===========================================================================
# Workspace-scope corrective patch
# (WORKSPACE_SCOPE_CORRECTIVE_FIX_REPORT.md), addressing review findings
# H-1 (symlink escape), M-2 (nested .git), M-3 (early-failure evidence).
#
# Enforcement vs accounting: the snapshot/classifier is a DETECTION layer and
# is tested as such here. OS-level write containment is configured via the
# Claude sandbox contract (also tested here, as a *configuration* contract);
# proving the OS actually blocks a write requires a real sandboxed Claude run
# and is deliberately NOT simulated.
# ===========================================================================

import os as _os  # noqa: E402


def _mk_symlink_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Reproduces the reviewer's H-1 fixture: a workspace with aliases under
    an authorized side-effect root pointing outside, into `.git`, and at a
    directory, plus one legitimate npm-style internal alias."""

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (ws / "result.txt").write_text("v1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external-file.txt").write_text("external secret\n", encoding="utf-8")
    ext_dir = outside / "external-dir"
    ext_dir.mkdir()
    (ext_dir / "inner.txt").write_text("inner\n", encoding="utf-8")
    nm = ws / "node_modules"
    nm.mkdir()
    _os.symlink(outside / "external-file.txt", nm / "external-file")
    _os.symlink(ws / ".git" / "config", nm / "git-config")
    _os.symlink(ext_dir, nm / "dir-link")
    _os.symlink(ws / "result.txt", nm / "internal-link")
    return ws, outside


def test_corrective_snapshot_never_dereferences_file_symlinks(tmp_path: Path) -> None:
    """3. The observer must not read a symlink's target bytes."""

    ws, _ = _mk_symlink_fixture(tmp_path)
    snap = claude_code_cli_module.snapshot_workspace_hashes(ws)
    external_content_hash = hashlib.sha256(b"external secret\n").hexdigest()
    assert external_content_hash not in snap.values()
    assert snap["node_modules/external-file"].startswith("symlink:")


def test_corrective_directory_symlink_observable_and_not_traversed(tmp_path: Path) -> None:
    """4. A directory symlink must be visible as an entry and must not be
    recursively descended (previously it was silently skipped entirely)."""

    ws, _ = _mk_symlink_fixture(tmp_path)
    snap = claude_code_cli_module.snapshot_workspace_hashes(ws)
    assert "node_modules/dir-link" in snap
    assert not any(key.startswith("node_modules/dir-link/") for key in snap)


def test_corrective_symlink_retarget_is_observed(tmp_path: Path) -> None:
    """5. Retargeting a symlink changes the snapshot even if target bytes
    would have hashed identically."""

    ws, outside = _mk_symlink_fixture(tmp_path)
    (outside / "same.txt").write_text("external secret\n", encoding="utf-8")  # identical bytes
    before = claude_code_cli_module.snapshot_workspace_hashes(ws)
    _os.remove(ws / "node_modules" / "external-file")
    _os.symlink(outside / "same.txt", ws / "node_modules" / "external-file")
    after = claude_code_cli_module.snapshot_workspace_hashes(ws)
    assert before["node_modules/external-file"] != after["node_modules/external-file"]


@pytest.mark.parametrize(
    "alias,expect_side_effect",
    [
        ("node_modules/external-file", False),
        ("node_modules/git-config", False),
        ("node_modules/dir-link", False),
        ("node_modules/internal-link", True),
    ],
    ids=["external-target", "git-metadata-target", "external-dir-target", "internal-target"],
)
def test_corrective_unsafe_symlink_targets_are_not_authorized_side_effects(
    tmp_path: Path, alias: str, expect_side_effect: bool
) -> None:
    """6/7/8/9. Lexical containment under an authorized root is NOT enough:
    the resolved target must stay inside the workspace and outside `.git`.
    A legitimate internal npm-style alias remains compatible."""

    ws, _ = _mk_symlink_fixture(tmp_path)
    _, side_effects, unexpected = claude_code_cli_module.classify_workspace_changes(
        (alias,), ("result.txt",), ("node_modules",), workspace_root=ws
    )
    assert (alias in side_effects) is expect_side_effect
    assert (alias in unexpected) is (not expect_side_effect)


def test_corrective_h1_end_to_end_fixture_classification(tmp_path: Path) -> None:
    """The reviewer's exact H-1 scenario: with only `node_modules` authorized,
    the external-file, `.git/config`, and external-directory aliases must all
    be unexpected rather than SUCCEEDED-with-two-side-effects."""

    ws, _ = _mk_symlink_fixture(tmp_path)
    snap = claude_code_cli_module.snapshot_workspace_hashes(ws)
    source, side_effects, unexpected = claude_code_cli_module.classify_workspace_changes(
        tuple(sorted(snap)), ("result.txt",), ("node_modules",), workspace_root=ws
    )
    assert source == ("result.txt",)
    assert set(unexpected) == {
        "node_modules/external-file",
        "node_modules/git-config",
        "node_modules/dir-link",
    }
    assert side_effects == ("node_modules/internal-link",)


# --- M-2: nested .git rejection -------------------------------------------


@pytest.mark.parametrize(
    "bad", [".git", ".git/config", "a/.git", "a/.git/config", "node_modules/x/.git/hooks/y", "a/b/.git"]
)
def test_corrective_nested_git_rejected(bad: str) -> None:
    """1. `.git` is forbidden at ANY depth, not only as the first component."""

    with pytest.raises(claude_code_cli_module.AllowedSideEffectPathError):
        claude_code_cli_module.normalize_allowed_side_effect_path(bad)


def test_corrective_direct_request_cannot_bypass_nested_git(workspace: Path) -> None:
    """2. The request trust boundary enforces it too, not just the CLI."""

    for bad in ("a/.git", "a/.git/config", "node_modules/x/.git"):
        with pytest.raises(claude_code_cli_module.AllowedSideEffectPathError):
            base_request(workspace, allowed_side_effect_paths=(bad,))


# --- Sandbox configuration contract (deterministic; NOT OS proof) ---------


def test_corrective_sandbox_settings_contract(tmp_path: Path) -> None:
    """10/11/12. The four required sandbox properties, no excludedCommands,
    and filesystem isolation that cannot be disabled."""

    ws = tmp_path / "ws"
    ws.mkdir()
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), ws)
    try:
        settings = json.loads(artifacts.settings_path.read_text(encoding="utf-8"))
        sandbox = settings["sandbox"]
        assert sandbox["enabled"] is True
        assert sandbox["failIfUnavailable"] is True
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["autoAllowBashIfSandboxed"] is False
        assert sandbox["excludedCommands"] == []
        assert sandbox["filesystem"]["disabled"] is False
        # Writes scoped to the workspace, with .git explicitly denied inside it.
        assert sandbox["filesystem"]["allowWrite"] == [str(Path(_os.path.realpath(ws)))]
        assert any(p.endswith("/.git") for p in sandbox["filesystem"]["denyWrite"])
        # The exact-argv PreToolUse guard remains present alongside the sandbox
        # -- selected by matcher, not position: the file-write guard entries
        # are generated before the Bash entry.
        _pretool_hook_by_matcher(settings, "Bash")
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_corrective_setting_sources_locked_down(workspace: Path, tmp_path: Path) -> None:
    """13. TOMTIT's execution boundary must not be widenable by an untrusted
    workspace `.claude/settings.json` / `settings.local.json`. An empty
    `--setting-sources` loads none of user/project/local."""

    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), workspace)
    try:
        argv = ClaudeCodeCLIAdapter()._build_argv(
            "/x/claude",
            base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,)),
            ("Bash(python3 writer.py exact.txt)",),
            artifacts,
        )
        assert "--setting-sources" in argv
        idx = argv.index("--setting-sources")
        assert argv[idx + 1] == ""  # empty => no user/project/local settings files
        assert idx < argv.index("--settings")
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_corrective_project_settings_fixture_is_not_referenced(workspace: Path) -> None:
    """13. A hostile project settings file in the workspace is never named on
    the argv; only TOMTIT's own settings path is passed."""

    claude_dir = workspace / ".claude"
    claude_dir.mkdir()
    hostile = claude_dir / "settings.json"
    hostile.write_text(json.dumps({"sandbox": {"enabled": False, "excludedCommands": ["*"]}}), encoding="utf-8")
    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts((_GUARD_ARGV_COMMAND,), workspace)
    try:
        argv = ClaudeCodeCLIAdapter()._build_argv(
            "/x/claude",
            base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,)),
            ("Bash(python3 writer.py exact.txt)",),
            artifacts,
        )
        assert str(hostile) not in argv
        assert argv[argv.index("--settings") + 1] == str(artifacts.settings_path)
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


# --- M-3: early-failure evidence preserves the approved contract ----------


def test_corrective_early_unavailable_preserves_side_effect_contract(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """22. An UNAVAILABLE executable must still record the approved roots."""

    import tomtit.adapters.claude_code_cli as module

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    result = ClaudeCodeCLIAdapter().run(
        base_request(workspace, allowed_side_effect_paths=("node_modules", ".next"))
    )
    assert result.status is ClaudeCodeCLIStatus.UNAVAILABLE
    assert result.allowed_side_effect_paths == ("node_modules", ".next")
    manifest = build_evidence_manifest(result)
    assert manifest["allowed_side_effect_paths"] == ["node_modules", ".next"]
    assert manifest["allowed_side_effect_path_count"] == 2
    # Observed counts stay zero -- nothing is fabricated.
    assert manifest["observed_authorized_side_effect_count"] == 0


def test_corrective_early_invalid_workspace_preserves_side_effect_contract(tmp_path: Path) -> None:
    """23. A different early-failure path preserves them too."""

    missing = tmp_path / "does-not-exist"
    result = ClaudeCodeCLIAdapter().run(
        base_request(missing, allowed_side_effect_paths=("node_modules",)), executable_path="/usr/bin/true"
    )
    assert result.error_category == "INVALID_WORKSPACE"
    assert result.allowed_side_effect_paths == ("node_modules",)
    assert build_evidence_manifest(result)["allowed_side_effect_path_count"] == 1


def test_corrective_early_guard_failure_preserves_side_effect_contract(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """23. Guard-artifact failure also preserves the approved contract."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )

    def _raise(authorized_commands: object, workspace_arg: object, *args: object) -> object:
        raise OSError("synthetic")

    monkeypatch.setattr(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _raise)
    script = write_fake_executable(tmp_path, "fake-claude-corr-guard", 'import pathlib\npathlib.Path("result.txt").write_text("x")')
    result = ClaudeCodeCLIAdapter().run(
        base_request(
            workspace,
            authorized_commands=(_GUARD_ARGV_COMMAND,),
            allowed_side_effect_paths=("node_modules",),
        ),
        executable_path=str(script),
    )
    assert result.error_category == "EXACT_BASH_GUARD_UNAVAILABLE"
    assert result.allowed_side_effect_paths == ("node_modules",)


# --- Independence + non-regression ----------------------------------------


def test_corrective_real_shape_accounting_still_holds() -> None:
    """Real-shape ACCOUNTING proof only -- explicitly not containment proof."""

    src_paths = tuple(f"src/file{i}.ts" for i in range(7)) + tuple(f"src/new{i}.ts" for i in range(10))
    observed = list(src_paths)
    observed += [f"node_modules/p{i}/index.js" for i in range(2517)]
    observed += [f".next/static/{i}.js" for i in range(192)]
    source, side_effects, unexpected = claude_code_cli_module.classify_workspace_changes(
        tuple(sorted(observed)), src_paths, ("node_modules", ".next")
    )
    assert (len(source), len(side_effects), len(unexpected)) == (17, 2709, 0)


def test_corrective_sandbox_does_not_relax_exact_command_authorization(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """14/15/16. Enabling the sandbox must not auto-approve Bash, and the two
    contracts stay independent in both directions."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path, "fake-claude-corr-indep", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    # side-effect authorization alone: no Bash gate artifacts at all
    only_side_effects = ClaudeCodeCLIAdapter().run(
        base_request(workspace, allowed_side_effect_paths=("node_modules",)), executable_path=str(script)
    )
    assert only_side_effects.exact_bash_guard_enabled is False
    # Settings + lockdown are unconditional now (file-write guard); the Bash
    # guard and --allowedTools are what must stay absent without Bash auth.
    assert "--settings" in only_side_effects.sanitized_argv
    assert "--setting-sources" in only_side_effects.sanitized_argv
    assert "--allowedTools" not in only_side_effects.sanitized_argv

    # Bash authorization alone: guard + sandbox present, no side-effect accounting
    only_bash = ClaudeCodeCLIAdapter().run(
        base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,)), executable_path=str(script)
    )
    assert only_bash.exact_bash_guard_enabled is True
    assert only_bash.allowed_side_effect_paths == ()
    assert only_bash.provider_output_withheld is True  # unchanged by this patch


# ===========================================================================
# Built-in file-tool guard integration
# (FILE_WRITE_GUARD_INTEGRATION_REPORT.md).
#
# Claude's OS-level Bash sandbox covers Bash and its descendants only. The
# built-in mutating file tools run inside the CLI process and are NOT
# sandboxed, so their PreToolUse guard is required on EVERY run -- including
# runs with no authorized Bash command. That makes TOMTIT settings and the
# --setting-sources lockdown unconditional, while the Bash guard, Bash
# sandbox, and --allowedTools stay conditional on authorized_commands.
# ===========================================================================

_FILE_GUARD_SOURCE_PATHS = ("result.txt", "src/app.ts")


def _artifacts_for(workspace: Path, authorized=(), allowed=_FILE_GUARD_SOURCE_PATHS):
    return claude_code_cli_module._write_exact_bash_guard_artifacts(authorized, workspace, allowed)


def _settings_of(artifacts) -> dict:
    return json.loads(artifacts.settings_path.read_text(encoding="utf-8"))


def _matchers_of(settings: dict) -> list[str]:
    return [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]


# --- 26-33. Every run ------------------------------------------------------


def test_file_guard_settings_present_on_every_run(workspace: Path, tmp_path: Path) -> None:
    """26/27/28. --settings and an empty --setting-sources on every run."""

    script = write_fake_executable(
        tmp_path, "fake-claude-fg-every", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))
    assert "--settings" in result.sanitized_argv
    assert "--setting-sources" in result.sanitized_argv
    assert result.sanitized_argv[result.sanitized_argv.index("--setting-sources") + 1] == ""


def test_file_guard_all_four_matchers_present(workspace: Path) -> None:
    """29/30. All four mutating file tools get an explicit matcher entry.

    Hook ordering is not part of the contract; hook identity is. This uses
    `_pretool_hook_by_matcher`, which fails loudly on both "missing" and
    "duplicated" rather than silently taking the first match.
    """

    artifacts = _artifacts_for(workspace)
    try:
        settings = _settings_of(artifacts)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            entry = _pretool_hook_by_matcher(settings, tool)
            cmd = entry["hooks"][0]["command"]
            assert str(claude_code_cli_module._EXACT_FILE_WRITE_GUARD_MODULE_PATH) in cmd
            assert " -E " in f" {cmd} " and " -s " in f" {cmd} "
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_pretool_use_hook_counts_by_authorization_state(workspace: Path) -> None:
    """Explicit hook-count invariant, independent of ordering: exactly one
    Write/Edit/MultiEdit/NotebookEdit hook always; exactly one Bash hook when
    `authorized_commands` is present; zero Bash hooks when it is absent."""

    def _count(settings: dict, matcher: str) -> int:
        return sum(1 for e in settings["hooks"]["PreToolUse"] if e["matcher"] == matcher)

    unauthorized = _artifacts_for(workspace, authorized=())
    try:
        settings = _settings_of(unauthorized)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            assert _count(settings, tool) == 1, tool
        assert _count(settings, "Bash") == 0
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(unauthorized)

    authorized = _artifacts_for(workspace, authorized=(_GUARD_ARGV_COMMAND,))
    try:
        settings = _settings_of(authorized)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            assert _count(settings, tool) == 1, tool
        assert _count(settings, "Bash") == 1
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(authorized)


def test_file_guard_config_contents_are_minimal_and_exact(workspace: Path) -> None:
    """31/32/33. Config carries only the canonical workspace and the exact
    source allowlist -- no prompt, no provider output, no authorized commands,
    and crucially no side-effect roots."""

    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts(
        (("npm", "run", "build"),), workspace, _FILE_GUARD_SOURCE_PATHS
    )
    try:
        raw = artifacts.file_write_config_path.read_text(encoding="utf-8")
        config = json.loads(raw)
        assert set(config) == {"workspace", "allowed_changed_paths"}
        assert config["allowed_changed_paths"] == list(_FILE_GUARD_SOURCE_PATHS)
        assert config["workspace"] == str(Path(os.path.realpath(workspace)))
        for forbidden in ("demo prompt", "npm", "node_modules", "run", "build"):
            assert forbidden not in raw, forbidden
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_file_guard_config_excludes_side_effect_paths(workspace: Path, tmp_path: Path) -> None:
    """35. Side-effect roots must never reach the file-write guard config."""

    script = write_fake_executable(
        tmp_path, "fake-claude-fg-se", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    captured = []
    original = claude_code_cli_module._write_exact_bash_guard_artifacts

    def _capture(auth, ws, allowed=()):
        a = original(auth, ws, allowed)
        captured.append(json.loads(a.file_write_config_path.read_text(encoding="utf-8")))
        return a

    import unittest.mock as _mock

    with _mock.patch.object(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _capture):
        ClaudeCodeCLIAdapter().run(
            base_request(workspace, allowed_side_effect_paths=("node_modules", ".next")),
            executable_path=str(script),
        )
    assert captured
    assert "node_modules" not in json.dumps(captured[0])
    assert ".next" not in json.dumps(captured[0])
    assert captured[0]["allowed_changed_paths"] == ["result.txt"]


# --- 34-40. No authorized_commands ----------------------------------------


def test_file_guard_present_without_bash_authorization(workspace: Path) -> None:
    """34/37/38/39. File guard yes; Bash guard, sandbox and allowedTools no."""

    artifacts = _artifacts_for(workspace, authorized=())
    try:
        settings = _settings_of(artifacts)
        matchers = _matchers_of(settings)
        assert "Write" in matchers and "NotebookEdit" in matchers
        assert "Bash" not in matchers                       # 38
        assert "sandbox" not in settings                     # 39
        assert artifacts.fingerprints_path is None
        assert artifacts.file_write_config_path.is_file()
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_no_bash_authorization_leaves_bash_guard_flag_false(workspace: Path, tmp_path: Path) -> None:
    """40. exact_bash_guard_enabled still means specifically the Bash guard."""

    script = write_fake_executable(
        tmp_path, "fake-claude-fg-flag", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))
    assert result.exact_bash_guard_enabled is False
    assert "--settings" in result.sanitized_argv       # 35
    assert "--setting-sources" in result.sanitized_argv  # 36
    assert "--allowedTools" not in result.sanitized_argv  # 37


# --- 41-47. With authorized_commands --------------------------------------


def test_authorized_commands_run_has_every_layer(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """41-47. File guard + Bash guard + sandbox + allowedTools together."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    script = write_fake_executable(
        tmp_path,
        "fake-claude-fg-auth",
        'import pathlib, sys\nsys.stdout.write("{}")\npathlib.Path("result.txt").write_text("x")',
    )
    result = ClaudeCodeCLIAdapter().run(
        base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,)), executable_path=str(script)
    )
    assert result.exact_bash_guard_enabled is True        # 45
    assert "--allowedTools" in result.sanitized_argv      # 44
    assert "--settings" in result.sanitized_argv
    assert result.authorized_command_fingerprints         # 46
    assert result.provider_output_withheld is True        # 47

    artifacts = _artifacts_for(workspace, authorized=(_GUARD_ARGV_COMMAND,))
    try:
        settings = _settings_of(artifacts)
        matchers = _matchers_of(settings)
        assert "Bash" in matchers                          # 42
        assert "Write" in matchers                         # 41
        sandbox = settings["sandbox"]                      # 43
        assert sandbox["enabled"] is True
        assert sandbox["failIfUnavailable"] is True
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["autoAllowBashIfSandboxed"] is False
        assert sandbox["excludedCommands"] == []
        assert artifacts.fingerprints_path is not None
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


# --- 48-52. Fail-closed startup -------------------------------------------


@pytest.mark.parametrize("mode", ["missing", "symlink", "directory"])
def test_untrusted_file_guard_module_prevents_worker_start(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """48/49/50. A missing, symlinked, or non-regular guard module must stop
    the run before the worker process is started."""

    if mode == "missing":
        bad = tmp_path / "absent-guard.py"
    elif mode == "symlink":
        real = tmp_path / "real-guard.py"
        real.write_text("# attacker\n", encoding="utf-8")
        bad = tmp_path / "linked-guard.py"
        bad.symlink_to(real)
    else:
        bad = tmp_path / "dir-guard.py"
        bad.mkdir()
    monkeypatch.setattr(claude_code_cli_module, "_EXACT_FILE_WRITE_GUARD_MODULE_PATH", bad)

    marker = tmp_path / "started.txt"
    script = write_fake_executable(
        tmp_path,
        f"fake-claude-fg-bad-{mode}",
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('started')\n"
        'pathlib.Path("result.txt").write_text("x")',
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "FILE_WRITE_GUARD_UNAVAILABLE"
    assert not marker.exists(), "worker must never start without the file-write guard"


def test_file_guard_artifact_creation_failure_prevents_worker_start(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """51. Artifact-construction failure fails closed before the worker."""

    def _raise(auth, ws, allowed=()):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _raise)
    marker = tmp_path / "started51.txt"
    script = write_fake_executable(
        tmp_path,
        "fake-claude-fg-artifact-fail",
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('started')\n"
        'pathlib.Path("result.txt").write_text("x")',
    )
    result = ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))
    assert result.status is ClaudeCodeCLIStatus.FAILED
    assert result.error_category == "FILE_WRITE_GUARD_UNAVAILABLE"
    assert not marker.exists()


def test_real_file_guard_module_passes_preflight() -> None:
    """Positive control: the shipped guard module is a trusted regular file."""

    path = claude_code_cli_module._verified_exact_file_write_guard_module_path()
    assert path == claude_code_cli_module._EXACT_FILE_WRITE_GUARD_MODULE_PATH
    assert path.is_file() and not path.is_symlink()


# --- 53-55. Artifact lifecycle --------------------------------------------


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_file_guard_artifacts_removed_after_run(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """53/54. The whole artifact bundle is removed whether the provider
    succeeds or fails."""

    captured: list[Path] = []
    original = claude_code_cli_module._write_exact_bash_guard_artifacts

    def _capture(auth, ws, allowed=()):
        a = original(auth, ws, allowed)
        captured.append(a.directory)
        return a

    monkeypatch.setattr(claude_code_cli_module, "_write_exact_bash_guard_artifacts", _capture)
    body = (
        'import pathlib\npathlib.Path("result.txt").write_text("x")'
        if outcome == "success"
        else "import sys\nsys.exit(3)"
    )
    script = write_fake_executable(tmp_path, f"fake-claude-fg-life-{outcome}", body)
    ClaudeCodeCLIAdapter().run(base_request(workspace), executable_path=str(script))

    assert len(captured) == 1
    assert not captured[0].exists(), "guard artifact directory must be cleaned up"


# --- 56-58. Authorization independence ------------------------------------


def test_side_effect_paths_do_not_authorize_file_writes(workspace: Path) -> None:
    """56. Side-effect roots grant the file guard nothing: a path under an
    authorized side-effect root is still not an authorized write target."""

    from tomtit.adapters.exact_file_write_guard import evaluate as file_evaluate

    config = (Path(os.path.realpath(workspace)), frozenset({"result.txt"}))
    payload = {"tool_name": "Write", "tool_input": {"file_path": "node_modules/pkg/index.js"}}
    assert file_evaluate(payload, config) is False


def test_source_allowlist_does_not_authorize_bash(workspace: Path, tmp_path: Path) -> None:
    """57. An Owner source allowlist creates no Bash authorization."""

    script = write_fake_executable(
        tmp_path, "fake-claude-fg-nobash", 'import pathlib\npathlib.Path("result.txt").write_text("x")'
    )
    result = ClaudeCodeCLIAdapter().run(
        base_request(workspace, allowed_changed_paths=("result.txt", "src/app.ts")),
        executable_path=str(script),
    )
    assert result.exact_bash_guard_enabled is False
    assert result.authorized_command_count == 0
    assert "--allowedTools" not in result.sanitized_argv


def test_authorized_commands_do_not_broaden_source_allowlist(workspace: Path) -> None:
    """58. Authorizing a Bash command adds nothing to the file guard's
    allowlist."""

    artifacts = claude_code_cli_module._write_exact_bash_guard_artifacts(
        (("npm", "run", "build"),), workspace, ("result.txt",)
    )
    try:
        config = json.loads(artifacts.file_write_config_path.read_text(encoding="utf-8"))
        assert config["allowed_changed_paths"] == ["result.txt"]
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


# --- 59-67. Existing security contracts unchanged -------------------------


def test_file_guard_integration_preserves_prompt_placement(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """59/60. Prompt still exactly once, immediately after -p, before
    --allowedTools."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    prompt = "file guard prompt placement check"
    artifacts = _artifacts_for(workspace, authorized=(_GUARD_ARGV_COMMAND,))
    try:
        argv = ClaudeCodeCLIAdapter()._build_argv(
            "/x/claude",
            base_request(workspace, prompt=prompt, authorized_commands=(_GUARD_ARGV_COMMAND,)),
            ("Bash(python3 writer.py exact.txt)",),
            artifacts,
        )
        assert argv.count(prompt) == 1
        assert argv[argv.index("-p") + 1] == prompt
        assert argv.index(prompt) < argv.index("--allowedTools")
        assert argv[argv.index("--setting-sources") + 1] == ""
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_file_guard_integration_preserves_bash_guard_hardening(workspace: Path) -> None:
    """63. The Bash guard hook still uses -E -s and the trusted script path."""

    artifacts = _artifacts_for(workspace, authorized=(_GUARD_ARGV_COMMAND,))
    try:
        settings = _settings_of(artifacts)
        bash_entry = next(e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "Bash")
        cmd = bash_entry["hooks"][0]["command"]
        assert str(claude_code_cli_module._EXACT_BASH_GUARD_MODULE_PATH) in cmd
        assert " -E " in f" {cmd} " and " -s " in f" {cmd} "
    finally:
        claude_code_cli_module._cleanup_exact_bash_guard_artifacts(artifacts)


def test_file_guard_integration_preserves_capability_flags() -> None:
    """66/67. Enable constants untouched by this integration."""

    assert claude_code_cli_module._CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT is False
    assert claude_code_cli_module._CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE is True


def test_file_guard_integration_preserves_provider_output_withholding(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """64. Provider-output withholding still keyed off authorized_commands."""

    monkeypatch.setattr(
        claude_code_cli_module, "_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE", True
    )
    payload = json.dumps({"permission_denials": [{"tool_input": {"command": "rm -rf /tmp/x"}}]})
    script = write_fake_executable(
        tmp_path,
        "fake-claude-fg-withhold",
        f'import pathlib, sys\nsys.stdout.write({payload!r})\npathlib.Path("result.txt").write_text("x")',
    )
    result = ClaudeCodeCLIAdapter().run(
        base_request(workspace, authorized_commands=(_GUARD_ARGV_COMMAND,)), executable_path=str(script)
    )
    assert result.provider_output_withheld is True
    assert "rm -rf" not in result.stdout_excerpt
    assert "rm -rf" not in render_evidence_manifest_json(result)
