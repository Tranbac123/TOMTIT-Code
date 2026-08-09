"""TOMTIT-owned PreToolUse Bash guard: defense-in-depth exact-argv authorization.

An isolated real-CLI probe established that Claude Code's own
`--allowedTools Bash(<command>)` permission rule is matched *after* a fixed
set of process-wrapper normalizations are stripped (`command`, `builtin`,
`timeout`, `time`, `nice`, `nohup`, `stdbuf`, zsh `noglob`, certain
env-assignment prefixes, bare `xargs`) -- so it enforces provider-normalized
equivalence, not TOMTIT's stricter Owner-approved exact argv. See
`EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md` for the reproduced
probe transcript and full design rationale.

This module is a second, TOMTIT-controlled enforcement layer registered as a
Claude Code PreToolUse hook for the `Bash` tool: it independently re-parses
the raw command Claude is about to execute using the *exact same* canonical
validator (`validate_authorized_command_argv`) and SHA-256 fingerprint
contract (`compute_authorized_command_fingerprint`) already used by
`ClaudeCodeCLIRequest` -- never a second, weaker validator -- and blocks
anything that does not fingerprint-match an Owner-approved authorized
command, including every wrapper variant Claude's own normalization would
otherwise let through.

Security semantics (do not weaken): a PASS (fingerprint match) is never
itself a permission grant. It exits 0 with no stdout output, deferring
entirely to Claude's own permission engine and `--allowedTools` -- the real
subprocess still must independently approve the call. Only a DENY is an
explicit decision: exit code 2 with a fixed, generic, command-value-free
message on stderr (Claude Code's documented PreToolUse blocking contract).
No code path here ever persists, logs, or echoes the raw command text.

--- Trusted import boundary (H-1) ---

Invoked as a standalone subprocess by the `claude` CLI itself (never
imported by it). The trusted `src/` root is derived *solely* from this
file's own resolved `__file__` path and is inserted at `sys.path[0]`
*unconditionally*, before any `tomtit` import is ever attempted anywhere in
this module -- never behind a `try: import ... except ImportError:`
fallback, which would let an ambient package (installed distribution, user
site-packages, `PYTHONPATH`, a `.pth` file, `sitecustomize`) win import
precedence whenever it happens to be importable at all. After import, the
resulting module's own resolved `__file__` is verified to live underneath
that same trusted root -- closing the residual case where something
(`sitecustomize.py`, a pre-populated `sys.modules` cache) bound
`tomtit.adapters.claude_code_cli` to something else *before* this script's
own code ran, which a `sys.path` insertion alone cannot out-rank (Python's
import system consults `sys.modules` before `sys.path`). If provenance
cannot be proven, this module falls back to always-deny stand-ins for the
three trusted names it needs (see `_load_trusted_claude_code_cli` below) --
never to a weaker or ambient implementation, and never by crashing.

This is also what lets the target repository run this guard without any
TOMTIT source files copied into it: the hook command line always references
this file by its absolute path inside TOMTIT's own source tree, and that
same file locates its own trusted `src/` root independent of `cwd`,
`PYTHONPATH`, or anything else the target repository or invoking
environment might contain.

--- Fail-closed process boundary (H-2) ---

This process has exactly two possible exit codes: `0` for a verified
exact-match PASS, and `2` for absolutely everything else -- a clean deny, a
malformed/missing fingerprints file, malformed stdin, a failed trusted
import, a provenance mismatch, or any other unexpected exception anywhere
in this module's call graph, `BaseException` included. Claude Code's
documented PreToolUse contract treats exit code 2 as the *only* blocking
outcome; any other non-zero exit is non-blocking and the tool call would
proceed regardless -- so an uncaught exception that produced Python's
default exit code 1 (as an earlier version of this module could) is exactly
as unsafe as a silent PASS. `main()` is the single place this guarantee is
proven: everything else in this module is free to raise on failure, because
`main()`'s own boundary converts *every* non-True outcome, exception or not,
into the identical fail-closed result, with no traceback and no echoed
input ever reaching stderr.
"""

from __future__ import annotations

import importlib
import json
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


