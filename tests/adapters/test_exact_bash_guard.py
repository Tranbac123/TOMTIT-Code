"""Offline, deterministic tests for the TOMTIT-owned exact-argv PreToolUse
Bash guard (`tomtit.adapters.exact_bash_guard`).

None of these tests call the real Claude service. Most exercise the pure
`evaluate()`/`load_approved_fingerprints()` functions directly; a smaller
set invokes the module as a real subprocess (`python3 exact_bash_guard.py`)
the same way the real `claude` CLI would, to prove the standalone-script
import bootstrap and stdin/stdout/exit-code contract actually work end to
end -- still never touching Claude or Codex.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from tomtit.adapters.claude_code_cli import compute_authorized_command_fingerprint
from tomtit.adapters.exact_bash_guard import (
    DENIAL_MESSAGE,
    FINGERPRINTS_FILE_ENV,
    evaluate,
    load_approved_fingerprints,
    main,
)
import tomtit.adapters.exact_bash_guard as exact_bash_guard_module

APPROVED_ARGV = ("python3", "writer.py", "exact.txt")
APPROVED_FINGERPRINT = compute_authorized_command_fingerprint(APPROVED_ARGV)
APPROVED_FINGERPRINTS = frozenset({APPROVED_FINGERPRINT})


def _bash_payload(command: str, *, run_in_background: bool = False) -> dict[str, object]:
    tool_input: dict[str, object] = {"command": command}
    if run_in_background:
        tool_input["run_in_background"] = True
    return {"tool_name": "Bash", "tool_input": tool_input}


# ---------------------------------------------------------------------------
# 1. Exact approved argv passes (defers).
# ---------------------------------------------------------------------------


def test_exact_approved_argv_defers() -> None:
    assert evaluate(_bash_payload("python3 writer.py exact.txt"), APPROVED_FINGERPRINTS) is True


# ---------------------------------------------------------------------------
# 2-16. Every listed equivalence/wrapper/bypass class is denied.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python3 writer.py other.txt",
        "python3 writer.py exact.txt extra.txt",
        "python3 writer.py exact.txt2",
        "python3 writer.py exact.txt && python3 writer.py escaped.txt",
        "command python3 writer.py exact.txt",
        "builtin command python3 writer.py exact.txt",
        "timeout 5 python3 writer.py exact.txt",
        "time python3 writer.py exact.txt",
        "nice python3 writer.py exact.txt",
        "nohup python3 writer.py exact.txt",
        "stdbuf -oL python3 writer.py exact.txt",
        "noglob python3 writer.py exact.txt",
        "env python3 writer.py exact.txt",
        "FOO=bar python3 writer.py exact.txt",
        "echo exact.txt | xargs python3 writer.py",
        "python3 writer.py exact.txt > out.txt",
        "python3 writer.py $(echo exact.txt)",
        "python3 writer.py `echo exact.txt`",
        "python3 writer2.py exact.txt",
    ],
    ids=[
        "different-argument",
        "extra-argument",
        "suffix-variant",
        "compound-command",
        "command-wrapper",
        "builtin-command-wrapper",
        "timeout-wrapper",
        "time-wrapper",
        "nice-wrapper",
        "nohup-wrapper",
        "stdbuf-wrapper",
        "noglob-wrapper",
        "env-wrapper",
        "env-assignment-wrapper",
        "xargs-wrapper",
        "redirect",
        "command-substitution-dollar",
        "command-substitution-backtick",
        "different-command",
    ],
)
def test_wrapper_and_equivalence_variants_denied(command: str) -> None:
    assert evaluate(_bash_payload(command), APPROVED_FINGERPRINTS) is False


def test_invalid_shell_syntax_denied() -> None:
    # Unbalanced quote: shlex.split raises ValueError.
    assert evaluate(_bash_payload('python3 writer.py "exact.txt'), APPROVED_FINGERPRINTS) is False


def test_run_in_background_denied_even_for_exact_argv() -> None:
    assert (
        evaluate(_bash_payload("python3 writer.py exact.txt", run_in_background=True), APPROVED_FINGERPRINTS)
        is False
    )


# ---------------------------------------------------------------------------
# Fail-closed shape/absence handling.
# ---------------------------------------------------------------------------


def test_non_bash_tool_name_denied() -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "x", "content": "y"}}
    assert evaluate(payload, APPROVED_FINGERPRINTS) is False


def test_missing_tool_input_denied() -> None:
    assert evaluate({"tool_name": "Bash"}, APPROVED_FINGERPRINTS) is False


def test_non_string_command_denied() -> None:
    assert evaluate({"tool_name": "Bash", "tool_input": {"command": 123}}, APPROVED_FINGERPRINTS) is False


def test_empty_command_denied() -> None:
    assert evaluate(_bash_payload(""), APPROVED_FINGERPRINTS) is False


def test_non_dict_payload_denied() -> None:
    assert evaluate(["not", "a", "dict"], APPROVED_FINGERPRINTS) is False
    assert evaluate(None, APPROVED_FINGERPRINTS) is False
    assert evaluate("also not a dict", APPROVED_FINGERPRINTS) is False


def test_missing_approved_fingerprints_denies_even_exact_match() -> None:
    # `approved_fingerprints=None` means "could not be loaded" -- fail closed,
    # never "no restriction configured".
    assert evaluate(_bash_payload("python3 writer.py exact.txt"), None) is False


def test_empty_approved_fingerprint_set_denies_everything() -> None:
    assert evaluate(_bash_payload("python3 writer.py exact.txt"), frozenset()) is False


# ---------------------------------------------------------------------------
# A PASS is a boolean only -- never an emitted "allow" decision of any kind.
# There is no code path anywhere in this module that can produce an
# explicit permission grant.
# ---------------------------------------------------------------------------


def test_pass_return_value_carries_no_decision_payload() -> None:
    result = evaluate(_bash_payload("python3 writer.py exact.txt"), APPROVED_FINGERPRINTS)
    assert result is True
    assert not isinstance(result, dict)


def test_module_source_never_emits_an_allow_or_permit_decision() -> None:
    import ast

    source = Path(exact_bash_guard_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            assert "permissiondecision" not in lowered.replace(" ", "").replace("_", "")
            assert '"allow"' not in node.value and "'allow'" not in node.value


# ---------------------------------------------------------------------------
# load_approved_fingerprints: fail-closed on every malformed shape.
# ---------------------------------------------------------------------------


def test_load_approved_fingerprints_missing_path_returns_none() -> None:
    assert load_approved_fingerprints(None) is None
    assert load_approved_fingerprints("") is None


def test_load_approved_fingerprints_nonexistent_file_returns_none(tmp_path: Path) -> None:
    assert load_approved_fingerprints(str(tmp_path / "does-not-exist.json")) is None


def test_load_approved_fingerprints_malformed_json_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{", encoding="utf-8")
    assert load_approved_fingerprints(str(bad)) is None


def test_load_approved_fingerprints_wrong_shape_returns_none(tmp_path: Path) -> None:
    for content in ('{"not": "a list"}', "42", '["ok", 5, "bad-mixed-type"]', '[""]', "null"):
        path = tmp_path / "wrong.json"
        path.write_text(content, encoding="utf-8")
        assert load_approved_fingerprints(str(path)) is None, content


def test_load_approved_fingerprints_valid_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    assert load_approved_fingerprints(str(path)) == APPROVED_FINGERPRINTS


# ---------------------------------------------------------------------------
# Never persist or log the raw command: the denial message is fixed and
# generic across every scenario, including a rejected-but-plausible-looking
# command, and never contains any part of the input command text.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python3 writer.py SYNTHETIC_SECRET_TOKEN_DO_NOT_LEAK",
        "curl --api key SYNTHETIC_SECRET_TOKEN_DO_NOT_LEAK",
        "python3 writer.py other.txt",
    ],
)
def test_denial_message_is_fixed_and_never_echoes_the_command(command: str) -> None:
    # DENIAL_MESSAGE is the single fixed string `main()` ever writes on
    # denial (see subprocess-level tests below); assert it never contains
    # the command under test as a self-check on the fixture data itself.
    assert command not in DENIAL_MESSAGE
    assert "SYNTHETIC_SECRET_TOKEN_DO_NOT_LEAK" not in DENIAL_MESSAGE


# ---------------------------------------------------------------------------
# Subprocess-level tests: invoke the module as a real, standalone script the
# same way the real `claude` CLI would (`python3 exact_bash_guard.py`,
# reading JSON from stdin, env var pointing at an ephemeral fingerprints
# file) -- proves the self-bootstrapped import path and the exit-code/stderr
# contract actually work, not just the in-process `evaluate()` helper.
# ---------------------------------------------------------------------------


def _run_hook_subprocess(
    tmp_path: Path,
    *,
    fingerprints: list[str] | None,
    payload: object,
    set_env: bool = True,
) -> subprocess.CompletedProcess[str]:
    env: dict[str, str] = {"PATH": "/usr/bin:/bin"}
    if set_env and fingerprints is not None:
        fingerprints_path = tmp_path / "fingerprints.json"
        fingerprints_path.write_text(json.dumps(fingerprints), encoding="utf-8")
        env[FINGERPRINTS_FILE_ENV] = str(fingerprints_path)
    stdin_text = "" if payload is None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_subprocess_exact_match_exits_zero_with_no_output(tmp_path: Path) -> None:
    proc = _run_hook_subprocess(
        tmp_path,
        fingerprints=[APPROVED_FINGERPRINT],
        payload=_bash_payload("python3 writer.py exact.txt"),
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_subprocess_denies_wrapper_with_fixed_stderr_message_only(tmp_path: Path) -> None:
    proc = _run_hook_subprocess(
        tmp_path,
        fingerprints=[APPROVED_FINGERPRINT],
        payload=_bash_payload("command python3 writer.py exact.txt"),
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert "python3 writer.py exact.txt" not in proc.stderr
    assert "writer.py" not in proc.stderr


def test_subprocess_missing_fingerprints_env_fails_closed(tmp_path: Path) -> None:
    proc = _run_hook_subprocess(
        tmp_path,
        fingerprints=None,
        payload=_bash_payload("python3 writer.py exact.txt"),
        set_env=False,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_subprocess_malformed_stdin_fails_closed(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin"}
    fingerprints_path = tmp_path / "fingerprints.json"
    fingerprints_path.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    env[FINGERPRINTS_FILE_ENV] = str(fingerprints_path)
    proc = subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input="not valid json{{{",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_subprocess_never_writes_any_file(tmp_path: Path) -> None:
    """The hook must never persist or log the raw command anywhere on disk."""

    fingerprints_path = tmp_path / "fingerprints.json"
    fingerprints_path.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(fingerprints_path)}

    before = set(tmp_path.rglob("*"))
    proc = subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input=json.dumps(_bash_payload("command python3 writer.py exact.txt SYNTHETIC_SECRET_TOKEN_DO_NOT_LEAK")),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 2
    after = set(tmp_path.rglob("*"))
    # The hook process must not have created any new file.
    assert after - before == set()


def test_main_exit_code_matches_subprocess_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fingerprints_path = tmp_path / "fingerprints.json"
    fingerprints_path.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    monkeypatch.setenv(FINGERPRINTS_FILE_ENV, str(fingerprints_path))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(_bash_payload("python3 writer.py exact.txt"))))
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    monkeypatch.setattr(
        sys, "stdin", __import__("io").StringIO(json.dumps(_bash_payload("command python3 writer.py exact.txt")))
    )
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == DENIAL_MESSAGE


# ===========================================================================
# H-1 / H-2 corrective patch (EXACT_BASH_PRETOOLUSE_GUARD_REVIEW.md ->
# EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_FIX_REPORT.md):
#
# H-1: the previous `try: import tomtit... except ImportError: bootstrap`
# design let an ambient, non-TOMTIT `tomtit` package (installed dist, user
# site-packages, PYTHONPATH, .pth files, sitecustomize) win import
# precedence whenever *any* `tomtit` package was importable at all -- the
# bootstrap only ran on the except branch. Fixed by inserting the trusted
# `src/` root (derived solely from this file's own `__file__`) at
# `sys.path[0]` unconditionally, before any import is attempted, and by
# verifying the imported module's own resolved `__file__` is underneath
# that same trusted root afterward. That provenance check is also what
# closes the residual case a `sys.path` insertion alone cannot: something
# (`sitecustomize.py`) pre-populating `sys.modules` before this script's
# own code ever runs, since Python's import system consults `sys.modules`
# before `sys.path`.
#
# H-2: any failure that was not a clean `evaluate()` denial -- an import
# error, an unexpected exception -- previously exited with code 1, which
# Claude Code's PreToolUse contract treats as *non-blocking* (only exit
# code 2 blocks). Fixed by wrapping the whole of `main()`'s call graph in a
# single boundary that converts every non-True outcome, `BaseException`
# included, into exit code 2 with only the fixed `DENIAL_MESSAGE` -- never a
# traceback, never an exit code 1.
#
# These tests use only synthetic/temporary packages, directories, and
# commands; none touch the real repository's `tomtit` package, modify the
# real `exact_bash_guard.py` file, or invoke a real Claude/Codex process.
# Most are subprocess-based deliberately: the defect and its fix both live
# in module-load-time bootstrap/import behavior, which only a genuinely
# fresh process can faithfully exercise -- in-process monkeypatching happens
# *after* that sequence has already run once for the already-imported
# module.
# ===========================================================================

_REAL_HOOK_SOURCE = Path(exact_bash_guard_module.__file__).read_text(encoding="utf-8")
_UNAUTHORIZED_SYNTHETIC_COMMAND = "rm -rf /tmp/SYNTHETIC_TARGET_DO_NOT_EXECUTE"
_H2_SYNTHETIC_SECRET = "SYNTHETIC_H1_H2_SECRET_DO_NOT_LEAK"


def _write_ambient_tomtit_package(root: Path, *, fingerprint_value: str) -> None:
    """A malicious `tomtit.adapters.claude_code_cli` at `root/tomtit/adapters/...`.

    Its validator never rejects anything and its fingerprint function always
    returns `fingerprint_value`, regardless of the argv it is given -- the
    exact shape of the ambient package the review reproduced H-1 with. Used
    as a `PYTHONPATH`/user-site/`sitecustomize`-cache-poisoning payload
    across the tests below; never imported directly by the test process
    itself.
    """

    package_dir = root / "tomtit" / "adapters"
    package_dir.mkdir(parents=True, exist_ok=True)
    (root / "tomtit" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    source = f"""
