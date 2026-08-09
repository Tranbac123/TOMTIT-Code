from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from tomtit_cli_v0 import (
    CLIValidationError,
    UnsupportedVerifierMode,
    VerifierMode,
    build_request,
    build_runtime_argv,
    execute,
)


@pytest.fixture
def valid_inputs(tmp_path: Path) -> dict[str, object]:
    workspace = tmp_path / "target"
    workspace.mkdir()
    prompt = tmp_path / "task.md"
    prompt.write_text("Implement the bounded change.\n", encoding="utf-8")
    return {
        "workspace": workspace,
        "task_prompt_file": prompt,
        "allowed_changed_paths": ["src/one.py", "src/two.py"],
        "allowed_side_effect_paths": ["build/cache"],
        "check_commands": ["python -m pytest -q", "python -m compileall src"],
        "verifier_mode": "INDEPENDENT_CORRECTNESS",
        "allow_one_repair": False,
        "implementation_timeout_seconds": 300,
        "review_timeout_seconds": 120,
        "evidence_directory": tmp_path / "evidence",
        "run_id": "cli-run-001",
    }


def request_from(values: dict[str, object], **changes: object):
    return build_request(**(values | changes))


def make_repository(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def test_valid_request_produces_expected_argv(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    assert build_runtime_argv(
        request,
        python_executable="/python",
        frozen_runner="/frozen/run_loop.py",
    ) == (
        "/python",
        "/frozen/run_loop.py",
        "--workspace",
        str(request.workspace),
        "--task-prompt-file",
        str(request.task_prompt_file),
        "--baseline-ref",
        "HEAD",
        "--allowed-path",
        "src/one.py",
        "--allowed-path",
        "src/two.py",
        "--check-command",
        "python -m pytest -q",
        "--check-command",
        "python -m compileall src",
        "--allowed-side-effect-path",
        "build/cache",
        "--implementation-timeout-seconds",
        "300",
        "--review-timeout-seconds",
        "120",
        "--loop-run-id",
        "cli-run-001",
        "--evidence-directory",
        str(request.evidence_root),
    )


def test_request_is_immutable_and_canonical(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    assert request.workspace.is_absolute()
    assert request.task_prompt_file.is_absolute()
    assert len(request.task_prompt_sha256) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.run_id = "changed"  # type: ignore[misc]


def test_invalid_workspace_rejected(valid_inputs: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(CLIValidationError, match="workspace") as raised:
        request_from(valid_inputs, workspace=tmp_path / "missing")
    assert raised.value.category == "INVALID_WORKSPACE"


def test_missing_task_prompt_rejected(valid_inputs: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, task_prompt_file=tmp_path / "missing.md")
    assert raised.value.category == "MISSING_TASK_PROMPT"


def test_empty_prompt_rejected(valid_inputs: dict[str, object], tmp_path: Path) -> None:
    prompt = tmp_path / "empty.md"
    prompt.write_text(" \n\t", encoding="utf-8")
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, task_prompt_file=prompt)
    assert raised.value.category == "EMPTY_TASK_PROMPT"


@pytest.mark.parametrize("unsafe", ["../escape.py", "/absolute.py", ".git/config", "a\\b"])
def test_unsafe_changed_path_rejected(valid_inputs: dict[str, object], unsafe: str) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, allowed_changed_paths=[unsafe])
    assert raised.value.category == "INVALID_ALLOWED_PATH"


@pytest.mark.parametrize("unsafe", ["../cache", "/tmp/cache", "a/.git/cache", "cache/"])
def test_unsafe_side_effect_path_rejected(valid_inputs: dict[str, object], unsafe: str) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, allowed_side_effect_paths=[unsafe])
    assert raised.value.category == "INVALID_SIDE_EFFECT_PATH"


def test_symlink_escape_changed_path_rejected(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    workspace = valid_inputs["workspace"]
    assert isinstance(workspace, Path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, allowed_changed_paths=["linked/file.py"])
    assert raised.value.category == "INVALID_ALLOWED_PATH"


def test_invalid_verifier_mode_rejected(valid_inputs: dict[str, object]) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, verifier_mode="OWNER_GATED_CORRECTNESS")
    assert raised.value.category == "INVALID_VERIFIER_MODE"


@pytest.mark.parametrize(
    ("field", "value"),
    [("implementation_timeout_seconds", 0), ("review_timeout_seconds", -1)],
)
def test_non_positive_timeout_rejected(
    valid_inputs: dict[str, object], field: str, value: int
) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, **{field: value})
    assert raised.value.category == "INVALID_TIMEOUT"


@pytest.mark.parametrize("run_id", ["", "../run", "run/id", "white space", "x" * 129])
def test_malformed_run_id_rejected(valid_inputs: dict[str, object], run_id: str) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, run_id=run_id)
    assert raised.value.category == "INVALID_RUN_ID"


