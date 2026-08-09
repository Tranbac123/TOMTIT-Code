"""Thin product CLI facade over TOMTIT's frozen bounded dogfood runner.

This module validates and canonicalizes product-facing inputs, translates the
request to an explicit subprocess argv, and reports only the terminal outcome
persisted by the frozen runtime. It deliberately owns no execution state,
review policy, repair logic, workspace hashing, or evidence semantics.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from tomtit.adapters.claude_code_cli import (
    AllowedSideEffectPathError,
    normalize_allowed_side_effect_paths,
)


COORDINATOR_ROOT = Path(__file__).resolve().parents[2]
FROZEN_RUNNER = COORDINATOR_ROOT / ".tomtit" / "dogfood" / "run_loop.py"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TRUSTED_TERMINAL_OUTCOMES = frozenset({"COMPLETED", "BLOCKED", "NOT_ACCEPTED", "FAILED"})


class VerifierMode(str, Enum):
    NONE = "NONE"
    MECHANICAL = "MECHANICAL"
    INDEPENDENT_CORRECTNESS = "INDEPENDENT_CORRECTNESS"


class CLIValidationError(ValueError):
    """A stable, pre-launch input validation failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class UnsupportedVerifierMode(CLIValidationError):
    """A valid V0 mode that the frozen runner cannot implement faithfully."""

    def __init__(self, mode: VerifierMode) -> None:
        super().__init__(
            "UNSUPPORTED_VERIFIER_MODE",
            f"{mode.value} is defined by CLI V0 but is not implemented by the frozen bounded runner",
        )
        self.mode = mode


@dataclasses.dataclass(frozen=True, slots=True)
class CLIRequest:
    """Canonical, immutable product request passed to the frozen runtime."""

    workspace: Path
    task_prompt_file: Path
    task_prompt_sha256: str
    allowed_changed_paths: tuple[str, ...]
    allowed_side_effect_paths: tuple[str, ...]
    check_commands: tuple[str, ...]
    verifier_mode: VerifierMode
    allow_one_repair: bool
    implementation_timeout_seconds: int
    review_timeout_seconds: int
    evidence_root: Path
    run_id: str
    baseline_ref: str = "HEAD"

    @property
    def evidence_directory(self) -> Path:
        """The run-specific directory the frozen runner will authoritatively populate."""

        return self.evidence_root / self.run_id


@dataclasses.dataclass(frozen=True, slots=True)
class CLIResult:
    """Concise product result without any invented runtime interpretation."""

    cli_run_id: str
    runtime_exit_code: int | None
    terminal_status: str
    evidence_directory: str
    implementation_attempts: int | None
    review_attempts: int | None
    repair_allowed: bool
    verifier_mode: str
    error_category: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _resolved_path(raw_path: str | Path) -> Path:
    return Path(raw_path).expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_common_directory(repository_root: Path) -> Path | None:
    """Resolve Git's common metadata directory without invoking Git.

    This recognizes both a primary checkout's ``.git`` directory and a
    linked worktree's ``.git`` pointer plus ``commondir`` file. It is used
    only for the narrow self-repository refusal.
    """

    metadata = repository_root / ".git"
    if metadata.is_dir():
        git_directory = metadata.resolve()
    elif metadata.is_file():
        try:
            marker = metadata.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir: "
        if not marker.startswith(prefix):
            return None
        referenced = Path(marker[len(prefix) :])
        git_directory = (
            referenced.resolve()
            if referenced.is_absolute()
            else (repository_root / referenced).resolve()
        )
    else:
        return None

    common_marker = git_directory / "commondir"
    if not common_marker.is_file():
        return git_directory
    try:
        common_value = common_marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not common_value:
        return None
    common_path = Path(common_value)
    return common_path.resolve() if common_path.is_absolute() else (git_directory / common_path).resolve()


def _owning_git_common_directory(path: Path) -> Path | None:
    """Return the nearest enclosing worktree's common Git directory.

    A target may be a normal descendant rather than a repository root. Walk
    upward from that target so a nested unrelated repository wins over any
    more distant enclosing repository, matching Git worktree ownership.
    """

    for candidate in (path, *path.parents):
        common_directory = _git_common_directory(candidate)
        if common_directory is not None:
            return common_directory
    return None