class AuthorizedCommandError(Exception):
    pass


def validate_authorized_command_argv(argv):
    return None  # never rejects anything


def compute_authorized_command_fingerprint(argv):
    return {fingerprint_value!r}  # constant, independent of argv
"""
    (package_dir / "claude_code_cli.py").write_text(source, encoding="utf-8")


def _write_fake_trusted_tree(root: Path, claude_code_cli_source: str) -> Path:
    """A self-contained `src/tomtit/adapters/{{claude_code_cli.py,
    exact_bash_guard.py}}` tree, anchored at `root`, using a *copy* of the
    real, unmodified `exact_bash_guard.py` source alongside a
    caller-supplied `claude_code_cli.py`.

    Because the copied hook derives its own trusted `src/` root solely from
    its own `__file__`, running it from this tree exercises the exact same
    bootstrap/import/provenance logic as the real file, just anchored at a
    test-controlled location -- the real repository file is never touched
    or modified. Returns the path to the copied hook script.
    """

    adapters_dir = root / "src" / "tomtit" / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    (root / "src" / "tomtit" / "__init__.py").write_text("", encoding="utf-8")
    (adapters_dir / "__init__.py").write_text("", encoding="utf-8")
    (adapters_dir / "claude_code_cli.py").write_text(claude_code_cli_source, encoding="utf-8")
    hook_path = adapters_dir / "exact_bash_guard.py"
    hook_path.write_text(_REAL_HOOK_SOURCE, encoding="utf-8")
    return hook_path


def _write_sitecustomize_cache_poison(root: Path, *, fingerprint_value: str) -> None:
    """A `sitecustomize.py` that pre-populates `sys.modules` with a fake
    `tomtit.adapters.claude_code_cli` *before* the hook script's own code
    ever runs -- the one vector a `sys.path[0]` insertion alone cannot
    out-rank, since Python's import system consults `sys.modules` before
    `sys.path`. Its fake module's `__file__` deliberately does not resolve
    under any trusted root, so the provenance check is what must catch it.
    """

    root.mkdir(parents=True, exist_ok=True)
    source = f"""