def test_repeated_allowed_paths_preserve_order(valid_inputs: dict[str, object]) -> None:
    request = request_from(
        valid_inputs,
        allowed_changed_paths=["z.py", "a.py", "z.py", "m.py"],
    )

    assert request.allowed_changed_paths == ("z.py", "a.py", "m.py")
    argv = build_runtime_argv(request)
    positions = [index for index, value in enumerate(argv) if value == "--allowed-path"]
    assert [argv[index + 1] for index in positions] == ["z.py", "a.py", "m.py"]


def test_subprocess_uses_argv_and_shell_false(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ignored", stderr="")

    execute(request, subprocess_runner=fake_run)

    assert isinstance(observed["argv"], tuple)
    assert observed["shell"] is False
    assert observed["check"] is False


def test_runtime_nonzero_exit_propagated(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 7, stdout="", stderr="runtime failed")

    result = execute(request, subprocess_runner=fake_run)

    assert result.runtime_exit_code == 7
    assert result.terminal_status == "RUNTIME_RESULT_UNAVAILABLE"


def test_zero_exit_without_runtime_result_is_not_completed(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="TERMINAL_OUTCOME=COMPLETED", stderr="")

    result = execute(request, subprocess_runner=fake_run)

    assert result.runtime_exit_code == 0
    assert result.terminal_status == "RUNTIME_RESULT_UNAVAILABLE"
    assert result.terminal_status != "COMPLETED"


def test_runtime_result_is_authority_for_terminal_status(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    def fake_run(argv, **_kwargs):
        request.evidence_directory.mkdir(parents=True)
        (request.evidence_directory / "loop-result.json").write_text(
            json.dumps(
                {
                    "loop_run_id": request.run_id,
                    "terminal_outcome": "COMPLETED",
                    "implementation_attempts": 1,
                    "review_attempts": 1,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = execute(request, subprocess_runner=fake_run)

    assert result.terminal_status == "COMPLETED"
    assert result.implementation_attempts == 1
    assert result.review_attempts == 1


def test_evidence_directory_propagated(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)
    argv = build_runtime_argv(request)
    evidence_flag = argv.index("--evidence-directory")

    assert argv[evidence_flag + 1] == str(request.evidence_root)
    assert execute(
        request,
        subprocess_runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    ).evidence_directory == str(request.evidence_root / request.run_id)


def test_evidence_root_outside_workspace_accepted(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs)

    assert request.evidence_directory.parent == Path(
        valid_inputs["evidence_directory"]
    ).resolve()


def test_run_evidence_directly_inside_workspace_rejected(
    valid_inputs: dict[str, object],
) -> None:
    workspace = valid_inputs["workspace"]
    assert isinstance(workspace, Path)

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, evidence_directory=workspace / "evidence")
    assert raised.value.category == "INVALID_EVIDENCE_DIRECTORY"


def test_run_evidence_symlink_pointing_inside_workspace_rejected(
    valid_inputs: dict[str, object],
) -> None:
    workspace = valid_inputs["workspace"]
    evidence_root = valid_inputs["evidence_directory"]
    run_id = valid_inputs["run_id"]
    assert isinstance(workspace, Path)
    assert isinstance(evidence_root, Path)
    assert isinstance(run_id, str)
    evidence_root.mkdir()
    inside_target = workspace / "evidence-target"
    inside_target.mkdir()
    (evidence_root / run_id).symlink_to(inside_target, target_is_directory=True)

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs)
    assert raised.value.category == "INVALID_EVIDENCE_DIRECTORY"


def test_run_evidence_symlink_pointing_elsewhere_rejected(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    evidence_root = valid_inputs["evidence_directory"]
    run_id = valid_inputs["run_id"]
    assert isinstance(evidence_root, Path)
    assert isinstance(run_id, str)
    evidence_root.mkdir()
    outside_target = tmp_path / "other-evidence"
    outside_target.mkdir()
    (evidence_root / run_id).symlink_to(outside_target, target_is_directory=True)

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs)
    assert raised.value.category == "INVALID_EVIDENCE_DIRECTORY"


def test_preexisting_runtime_result_is_rejected(valid_inputs: dict[str, object]) -> None:
    evidence_root = valid_inputs["evidence_directory"]
    run_id = valid_inputs["run_id"]
    assert isinstance(evidence_root, Path)
    assert isinstance(run_id, str)
    run_directory = evidence_root / run_id
    run_directory.mkdir(parents=True)
    (run_directory / "loop-result.json").write_text(
        '{"terminal_outcome":"COMPLETED"}', encoding="utf-8"
    )

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs)
    assert raised.value.category == "EVIDENCE_COLLISION"


def test_normal_new_outside_workspace_run_evidence_accepted(
    valid_inputs: dict[str, object],
) -> None:
    request = request_from(valid_inputs)

    assert not request.evidence_directory.exists()
    assert request.evidence_directory != request.workspace


def test_allow_one_repair_propagated(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs, allow_one_repair=True)

    assert "--allow-one-repair" in build_runtime_argv(request)
    result = execute(
        request,
        subprocess_runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )
    assert result.repair_allowed is True


@pytest.mark.parametrize("repair_allowed", [False, True])
def test_exact_repair_booleans_are_preserved(
    valid_inputs: dict[str, object], repair_allowed: bool
) -> None:
    request = request_from(valid_inputs, allow_one_repair=repair_allowed)

    assert request.allow_one_repair is repair_allowed


@pytest.mark.parametrize("invalid_value", ["false", "true", 0, 1, None, []])
def test_non_boolean_repair_values_rejected(
    valid_inputs: dict[str, object], invalid_value: object
) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, allow_one_repair=invalid_value)
    assert raised.value.category == "INVALID_ALLOW_ONE_REPAIR"


def test_coordinator_root_rejected(valid_inputs: dict[str, object], tmp_path: Path) -> None:
    coordinator = make_repository(tmp_path / "tomtit")

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, workspace=coordinator, coordinator_root=coordinator)
    assert raised.value.category == "SELF_REPOSITORY_WORKSPACE"


