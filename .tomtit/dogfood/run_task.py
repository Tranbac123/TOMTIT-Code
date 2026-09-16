#!/usr/bin/env python3
"""Local-only, one-shot manual dogfood runner for the Gate 3 adapter."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

COORDINATOR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(COORDINATOR_ROOT / "src"))

from tomtit.adapters.claude_code_cli import (  # noqa: E402
    AllowedSideEffectPathError,
    AuthorizedCommandError,
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIResult,
    compute_authorized_command_fingerprint,
    compute_authorized_command_set_fingerprint,
    normalize_allowed_side_effect_paths,
    normalize_authorized_commands,
    render_evidence_manifest_json,
    snapshot_workspace_contents,
    write_evidence,
)


IMPLEMENTATION_ATTEMPT_NUMBER = 1


class ConfigurationError(Exception):
    """A local safety or argument failure before the adapter is called."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded local dogfood task.")
    parser.add_argument("--workspace", required=True, metavar="PATH")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt", metavar="TEXT")
    prompt.add_argument("--prompt-file", metavar="PATH")
    parser.add_argument("--allowed-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--authorized-command", action="append", default=[], metavar="TEXT")
    parser.add_argument("--allowed-side-effect-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--timeout-seconds", type=int, default=300, metavar="INTEGER")
    parser.add_argument("--model", metavar="TEXT")
    parser.add_argument("--permission-mode", default="acceptEdits", metavar="TEXT")
    parser.add_argument("--task-id", metavar="TEXT")
    parser.add_argument("--run-id", metavar="TEXT")
    parser.add_argument("--executable", metavar="PATH")
    parser.add_argument("--evidence-directory", metavar="PATH")
    return parser.parse_args(argv)


def git_output(workspace: Path, *arguments: str) -> tuple[int, str]:
    """Run a fixed Git argv list; no user input is interpolated into a shell."""

    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip()


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_allowed_paths(raw_paths: list[str], workspace: Path) -> tuple[str, ...]:
    if not raw_paths:
        raise ConfigurationError("at least one exact allowed path is required")
    normalized: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path or "\\" in raw_path:
            raise ConfigurationError("allowed paths must be non-empty POSIX relative paths")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute() or raw_path == "." or ".." in candidate.parts:
            raise ConfigurationError("allowed paths must be safe relative paths")
        value = candidate.as_posix()
        if value in ("", ".") or value == ".git" or value.startswith(".git/"):
            raise ConfigurationError("allowed paths may not target Git metadata")
        resolved = (workspace / Path(*candidate.parts)).resolve(strict=False)
        if not is_within(resolved, workspace):
            raise ConfigurationError("allowed path resolves outside the workspace")
        normalized.add(value)
    return tuple(sorted(normalized))