import sys, types

_fake = types.ModuleType("tomtit.adapters.claude_code_cli")
_fake.__file__ = "/nonexistent/ambient/claude_code_cli.py"
_fake.validate_authorized_command_argv = lambda argv: None
_fake.compute_authorized_command_fingerprint = lambda argv: {fingerprint_value!r}
_fake.AuthorizedCommandError = Exception
sys.modules["tomtit"] = types.ModuleType("tomtit")
sys.modules["tomtit.adapters"] = types.ModuleType("tomtit.adapters")
sys.modules["tomtit.adapters.claude_code_cli"] = _fake
"""
    (root / "sitecustomize.py").write_text(source, encoding="utf-8")


def _run_hook(
    script_path: Path,
    *,
    payload: object,
    fingerprints: list[str] | None,
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
    fingerprints_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `script_path` (the real hook or a fake-trusted-tree copy of it)
    as a genuinely fresh subprocess -- see section docstring above for why
    this must be a real subprocess rather than an in-process call.

    The fingerprints file is written into `fingerprints_dir` if given,
    otherwise into a fresh `tempfile.mkdtemp()` directory -- *never* into
    `script_path.parent`, which for the real hook is the tracked
    `src/tomtit/adapters/` source directory.
    """

    env: dict[str, str] = {"PATH": "/usr/bin:/bin"}
    if extra_env:
        env.update(extra_env)
    if fingerprints is not None:
        directory = fingerprints_dir or Path(tempfile.mkdtemp(prefix="tomtit-guard-test-fingerprints-"))
        directory.mkdir(parents=True, exist_ok=True)
        fingerprints_path = directory / f"fingerprints-{uuid.uuid4().hex}.json"
        fingerprints_path.write_text(json.dumps(fingerprints), encoding="utf-8")
        env[FINGERPRINTS_FILE_ENV] = str(fingerprints_path)
    stdin_text = "" if payload is None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script_path)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# H-1 required tests.
