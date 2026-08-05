#!/usr/bin/env python3
"""Gate 3 real spike: one controlled, billable Claude Code CLI invocation.

This script is intentionally NOT part of the pytest suite: it makes exactly one
real Claude Code CLI call (two only if the first fails solely on invocation
syntax) against an isolated temporary Git repository, never against this
project's own source tree.

Owner rerun command:

    python3 scripts/run_claude_code_cli_spike.py

Exit codes:
    0  full spike acceptance criteria passed
    1  the CLI ran but the result did not meet acceptance criteria
    2  the CLI was not discovered, or authentication/invocation was unavailable
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIStatus,
    discover_claude_executable,
    snapshot_workspace_contents,
    write_evidence,
)

REQUIRED_RESULT_CONTENT = "TOMTIT Claude Code CLI adapter smoke test passed"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "gate3"
TASK_PROMPT = (
    "Modify only result.txt.\n\n"
    "Replace its full contents with exactly:\n\n"
    f"{REQUIRED_RESULT_CONTENT}\n\n"
    "Do not modify, create, rename or delete any other file."
)


def _run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def create_temp_git_workspace() -> Path:
    workspace = Path(tempfile.mkdtemp(prefix="tomtit-gate3-spike-"))
    (workspace / "README.md").write_text(
        "# TOMTIT Gate 3 spike fixture\n\nDisposable repository for one bounded smoke test.\n",
        encoding="utf-8",
    )
    (workspace / "result.txt").write_text("not completed\n", encoding="utf-8")
    _run_git("init", "-q", cwd=workspace)
    _run_git(
        "-c",
        "user.name=tomtit-gate3-spike",
        "-c",
        "user.email=tomtit-gate3-spike@example.invalid",
        "add",
        "-A",
        cwd=workspace,
    )
    _run_git(
        "-c",
        "user.name=tomtit-gate3-spike",
        "-c",
        "user.email=tomtit-gate3-spike@example.invalid",
        "commit",
        "-q",
        "-m",
        "Initial spike fixture",
        cwd=workspace,
    )
    return workspace


def looks_like_invocation_syntax_error(stdout_excerpt: str, stderr_excerpt: str, exit_code: int | None) -> bool:
    """A bounded, documented trigger for the one allowed correction retry.

    Covers two cases actually observed for this CLI: a classic argument-parsing
    error on stderr, and a case discovered only during the real spike — the
    CLI's own default model id is stale for this account/version pairing, which
    `--output-format json` reports as a `not_found_error` inside the JSON
    result body rather than as a stderr usage message. Both are invocation
    defects fixable by a flag documented in `claude --help`, not a change in
    task intent, and neither reflects a real task failure.
    """

    if exit_code in (None, 0):
        return False
    lowered_stderr = stderr_excerpt.lower()
    if any(
        marker in lowered_stderr
        for marker in ("unknown option", "unrecognized", "invalid choice", "usage:")
    ):
        return True
    lowered_stdout = stdout_excerpt.lower()
    return '"not_found_error"' in lowered_stdout and '"model:' in lowered_stdout


def build_request(workspace: Path, *, permission_mode: str) -> ClaudeCodeCLIRequest:
    return ClaudeCodeCLIRequest(
        task_id="gate3-real-spike",
        run_id=str(uuid.uuid4()),
        attempt=1,
        goal="Replace result.txt with the required smoke-test string",
        prompt=TASK_PROMPT,
        working_directory=workspace,
        timeout_seconds=180,
        allowed_changed_paths=("result.txt",),
        permission_mode=permission_mode,
    )


def result_content_matches(workspace: Path) -> bool:
    result_path = workspace / "result.txt"
    if not result_path.is_file():
        return False
    content = result_path.read_text(encoding="utf-8")
    return content == REQUIRED_RESULT_CONTENT or content == REQUIRED_RESULT_CONTENT + "\n"


def print_summary(label: str, result) -> None:  # noqa: ANN001
    print(f"--- {label} ---")
    print(f"status={result.status.value} success={result.success}")
    print(f"exit_code={result.exit_code} timed_out={result.timed_out}")
    print(f"duration_ms={result.duration_ms}")
    print(f"changed_paths={list(result.changed_paths)}")
    print(f"unexpected_changed_paths={list(result.unexpected_changed_paths)}")
    if result.error_category:
        print(f"error_category={result.error_category} error_message={result.error_message}")
    excerpt = result.stdout_excerpt.strip()
    if excerpt:
        bounded = excerpt if len(excerpt) <= 300 else excerpt[:300] + "...(truncated for console)"
        print(f"stdout_excerpt={bounded!r}")


def main() -> int:
    executable = discover_claude_executable()
    if executable is None:
        print("BLOCKED: claude executable was not discovered via shutil.which('claude').")
        print("STATUS=BLOCKED_REAL_CLI_UNAVAILABLE")
        return 2

    version_probe = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    cli_version = version_probe.stdout.strip() or version_probe.stderr.strip()
    print(f"Discovered claude executable: {executable}")
    print(f"claude --version -> {cli_version!r} (exit {version_probe.returncode})")

    workspace = create_temp_git_workspace()
    print(f"Isolated temporary Git workspace: {workspace}")

    before_contents = snapshot_workspace_contents(workspace)
    adapter = ClaudeCodeCLIAdapter()

    invocation_log: list[str] = []
    request = build_request(workspace, permission_mode="acceptEdits")
    invocation_log.append("attempt-1: permission_mode=acceptEdits")
    result = adapter.run(request, executable_path=executable)
    print_summary("attempt 1", result)

    retried = False
    if result.status is not ClaudeCodeCLIStatus.SUCCEEDED and looks_like_invocation_syntax_error(
        result.stdout_excerpt, result.stderr_excerpt, result.exit_code
    ):
        print(
            "First invocation failed on an invocation defect (see error_category above), "
            "not a task failure; retrying once with a corrected invocation."
        )
        retried = True
        # Bounded fallback: pin an explicit, --help-documented model alias
        # instead of relying on the CLI's own (stale, in this environment)
        # default model resolution.
        fallback_request = ClaudeCodeCLIRequest(
            task_id=request.task_id,
            run_id=request.run_id,
            attempt=1,
            goal=request.goal,
            prompt=request.prompt,
            working_directory=workspace,
            timeout_seconds=180,
            allowed_changed_paths=("result.txt",),
            permission_mode="acceptEdits",
            model="sonnet",
        )
        invocation_log.append("attempt-2 (invocation-correction retry): model=sonnet")
        result = adapter.run(fallback_request, executable_path=executable)
        print_summary("attempt 2 (invocation-correction retry)", result)

    after_contents = snapshot_workspace_contents(workspace)

    content_ok = result_content_matches(workspace)
    overall_pass = (
        result.status is ClaudeCodeCLIStatus.SUCCEEDED
        and result.success
        and content_ok
        and result.changed_paths == ("result.txt",)
        and result.unexpected_changed_paths == ()
    )

    finalized = write_evidence(
        result,
        before_contents=before_contents,
        after_contents=after_contents,
        output_dir=EVIDENCE_DIR,
    )

    print("--- invocation log ---")
    for entry in invocation_log:
        print(entry)
    print(f"retried_due_to_syntax_error={retried}")
    print(f"result.txt content matches required string: {content_ok}")
    print(f"evidence manifest: {finalized.evidence_manifest_path}")
    print(f"evidence patch: {finalized.patch_path}")
    print(
        f"Temporary workspace retained at {workspace} for inspection; "
        "safe to delete manually."
    )
    print(f"OVERALL_PASS={overall_pass}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