def validate_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser().resolve(strict=False)
    if not workspace.exists() or not workspace.is_dir():
        raise ConfigurationError("workspace must exist and be a directory")
    if workspace == COORDINATOR_ROOT or is_within(workspace, COORDINATOR_ROOT):
        raise ConfigurationError("the TOMTIT-Code repository may not be a target")
    code, inside = git_output(workspace, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside != "true":
        raise ConfigurationError("workspace must be a Git repository")
    code, top_level = git_output(workspace, "rev-parse", "--show-toplevel")
    if code != 0 or Path(top_level).resolve() != workspace:
        raise ConfigurationError("workspace must be the root of its Git repository")
    code, status = git_output(workspace, "status", "--porcelain=v1", "--untracked-files=no")
    if code != 0 or status:
        raise ConfigurationError("workspace tracked worktree and index must be clean")
    code, git_dir = git_output(workspace, "rev-parse", "--git-dir")
    if code != 0:
        raise ConfigurationError("workspace Git metadata could not be located")
    git_path = Path(git_dir)
    if not git_path.is_absolute():
        git_path = (workspace / git_path).resolve()
    active_state = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
    if any((git_path / state).exists() for state in active_state):
        raise ConfigurationError("workspace has an active Git operation")
    code, submodules = git_output(workspace, "submodule", "status", "--recursive")
    if (workspace / ".gitmodules").exists() or code != 0 or submodules:
        raise ConfigurationError("workspace submodule state is not supported by this runner")
    return workspace


def read_task(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        task = args.prompt
    else:
        try:
            task = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError("prompt file could not be read") from exc
    if not task.strip():
        raise ConfigurationError("task prompt must not be empty")
    return task


def build_prompt(task: str, allowed_paths: tuple[str, ...]) -> str:
    paths = "\n".join(f"- {path}" for path in allowed_paths)
    return (
        f"{task}\n\n"
        "Safety constraints for this bounded manual dogfood task:\n"
        "You may modify only these exact paths:\n"
        f"{paths}\n"
        "Do not modify any other path. Do not create a commit. "
        "Run only task-relevant checks. If the task requires an out-of-scope file, stop and explain why."
    )


def evidence_directory(args: argparse.Namespace, run_id: str, workspace: Path) -> Path:
    raw = args.evidence_directory
    directory = Path(raw).expanduser() if raw else COORDINATOR_ROOT / ".tomtit" / "dogfood" / "runs" / run_id
    if not directory.is_absolute():
        directory = (Path.cwd() / directory).resolve(strict=False)
    else:
        directory = directory.resolve(strict=False)
    if is_within(directory, workspace):
        raise ConfigurationError("evidence directory must be outside the target workspace")
    if directory.exists():
        if not directory.is_dir():
            raise ConfigurationError("evidence directory must be a directory")
        if any(directory.iterdir()):
            raise ConfigurationError("evidence directory already exists and is non-empty")
    return directory


def persist_evidence(
    result: ClaudeCodeCLIResult,
    before: dict[str, bytes | None],
    after: dict[str, bytes | None],
    directory: Path,
    allowed_paths: tuple[str, ...],
    workspace: Path,
    authorized_commands: tuple[tuple[str, ...], ...] = (),
) -> ClaudeCodeCLIResult:
    adapter_result = write_evidence(result, before_contents=before, after_contents=after, output_dir=directory)
    adapter_patch = Path(adapter_result.patch_path or "")
    adapter_manifest = Path(adapter_result.evidence_manifest_path or "")
    patch_path = directory / "diff.patch"
    manifest_path = directory / "result.json"
    adapter_patch.replace(patch_path)
    adapter_manifest.replace(manifest_path)
    finalized = dataclasses.replace(
        adapter_result,
        patch_path=str(patch_path),
        evidence_manifest_path=str(manifest_path),
    )
    manifest_path.write_text(render_evidence_manifest_json(finalized), encoding="utf-8")
    receipt = {
        "task_id": finalized.task_id,
        "run_id": finalized.run_id,
        "attempt": IMPLEMENTATION_ATTEMPT_NUMBER,
        "workspace": str(workspace),
        "status": finalized.status.value,
        "success": finalized.success,
        "exit_code": finalized.exit_code,
        "timed_out": finalized.timed_out,
        "provider_invoked": finalized.provider_invoked,
        "duration_ms": finalized.duration_ms,
        "changed_paths": list(finalized.changed_paths),
        "unexpected_changed_paths": list(finalized.unexpected_changed_paths),
        "allowed_changed_paths": list(allowed_paths),
        "error_category": finalized.error_category,
        "error_message": finalized.error_message,
        "adapter_manifest_path": finalized.evidence_manifest_path,
        "adapter_patch_path": finalized.patch_path,
        # Fingerprints only -- never the raw authorized-command argv or a
        # raw rendered Bash rule (Codex corrective review, HIGH finding 2).
        "authorized_command_fingerprints": [
            compute_authorized_command_fingerprint(argv) for argv in authorized_commands
        ],
        "authorized_command_set_fingerprint": (
            compute_authorized_command_set_fingerprint(authorized_commands) if authorized_commands else None
        ),
        "authorized_command_count": len(authorized_commands),
        "authorized_command_cli_rules": list(finalized.authorized_command_cli_rules),
        # Authorized generated side effects. The approved roots themselves
        # are small and Owner-authored, so they are recorded verbatim for
        # inheritance checks; the *observed* generated paths are recorded
        # only as bounded counts plus a small sample.
        "allowed_side_effect_paths": list(finalized.allowed_side_effect_paths),
        "allowed_side_effect_path_count": len(finalized.allowed_side_effect_paths),
        "observed_authorized_side_effect_count": finalized.side_effect_changed_path_count,
        "observed_authorized_side_effect_counts_by_root": {
            root: count for root, count in finalized.side_effect_changed_path_counts_by_root
        },
        "observed_authorized_side_effect_sample": list(finalized.side_effect_changed_paths_sample),
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return finalized


def print_receipt(result: ClaudeCodeCLIResult, directory: Path) -> None:
    print(f"DOGFOOD_RUN_ID={result.run_id}")
    print(f"STATUS={result.status.value}")
    print(f"SUCCESS={str(result.success).lower()}")
    print(f"EXIT_CODE={result.exit_code}")
    print(f"TIMED_OUT={str(result.timed_out).lower()}")
    print(f"DURATION_MS={result.duration_ms}")
    print(f"CHANGED_PATHS={json.dumps(list(result.changed_paths))}")
    print(f"UNEXPECTED_CHANGED_PATHS={json.dumps(list(result.unexpected_changed_paths))}")
    print(f"ERROR_CATEGORY={result.error_category or ''}")
    print(f"AUTHORIZED_COMMAND_CLI_RULES={json.dumps(list(result.authorized_command_cli_rules))}")
    # Counts and per-root totals only -- a real run generated 2709 side-effect
    # paths, which must never be dumped into a terminal summary.
    print(f"ALLOWED_SIDE_EFFECT_PATHS={json.dumps(list(result.allowed_side_effect_paths))}")
    print(f"AUTHORIZED_SIDE_EFFECT_COUNT={result.side_effect_changed_path_count}")
    print(
        "AUTHORIZED_SIDE_EFFECT_COUNTS_BY_ROOT="
        f"{json.dumps({root: count for root, count in result.side_effect_changed_path_counts_by_root})}"
    )
    print(f"EVIDENCE_DIRECTORY={directory}")


def main(
    argv: list[str] | None = None,
    *,
    provider_observer: Callable[[bool | None], None] | None = None,
) -> int:
    args = parse_args(argv)
    try:
        if args.timeout_seconds <= 0:
            raise ConfigurationError("timeout must be positive")
        workspace = validate_workspace(args.workspace)
        allowed_paths = validate_allowed_paths(args.allowed_path, workspace)
        task = read_task(args)
        run_id = args.run_id or str(uuid.uuid4())
        if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
            raise ConfigurationError("run id must be a simple non-empty identifier")
        task_id = args.task_id or f"manual-dogfood-{run_id}"
        directory = evidence_directory(args, run_id, workspace)
        try:
            authorized_commands = normalize_authorized_commands(args.authorized_command)
        except AuthorizedCommandError as exc:
            raise ConfigurationError(f"{exc.category}: {exc.message}") from exc
        # Independent of --authorized-command: approving a command grants no
        # filesystem scope, and approving a side-effect root grants no Bash
        # execution. Both gates must pass on their own.
        try:
            allowed_side_effect_paths = normalize_allowed_side_effect_paths(args.allowed_side_effect_path)
        except AllowedSideEffectPathError as exc:
            raise ConfigurationError(f"{exc.category}: {exc.message}") from exc
    except ConfigurationError:
        if provider_observer is not None:
            provider_observer(False)
        print("STATUS=CONFIGURATION_FAILURE")
        return 2

    request = ClaudeCodeCLIRequest(
        task_id=task_id,
        run_id=run_id,
        attempt=1,
        goal="Owner-directed bounded manual dogfood task",
        prompt=build_prompt(task, allowed_paths),
        working_directory=workspace,
        timeout_seconds=args.timeout_seconds,
        allowed_changed_paths=allowed_paths,
        model=args.model,
        permission_mode=args.permission_mode,
        authorized_commands=authorized_commands,
        allowed_side_effect_paths=allowed_side_effect_paths,
    )
    before = snapshot_workspace_contents(workspace)
    try:
        result = ClaudeCodeCLIAdapter().run(request, executable_path=args.executable)
    except Exception:
        if provider_observer is not None:
            provider_observer(None)
        print("STATUS=INTERNAL_ERROR")
        return 3
    if provider_observer is not None:
        provider_observer(result.provider_invoked)
    after = snapshot_workspace_contents(workspace)
    try:
        finalized = persist_evidence(
            result, before, after, directory, allowed_paths, workspace, authorized_commands
        )
    except Exception:
        print("STATUS=INTERNAL_ERROR")
        return 3
    print_receipt(finalized, directory)
    if finalized.success and not finalized.unexpected_changed_paths:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