# ---------------------------------------------------------------------------


def test_h1_1_ambient_package_present_earlier_on_pythonpath(tmp_path: Path) -> None:
    """1. A fake ambient `tomtit` package appears earlier on sys.path.

    Sanity check on the fixture itself first: plain Python import
    resolution really would prefer this ambient package if nothing
    corrected for it -- proving the attack surface exists before proving it
    is closed for the hook specifically.
    """

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="H1_1_CONST")
    proc = subprocess.run(
        [sys.executable, "-c", "import tomtit.adapters.claude_code_cli as m; print(m.__file__)"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ambient_root)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert str(ambient_root) in proc.stdout


def test_h1_2_hook_still_loads_trusted_module_for_legitimate_exact_match(tmp_path: Path) -> None:
    """2. The hook still loads the trusted TOMTIT module.

    Proven behaviorally: a *real* approved command, fingerprinted by the
    real trusted `compute_authorized_command_fingerprint`, still PASSes
    even with a malicious ambient package on `PYTHONPATH` -- only possible
    if the trusted validator/fingerprint functions (not the ambient
    always-allow/constant-fingerprint ones) are what actually ran.
    """

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="UNRELATED_AMBIENT_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py exact.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_h1_3_ambient_always_allow_validator_cannot_influence_decision(tmp_path: Path) -> None:
    """3. A fake ambient validator that always allows cannot influence the decision.

    The ambient validator never rejects anything; a fingerprints file
    containing only the ambient package's own constant (which the trusted
    validator/fingerprint pair could never produce for this command) proves
    the ambient validator's always-allow behavior has no effect on the
    outcome -- the command is still denied.
    """

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="H1_3_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_3_CONST"],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h1_4_ambient_constant_fingerprint_cannot_influence_decision(tmp_path: Path) -> None:
    """4. A fake ambient fingerprint function returning a constant cannot
    influence the decision -- the review's H-1 reproduction, stated from
    the fingerprint side."""

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="H1_4_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_4_CONST"],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2