@pytest.mark.parametrize("descendant_name", ["src", "tests"])
def test_coordinator_descendant_rejected(
    valid_inputs: dict[str, object], tmp_path: Path, descendant_name: str
) -> None:
    coordinator = make_repository(tmp_path / "tomtit")
    descendant = coordinator / descendant_name
    descendant.mkdir()

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, workspace=descendant, coordinator_root=coordinator)
    assert raised.value.category == "SELF_REPOSITORY_WORKSPACE"


def make_linked_worktree(tmp_path: Path, target: Path) -> Path:
    common = tmp_path / "main" / ".git"
    common.mkdir(parents=True)
    worktree_git = common / "worktrees" / "target"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    (target / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
    return common.parent


def test_tomtit_linked_worktree_root_rejected(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    target = valid_inputs["workspace"]
    assert isinstance(target, Path)
    coordinator = make_linked_worktree(tmp_path, target)

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, coordinator_root=coordinator)
    assert raised.value.category == "SELF_REPOSITORY_WORKSPACE"


def test_tomtit_linked_worktree_descendant_rejected(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    target = valid_inputs["workspace"]
    assert isinstance(target, Path)
    coordinator = make_linked_worktree(tmp_path, target)
    descendant = target / "src"
    descendant.mkdir()

    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, workspace=descendant, coordinator_root=coordinator)
    assert raised.value.category == "SELF_REPOSITORY_WORKSPACE"


def test_unrelated_repository_root_allowed(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    coordinator = make_repository(tmp_path / "tomtit")
    unrelated = valid_inputs["workspace"]
    assert isinstance(unrelated, Path)
    (unrelated / ".git").mkdir()

    request = request_from(valid_inputs, coordinator_root=coordinator)
    assert request.workspace == unrelated.resolve()


def test_unrelated_repository_descendant_allowed(
    valid_inputs: dict[str, object], tmp_path: Path
) -> None:
    coordinator = make_repository(tmp_path / "tomtit")
    unrelated = valid_inputs["workspace"]
    assert isinstance(unrelated, Path)
    (unrelated / ".git").mkdir()
    descendant = unrelated / "src"
    descendant.mkdir()

    request = request_from(
        valid_inputs,
        workspace=descendant,
        coordinator_root=coordinator,
    )
    assert request.workspace == descendant.resolve()


def test_no_git_write_command_is_generated(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs, check_commands=["git status --short"])
    argv = build_runtime_argv(request)
    generated_git_writes = {
        ("git", "add"),
        ("git", "commit"),
        ("git", "reset"),
        ("git", "stash"),
        ("git", "checkout"),
        ("git", "push"),
        ("git", "merge"),
    }

    assert not any(
        argv[index : index + 2] in generated_git_writes for index in range(len(argv) - 1)
    )


def test_none_verifier_mode_is_explicitly_unsupported(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs, verifier_mode=VerifierMode.NONE)

    with pytest.raises(UnsupportedVerifierMode) as raised:
        build_runtime_argv(request)
    assert raised.value.category == "UNSUPPORTED_VERIFIER_MODE"


def test_mechanical_verifier_mode_is_explicitly_unsupported(
    valid_inputs: dict[str, object],
) -> None:
    request = request_from(valid_inputs, verifier_mode=VerifierMode.MECHANICAL)

    with pytest.raises(UnsupportedVerifierMode) as raised:
        build_runtime_argv(request)
    assert raised.value.category == "UNSUPPORTED_VERIFIER_MODE"


def test_independent_correctness_maps_to_frozen_loop(valid_inputs: dict[str, object]) -> None:
    request = request_from(valid_inputs, verifier_mode=VerifierMode.INDEPENDENT_CORRECTNESS)
    argv = build_runtime_argv(request)

    assert Path(argv[1]).name == "run_loop.py"
    assert request.verifier_mode is VerifierMode.INDEPENDENT_CORRECTNESS


def test_empty_check_command_rejected(valid_inputs: dict[str, object]) -> None:
    with pytest.raises(CLIValidationError) as raised:
        request_from(valid_inputs, check_commands=["  "])
    assert raised.value.category == "EMPTY_CHECK_COMMAND"