def _reject_self_workspace(workspace: Path, coordinator_root: Path) -> None:
    coordinator_root = coordinator_root.resolve()
    target_common = _owning_git_common_directory(workspace)
    coordinator_common = _owning_git_common_directory(coordinator_root)
    if (
        workspace == coordinator_root
        or (
            target_common is not None
            and coordinator_common is not None
            and target_common == coordinator_common
        )
    ):
        raise CLIValidationError(
            "SELF_REPOSITORY_WORKSPACE",
            "the TOMTIT repository, its worktrees, and their descendants cannot be target workspaces",
        )


def _canonical_paths(
    raw_paths: Sequence[str],
    workspace: Path,
    *,
    required: bool,
    category: str,
) -> tuple[str, ...]:
    if required and not raw_paths:
        raise CLIValidationError(category, "at least one allowed changed path is required")
    try:
        # Reuse the frozen adapter's public, fail-closed relative-path
        # canonicalizer. Its order-preserving deduplication is also the CLI's
        # canonical representation for repeated flags.
        normalized = normalize_allowed_side_effect_paths(raw_paths)
    except AllowedSideEffectPathError as exc:
        raise CLIValidationError(category, exc.message) from None

    for relative_path in normalized:
        resolved = (workspace / relative_path).resolve(strict=False)
        if not _is_within(resolved, workspace):
            raise CLIValidationError(category, "an allowed path resolves outside the workspace")
    return normalized


def _canonical_checks(raw_commands: Sequence[str]) -> tuple[str, ...]:
    commands: list[str] = []
    for command in raw_commands:
        if not isinstance(command, str) or not command.strip():
            raise CLIValidationError("EMPTY_CHECK_COMMAND", "check commands must be non-empty")
        commands.append(command.strip())
    if not commands:
        raise CLIValidationError(
            "NO_CHECK_COMMANDS",
            "INDEPENDENT_CORRECTNESS requires at least one deterministic check command",
        )
    return tuple(commands)