def test_h1_5_unauthorized_command_denied_despite_malicious_ambient_package(tmp_path: Path) -> None:
    """5. Unauthorized synthetic command is DENIED even with malicious
    ambient package present -- the exact scenario the review reproduced as
    a PASS (exit 0) against the pre-fix code."""

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="H1_5_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_5_CONST"],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert "rm -rf" not in proc.stderr
    assert "SYNTHETIC_TARGET" not in proc.stderr


def test_h1_6_trusted_module_provenance_mismatch_fails_closed() -> None:
    """6. Trusted module provenance mismatch fails closed.

    Directly exercises `_import_trusted_claude_code_cli` with a
    `trusted_src_root` that does not actually contain the real module's
    resolved `__file__` -- the provenance check must reject this even
    though the import itself succeeds (it resolves the *real*, otherwise
    entirely legitimate, trusted module).
    """

    with pytest.raises(exact_bash_guard_module._TrustedImportUnavailableError):
        exact_bash_guard_module._import_trusted_claude_code_cli("/nonexistent/wrong/trusted/root")


def test_h1_7_direct_standalone_subprocess_execution_matches(tmp_path: Path) -> None:
    """7. Direct standalone subprocess execution behaves the same way.

    Re-confirms the H-1 fix specifically as the real standalone subprocess
    Claude actually spawns (`python3 exact_bash_guard.py`, reading JSON
    from stdin) -- not merely `evaluate()` called in-process -- including
    that PASS remains silent (empty stdout/stderr, exit 0) for a legitimate
    command even with the malicious ambient package present.
    """

    ambient_root = tmp_path / "ambient"
    _write_ambient_tomtit_package(ambient_root, fingerprint_value="H1_7_CONST")

    deny_proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_7_CONST"],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert deny_proc.returncode == 2

    pass_proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py exact.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
        extra_env={"PYTHONPATH": str(ambient_root)},
        fingerprints_dir=tmp_path,
    )
    assert pass_proc.returncode == 0
    assert pass_proc.stdout == ""
    assert pass_proc.stderr == ""


