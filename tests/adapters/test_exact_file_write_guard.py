"""Offline, deterministic tests for the TOMTIT-owned exact file-write
PreToolUse guard (`tomtit.adapters.exact_file_write_guard`).

Claude's OS-level Bash sandbox does not cover the built-in mutating file tools
(`Write`, `Edit`, `MultiEdit`, `NotebookEdit`), which run inside the CLI
process. This guard is that boundary, enforced before the tool executes.

No test here calls the real Claude service. Most invoke the guard as a real
standalone subprocess -- exactly as the `claude` CLI would -- so the
stdin/exit-code/stderr contract is proven end to end rather than only through
the in-process `evaluate()` helper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tomtit.adapters.exact_file_write_guard import (
    CONFIG_FILE_ENV,
    DENIAL_MESSAGE,
    evaluate,
    load_guard_config,
    main,
)
import tomtit.adapters.exact_file_write_guard as guard_module

_ALLOWED_SOURCE = "src/file.ts"
_ALLOWED_NOTEBOOK = "notebooks/analysis.ipynb"
_SYNTHETIC_SECRET = "SYNTHETIC_FILEGUARD_SECRET_DO_NOT_LEAK"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace with the two Owner-authorized targets plus decoys."""

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "notebooks").mkdir()
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (ws / "src" / "file.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (ws / "src" / "other.ts").write_text("export const b = 2;\n", encoding="utf-8")
    (ws / "notebooks" / "analysis.ipynb").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.txt").write_text("external\n", encoding="utf-8")
    return ws


def _write_config(tmp_path: Path, workspace: Path, allowed: list[str] | None = None) -> Path:
    """The guard's config artifact, written outside the workspace."""

    config_path = tmp_path / "file_write_guard_config.json"
    config_path.write_text(
        json.dumps(
            {
                "workspace": str(Path(os.path.realpath(workspace))),
                "allowed_changed_paths": [_ALLOWED_SOURCE, _ALLOWED_NOTEBOOK] if allowed is None else allowed,
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _payload(tool: str, target: object, *, field: str | None = None) -> dict[str, object]:
    if field is None:
        field = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {field: target}}


def _run_guard(
    config_path: Path | None,
    payload: object,
    *,
    raw_stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the guard as the real standalone subprocess the CLI would spawn."""

    env = {"PATH": "/usr/bin:/bin"}
    if config_path is not None:
        env[CONFIG_FILE_ENV] = str(config_path)
    stdin_text = raw_stdin if raw_stdin is not None else ("" if payload is None else json.dumps(payload))
    return subprocess.run(
        [sys.executable, "-E", "-s", guard_module.__file__],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# 1-4. Exact authorized targets defer (exit 0), for every mutating tool.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,target",
    [
        ("Write", _ALLOWED_SOURCE),
        ("Edit", _ALLOWED_SOURCE),
        ("MultiEdit", _ALLOWED_SOURCE),
        ("NotebookEdit", _ALLOWED_NOTEBOOK),
    ],
)
def test_exact_authorized_target_defers(tmp_path: Path, workspace: Path, tool: str, target: str) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload(tool, target))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_absolute_form_of_authorized_target_defers(tmp_path: Path, workspace: Path) -> None:
    """An absolute path that lands on the authorized relative path is fine."""

    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", str(workspace / _ALLOWED_SOURCE)))
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# 5-12. Path-boundary denials.
# ---------------------------------------------------------------------------


def test_unapproved_in_workspace_path_denied(tmp_path: Path, workspace: Path) -> None:
    """5. Exact allowlist, not a directory-root grant."""

    config = _write_config(tmp_path, workspace)
    for target in ("src/other.ts", "src/file.ts.backup", "src/sub/file.ts", "README.md"):
        proc = _run_guard(config, _payload("Write", target))
        assert proc.returncode == 2, target


def test_absolute_path_outside_workspace_denied(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", str(tmp_path / "outside" / "external.txt")))
    assert proc.returncode == 2


def test_traversal_outside_workspace_denied(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    for target in ("../outside/external.txt", "src/../../outside/external.txt", "../../etc/passwd"):
        proc = _run_guard(config, _payload("Write", target))
        assert proc.returncode == 2, target


def test_git_metadata_denied(tmp_path: Path, workspace: Path) -> None:
    """8/9. `.git` rejected as a component, at any depth, lexical or resolved."""

    config = _write_config(tmp_path, workspace)
    for target in (".git/config", ".git/hooks/pre-commit", "src/.git/config", "a/b/.git/x"):
        proc = _run_guard(config, _payload("Write", target))
        assert proc.returncode == 2, target


def test_symlink_to_outside_workspace_denied(tmp_path: Path, workspace: Path) -> None:
    """10. The lexical path is inside the workspace; the resolved target is not."""

    external = tmp_path / "outside" / "external.txt"
    (workspace / "src" / "file.ts").unlink()
    os.symlink(external, workspace / "src" / "file.ts")
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


def test_symlink_into_git_denied(tmp_path: Path, workspace: Path) -> None:
    """11. An authorized-looking lexical path that resolves into `.git/**`."""

    (workspace / "src" / "file.ts").unlink()
    os.symlink(workspace / ".git" / "config", workspace / "src" / "file.ts")
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


def test_lexical_alias_to_authorized_file_denied(tmp_path: Path, workspace: Path) -> None:
    """12. The documented stricter policy: an alias must not gain permission
    merely by resolving to an authorized file. Otherwise any writable name in
    the workspace becomes a second handle on an allowed path, and the exact
    source allowlist silently widens."""

    os.symlink(workspace / "src" / "file.ts", workspace / "alias.ts")
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", "alias.ts"))
    assert proc.returncode == 2


def test_symlinked_parent_directory_escape_denied(tmp_path: Path, workspace: Path) -> None:
    """A symlinked *parent* must not let an authorized-looking leaf escape."""

    outside_dir = tmp_path / "outside" / "srcdir"
    outside_dir.mkdir()
    (outside_dir / "file.ts").write_text("x", encoding="utf-8")
    import shutil

    shutil.rmtree(workspace / "src")
    os.symlink(outside_dir, workspace / "src")
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# 13-20. Malformed / hostile payload shapes.
# ---------------------------------------------------------------------------


def test_malformed_json_denied(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, None, raw_stdin='{"tool_name": "Write", unterminated')
    assert proc.returncode == 2


def test_empty_stdin_denied(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, None, raw_stdin="")
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_input": {"file_path": _ALLOWED_SOURCE}},                       # 14 missing tool_name
        {"tool_name": 123, "tool_input": {"file_path": _ALLOWED_SOURCE}},     # non-string tool_name
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},               # 15 unsupported tool
        {"tool_name": "Read", "tool_input": {"file_path": _ALLOWED_SOURCE}},  # non-mutating tool
        {"tool_name": "Write"},                                               # 16 missing tool_input
        {"tool_name": "Write", "tool_input": "not-a-dict"},
        {"tool_name": "Write", "tool_input": {}},                             # 17 missing target field
        {"tool_name": "Write", "tool_input": {"file_path": 123}},             # 18 non-string target
        {"tool_name": "Write", "tool_input": {"file_path": None}},
        {"tool_name": "Write", "tool_input": {"file_path": ""}},              # 19 empty target
        {"tool_name": "Write", "tool_input": {"file_path": "src/fi\x00le.ts"}},  # 20 NUL
        ["not", "a", "dict"],
        None,
        "a bare string",
    ],
    ids=[
        "missing-tool_name", "non-string-tool_name", "unsupported-Bash", "non-mutating-Read",
        "missing-tool_input", "non-dict-tool_input", "missing-target-field", "non-string-target",
        "null-target", "empty-target", "nul-target", "list-payload", "null-payload", "string-payload",
    ],
)
def test_malformed_payload_denied(tmp_path: Path, workspace: Path, payload: object) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, payload)
    assert proc.returncode == 2


def test_notebook_edit_using_file_path_denied(tmp_path: Path, workspace: Path) -> None:
    """NotebookEdit's target field is `notebook_path`; supplying `file_path`
    instead must not slip past as a missing-field no-op."""

    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("NotebookEdit", _ALLOWED_NOTEBOOK, field="file_path"))
    assert proc.returncode == 2


def test_write_using_notebook_path_denied(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    proc = _run_guard(config, _payload("Write", _ALLOWED_SOURCE, field="notebook_path"))
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# 21-23. Config artifact failures fail closed.
# ---------------------------------------------------------------------------


def test_missing_config_env_denied(workspace: Path) -> None:
    proc = _run_guard(None, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


def test_missing_config_file_denied(tmp_path: Path, workspace: Path) -> None:
    proc = _run_guard(tmp_path / "does-not-exist.json", _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


def test_malformed_config_denied(tmp_path: Path, workspace: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{", encoding="utf-8")
    proc = _run_guard(bad, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "content",
    [
        json.dumps([]),                                                       # not an object
        json.dumps({"allowed_changed_paths": [_ALLOWED_SOURCE]}),             # no workspace
        json.dumps({"workspace": "relative/path", "allowed_changed_paths": []}),  # non-absolute
        json.dumps({"workspace": "/abs", "allowed_changed_paths": "not-a-list"}),
        json.dumps({"workspace": "/abs", "allowed_changed_paths": [1, 2]}),   # non-string entries
        json.dumps({"workspace": "/abs", "allowed_changed_paths": [""]}),     # empty entry
        json.dumps({"workspace": "", "allowed_changed_paths": []}),
    ],
    ids=["list", "no-workspace", "relative-workspace", "allowlist-not-list",
         "allowlist-non-string", "allowlist-empty-entry", "empty-workspace"],
)
def test_invalid_config_schema_denied(tmp_path: Path, workspace: Path, content: str) -> None:
    bad = tmp_path / "schema.json"
    bad.write_text(content, encoding="utf-8")
    proc = _run_guard(bad, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


def test_empty_allowlist_denies_everything(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace, allowed=[])
    proc = _run_guard(config, _payload("Write", _ALLOWED_SOURCE))
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# 24-25. Unexpected exceptions fail closed (in-process: the subprocess
#        boundary cannot reach these functions).
# ---------------------------------------------------------------------------


def test_unexpected_exception_denied(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(payload: object, config: object) -> bool:
        raise RuntimeError("synthetic unexpected failure")

    monkeypatch.setattr(guard_module, "evaluate", _boom)
    monkeypatch.setenv(CONFIG_FILE_ENV, str(_write_config(tmp_path, workspace)))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(_payload("Write", _ALLOWED_SOURCE))))
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == DENIAL_MESSAGE
    assert "Traceback" not in captured.err
    assert "RuntimeError" not in captured.err


@pytest.mark.parametrize(
    "exc", [KeyboardInterrupt, SystemExit, MemoryError, BaseException], ids=["kbint", "sysexit", "memory", "base"]
)
def test_base_exception_denied(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
) -> None:
    """`BaseException` is caught deliberately: an uncaught KeyboardInterrupt
    (130) or SystemExit(1) is just as non-blocking under Claude's PreToolUse
    contract as a crash, so both must become the blocking exit 2."""

    def _boom(payload: object, config: object) -> bool:
        raise exc()

    monkeypatch.setattr(guard_module, "evaluate", _boom)
    monkeypatch.setenv(CONFIG_FILE_ENV, str(_write_config(tmp_path, workspace)))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(_payload("Write", _ALLOWED_SOURCE))))
    assert main() == 2


def test_stdin_read_failure_denied(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenStdin:
        def read(self) -> str:
            raise OSError("synthetic stdin failure")

    monkeypatch.setenv(CONFIG_FILE_ENV, str(_write_config(tmp_path, workspace)))
    monkeypatch.setattr(sys, "stdin", _BrokenStdin())
    assert main() == 2


# ---------------------------------------------------------------------------
# Output discipline: fixed denial, no path echo, no traceback, only 0/2.
# ---------------------------------------------------------------------------


def test_denial_output_is_fixed_and_never_echoes_the_target(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    secret_target = f"src/{_SYNTHETIC_SECRET}.ts"
    proc = _run_guard(config, _payload("Write", secret_target))
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert _SYNTHETIC_SECRET not in proc.stderr
    assert _SYNTHETIC_SECRET not in proc.stdout
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""


def test_only_exit_codes_zero_and_two_are_reachable(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    observed = set()
    for payload, cfg, raw in [
        (_payload("Write", _ALLOWED_SOURCE), config, None),
        (_payload("Write", "src/other.ts"), config, None),
        (_payload("Write", "../escape"), config, None),
        (None, config, "not json{{{"),
        (_payload("Write", _ALLOWED_SOURCE), None, None),
        (_payload("Write", _ALLOWED_SOURCE), tmp_path / "missing.json", None),
        ({"tool_name": "Unknown"}, config, None),
    ]:
        observed.add(_run_guard(cfg, payload, raw_stdin=raw).returncode)
    assert observed <= {0, 2}
    assert 1 not in observed


def test_guard_writes_no_files(tmp_path: Path, workspace: Path) -> None:
    """The guard must never persist the evaluated path anywhere."""

    config = _write_config(tmp_path, workspace)
    before = set(tmp_path.rglob("*"))
    _run_guard(config, _payload("Write", f"src/{_SYNTHETIC_SECRET}.ts"))
    assert set(tmp_path.rglob("*")) - before == set()


# ---------------------------------------------------------------------------
# load_guard_config / evaluate unit behavior.
# ---------------------------------------------------------------------------


def test_load_guard_config_round_trips(tmp_path: Path, workspace: Path) -> None:
    config = _write_config(tmp_path, workspace)
    loaded = load_guard_config(str(config))
    assert loaded is not None
    root, allowed = loaded
    assert root == Path(os.path.realpath(workspace))
    assert allowed == frozenset({_ALLOWED_SOURCE, _ALLOWED_NOTEBOOK})


def test_load_guard_config_none_for_missing_path() -> None:
    assert load_guard_config(None) is None
    assert load_guard_config("") is None


def test_evaluate_denies_when_config_is_none() -> None:
    """`None` config means "could not load" -- deny, never "unrestricted"."""

    assert evaluate(_payload("Write", _ALLOWED_SOURCE), None) is False


def test_supported_tools_are_exactly_the_four_mutating_tools() -> None:
    assert set(guard_module._MUTATING_FILE_TOOL_TARGET_FIELDS) == {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
    }
    assert guard_module._MUTATING_FILE_TOOL_TARGET_FIELDS["Write"] == "file_path"
    assert guard_module._MUTATING_FILE_TOOL_TARGET_FIELDS["Edit"] == "file_path"
    assert guard_module._MUTATING_FILE_TOOL_TARGET_FIELDS["MultiEdit"] == "file_path"
    assert guard_module._MUTATING_FILE_TOOL_TARGET_FIELDS["NotebookEdit"] == "notebook_path"