def _trusted_src_root() -> str:
    """The absolute, resolved repository `src/` directory.

    Derived *solely* from this file's own on-disk location (three levels
    up: `adapters/` -> `tomtit/` -> `src/`) -- never from `cwd`,
    `PYTHONPATH`, an environment variable, or any other ambient signal that
    a target repository or invoking environment could influence.
    """

    return str(Path(__file__).resolve().parents[2])


def _bootstrap_trusted_src_path() -> str:
    """Make the trusted `src/` root `sys.path[0]`, unconditionally.

    Called before any `tomtit` import is attempted -- never behind a `try`/
    `except ImportError` fallback, which would only run this *after* an
    ambient import had already been allowed to succeed. Removes every
    existing occurrence of the exact same resolved path first, so this is
    idempotent (safe to call more than once in one process) and the trusted
    entry always ends up in the winning position rather than merely
    "present somewhere" on `sys.path`.
    """

    trusted_src_root = _trusted_src_root()
    sys.path[:] = [entry for entry in sys.path if entry != trusted_src_root]
    sys.path.insert(0, trusted_src_root)
    return trusted_src_root


class _TrustedImportUnavailableError(Exception):
    """The trusted TOMTIT import could not be completed, or its provenance
    could not be proven.

    Used as the *effective* `AuthorizedCommandError` whenever the real
    trusted import fails (see `_load_trusted_claude_code_cli`) -- so
    `_command_fingerprint_is_approved`'s existing, unmodified
    `except AuthorizedCommandError:` clause denies cleanly through its
    already-reviewed fail-closed path, with no special-casing needed
    anywhere else in this module. Its message is always a fixed, static
    string -- never an ambient module path, a raw command, or any other
    dynamic value -- and is in any case never printed anywhere; only the
    fixed `DENIAL_MESSAGE` ever reaches stderr.
    """


def _unavailable_validate_authorized_command_argv(argv: object) -> None:
    """Always-deny stand-in used only when the trusted validator could not be loaded."""

    raise _TrustedImportUnavailableError("trusted TOMTIT validator unavailable")


def _unavailable_compute_authorized_command_fingerprint(argv: object) -> str:
    """Always-deny stand-in used only when the trusted fingerprint function could not be loaded."""

    raise _TrustedImportUnavailableError("trusted TOMTIT fingerprint function unavailable")


def _import_trusted_claude_code_cli(trusted_src_root: str) -> ModuleType:
    """Import `tomtit.adapters.claude_code_cli` and prove its provenance.

    Must be called only after `trusted_src_root` has already been inserted
    at `sys.path[0]`. `importlib.import_module`, like a plain `import`
    statement, consults `sys.modules` *before* `sys.path` -- so the
    `sys.path[0]` insertion alone cannot out-rank a `tomtit.adapters.
    claude_code_cli` module some ambient mechanism (most notably a
    `sitecustomize.py` executed by the `site` module at interpreter
    startup, before this script's own code ever ran) already imported and
    cached. The provenance check below is what closes that residual gap: it
    verifies the *resulting* module's own resolved `__file__` actually
    lives underneath the trusted root, regardless of *how* that module came
    to be bound -- so a `sys.modules`-cache-poisoning attack is caught
    exactly like an ordinary `sys.path`-ordering attack, by verifying the
    outcome rather than trying to enumerate and control every mechanism
    that could produce the wrong one.

    Raises `_TrustedImportUnavailableError` (or lets the underlying
    `ImportError`/`AttributeError` propagate) on any problem -- missing
    `__file__`, a provenance mismatch, or a module missing a required
    attribute -- and never returns a module whose provenance was not
    positively proven. Never logs the ambient or mismatched path anywhere.
    """

    module = importlib.import_module("tomtit.adapters.claude_code_cli")
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise _TrustedImportUnavailableError("trusted module import has no provable file location")
    resolved_module_path = Path(module_file).resolve()
    resolved_trusted_root = Path(trusted_src_root).resolve()
    try:
        resolved_module_path.relative_to(resolved_trusted_root)
    except ValueError:
        raise _TrustedImportUnavailableError("trusted module provenance could not be proven") from None
    return module