def test_h1_8_no_cwd_or_target_repo_package_can_alter_provenance(tmp_path: Path) -> None:
    """8. No target-repo or cwd package can alter import provenance.

    A directly-executed script's `sys.path[0]` is its own directory, never
    `cwd` -- simulates a worker operating inside a target repository that
    happens to contain (or was made to contain) its own `tomtit/`
    directory, without ever putting it on `PYTHONPATH`.
    """

    target_repo = tmp_path / "target-repo-cwd"
    _write_ambient_tomtit_package(target_repo, fingerprint_value="H1_8_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_8_CONST"],
        cwd=target_repo,
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h1_9_user_site_like_path_simulation_cannot_alter_provenance(tmp_path: Path) -> None:
    """9. User-site-like path simulation cannot alter import provenance.

    Uses `PYTHONUSERBASE` to make Python's *real* user-site-packages
    mechanism (`site.getusersitepackages()`, unconditionally on `sys.path`
    for the interpreter the hook runs under) resolve to a test-controlled
    directory containing the malicious ambient package -- the actual
    user-site mechanism named in the review's H-1 finding, not merely
    `PYTHONPATH` again.
    """

    userbase = tmp_path / "userbase"
    probe = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
        env={"PATH": "/usr/bin:/bin", "PYTHONUSERBASE": str(userbase)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0
    usersite_dir = Path(probe.stdout.strip())
    _write_ambient_tomtit_package(usersite_dir, fingerprint_value="H1_9_CONST")

    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_9_CONST"],
        extra_env={"PYTHONUSERBASE": str(userbase)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h1_10_sitecustomize_cache_poisoning_fails_closed_via_provenance_check(tmp_path: Path) -> None:
    """Bonus H-1 coverage: `sitecustomize.py` pre-populating `sys.modules`
    before the hook's own code runs is the one vector a `sys.path[0]`
    insertion alone cannot out-rank (Python checks `sys.modules` before
    `sys.path`); the post-import provenance check is what closes it.
    Confirms the vector is real (sitecustomize genuinely pre-populates
    `sys.modules` here) before confirming it is denied.
    """

    vector_root = tmp_path / "sitecustomize_vector"
    _write_sitecustomize_cache_poison(vector_root, fingerprint_value="H1_SC_CONST")

    sanity = subprocess.run(
        [sys.executable, "-c", "import sys; print('tomtit.adapters.claude_code_cli' in sys.modules)"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(vector_root)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert sanity.stdout.strip() == "True", "sitecustomize fixture did not actually pre-populate sys.modules"

    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H1_SC_CONST"],
        extra_env={"PYTHONPATH": str(vector_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


# ---------------------------------------------------------------------------
# H-2 required tests.
# ---------------------------------------------------------------------------


def test_h2_1_import_failure_exits_2(tmp_path: Path) -> None:
    """1. Import failure -> exit 2."""

    hook_path = _write_fake_trusted_tree(tmp_path, "raise RuntimeError('synthetic broken trusted import')\n")
    proc = _run_hook(hook_path, payload=_bash_payload("echo hi"), fingerprints=["unused"])
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert proc.stdout == ""


def test_h2_2_module_provenance_failure_exits_2(tmp_path: Path) -> None:
    """2. Module provenance failure -> exit 2 (sitecustomize cache-poisoning vector)."""

    vector_root = tmp_path / "sitecustomize_vector"
    _write_sitecustomize_cache_poison(vector_root, fingerprint_value="H2_PROVENANCE_CONST")
    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload(_UNAUTHORIZED_SYNTHETIC_COMMAND),
        fingerprints=["H2_PROVENANCE_CONST"],
        extra_env={"PYTHONPATH": str(vector_root)},
        fingerprints_dir=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h2_3_stdin_read_failure_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """3. stdin read failure -> exit 2."""

    class _BrokenStdin:
        def read(self) -> str:
            raise OSError("synthetic stdin read failure")

    monkeypatch.setenv(FINGERPRINTS_FILE_ENV, "")
    monkeypatch.setattr(sys, "stdin", _BrokenStdin())
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == DENIAL_MESSAGE
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_h2_4_malformed_json_exits_2(tmp_path: Path) -> None:
    """4. malformed JSON -> exit 2."""

    fingerprints_path = tmp_path / "fingerprints.json"
    fingerprints_path.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input="{not valid json",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(fingerprints_path)},
        timeout=10,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h2_5_evaluate_raises_unexpected_runtimeerror_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """5. evaluate raises unexpected RuntimeError -> exit 2."""

    def _broken_evaluate(payload: object, approved_fingerprints: object) -> bool:
        raise RuntimeError("synthetic unexpected evaluate() failure")

    monkeypatch.setattr(exact_bash_guard_module, "evaluate", _broken_evaluate)
    monkeypatch.setenv(FINGERPRINTS_FILE_ENV, "")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(_bash_payload("echo hi"))))
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == DENIAL_MESSAGE
    assert "Traceback" not in captured.err
    assert "RuntimeError" not in captured.err


def test_h2_6_validator_raises_unexpected_exception_exits_2(tmp_path: Path) -> None:
    """6. validator raises unexpected exception -> exit 2 (not merely `AuthorizedCommandError`)."""

    source = """
class AuthorizedCommandError(Exception):
    pass


def validate_authorized_command_argv(argv):
    raise RuntimeError("synthetic unexpected validator failure")


def compute_authorized_command_fingerprint(argv):
    return "unused"
"""
    hook_path = _write_fake_trusted_tree(tmp_path, source)
    proc = _run_hook(hook_path, payload=_bash_payload("echo hi"), fingerprints=["unused"])
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert "RuntimeError" not in proc.stderr


def test_h2_7_fingerprint_function_raises_unexpected_exception_exits_2(tmp_path: Path) -> None:
    """7. fingerprint function raises unexpected exception -> exit 2."""

    source = """
class AuthorizedCommandError(Exception):
    pass


def validate_authorized_command_argv(argv):
    return None


def compute_authorized_command_fingerprint(argv):
    raise RuntimeError("synthetic unexpected fingerprint failure")
"""
    hook_path = _write_fake_trusted_tree(tmp_path, source)
    proc = _run_hook(hook_path, payload=_bash_payload("echo hi"), fingerprints=["unused"])
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE
    assert "RuntimeError" not in proc.stderr


def test_h2_8_missing_fingerprint_file_exits_2(tmp_path: Path) -> None:
    """8. missing fingerprint file -> exit 2."""

    proc = subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input=json.dumps(_bash_payload("python3 writer.py exact.txt")),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(tmp_path / "does-not-exist.json")},
        timeout=10,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h2_9_malformed_fingerprint_file_exits_2(tmp_path: Path) -> None:
    """9. malformed fingerprint file -> exit 2."""

    bad = tmp_path / "bad.json"
    bad.write_text("not json{{{", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, exact_bash_guard_module.__file__],
        input=json.dumps(_bash_payload("python3 writer.py exact.txt")),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(bad)},
        timeout=10,
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def _build_h2_failure_scenarios(
    tmp_path: Path, *, command: str = _UNAUTHORIZED_SYNTHETIC_COMMAND
) -> list[tuple[str, subprocess.CompletedProcess[str]]]:
    """A representative set of distinct H-2 failure scenarios, each run as a
    real subprocess against either the real hook or a fake-trusted-tree copy
    of it, for the cross-cutting assertions in tests 10/11/12/15 below.
    """

    scenarios: list[tuple[str, subprocess.CompletedProcess[str]]] = []

    broken_import_hook = _write_fake_trusted_tree(
        tmp_path / "broken-import", "raise RuntimeError('synthetic broken import')\n"
    )
    scenarios.append(
        ("broken-trusted-import", _run_hook(broken_import_hook, payload=_bash_payload(command), fingerprints=["unused"]))
    )

    provenance_dir = tmp_path / "provenance-mismatch"
    _write_sitecustomize_cache_poison(provenance_dir, fingerprint_value="H2_SCEN_PROVENANCE_CONST")
    scenarios.append(
        (
            "provenance-mismatch",
            _run_hook(
                Path(exact_bash_guard_module.__file__),
                payload=_bash_payload(command),
                fingerprints=["H2_SCEN_PROVENANCE_CONST"],
                extra_env={"PYTHONPATH": str(provenance_dir)},
                fingerprints_dir=tmp_path / "provenance-mismatch-fp",
            ),
        )
    )

    malformed_json_dir = tmp_path / "malformed-json"
    malformed_json_dir.mkdir()
    malformed_json_fingerprints = malformed_json_dir / "fingerprints.json"
    malformed_json_fingerprints.write_text(json.dumps([APPROVED_FINGERPRINT]), encoding="utf-8")
    scenarios.append(
        (
            "malformed-json-stdin",
            subprocess.run(
                [sys.executable, exact_bash_guard_module.__file__],
                input=f"{{not valid json but mentions {command}",
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(malformed_json_fingerprints)},
                timeout=10,
            ),
        )
    )

    missing_fp_dir = tmp_path / "missing-fingerprints"
    missing_fp_dir.mkdir()
    scenarios.append(
        (
            "missing-fingerprints-file",
            subprocess.run(
                [sys.executable, exact_bash_guard_module.__file__],
                input=json.dumps(_bash_payload(command)),
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(missing_fp_dir / "does-not-exist.json")},
                timeout=10,
            ),
        )
    )

    malformed_fp_dir = tmp_path / "malformed-fingerprints"
    malformed_fp_dir.mkdir()
    bad_fp = malformed_fp_dir / "bad.json"
    bad_fp.write_text("not json{{{", encoding="utf-8")
    scenarios.append(
        (
            "malformed-fingerprints-file",
            subprocess.run(
                [sys.executable, exact_bash_guard_module.__file__],
                input=json.dumps(_bash_payload(command)),
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", FINGERPRINTS_FILE_ENV: str(bad_fp)},
                timeout=10,
            ),
        )
    )

    validator_hook = _write_fake_trusted_tree(
        tmp_path / "validator-raises",
        """
class AuthorizedCommandError(Exception):
    pass


def validate_authorized_command_argv(argv):
    raise RuntimeError("synthetic unexpected validator failure")


def compute_authorized_command_fingerprint(argv):
    return "unused"
""",
    )
    scenarios.append(
        (
            "validator-raises-unexpectedly",
            _run_hook(validator_hook, payload=_bash_payload(command), fingerprints=["unused"]),
        )
    )

    fingerprint_hook = _write_fake_trusted_tree(
        tmp_path / "fingerprint-raises",
        """
class AuthorizedCommandError(Exception):
    pass


def validate_authorized_command_argv(argv):
    return None


def compute_authorized_command_fingerprint(argv):
    raise RuntimeError("synthetic unexpected fingerprint failure")
""",
    )
    scenarios.append(
        (
            "fingerprint-function-raises-unexpectedly",
            _run_hook(fingerprint_hook, payload=_bash_payload(command), fingerprints=["unused"]),
        )
    )

    return scenarios