def _positive_timeout(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CLIValidationError("INVALID_TIMEOUT", f"{name} must be a positive integer")
    return value


def _verifier_mode(raw_mode: str | VerifierMode) -> VerifierMode:
    try:
        return raw_mode if isinstance(raw_mode, VerifierMode) else VerifierMode(raw_mode)
    except (TypeError, ValueError):
        raise CLIValidationError(
            "INVALID_VERIFIER_MODE",
            "verifier mode must be NONE, MECHANICAL, or INDEPENDENT_CORRECTNESS",
        ) from None


def build_request(
    *,
    workspace: str | Path,
    task_prompt_file: str | Path,
    allowed_changed_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str] = (),
    check_commands: Sequence[str],
    verifier_mode: str | VerifierMode,
    allow_one_repair: bool,
    implementation_timeout_seconds: int,
    review_timeout_seconds: int,
    evidence_directory: str | Path,
    run_id: str | None = None,
    coordinator_root: Path | None = None,
) -> CLIRequest:
    """Validate raw inputs and return the immutable canonical request."""

    canonical_workspace = _resolved_path(workspace)
    if not canonical_workspace.exists() or not canonical_workspace.is_dir():
        raise CLIValidationError("INVALID_WORKSPACE", "workspace must exist and be a directory")
    _reject_self_workspace(canonical_workspace, coordinator_root or COORDINATOR_ROOT)

    canonical_prompt = _resolved_path(task_prompt_file)
    if not canonical_prompt.is_file():
        raise CLIValidationError("MISSING_TASK_PROMPT", "task prompt file does not exist")
    try:
        prompt_bytes = canonical_prompt.read_bytes()
        prompt_text = prompt_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise CLIValidationError(
            "INVALID_TASK_PROMPT", "task prompt file must be readable UTF-8 text"
        ) from None
    if not prompt_text.strip():
        raise CLIValidationError("EMPTY_TASK_PROMPT", "task prompt must not be empty")

    canonical_mode = _verifier_mode(verifier_mode)
    canonical_changed_paths = _canonical_paths(
        allowed_changed_paths,
        canonical_workspace,
        required=True,
        category="INVALID_ALLOWED_PATH",
    )
    canonical_side_effect_paths = _canonical_paths(
        allowed_side_effect_paths,
        canonical_workspace,
        required=False,
        category="INVALID_SIDE_EFFECT_PATH",
    )
    canonical_checks = _canonical_checks(check_commands)
    implementation_timeout = _positive_timeout(
        implementation_timeout_seconds, "implementation timeout"
    )
    review_timeout = _positive_timeout(review_timeout_seconds, "review timeout")
    if not isinstance(allow_one_repair, bool):
        raise CLIValidationError(
            "INVALID_ALLOW_ONE_REPAIR", "allow_one_repair must be exactly true or false"
        )

    canonical_run_id = str(uuid.uuid4()) if run_id is None else run_id
    if not isinstance(canonical_run_id, str) or not _RUN_ID_PATTERN.fullmatch(canonical_run_id):
        raise CLIValidationError(
            "INVALID_RUN_ID",
            "run id must be 1-128 characters using letters, digits, dot, underscore, or hyphen",
        )

    evidence_root = _resolved_path(evidence_directory)
    if evidence_root.exists() and not evidence_root.is_dir():
        raise CLIValidationError("INVALID_EVIDENCE_DIRECTORY", "evidence path must be a directory")
    run_evidence_directory = evidence_root / canonical_run_id
    resolved_run_evidence_directory = run_evidence_directory.resolve(strict=False)
    if _is_within(resolved_run_evidence_directory, canonical_workspace):
        raise CLIValidationError(
            "INVALID_EVIDENCE_DIRECTORY",
            "run evidence directory must be outside the target workspace",
        )
    if run_evidence_directory.is_symlink():
        raise CLIValidationError(
            "INVALID_EVIDENCE_DIRECTORY",
            "a pre-existing run evidence path may not be a symlink",
        )
    if run_evidence_directory.exists():
        if not run_evidence_directory.is_dir():
            raise CLIValidationError(
                "EVIDENCE_COLLISION", "run evidence path exists and is not a directory"
            )
        try:
            has_existing_evidence = next(run_evidence_directory.iterdir(), None) is not None
        except OSError:
            raise CLIValidationError(
                "INVALID_EVIDENCE_DIRECTORY", "run evidence directory is not readable"
            ) from None
        if has_existing_evidence:
            raise CLIValidationError(
                "EVIDENCE_COLLISION", "run evidence directory already exists and is non-empty"
            )

    return CLIRequest(
        workspace=canonical_workspace,
        task_prompt_file=canonical_prompt,
        task_prompt_sha256=hashlib.sha256(prompt_text.strip().encode("utf-8")).hexdigest(),
        allowed_changed_paths=canonical_changed_paths,
        allowed_side_effect_paths=canonical_side_effect_paths,
        check_commands=canonical_checks,
        verifier_mode=canonical_mode,
        allow_one_repair=allow_one_repair,
        implementation_timeout_seconds=implementation_timeout,
        review_timeout_seconds=review_timeout,
        evidence_root=evidence_root,
        run_id=canonical_run_id,
    )


def build_runtime_argv(
    request: CLIRequest,
    *,
    python_executable: str | Path | None = None,
    frozen_runner: str | Path | None = None,
) -> tuple[str, ...]:
    """Translate a request to the frozen runner's argv without a shell."""

    if request.verifier_mode is not VerifierMode.INDEPENDENT_CORRECTNESS:
        raise UnsupportedVerifierMode(request.verifier_mode)

    argv = [
        str(python_executable or sys.executable),
        str(frozen_runner or FROZEN_RUNNER),
        "--workspace",
        str(request.workspace),
        "--task-prompt-file",
        str(request.task_prompt_file),
        "--baseline-ref",
        request.baseline_ref,
    ]
    for path in request.allowed_changed_paths:
        argv.extend(("--allowed-path", path))
    for command in request.check_commands:
        argv.extend(("--check-command", command))
    for path in request.allowed_side_effect_paths:
        argv.extend(("--allowed-side-effect-path", path))
    argv.extend(
        (
            "--implementation-timeout-seconds",
            str(request.implementation_timeout_seconds),
            "--review-timeout-seconds",
            str(request.review_timeout_seconds),
            "--loop-run-id",
            request.run_id,
            "--evidence-directory",
            str(request.evidence_root),
        )
    )
    if request.allow_one_repair:
        argv.append("--allow-one-repair")
    return tuple(argv)


def _attempt_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _runtime_result(request: CLIRequest) -> dict[str, Any] | None:
    result_path = request.evidence_directory / "loop-result.json"
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("loop_run_id") != request.run_id:
        return None
    return value