def _load_trusted_claude_code_cli() -> tuple[Callable[..., object], Callable[..., object], type[BaseException]]:
    """The three trusted names this guard needs, or always-deny stand-ins.

    This is the single place the trusted-path bootstrap, the `tomtit`
    import, and the module-provenance verification all happen -- and the
    single place their combined failure (for *any* reason, `BaseException`
    included: a broken install, a corrupted trusted file, a provenance
    mismatch, an out-of-memory condition during import, anything) is
    converted into a safe, well-typed, always-raising fallback rather than
    letting an exception escape module load. Module load must never fail
    here: a standalone script whose own top-level code raises produces
    Python's default uncaught-exception behavior (a traceback on stderr and
    exit code 1), which is exactly the non-blocking, fail-*open* outcome
    this guard exists to prevent (Claude Code's PreToolUse contract only
    treats exit code 2 as blocking).

    Never logs the ambient module path, the mismatch reason, or anything
    else about *why* the trusted import failed -- only whether it did.
    """

    try:
        trusted_src_root = _bootstrap_trusted_src_path()
        module = _import_trusted_claude_code_cli(trusted_src_root)
        return (
            module.validate_authorized_command_argv,
            module.compute_authorized_command_fingerprint,
            module.AuthorizedCommandError,
        )
    except BaseException:  # noqa: BLE001 -- intentional: this is the standalone security-hook
        # trust boundary itself (see module docstring, "Trusted import boundary (H-1)" and
        # "Fail-closed process boundary (H-2)"). Any failure here -- corrupted install, provenance
        # mismatch, an exotic interpreter-level error -- must fall back to the always-deny stand-ins
        # below rather than let module load raise, which would otherwise produce Python's default
        # uncaught-exception exit code 1 (non-blocking under Claude Code's PreToolUse contract) instead
        # of this guard's required exit code 2 (blocking). Never used elsewhere in TOMTIT product code.
        return (
            _unavailable_validate_authorized_command_argv,
            _unavailable_compute_authorized_command_fingerprint,
            _TrustedImportUnavailableError,
        )


# Populated exactly once, at module load, by the trusted-import boundary above -- either the real
# trusted functions/exception class from `tomtit.adapters.claude_code_cli`'s verified-provenance
# module, or always-deny stand-ins if that could not be proven. `evaluate()` and
# `_command_fingerprint_is_approved()` below are otherwise completely unmodified from the
# already-reviewed, must-not-redesign implementation: they simply reference these three names exactly
# as before, and behave identically regardless of which case produced them.
validate_authorized_command_argv, compute_authorized_command_fingerprint, AuthorizedCommandError = (
    _load_trusted_claude_code_cli()
)


# The single env var this hook reads: the path to an ephemeral, session-
# scoped JSON file containing *only* SHA-256 fingerprints of Owner-approved
# authorized commands -- never their raw argv. Written by
# `claude_code_cli._write_exact_bash_guard_artifacts`. Duplicated as a
# literal there (not imported from here) so the adapter never depends on
# being able to import this module.
FINGERPRINTS_FILE_ENV = "TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE"

# Fixed, generic, command-value-free denial text. The command that triggered
# a denial -- approved-looking or not -- must never appear in a decision
# surfaced to Claude or the user (Codex exact-command review lineage:
# rejected values must never be echoed back). This is also the *only*
# thing this process ever writes to stderr, on every non-PASS outcome
# without exception -- a clean deny and an unexpected internal failure are
# indistinguishable from the outside, by design (H-2).
DENIAL_MESSAGE = (
    "TOMTIT exact-argv authorization gate: this Bash command does not "
    "exactly match an Owner-approved authorized command and was blocked "
    "before execution."
)

_PASS_EXIT_CODE = 0
_DENY_EXIT_CODE = 2


def load_approved_fingerprints(path_text: str | None) -> frozenset[str] | None:
    """The approved fingerprint set from its ephemeral file.

    Returns `None` when it cannot be safely loaded -- missing env var,
    missing file, unreadable file, or a shape other than a JSON array of
    non-empty strings. Callers must treat `None` as fail-closed (deny
    everything), never as "no restriction configured."
    """

    if not path_text:
        return None
    try:
        raw = Path(path_text).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not all(isinstance(item, str) and item for item in data):
        return None
    return frozenset(data)