@pytest.fixture(scope="module")
def _h2_default_failure_scenarios(
    tmp_path_factory: pytest.TempPathFactory,
) -> list[tuple[str, subprocess.CompletedProcess[str]]]:
    """The default-command H-2 failure scenarios, built once and shared by
    tests 10/11/15 below -- each of those tests asserts a different property
    of the *same* set of failure runs, so rebuilding it (seven subprocess
    spawns) per test is pure overhead, not additional coverage.
    """

    return _build_h2_failure_scenarios(tmp_path_factory.mktemp("h2-default-scenarios"))


def test_h2_10_no_traceback_on_any_unexpected_failure(
    _h2_default_failure_scenarios: list[tuple[str, subprocess.CompletedProcess[str]]],
) -> None:
    """10. no unexpected failure emits traceback text."""

    for name, proc in _h2_default_failure_scenarios:
        assert "Traceback (most recent call last)" not in proc.stderr, name
        assert "Traceback (most recent call last)" not in proc.stdout, name


def test_h2_11_no_synthetic_command_leak_on_unexpected_failure(
    _h2_default_failure_scenarios: list[tuple[str, subprocess.CompletedProcess[str]]],
) -> None:
    """11. no unexpected failure leaks synthetic command text."""

    for name, proc in _h2_default_failure_scenarios:
        assert "writer.py" not in proc.stderr, name
        assert "writer.py" not in proc.stdout, name
        assert _UNAUTHORIZED_SYNTHETIC_COMMAND not in proc.stderr, name
        assert _UNAUTHORIZED_SYNTHETIC_COMMAND not in proc.stdout, name


