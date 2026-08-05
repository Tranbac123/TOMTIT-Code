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
import threading
import time
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