def _command_fingerprint_is_approved(command: str, approved_fingerprints: frozenset[str]) -> bool:
    """Parse `command` and check exact fingerprint membership.

    Uses the exact same canonical validator/fingerprint contract
    `ClaudeCodeCLIRequest` uses -- never a second, weaker validator. Any
    wrapper (`command`, `builtin`, `timeout`, `time`, `nice`, `nohup`,
    `stdbuf`, `noglob`, `env`, `xargs`, ...), extra argument, suffix/prefix
    variant, reordering, compound command, redirect, substitution, or
    unparseable/rejected syntax either changes the parsed argv (so it cannot
    fingerprint-match the approved argv) or is rejected outright by
    `validate_authorized_command_argv` before fingerprinting is even
    attempted. There is no separate wrapper-name denylist to keep in sync:
    equality is the only thing that ever passes.

    If the trusted import could not be completed (see module-level
    `_load_trusted_claude_code_cli`), `validate_authorized_command_argv` is
    the always-raising stand-in, so this denies here exactly as it would
    for a real `AuthorizedCommandError` -- no separate handling needed.
    """

    try:
        argv = tuple(shlex.split(command))
    except ValueError:
        return False
    try:
        validate_authorized_command_argv(argv)
    except AuthorizedCommandError:
        return False
    return compute_authorized_command_fingerprint(argv) in approved_fingerprints


def evaluate(payload: object, approved_fingerprints: frozenset[str] | None) -> bool:
    """True means PASS (defer to Claude's native engine); False means DENY.

    A PASS is never itself a permission grant -- see module docstring.
    Every unrecognized shape, missing field, wrong tool name, or
    `run_in_background=True` request fails closed to DENY; there is no code
    path that infers PASS from absence of information. This MVP always
    fails closed on `run_in_background=True` rather than attempting to
    reason about background-execution safety.
    """

    if approved_fingerprints is None:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("tool_name") != "Bash":
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    if tool_input.get("run_in_background") is True:
        return False
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return False
    return _command_fingerprint_is_approved(command, approved_fingerprints)


def _run() -> bool:
    """Returns True only for a verified exact-match PASS.

    Every other outcome must be treated as DENY by the caller -- including
    any exception raised anywhere in this function's call graph (reading
    stdin, parsing JSON, loading the fingerprints file, evaluating the
    payload). This function is deliberately free to let any exception
    propagate uncaught: `main()`'s single outer boundary is what converts
    *every one* of them into the identical fail-closed outcome, so
    "on failure, deny" only has to be proven correct in exactly one place.
    """

    approved_fingerprints = load_approved_fingerprints(os.environ.get(FINGERPRINTS_FILE_ENV))
    raw_stdin = sys.stdin.read()
    payload: object = json.loads(raw_stdin) if raw_stdin else None
    return evaluate(payload, approved_fingerprints)


def main() -> int:
    """The single fail-closed boundary (H-2): exactly two possible outcomes.

    `_PASS_EXIT_CODE` (0) only when `_run()` returns `True` cleanly, with no
    stdout/stderr output. `_DENY_EXIT_CODE` (2) for every other case --
    including any exception anywhere in `_run()`'s call graph, and even a
    failure while writing the denial message itself (an unlikely broken-
    stderr scenario, but still converted to a deny rather than allowed to
    escape uncaught). `BaseException` is caught deliberately, not `Exception`
    -- `KeyboardInterrupt`/`SystemExit` are not given special treatment here,
    since Python's default handling of an uncaught `KeyboardInterrupt`
    (exit code 130) would be exactly as non-blocking, under Claude Code's
    PreToolUse contract, as the exit-code-1 behavior H-2 exists to close.
    Never prints a traceback, never echoes stdin, a command, or a
    fingerprint value -- the only thing ever written to stderr is the fixed
    `DENIAL_MESSAGE`, and only on the deny path.
    """

    try:
        if _run():
            return _PASS_EXIT_CODE
    except BaseException:  # noqa: BLE001 -- intentional: see docstring and module docstring
        # "Fail-closed process boundary (H-2)". This is the standalone security-hook boundary
        # itself; broad exception handling is deliberately not used elsewhere in TOMTIT product code.
        pass

    try:
        print(DENIAL_MESSAGE, file=sys.stderr)
    except BaseException:  # noqa: BLE001 -- see above: even a broken stderr must not prevent the
        # blocking exit code from being returned.
        pass

    return _DENY_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
