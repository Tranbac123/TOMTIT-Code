"""PRODUCT-RUNTIME-REAL-EXECUTION-01: one real run against a real repository.

Runtime used to execute nothing: a ``FakeWorker`` returned a canned result and
there was no workspace at all. These tests cover the slice that changes that —
a real Git workspace, pinned before execution, worked on by the real adapter,
observed by real checks — and the boundaries that slice does *not* cross.

Nothing here calls a paid model. The provider is a deterministic local script
passed through the adapter's own ``executable_path`` seam, so the adapter layer
under test is the production one.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tomtit.domain.contracts import WorkerResult
from tomtit.fakes import FakeCheckRunner, FakeWorker
from tomtit.runtime.execution import (
    NoObservedChangesError,
    ProductRuntimeCheckRunner,
    ProductRuntimeClaudeWorker,
    ProductRuntimeExecution,
    create_product_runtime,
)
from tomtit.runtime.ports import CheckRunnerPort, WorkerPort
from tomtit.runtime.runtime import Runtime
from tomtit.runtime.workspace import (
    BoundWorkspace,
    DirtyWorkspaceError,
    NotAGitRepositoryError,
    UnbornHeadError,
    WorkspaceDriftError,
    bind_workspace,
)

from ._workspace_fixture import (
    commit_all,
    git,
    head_commit,
    make_repository,
    task_contract,
    verification_plan,
    write_provider,
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    make_repository(tmp_path / "project")
    return tmp_path / "project"


def build_runtime(tmp_path: Path, repository: Path, **overrides):
    values = {
        "run_id": "run-real",
        "task_contract": task_contract(),
        "verification_plan": verification_plan(),
        "workspace_path": repository,
        "report_directory": tmp_path / "reports",
        "executable_path": str(write_provider(tmp_path)),
        # The adapter guard takes literal paths, not the contract's globs.
        "allowed_changed_paths": ("src/fixture.py",),
    }
    values.update(overrides)
    return create_product_runtime(**values)  # type: ignore[arg-type]


# -- ports: fakes and real implementations are interchangeable ---------------


def test_fakes_and_real_components_satisfy_the_same_ports() -> None:
    fake_worker = FakeWorker(result=WorkerResult.model_construct())
    fake_checks = FakeCheckRunner(artifact_directory=Path("."))
    assert isinstance(fake_worker, WorkerPort)
    assert isinstance(fake_checks, CheckRunnerPort)

    for real, methods in (
        (ProductRuntimeClaudeWorker, ("execute",)),
        (ProductRuntimeCheckRunner, ("run", "collect_patch_evidence")),
    ):
        for method in methods:
            assert callable(getattr(real, method)), (real.__name__, method)

    parameters = inspect.signature(Runtime.__init__).parameters
    assert "FakeWorker" not in str(parameters["worker"].annotation)
    assert "FakeCheckRunner" not in str(parameters["check_runner"].annotation)


# -- workspace binding -------------------------------------------------------


def test_non_git_workspace_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAGitRepositoryError):
        bind_workspace(plain)


def test_missing_workspace_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepositoryError):
        bind_workspace(tmp_path / "absent")


def test_unborn_head_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(UnbornHeadError):
        bind_workspace(empty)


def test_subdirectory_is_not_a_workspace_root(repository: Path) -> None:
    with pytest.raises(NotAGitRepositoryError):
        bind_workspace(repository / "src")


def test_clean_workspace_binds_exact_head(repository: Path) -> None:
    bound = bind_workspace(repository)
    assert bound.head_commit == head_commit(repository)
    assert bound.execution_base_revision.revision_type == "GIT_COMMIT"
    assert bound.execution_base_revision.revision_value == bound.head_commit
    # A clean, unchanged tree is exactly represented by its own HEAD.
    assert bound.resolve_output_revision().revision_value == bound.head_commit


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked"])
def test_dirty_workspace_is_refused(repository: Path, kind: str) -> None:
    """§5.3 has no active revision type for uncommitted source."""

    if kind == "modified":
        (repository / "src" / "fixture.py").write_text("dirty\n", encoding="utf-8")
    elif kind == "staged":
        (repository / "src" / "fixture.py").write_text("dirty\n", encoding="utf-8")
        git(repository, "add", "-A")
    else:
        (repository / "stray.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(DirtyWorkspaceError):
        bind_workspace(repository)


def test_bound_workspace_cannot_claim_a_commit_the_repository_is_not_on(
    repository: Path,
) -> None:
    with pytest.raises(WorkspaceDriftError):
        BoundWorkspace(path=repository, head_commit="0" * 40)


def test_head_moving_off_the_base_is_detected(repository: Path) -> None:
    bound = bind_workspace(repository)
    (repository / "src" / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    commit_all(repository, "moved")
    with pytest.raises(WorkspaceDriftError):
        bound.assert_base_unchanged()


# -- source semantics: base is the input, revision is the output -------------


def test_execution_base_is_the_input_and_never_the_report_revision(
    tmp_path: Path, repository: Path
) -> None:
    """PRRE-02: the two facts are distinct and only one reaches the report."""

    base = head_commit(repository)
    runtime = build_runtime(tmp_path, repository)
    assert runtime.execution_base_revision.revision_value == base

    runtime.run_scenario_1()

    # The worker really modified the workspace, so the output is dirty...
    assert "VALUE = 42" in (repository / "src" / "fixture.py").read_text()
    # ...the base it started from is unchanged...
    assert runtime.execution_base_revision.revision_value == base
    # ...and no §4.1 revision describes that output.
    assert runtime.workspace.resolve_output_revision() is None


def test_worker_cannot_override_the_source_revision(
    tmp_path: Path, repository: Path
) -> None:
    """A4: no worker output field reaches provenance."""

    runtime = build_runtime(tmp_path, repository)
    runtime.run_scenario_1()

    worker_result = runtime.record.worker_result
    assert worker_result is not None
    assert "source_revision" not in worker_result.model_dump()

    # Compiled code, so prose in the docstring cannot satisfy or break this.
    reached = set(Runtime._validated_output_revision.__code__.co_names)
    assert "resolve_output_revision" in reached
    assert reached & {"_worker", "worker_result"} == set(), reached


def test_runtime_exposes_no_seam_to_set_a_revision() -> None:
    parameters = inspect.signature(Runtime.__init__).parameters
    assert "source_revision" not in parameters
    assert "report_id_factory" not in parameters
    # A loose workspace parameter is gone: the only route to provenance is the
    # inseparable execution binding.
    assert "workspace" not in parameters
    assert "execution" in parameters


# -- real worker and real check runner ---------------------------------------


def test_real_worker_executes_the_adapter_inside_the_bound_workspace(
    tmp_path: Path, repository: Path
) -> None:
    bound = bind_workspace(repository)
    worker = ProductRuntimeClaudeWorker(
        workspace=bound,
        run_id="run-real",
        artifact_directory=tmp_path / "artifacts",
        executable_path=str(write_provider(tmp_path)),
        allowed_changed_paths=("src/fixture.py",),
    )

    result = worker.execute(task_contract(), 1)

    assert result.status.value == "completed"
    assert "src/fixture.py" in result.changed_files
    assert "VALUE = 42" in (repository / "src" / "fixture.py").read_text()
    assert worker.last_result is not None and worker.last_result.provider_invoked
    raw = json.loads(Path(result.raw_output_path).read_text(encoding="utf-8"))
    assert raw["bound_execution_base"] == bound.head_commit


def test_real_worker_cannot_be_pointed_outside_the_bound_workspace(
    tmp_path: Path, repository: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "victim.txt").write_text("original\n", encoding="utf-8")

    bound = bind_workspace(repository)
    worker = ProductRuntimeClaudeWorker(
        workspace=bound,
        run_id="run-real",
        artifact_directory=tmp_path / "artifacts",
        executable_path=str(write_provider(tmp_path)),
        allowed_changed_paths=("src/fixture.py",),
    )
    assert set(inspect.signature(worker.execute).parameters) == {
        "task_contract",
        "attempt_number",
    }
    worker.execute(task_contract(), 1)
    assert (outside / "victim.txt").read_text() == "original\n"


def test_real_check_runner_executes_a_deterministic_command(
    tmp_path: Path, repository: Path
) -> None:
    bound = bind_workspace(repository)
    contract = task_contract()
    runner = ProductRuntimeCheckRunner(
        workspace=bound, artifact_directory=tmp_path / "artifacts"
    )

    failing = runner.run(contract.required_checks[0], "run-real", 1)
    assert failing.exit_code == 1

    (repository / "src" / "fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
    passing = runner.run(contract.required_checks[0], "run-real", 2)
    assert passing.exit_code == 0
    assert Path(passing.stdout_path).is_file()
    assert Path(passing.stderr_path).is_file()


def test_real_check_runner_refuses_a_cwd_outside_the_workspace(
    tmp_path: Path, repository: Path
) -> None:
    bound = bind_workspace(repository)
    contract = task_contract()
    runner = ProductRuntimeCheckRunner(
        workspace=bound, artifact_directory=tmp_path / "artifacts"
    )
    escaping = contract.required_checks[0].model_copy(update={"cwd_relative": "../.."})
    with pytest.raises(ValueError, match="inside the bound workspace"):
        runner.run(escaping, "run-real", 1)


def test_no_change_means_no_patch_evidence(tmp_path: Path, repository: Path) -> None:
    bound = bind_workspace(repository)
    runner = ProductRuntimeCheckRunner(
        workspace=bound, artifact_directory=tmp_path / "artifacts"
    )
    with pytest.raises(NoObservedChangesError):
        runner.collect_patch_evidence("run-real", 1)


# -- composition root and terminal report ------------------------------------


def test_product_runtime_composition_exists_outside_tests() -> None:
    from tomtit.runtime import execution

    assert inspect.isfunction(execution.create_product_runtime)
    assert Path(inspect.getfile(execution)).parts[-3:] == (
        "tomtit",
        "runtime",
        "execution.py",
    )


def test_terminal_report_of_dirty_output_carries_no_revision(
    tmp_path: Path, repository: Path
) -> None:
    """A9: §4.1 names the output, and a dirty tree has no active one."""

    base = head_commit(repository)
    runtime = build_runtime(tmp_path, repository)
    runtime.run_scenario_1()

    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert published["run_id"] == "run-real"
    assert published["source_revision"] is None
    # The base is known internally and deliberately does not leak into the field.
    assert runtime.execution_base_revision.revision_value == base


def test_run_without_a_workspace_publishes_no_revision(tmp_path: Path) -> None:
    """The fixture scenarios keep their fail-closed behaviour unchanged."""

    from evaluation._runtime_fixture import run_product_runtime

    runtime = run_product_runtime(tmp_path)
    assert runtime.workspace is None
    assert runtime.execution_base_revision is None
    published = json.loads((tmp_path / "report.v1.json").read_text(encoding="utf-8"))
    assert published["source_revision"] is None


def test_product_runtime_does_not_use_dogfood(tmp_path: Path, repository: Path) -> None:
    """Product Runtime and the dogfood orchestrator stay separate systems."""

    import ast

    from tomtit.runtime import execution

    tree = ast.parse(Path(execution.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        for module in modules:
            assert "dogfood" not in module, module
            assert "run_review" not in module and "run_loop" not in module, module

    runtime = build_runtime(tmp_path, repository)
    runtime.run_scenario_1()
    assert runtime.events
    assert not list((tmp_path / "reports").glob("*.jsonl"))


def test_execution_binding_is_exposed_read_only(
    tmp_path: Path, repository: Path
) -> None:
    runtime = build_runtime(tmp_path, repository)
    assert type(runtime.execution) is ProductRuntimeExecution
    assert runtime.workspace is runtime.execution.workspace