def execute(
    request: CLIRequest,
    *,
    subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    python_executable: str | Path | None = None,
    frozen_runner: str | Path | None = None,
) -> CLIResult:
    """Launch the frozen runtime and derive no outcome beyond its evidence."""

    argv = build_runtime_argv(
        request,
        python_executable=python_executable,
        frozen_runner=frozen_runner,
    )
    run_subprocess = subprocess_runner or subprocess.run
    try:
        completed = run_subprocess(
            argv,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return CLIResult(
            cli_run_id=request.run_id,
            runtime_exit_code=None,
            terminal_status="RUNTIME_LAUNCH_FAILURE",
            evidence_directory=str(request.evidence_directory),
            implementation_attempts=None,
            review_attempts=None,
            repair_allowed=request.allow_one_repair,
            verifier_mode=request.verifier_mode.value,
            error_category="RUNTIME_LAUNCH_FAILURE",
            error_message=exc.__class__.__name__,
        )

    runtime_result = _runtime_result(request)
    terminal_status = "RUNTIME_RESULT_UNAVAILABLE"
    implementation_attempts = None
    review_attempts = None
    error_category = None
    error_message = None
    if runtime_result is not None:
        reported_status = runtime_result.get("terminal_outcome")
        if reported_status in _TRUSTED_TERMINAL_OUTCOMES:
            terminal_status = reported_status
        else:
            error_category = "INVALID_RUNTIME_RESULT"
            error_message = "runtime result did not contain a recognized terminal outcome"
        implementation_attempts = _attempt_count(runtime_result.get("implementation_attempts"))
        review_attempts = _attempt_count(runtime_result.get("review_attempts"))

    return CLIResult(
        cli_run_id=request.run_id,
        runtime_exit_code=completed.returncode,
        terminal_status=terminal_status,
        evidence_directory=str(request.evidence_directory),
        implementation_attempts=implementation_attempts,
        review_attempts=review_attempts,
        repair_allowed=request.allow_one_repair,
        verifier_mode=request.verifier_mode.value,
        error_category=error_category,
        error_message=error_message,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tomtit", description="TOMTIT product CLI V0")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="launch one bounded software task")
    run.add_argument("--workspace", required=True)
    run.add_argument("--task-prompt-file", required=True)
    run.add_argument("--allowed-path", action="append", default=[])
    run.add_argument("--allowed-side-effect-path", action="append", default=[])
    run.add_argument("--check-command", action="append", default=[])
    run.add_argument("--verifier-mode", required=True)
    run.add_argument("--allow-one-repair", action="store_true")
    run.add_argument("--implementation-timeout-seconds", required=True, type=int)
    run.add_argument("--review-timeout-seconds", required=True, type=int)
    run.add_argument("--evidence-directory", required=True)
    run.add_argument("--run-id")
    return parser


def _failure_result(args: argparse.Namespace, error: CLIValidationError) -> CLIResult:
    raw_run_id = getattr(args, "run_id", None)
    run_id = raw_run_id if isinstance(raw_run_id, str) and raw_run_id else "UNASSIGNED"
    raw_evidence = getattr(args, "evidence_directory", "")
    evidence = str(_resolved_path(raw_evidence)) if raw_evidence else ""
    raw_mode = getattr(args, "verifier_mode", "")
    mode = raw_mode.value if isinstance(raw_mode, VerifierMode) else str(raw_mode)
    return CLIResult(
        cli_run_id=run_id,
        runtime_exit_code=None,
        terminal_status=error.category,
        evidence_directory=evidence,
        implementation_attempts=None,
        review_attempts=None,
        repair_allowed=bool(getattr(args, "allow_one_repair", False)),
        verifier_mode=mode,
        error_category=error.category,
        error_message=error.message,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = build_request(
            workspace=args.workspace,
            task_prompt_file=args.task_prompt_file,
            allowed_changed_paths=args.allowed_path,
            allowed_side_effect_paths=args.allowed_side_effect_path,
            check_commands=args.check_command,
            verifier_mode=args.verifier_mode,
            allow_one_repair=args.allow_one_repair,
            implementation_timeout_seconds=args.implementation_timeout_seconds,
            review_timeout_seconds=args.review_timeout_seconds,
            evidence_directory=args.evidence_directory,
            run_id=args.run_id,
        )
        result = execute(request)
    except CLIValidationError as exc:
        result = _failure_result(args, exc)
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 2

    print(json.dumps(result.to_dict(), sort_keys=True))
    if result.runtime_exit_code is None:
        return 3
    return result.runtime_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