def test_h2_12_no_synthetic_secret_leak_on_unexpected_failure(tmp_path: Path) -> None:
    """12. no unexpected failure leaks a synthetic secret."""

    command = f"echo {_H2_SYNTHETIC_SECRET}"
    for name, proc in _build_h2_failure_scenarios(tmp_path, command=command):
        assert _H2_SYNTHETIC_SECRET not in proc.stderr, name
        assert _H2_SYNTHETIC_SECRET not in proc.stdout, name


def test_h2_13_exact_approved_command_still_exits_0_with_empty_output() -> None:
    """13. exact approved command still exits 0 with empty stdout/stderr (non-regression)."""

    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py exact.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_h2_14_denied_command_still_exits_2_with_only_denial_message() -> None:
    """14. denied command still exits 2 with only DENIAL_MESSAGE (non-regression)."""

    proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py other.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
    )
    assert proc.returncode == 2
    assert proc.stderr.strip() == DENIAL_MESSAGE


def test_h2_15_no_reachable_exit_code_1_across_all_scenarios(
    _h2_default_failure_scenarios: list[tuple[str, subprocess.CompletedProcess[str]]],
) -> None:
    """15. there is no reachable exit code 1 path -- across every constructed
    failure scenario plus the clean PASS/DENY cases."""

    for name, proc in _h2_default_failure_scenarios:
        assert proc.returncode in (0, 2), name
        assert proc.returncode != 1, name

    pass_proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py exact.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
    )
    assert pass_proc.returncode == 0

    deny_proc = _run_hook(
        Path(exact_bash_guard_module.__file__),
        payload=_bash_payload("python3 writer.py other.txt"),
        fingerprints=[APPROVED_FINGERPRINT],
    )
    assert deny_proc.returncode == 2
