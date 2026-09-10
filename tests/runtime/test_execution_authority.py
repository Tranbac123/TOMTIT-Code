"""Adversarial matrix for the REAL-EXECUTION-01 authority guarantees.

Each test is an attack, not a feature check: the question is whether a caller
can obtain provenance, scope, or a canonical success it did not earn. Where a
property is about *completion*, the test drives the real completion path rather
than a helper, because a validator that exists but is never called guarantees
nothing.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tomtit.domain.contracts import WorkerResult
from tomtit.domain.enums import ScopeOperation, WorkerStatus
from tomtit.fakes import FakeCheckRunner, FakeWorker
from tomtit.runtime.events import InMemoryEventLog
from tomtit.runtime.execution import (
    ExecutionBindingError,
    ProductRuntimeCheckRunner,
    ProductRuntimeClaudeWorker,
    ProductRuntimeExecution,
    ScopeAuthorityError,
    create_product_runtime,
)
from tomtit.runtime.runtime import CompletionConditionsNotMet, Runtime
from tomtit.runtime.workspace import (
    DirtyWorkspaceError,
    UnrelatedHistoryError,
    bind_workspace,
)

from ._workspace_fixture import (
    commit_all,
    git,
    head_commit,
    make_repository,
    renaming_provider,
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
    # Different content, so B's commit genuinely differs from A's. With
    # identical content, author and timestamp the two hashes collide and any
    # "different repository" assertion would be vacuously true.
    make_repository(tmp_path / "B", value=99, marker="other")
    return tmp_path / "B"


def _worker(tmp_path: Path, workspace, **overrides):
    values = {
        "workspace": workspace,
        "run_id": "run-x",
        "artifact_directory": tmp_path / "artifacts",
        "executable_path": str(write_provider(tmp_path)),
        "allowed_changed_paths": ("src/fixture.py",),
    }
    values.update(overrides)
    return ProductRuntimeClaudeWorker(**values)


def _checks(tmp_path: Path, workspace):
    # No task_contract: scope is Runtime's canonical decision, not the runner's.
    return ProductRuntimeCheckRunner(
        workspace=workspace, artifact_directory=tmp_path / "artifacts"
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


# -- A1/A2: components bound to a different repository than the run ----------


def test_a1_workspace_and_worker_from_different_repositories(
    tmp_path: Path, repo_a: Path, repo_b: Path
) -> None:
    workspace_a, workspace_b = bind_workspace(repo_a), bind_workspace(repo_b)
    with pytest.raises(ExecutionBindingError, match="worker is bound to"):
        ProductRuntimeExecution(
            workspace=workspace_a,
            worker=_worker(tmp_path, workspace_b),
            check_runner=_checks(tmp_path, workspace_b),
        )


def test_a2_workspace_and_check_runner_from_different_repositories(
    tmp_path: Path, repo_a: Path, repo_b: Path
) -> None:
    workspace_a, workspace_b = bind_workspace(repo_a), bind_workspace(repo_b)
    with pytest.raises(ExecutionBindingError, match="check_runner is bound to"):
        ProductRuntimeExecution(
            workspace=workspace_a,
            worker=_worker(tmp_path, workspace_a),
            check_runner=_checks(tmp_path, workspace_b),
        )


def test_binding_uses_identity_not_path_equality(
    tmp_path: Path, repo_a: Path
) -> None:
    """Equal paths are the easiest thing for a caller to arrange."""

    first, second = bind_workspace(repo_a), bind_workspace(repo_a)
    assert first.path == second.path and first.head_commit == second.head_commit
    with pytest.raises(ExecutionBindingError):
        ProductRuntimeExecution(
            workspace=first,
            worker=_worker(tmp_path, second),
            check_runner=_checks(tmp_path, second),
        )


# -- A3: fixture components cannot inherit a real workspace's provenance -----


@pytest.mark.parametrize("swap", ["worker", "check_runner", "both"])
def test_a3_fake_components_cannot_acquire_real_provenance(
    tmp_path: Path, repo_a: Path, swap: str
) -> None:
    workspace = bind_workspace(repo_a)
    fake_worker = FakeWorker(
        result=WorkerResult(
            status=WorkerStatus.COMPLETED,
            summary="claimed",
            changed_files=("src/fixture.py",),
            commands_executed=[],
            claimed_checks=(),
            remaining_risks=[],
            raw_output_path="a/b.json",
        )
    )
    fake_checks = FakeCheckRunner(artifact_directory=tmp_path / "fake")
    with pytest.raises(ExecutionBindingError):
        ProductRuntimeExecution(
            workspace=workspace,
            worker=(
                fake_worker if swap in {"worker", "both"} else _worker(tmp_path, workspace)
            ),
            check_runner=(
                fake_checks
                if swap in {"check_runner", "both"}
                else _checks(tmp_path, workspace)
            ),
        )


def test_a3_fixture_runtime_gets_no_workspace_provenance(tmp_path: Path) -> None:
    runtime = Runtime(
        run_id="fixture",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=FakeWorker(result=WorkerResult.model_construct()),
        check_runner=FakeCheckRunner(artifact_directory=tmp_path / "fake"),
        event_log=InMemoryEventLog(),
        report_directory=tmp_path / "reports",
    )
    assert runtime.execution is None
    assert runtime.workspace is None
    assert runtime.execution_base_revision is None


# -- A4: no revision can be injected through any public parameter ------------


def test_a4_no_public_parameter_accepts_a_revision(
    tmp_path: Path, repo_a: Path
) -> None:
    for target in (Runtime.__init__, create_product_runtime):
        parameters = inspect.signature(target).parameters
        for forbidden in ("source_revision", "revision", "commit", "head_commit"):
            assert forbidden not in parameters, (target, forbidden)

    with pytest.raises(ValueError):
        Runtime(
            run_id="x",
            task_contract=task_contract(),
            verification_plan=verification_plan(),
            event_log=InMemoryEventLog(),
            report_directory=tmp_path / "reports",
            execution=bind_workspace(repo_a),
        )


def test_a4_mixing_a_binding_with_loose_components_is_refused(
    tmp_path: Path, repo_a: Path
) -> None:
    workspace = bind_workspace(repo_a)
    execution = ProductRuntimeExecution(
        workspace=workspace,
        worker=_worker(tmp_path, workspace),
        check_runner=_checks(tmp_path, workspace),
    )
    with pytest.raises(ValueError, match="already supplies"):
        Runtime(
            run_id="x",
            task_contract=task_contract(),
            verification_plan=verification_plan(),
            worker=FakeWorker(result=WorkerResult.model_construct()),
            event_log=InMemoryEventLog(),
            report_directory=tmp_path / "reports",
            execution=execution,
        )


# -- A5: drift between binding and provider invocation -----------------------


def test_a5_workspace_dirtied_after_binding_stops_the_worker(
    tmp_path: Path, repo_a: Path
) -> None:
    sentinel = tmp_path / "provider-ran"
    workspace = bind_workspace(repo_a)
    worker = _worker(
        tmp_path,
        workspace,
        executable_path=str(write_provider(tmp_path, sentinel=sentinel)),
    )
    (repo_a / "drift.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(DirtyWorkspaceError):
        worker.execute(task_contract(), 1)
    assert not sentinel.exists()


def test_a5_dirty_workspace_refused_before_composition(
    tmp_path: Path, repo_a: Path
) -> None:
    sentinel = tmp_path / "provider-ran"
    (repo_a / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(DirtyWorkspaceError):
        _run(
            tmp_path,
            repo_a,
            executable_path=str(write_provider(tmp_path, sentinel=sentinel)),
        )
    assert not sentinel.exists()
    assert not (tmp_path / "reports" / "report.v1.json").exists()


# -- A6/A15: a committed out-of-scope change must not hide ------------------


def _sneaky_provider(tmp_path: Path) -> Path:
    return write_provider(
        tmp_path,
        "sneaky-claude",
        body=(
            "pathlib.Path('sneaked.txt').write_text('exfiltrated\\n', encoding='utf-8')\n"
            "subprocess.run(['git','add','sneaked.txt'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','sneak'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )


def test_a6_committed_out_of_scope_change_is_visible(
    tmp_path: Path, repo_a: Path
) -> None:
    """git status alone would report only the dirty in-scope file."""

    runtime = _run(tmp_path, repo_a, executable_path=str(_sneaky_provider(tmp_path)))
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    delta = runtime.workspace.collect_delta()
    assert "sneaked.txt" in delta.all_changed_paths
    assert "src/fixture.py" in delta.all_changed_paths
    assert delta.current_head != delta.base_commit


def test_a15_scope_violation_cannot_reach_canonical_success(
    tmp_path: Path, repo_a: Path
) -> None:
    """Driven through the real completion path, not a helper."""

    runtime = _run(tmp_path, repo_a, executable_path=str(_sneaky_provider(tmp_path)))
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    assert runtime.record.state.value != "COMPLETED"
    assert not (tmp_path / "reports" / "report.v1.json").exists()
    assert runtime.publication_receipt is None
    evidence = runtime.record.observed_patch
    assert evidence is not None and "sneaked.txt" in evidence.scope_violations


# -- A7/A8: the adapter allowlist may narrow authority, never widen it -------


def test_a7_literal_outside_task_scope_is_refused_before_execution(
    tmp_path: Path, repo_a: Path
) -> None:
    sentinel = tmp_path / "provider-ran"
    with pytest.raises(ScopeAuthorityError, match="never widen"):
        _run(
            tmp_path,
            repo_a,
            executable_path=str(write_provider(tmp_path, sentinel=sentinel)),
            allowed_changed_paths=("outside.txt",),
        )
    assert not sentinel.exists()


def test_a7_direct_worker_construction_is_also_refused(
    tmp_path: Path, repo_a: Path
) -> None:
    """Bypassing the composition root does not bypass the authority check."""

    sentinel = tmp_path / "provider-ran"
    worker = _worker(
        tmp_path,
        bind_workspace(repo_a),
        allowed_changed_paths=("outside.txt",),
        executable_path=str(write_provider(tmp_path, sentinel=sentinel)),
    )
    with pytest.raises(ScopeAuthorityError):
        worker.execute(task_contract(), 1)
    assert not sentinel.exists()


def test_a8_literal_inside_task_scope_is_accepted(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()
    assert runtime.record.state.value == "COMPLETED"


def test_glob_only_contract_forwards_no_literal(tmp_path: Path, repo_a: Path) -> None:
    """A glob is never mistranslated into the literal preventive guard."""

    worker = _worker(tmp_path, bind_workspace(repo_a), allowed_changed_paths=None)
    assert worker._adapter_allowed_paths(task_contract(pattern="src/*")) == ()
    assert worker._adapter_allowed_paths(
        task_contract(pattern="src/fixture.py")
    ) == ("src/fixture.py",)


# -- A9/A10: dirty output has no revision; clean output resolves exactly ------


def test_a9_dirty_output_publishes_no_revision(tmp_path: Path, repo_a: Path) -> None:
    base = head_commit(repo_a)
    runtime = _run(tmp_path, repo_a)
    runtime.run_scenario_1()

    published = json.loads(
        (tmp_path / "reports" / "report.v1.json").read_text(encoding="utf-8")
    )
    assert published["source_revision"] is None
    assert runtime.execution_base_revision.revision_value == base


def test_a10_clean_final_commit_resolves_to_that_commit(repo_a: Path) -> None:
    """The resolver, exercised at the workspace boundary.

    A production worker may not write Git metadata, so this drives the resolver
    directly rather than manufacturing a worker that commits.
    """

    workspace = bind_workspace(repo_a)
    base = workspace.head_commit
    assert workspace.resolve_output_revision().revision_value == base

    (repo_a / "src" / "fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
    assert workspace.resolve_output_revision() is None

    final = commit_all(repo_a, "done")
    resolved = workspace.resolve_output_revision()
    assert resolved is not None and resolved.revision_value == final != base


# -- A13: unrelated history, rejected on the actual completion path ----------


def test_a13_unrelated_history_fails_on_the_completion_path(
    tmp_path: Path, repo_a: Path
) -> None:
    """Not a helper test: the run is driven to completion and must refuse.

    The provider rewrites history onto an orphan commit whose *tree* differs
    from the base only by the legitimate in-scope edit. So the scope check
    passes and the required check passes; ancestry is the only thing wrong, and
    the run must still fail closed. That isolates the gate: if ancestry were
    validated only in an unused helper, this run would complete.
    """

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
    base = runtime.workspace.head_commit

    with pytest.raises(UnrelatedHistoryError):
        runtime.run_scenario_1()

    delta = runtime.workspace.collect_delta()
    assert delta.base_is_ancestor is False
    assert delta.current_head != base
    # The tree is clean and in scope, so nothing *else* explains the refusal.
    assert delta.final_clean
    assert delta.all_changed_paths == ("src/fixture.py",)

    assert runtime.record.state.value != "COMPLETED"
    assert not (tmp_path / "reports" / "report.v1.json").exists()
    assert runtime.publication_receipt is None


def test_a13_resolver_also_refuses_unrelated_history(repo_a: Path) -> None:
    """And the resolver itself yields no revision for that state."""

    workspace = bind_workspace(repo_a)
    git(repo_a, "checkout", "-q", "--orphan", "rewritten")
    (repo_a / "src" / "fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
    commit_all(repo_a, "rewritten")

    delta = workspace.collect_delta()
    assert delta.final_clean and delta.base_is_ancestor is False
    assert workspace.resolve_output_revision(delta) is None


# -- A14: the delta is a union of all four change classes --------------------


def test_a14_complete_delta_includes_every_change_class(repo_a: Path) -> None:
    workspace = bind_workspace(repo_a)

    (repo_a / "committed.txt").write_text("c\n", encoding="utf-8")
    commit_all(repo_a, "c")
    (repo_a / "staged.txt").write_text("s\n", encoding="utf-8")
    git(repo_a, "add", "staged.txt")
    (repo_a / "src" / "fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
    (repo_a / "untracked.txt").write_text("u\n", encoding="utf-8")

    delta = workspace.collect_delta()
    assert delta.committed_paths == ("committed.txt",)
    assert delta.staged_paths == ("staged.txt",)
    assert delta.unstaged_paths == ("src/fixture.py",)
    assert delta.untracked_paths == ("untracked.txt",)
    assert delta.all_changed_paths == (
        "committed.txt",
        "src/fixture.py",
        "staged.txt",
        "untracked.txt",
    )
    assert delta.has_uncommitted_changes


def test_a14_delta_survives_awkward_paths(repo_a: Path) -> None:
    """NUL-separated parsing, so a space in a path does not split a record."""

    workspace = bind_workspace(repo_a)
    (repo_a / "src" / "a file.py").write_text("x\n", encoding="utf-8")
    assert "src/a file.py" in workspace.collect_delta().all_changed_paths


# -- A16..A19: path movement and deletion ------------------------------------


def test_a16_rename_from_outside_into_scope_is_detected(
    tmp_path: Path, repo_a: Path
) -> None:
    """The exploit: a move launders an out-of-scope path into an in-scope one."""

    runtime = _run(
        tmp_path,
        repo_a,
        executable_path=str(
            renaming_provider(tmp_path, "outside.txt", "src/renamed.py")
        ),
    )
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    delta = runtime.workspace.collect_delta()
    # Both halves of the move are visible, which is the whole point.
    assert "outside.txt" in delta.all_changed_paths
    assert "src/renamed.py" in delta.all_changed_paths
    assert ("outside.txt", "DELETE") in delta.operations

    evidence = runtime.record.observed_patch
    assert evidence is not None and "outside.txt" in evidence.scope_violations
    assert runtime.record.state.value != "COMPLETED"
    assert not (tmp_path / "reports" / "report.v1.json").exists()


def test_a17_rename_from_inside_scope_to_outside_is_detected(
    tmp_path: Path, repo_a: Path
) -> None:
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(pattern="src/*", operations=ALL_OPERATIONS),
        executable_path=str(
            renaming_provider(tmp_path, "src/fixture.py", "escaped.py")
        ),
    )
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert evidence is not None and "escaped.py" in evidence.scope_violations
    assert runtime.record.state.value != "COMPLETED"


def test_a18_rename_within_scope_is_allowed(tmp_path: Path, repo_a: Path) -> None:
    """Both halves inside scope, and the scope permits both operations."""

    provider = write_provider(
        tmp_path,
        "rename-in-scope",
        body=(
            "subprocess.run(['git','mv','src/extra.py','src/moved.py'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','move'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(pattern="src/*", operations=ALL_OPERATIONS),
        executable_path=str(provider),
    )
    runtime.run_scenario_1()

    assert runtime.record.state.value == "COMPLETED"
    evidence = runtime.record.observed_patch
    paths = {item.path for item in evidence.changed_files}
    assert {"src/extra.py", "src/moved.py", "src/fixture.py"} <= paths
    assert evidence.scope_violations == ()


def test_a19_deletion_outside_scope_is_detected(tmp_path: Path, repo_a: Path) -> None:
    provider = write_provider(
        tmp_path,
        "delete-outside",
        body=(
            "subprocess.run(['git','rm','-q','outside.txt'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','del'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    runtime = _run(
        tmp_path,
        repo_a,
        task_contract=task_contract(pattern="src/*", operations=ALL_OPERATIONS),
        executable_path=str(provider),
    )
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert evidence.scope_violations == ("outside.txt",)
    assert runtime.record.state.value != "COMPLETED"


def test_in_scope_deletion_without_delete_authority_is_a_violation(
    tmp_path: Path, repo_a: Path
) -> None:
    """Scope is per-operation, not merely per-path."""

    provider = write_provider(
        tmp_path,
        "delete-in-scope",
        body=(
            "subprocess.run(['git','rm','-q','src/extra.py'], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            "'commit','-qm','del'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )
    # CREATE/MODIFY only: deleting an in-scope path is still unauthorized.
    runtime = _run(tmp_path, repo_a, executable_path=str(provider))
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    evidence = runtime.record.observed_patch
    assert evidence.scope_violations == ("src/extra.py",)
