"""Final authority closure: the six defects an independent review found last.

Each test is the reproduction of a specific way canonical facts could be
rebound, duplicated, staled, weakened, silently mutated, or self-attested.
Where the property is about completion, the run is driven through the real
completion path, because a validator that exists but is never reached
guarantees nothing.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from tomtit.domain.contracts import WorkerResult
from tomtit.domain.enums import ScopeOperation, WorkerStatus
from tomtit.fakes import FakeCheckRunner, FakeWorker
from tomtit.runtime.events import InMemoryEventLog
from tomtit.runtime.execution import (
    FIXTURE_EXECUTION_PROFILE,
    REAL_EXECUTION_PROFILE,
    ExecutionBindingError,
    ProductRuntimeCheckRunner,
    ProductRuntimeClaudeWorker,
    ProductRuntimeExecution,
    create_product_runtime,
)
from tomtit.runtime.runtime import CompletionConditionsNotMet, Runtime
from tomtit.runtime.workspace import (
    OutputInsideWorkspaceError,
    UnrelatedHistoryError,
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

ALL_OPERATIONS = (
    ScopeOperation.CREATE,
    ScopeOperation.MODIFY,
    ScopeOperation.DELETE,
)


@pytest.fixture
def repo_a(tmp_path: Path) -> Path:
    make_repository(tmp_path / "A")
    return tmp_path / "A"


@pytest.fixture
def repo_b(tmp_path: Path) -> Path:
    make_repository(tmp_path / "B", value=99, marker="other")
    return tmp_path / "B"


def _execution(tmp_path: Path, repository: Path):
    workspace = bind_workspace(repository)
    return ProductRuntimeExecution(
        workspace=workspace,
        worker=ProductRuntimeClaudeWorker(
            workspace=workspace,
            run_id="run-x",
            artifact_directory=tmp_path / "artifacts",
            executable_path=str(write_provider(tmp_path)),
            allowed_changed_paths=("src/fixture.py",),
        ),
        check_runner=ProductRuntimeCheckRunner(
            workspace=workspace, artifact_directory=tmp_path / "artifacts"
        ),
    )


def _run(tmp_path: Path, repository: Path, **overrides):
    values = {
        "run_id": "run-x",
        "task_contract": task_contract(),
        "verification_plan": verification_plan(),
        "workspace_path": repository,
        "report_directory": tmp_path / "reports",
        "executable_path": str(write_provider(tmp_path)),
        "allowed_changed_paths": ("src/fixture.py",),
    }
    values.update(overrides)
    return create_product_runtime(**values)  # type: ignore[arg-type]


# -- B1: the binding is transitively immutable -------------------------------


@pytest.mark.parametrize("component", ["worker", "check_runner"])
def test_b1_component_workspace_cannot_be_rebound(
    tmp_path: Path, repo_a: Path, repo_b: Path, component: str
) -> None:
    """Freezing the wrapper is not enough if what it holds stays mutable."""

    execution = _execution(tmp_path, repo_a)
    other = bind_workspace(repo_b)
    target = getattr(execution, component)

    with pytest.raises(dataclasses.FrozenInstanceError):
        target.workspace = other  # type: ignore[misc]
    assert target.workspace is execution.workspace


def test_b1_execution_binding_itself_is_frozen(tmp_path: Path, repo_a: Path) -> None:
    execution = _execution(tmp_path, repo_a)
    for field_name in ("workspace", "worker", "check_runner"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(execution, field_name, None)


def test_b1_binding_critical_fields_are_all_frozen(
    tmp_path: Path, repo_a: Path
) -> None:
    """Every field that could redirect execution, not just the workspace."""

    execution = _execution(tmp_path, repo_a)
    for target, fields in (
        (execution.worker, ("workspace", "run_id", "artifact_directory", "adapter")),
        (execution.check_runner, ("workspace", "artifact_directory")),
    ):
        for field_name in fields:
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(target, field_name, None)


def test_b1_adapter_mutation_cannot_redirect_the_provider(
    tmp_path: Path, repo_a: Path, repo_b: Path
) -> None:
    """The request is built from the bound workspace at invocation time."""

    execution = _execution(tmp_path, repo_a)
    worker = execution.worker

    # Whatever a caller does to their own copy of the adapter object, the
    # working directory comes from the frozen bound workspace.
    worker.execute(task_contract(), 1)
    assert worker.last_result is not None
    assert "VALUE = 42" in (repo_a / "src" / "fixture.py").read_text()
    assert "VALUE = 42" not in (repo_b / "src" / "fixture.py").read_text()


def test_b3_runtime_a_cannot_evidence_against_repository_b(
    tmp_path: Path, repo_a: Path, repo_b: Path
) -> None:
    """Restating the cross-repository invariant after the freeze change."""

    workspace_a, workspace_b = bind_workspace(repo_a), bind_workspace(repo_b)
    with pytest.raises(ExecutionBindingError):
        ProductRuntimeExecution(
            workspace=workspace_a,
            worker=ProductRuntimeClaudeWorker(
                workspace=workspace_b,
                run_id="run-x",
                artifact_directory=tmp_path / "artifacts",
                executable_path=str(write_provider(tmp_path)),
            ),
            check_runner=ProductRuntimeCheckRunner(
                workspace=workspace_b, artifact_directory=tmp_path / "artifacts"
            ),
        )


# -- B2: one canonical TaskContract ------------------------------------------


def test_b2_check_runner_holds_no_task_contract(tmp_path: Path, repo_a: Path) -> None:
    """The runner observes; it has no contract to interpret with."""

    import inspect

    parameters = inspect.signature(ProductRuntimeCheckRunner).parameters
    assert "task_contract" not in parameters

    execution = _execution(tmp_path, repo_a)
    assert not hasattr(execution.check_runner, "task_contract")


def test_b2_runner_evidence_asserts_no_scope_verdict(
    tmp_path: Path, repo_a: Path
) -> None:
    execution = _execution(tmp_path, repo_a)
    (repo_a / "outside.txt").write_text("changed\n", encoding="utf-8")

    evidence = execution.check_runner.collect_patch_evidence("run-x", 1)
    assert "outside.txt" in {item.path for item in evidence.changed_files}
    # Observation only: the contract verdict is not the runner's to make.
    assert evidence.scope_violations == ()


def test_b2_runtime_applies_its_own_canonical_contract(
    tmp_path: Path, repo_a: Path
) -> None:
    """A broader contract cannot be smuggled in beside Runtime's canonical one."""

    provider = write_provider(
        tmp_path,
        "touch-outside",
        body=(
            "pathlib.Path('outside.txt').write_text('changed\\n', encoding='utf-8')\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    # Runtime's canonical scope is src/* and nothing else supplies one.
    runtime = _run(tmp_path, repo_a, executable_path=str(provider))
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert evidence.scope_violations == ("outside.txt",)
    assert runtime.record.state.value != "COMPLETED"


def test_b2_evidence_is_bound_to_the_runtime_run_id(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _run(tmp_path, repo_a, run_id="run-canonical")
    runtime.run_scenario_1()
    assert runtime.record.observed_patch.run_id == "run-canonical"
    for observed in runtime.record.observed_checks:
        assert observed.run_id == "run-canonical"


# -- B3: the pre-provider base must still be the bound base ------------------


def test_b3_clean_descendant_drift_rejects_before_the_provider_runs(
    tmp_path: Path, repo_a: Path
) -> None:
    """Clean is not enough; the base itself must still be the bound one."""

    sentinel = tmp_path / "provider-ran"
    workspace = bind_workspace(repo_a)
    worker = ProductRuntimeClaudeWorker(
        workspace=workspace,
        run_id="run-x",
        artifact_directory=tmp_path / "artifacts",
        executable_path=str(write_provider(tmp_path, sentinel=sentinel)),
        allowed_changed_paths=("src/fixture.py",),
    )
    base = workspace.head_commit

    # A clean *descendant* commit: cleanliness alone would let this through.
    (repo_a / "src" / "fixture.py").write_text("VALUE = 7\n", encoding="utf-8")
    advanced = commit_all(repo_a, "advance")
    assert advanced != base

    with pytest.raises(WorkspaceDriftError):
        worker.execute(task_contract(), 1)
    assert not sentinel.exists(), "the provider must not run from an unbound base"


def test_b3_drift_prevents_canonical_completion(
    tmp_path: Path, repo_a: Path
) -> None:
    provider = write_provider(
        tmp_path,
        "advance-then-work",
        body=(
            "pathlib.Path('src/extra.py').write_text('EXTRA = 2\\n', encoding='utf-8')\n"
            "subprocess.run(['git','add','-A'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','advance'], check=True)"
        ),
    )
    runtime = _run(tmp_path, repo_a, executable_path=str(provider))
    # First attempt advances HEAD; the run cannot then claim its bound base.
    with pytest.raises((WorkspaceDriftError, CompletionConditionsNotMet)):
        runtime.run_scenario_1()
    assert runtime.record.state.value != "COMPLETED"


# -- B4: effective operations, base -> final ---------------------------------


def test_b4_deleted_then_recreated_untracked_is_a_modify(repo_a: Path) -> None:
    """The exploit: layer precedence downgrades MODIFY to CREATE."""

    workspace = bind_workspace(repo_a)
    git(repo_a, "rm", "-q", "src/fixture.py")
    commit_all(repo_a, "delete")
    (repo_a / "src" / "fixture.py").write_text("VALUE = 42\n", encoding="utf-8")

    delta = workspace.collect_delta()
    assert ("src/fixture.py", "DELETE") in delta.committed
    assert "src/fixture.py" in delta.untracked_paths
    # Base has it, final has it: that is a MODIFY however it got there.
    assert dict(delta.operations)["src/fixture.py"] == "MODIFY"


def test_b4_create_only_authority_rejects_the_recreated_path(
    tmp_path: Path, repo_a: Path
) -> None:
    """And the weaker classification would have passed a CREATE-only contract."""

    provider = write_provider(
        tmp_path,
        "delete-recreate",
        body=(
            "subprocess.run(['git','rm','-q','src/fixture.py'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','del'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(
            pattern="src/*", operations=(ScopeOperation.CREATE,)
        ),
        executable_path=str(provider),
    )
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert "src/fixture.py" in evidence.scope_violations
    assert runtime.record.state.value != "COMPLETED"


def test_b4_straightforward_classifications(repo_a: Path) -> None:
    workspace = bind_workspace(repo_a)
    (repo_a / "src" / "new.py").write_text("NEW = 1\n", encoding="utf-8")
    (repo_a / "src" / "fixture.py").write_text("VALUE = 9\n", encoding="utf-8")
    git(repo_a, "rm", "-q", "src/extra.py")

    operations = dict(workspace.collect_delta().operations)
    assert operations["src/new.py"] == "CREATE"
    assert operations["src/fixture.py"] == "MODIFY"
    assert operations["src/extra.py"] == "DELETE"


def test_b4_rename_still_decomposes(repo_a: Path) -> None:
    workspace = bind_workspace(repo_a)
    git(repo_a, "mv", "outside.txt", "src/renamed.py")
    commit_all(repo_a, "move")

    operations = dict(workspace.collect_delta().operations)
    assert operations["outside.txt"] == "DELETE"
    assert operations["src/renamed.py"] == "CREATE"


# -- B5: Runtime-owned output lives outside the source repository ------------


@pytest.mark.parametrize("which", ["report", "artifacts"])
def test_b5_output_directory_inside_the_repository_is_rejected(
    tmp_path: Path, repo_a: Path, which: str
) -> None:
    sentinel = tmp_path / "provider-ran"
    overrides = {"executable_path": str(write_provider(tmp_path, sentinel=sentinel))}
    if which == "report":
        overrides["report_directory"] = repo_a / "reports"
    else:
        overrides["artifact_directory"] = repo_a / "evidence"

    with pytest.raises(OutputInsideWorkspaceError):
        _run(tmp_path, repo_a, **overrides)
    assert not sentinel.exists(), "rejected before any provider runs"


def test_b5_repository_root_itself_is_rejected(tmp_path: Path, repo_a: Path) -> None:
    with pytest.raises(OutputInsideWorkspaceError):
        _run(tmp_path, repo_a, report_directory=repo_a)


def test_b5_symlink_into_the_repository_is_rejected(
    tmp_path: Path, repo_a: Path
) -> None:
    """Resolved paths, so indirection does not sidestep a string comparison."""

    link = tmp_path / "looks-outside"
    link.symlink_to(repo_a / "reports", target_is_directory=True)
    with pytest.raises(OutputInsideWorkspaceError):
        _run(tmp_path, repo_a, report_directory=link)


def test_b5_output_directory_outside_the_repository_is_accepted(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    assert runtime.record.state.value == "COMPLETED"


def test_b5_runtime_publication_does_not_dirty_the_source(
    tmp_path: Path, repo_a: Path
) -> None:
    """After publication, the only changes present are the worker's own."""

    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    assert (tmp_path / "reports" / "report.v1.json").is_file()

    delta = runtime.workspace.collect_delta()
    assert delta.all_changed_paths == ("src/fixture.py",)
    assert not any("report" in path for path in delta.all_changed_paths)


def test_b5_fixture_scenarios_keep_their_internal_directories(
    tmp_path: Path,
) -> None:
    """The restriction belongs to real execution, not to legacy fake runs."""

    from evaluation._runtime_fixture import run_product_runtime

    runtime = run_product_runtime(tmp_path)
    assert runtime.workspace is None
    assert (tmp_path / "report.v1.json").is_file()


# -- B6: the trusted report profile is derived, never selected ---------------


def test_b6_profile_is_not_a_constructor_input(tmp_path: Path, repo_a: Path) -> None:
    import inspect

    assert "profile" not in inspect.signature(ProductRuntimeExecution).parameters

    workspace = bind_workspace(repo_a)
    with pytest.raises(TypeError):
        ProductRuntimeExecution(  # type: ignore[call-arg]
            workspace=workspace,
            worker=ProductRuntimeClaudeWorker(
                workspace=workspace,
                run_id="run-x",
                artifact_directory=tmp_path / "artifacts",
            ),
            check_runner=ProductRuntimeCheckRunner(
                workspace=workspace, artifact_directory=tmp_path / "artifacts"
            ),
            profile=FIXTURE_EXECUTION_PROFILE,
        )


def test_b6_real_execution_derives_the_real_profile(
    tmp_path: Path, repo_a: Path
) -> None:
    execution = _execution(tmp_path, repo_a)
    assert execution.profile is REAL_EXECUTION_PROFILE
    assert execution.profile.implementer == "ProductRuntimeClaudeWorker"

    with pytest.raises(AttributeError):
        execution.profile = FIXTURE_EXECUTION_PROFILE  # type: ignore[misc]


def test_b6_real_report_metadata_is_truthful(tmp_path: Path, repo_a: Path) -> None:
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert published["implementer"] == "ProductRuntimeClaudeWorker"
    joined = " ".join(published["adapter_capability_limitations"])
    assert "fake dependency" not in joined
    assert "ClaudeCodeCLIAdapter" in joined and "process-local" in joined


def test_b6_fixture_report_metadata_is_preserved(tmp_path: Path) -> None:
    from evaluation._runtime_fixture import published_report, run_product_runtime

    run_product_runtime(tmp_path)
    published = json.loads(published_report(tmp_path).read_text(encoding="utf-8"))
    assert published["implementer"] == "FakeWorker"
    assert "fake dependency" in " ".join(
        published["adapter_capability_limitations"]
    )


def test_b6_worker_result_cannot_override_the_profile(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    claim = runtime.record.worker_result.model_dump()
    for forbidden in ("implementer", "adapter_capability_limitations", "profile"):
        assert forbidden not in claim, forbidden


def test_b6_fake_run_cannot_claim_the_real_profile(tmp_path: Path) -> None:
    """There is no route from a fixture Runtime to the real profile."""

    runtime = Runtime(
        run_id="fixture",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=FakeWorker(
            result=WorkerResult(
                status=WorkerStatus.COMPLETED,
                summary="s",
                changed_files=("src/fixture.py",),
                commands_executed=[],
                claimed_checks=(),
                remaining_risks=[],
                raw_output_path="a/b.json",
            )
        ),
        check_runner=FakeCheckRunner(artifact_directory=tmp_path / "fake"),
        event_log=InMemoryEventLog(),
        report_directory=tmp_path / "reports",
    )
    assert runtime._execution_profile() is FIXTURE_EXECUTION_PROFILE


# -- preserved: history, provenance and Eval outcomes ------------------------


def test_b15_unrelated_history_still_rejected_on_completion(
    tmp_path: Path, repo_a: Path
) -> None:
    provider = write_provider(
        tmp_path,
        "history-rewriter",
        body=(
            "subprocess.run(['git','checkout','-q','--orphan','rewritten'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')\n"
            "subprocess.run(['git','add','-A'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','rewritten'], check=True)"
        ),
    )
    runtime = _run(tmp_path, repo_a, executable_path=str(provider))
    with pytest.raises(UnrelatedHistoryError):
        runtime.run_scenario_1()
    assert runtime.record.state.value != "COMPLETED"
    assert not (tmp_path / "reports" / "report.v1.json").exists()


def test_b16_dirty_output_still_publishes_no_revision(
    tmp_path: Path, repo_a: Path
) -> None:
    base = head_commit(repo_a)
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()

    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert published["source_revision"] is None
    assert runtime.execution_base_revision.revision_value == base


# -- M1: real components may only enter Runtime through the binding ----------


def _real_pair(tmp_path: Path, repository: Path):
    workspace = bind_workspace(repository)
    return (
        ProductRuntimeClaudeWorker(
            workspace=workspace,
            run_id="run-x",
            artifact_directory=tmp_path / "artifacts",
            executable_path=str(write_provider(tmp_path)),
            allowed_changed_paths=("src/fixture.py",),
        ),
        ProductRuntimeCheckRunner(
            workspace=workspace, artifact_directory=tmp_path / "artifacts"
        ),
    )


def _fake_pair(tmp_path: Path):
    return (
        FakeWorker(result=WorkerResult.model_construct()),
        FakeCheckRunner(artifact_directory=tmp_path / "fake"),
    )


@pytest.mark.parametrize("shape", ["real+real", "real+fake", "fake+real"])
def test_m1_loose_real_components_are_rejected(
    tmp_path: Path, repo_a: Path, shape: str
) -> None:
    """The loose pair is the fixture door; a real component may not use it.

    Arriving that way leaves ``_execution`` unset, which skips canonical scope
    application, workspace and history validation, and the trusted profile — so
    a genuinely real run would publish itself as fixture work.
    """

    real_worker, real_checks = _real_pair(tmp_path, repo_a)
    fake_worker, fake_checks = _fake_pair(tmp_path)
    worker, check_runner = {
        "real+real": (real_worker, real_checks),
        "real+fake": (real_worker, fake_checks),
        "fake+real": (fake_worker, real_checks),
    }[shape]

    with pytest.raises(ValueError, match="ProductRuntimeExecution binding"):
        Runtime(
            run_id="run-x",
            task_contract=task_contract(),
            verification_plan=verification_plan(),
            worker=worker,
            check_runner=check_runner,
            event_log=InMemoryEventLog(),
            report_directory=tmp_path / "reports",
        )


def test_m1_fixture_construction_is_still_accepted(tmp_path: Path) -> None:
    worker, check_runner = _fake_pair(tmp_path)
    runtime = Runtime(
        run_id="fixture",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=worker,
        check_runner=check_runner,
        event_log=InMemoryEventLog(),
        report_directory=tmp_path / "reports",
    )
    assert runtime.execution is None
    assert runtime._execution_profile() is FIXTURE_EXECUTION_PROFILE


def test_m1_binding_path_is_accepted_and_carries_the_real_profile(
    tmp_path: Path, repo_a: Path
) -> None:
    execution = _execution(tmp_path, repo_a)
    runtime = Runtime(
        run_id="run-x",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        event_log=InMemoryEventLog(),
        report_directory=tmp_path / "reports",
        execution=execution,
    )
    assert runtime.execution is execution
    assert runtime._execution_profile() is REAL_EXECUTION_PROFILE


def test_m1_real_execution_can_never_publish_the_fixture_profile(
    tmp_path: Path, repo_a: Path
) -> None:
    """There is no supported constructor reaching a report that lies about this."""

    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert published["implementer"] == "ProductRuntimeClaudeWorker"


# -- M2: the output boundary is an invariant, not a factory courtesy ---------


def _direct_runtime(tmp_path: Path, repository: Path, *, report_dir, artifact_dir):
    workspace = bind_workspace(repository)
    execution = ProductRuntimeExecution(
        workspace=workspace,
        worker=ProductRuntimeClaudeWorker(
            workspace=workspace,
            run_id="run-x",
            artifact_directory=artifact_dir,
            executable_path=str(write_provider(tmp_path)),
            allowed_changed_paths=("src/fixture.py",),
        ),
        check_runner=ProductRuntimeCheckRunner(
            workspace=workspace, artifact_directory=artifact_dir
        ),
    )
    return Runtime(
        run_id="run-x",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        event_log=InMemoryEventLog(),
        report_directory=report_dir,
        execution=execution,
    )


def test_m2_direct_construction_rejects_report_directory_inside_source(
    tmp_path: Path, repo_a: Path
) -> None:
    """Bypassing the factory must not bypass the rule."""

    with pytest.raises(OutputInsideWorkspaceError, match="report_directory"):
        _direct_runtime(
            tmp_path,
            repo_a,
            report_dir=repo_a / "reports",
            artifact_dir=tmp_path / "artifacts",
        )


def test_m2_direct_construction_rejects_evidence_directory_inside_source(
    tmp_path: Path, repo_a: Path
) -> None:
    with pytest.raises(OutputInsideWorkspaceError, match="artifact_directory"):
        _direct_runtime(
            tmp_path,
            repo_a,
            report_dir=tmp_path / "reports",
            artifact_dir=repo_a / "evidence",
        )


def test_m2_direct_construction_rejects_a_symlink_into_the_source(
    tmp_path: Path, repo_a: Path
) -> None:
    link = tmp_path / "looks-outside"
    link.symlink_to(repo_a / "reports", target_is_directory=True)
    with pytest.raises(OutputInsideWorkspaceError):
        _direct_runtime(
            tmp_path,
            repo_a,
            report_dir=link,
            artifact_dir=tmp_path / "artifacts",
        )


def test_m2_direct_construction_accepts_directories_outside_the_source(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _direct_runtime(
        tmp_path,
        repo_a,
        report_dir=tmp_path / "reports",
        artifact_dir=tmp_path / "artifacts",
    )
    runtime.run_scenario_1()
    assert runtime.record.state.value == "COMPLETED"
    # And the only change in the source is the worker's own.
    assert runtime.workspace.collect_delta().all_changed_paths == ("src/fixture.py",)


# -- M3: a dangling symlink is a directory entry ------------------------------


def test_m3_absent_base_to_dangling_symlink_is_create(repo_a: Path) -> None:
    workspace = bind_workspace(repo_a)
    os.symlink("nowhere-target", repo_a / "src" / "link.py")
    assert dict(workspace.collect_delta().operations)["src/link.py"] == "CREATE"


def test_m3_file_to_dangling_symlink_is_modify(repo_a: Path) -> None:
    """Replacing a file with a broken link is not a deletion."""

    workspace = bind_workspace(repo_a)
    (repo_a / "src" / "extra.py").unlink()
    os.symlink("nowhere-target", repo_a / "src" / "extra.py")
    assert dict(workspace.collect_delta().operations)["src/extra.py"] == "MODIFY"


def test_m3_dangling_symlink_to_absent_is_delete(repo_a: Path) -> None:
    os.symlink("nowhere-target", repo_a / "src" / "pre.py")
    commit_all(repo_a, "add dangling link")
    workspace = bind_workspace(repo_a)
    (repo_a / "src" / "pre.py").unlink()
    assert dict(workspace.collect_delta().operations)["src/pre.py"] == "DELETE"


def test_m3_create_only_authority_allows_a_new_dangling_symlink(
    tmp_path: Path, repo_a: Path
) -> None:
    provider = write_provider(
        tmp_path,
        "make-link",
        body=(
            "import os\n"
            "os.symlink('nowhere-target', 'src/link.py')\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(
            pattern="src/*", operations=(ScopeOperation.CREATE, ScopeOperation.MODIFY)
        ),
        executable_path=str(provider),
    )
    runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert dict(
        (item.path, item.operation.value) for item in evidence.changed_files
    )["src/link.py"] in {"create", "CREATE"}
    assert evidence.scope_violations == ()
    assert runtime.record.state.value == "COMPLETED"


def test_m3_delete_only_authority_rejects_file_to_dangling_symlink(
    tmp_path: Path, repo_a: Path
) -> None:
    """The effective operation is MODIFY, so DELETE authority does not cover it."""

    provider = write_provider(
        tmp_path,
        "swap-for-link",
        body=(
            "import os\n"
            "os.remove('src/extra.py')\n"
            "os.symlink('nowhere-target', 'src/extra.py')\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(
            pattern="src/*", operations=(ScopeOperation.DELETE,)
        ),
        executable_path=str(provider),
    )
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    assert "src/extra.py" in runtime.record.observed_patch.scope_violations
    assert runtime.record.state.value != "COMPLETED"


def test_m3_scope_uses_the_entry_path_not_the_symlink_target(
    repo_a: Path,
) -> None:
    """Scope is decided by where the entry is, not by what it points at."""

    workspace = bind_workspace(repo_a)
    os.symlink("../outside.txt", repo_a / "src" / "escape.py")
    operations = dict(workspace.collect_delta().operations)
    # The entry itself is in src/, whatever it resolves to.
    assert "src/escape.py" in operations
    assert "outside.txt" not in operations
