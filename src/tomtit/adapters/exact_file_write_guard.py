"""TOMTIT-owned PreToolUse guard for Claude's built-in mutating file tools.

Claude Code's OS-level Bash sandbox covers `Bash` and its descendants. It does
NOT cover the built-in mutating file tools (`Write`, `Edit`, `MultiEdit`,
`NotebookEdit`), which run inside the CLI process itself. Without this guard
those tools could write anywhere the CLI process can reach -- outside the
workspace, into `.git/**`, or to in-workspace source files the Owner never
authorized -- and TOMTIT would only discover it *after the fact* via the
post-run workspace snapshot. Post-run detection is not a boundary; this guard
is the boundary, enforced before the tool executes.

Payload contract (verified against the installed Claude Code 2.1.223 binary,
not guessed):

    Write        -> tool_input.file_path
    Edit         -> tool_input.file_path
    MultiEdit    -> tool_input.file_path
    NotebookEdit -> tool_input.notebook_path

Authorization contract: a mutating file tool may target only a path that is
explicitly listed in the Owner's `allowed_changed_paths`. That list is an
EXACT source-file allowlist, not a directory-root grant --
`allowed_changed_paths = ["src/file.ts"]` authorizes `src/file.ts` and nothing
else (not `src/file.ts.backup`, not `src/other.ts`, not `src/sub/file.ts`).

`allowed_side_effect_paths` deliberately does NOT authorize these tools. That
contract exists to *account for* generated output (e.g. `node_modules/**`),
not to permit source editing. The two contracts stay independent in both
directions, exactly as `authorized_commands` and the filesystem contracts do.

Symlink policy (deliberately the stricter MVP choice, documented because it is
a real product decision, not an implementation detail): BOTH the lexical
requested path AND its fully resolved target must be Owner-authorized. An
alias therefore never gains permission merely by resolving to an allowed file.

    allowed_changed_paths = ["src/file.ts"]

    Write("src/file.ts")                                  -> ALLOW
    Write("alias.ts")      where alias.ts -> src/file.ts   -> DENY
    Write("src/file.ts")   where it -> /tmp/outside        -> DENY
    Write("src/file.ts")   where it -> .git/config         -> DENY

Requiring both halves prevents alias-based widening of the Owner contract:
otherwise any writable name in the workspace could be turned into a second
handle on an authorized file, and the "exact source allowlist" would silently
become "anything that can be pointed at an allowed file".

Security semantics (identical in shape to `exact_bash_guard.py`): exit 0 means
only "this target is authorized -- defer to Claude's remaining permission
layers". It is never an explicit grant and never bypasses another check. Exit
2 blocks the call. Every other outcome -- malformed input, missing config,
unexpected exception, `BaseException` -- is exit 2, because under Claude
Code's PreToolUse contract exit 2 is the ONLY blocking non-zero code; any
other exit is treated as a non-blocking error and the tool would proceed.

Nothing provider-controlled is ever echoed back: the denial message is a fixed
constant, so a rejected path (which may itself be attacker-influenced) never
reaches evidence, logs, or the user.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The single env var this hook reads: the path to an ephemeral JSON artifact
# holding ONLY the canonical workspace root and the Owner-approved source
# allowlist. Duplicated as a literal in `claude_code_cli` (not imported) so the
# adapter never depends on importing this standalone module.
CONFIG_FILE_ENV = "TOMTIT_EXACT_FILE_WRITE_GUARD_CONFIG_FILE"

# Fixed, generic, path-value-free denial text.
DENIAL_MESSAGE = (
    "TOMTIT source-authorization gate: this file-modifying tool call targets a "
    "path that is not an Owner-approved source path, or resolves outside the "
    "workspace, and was blocked before execution."
)

# Confirmed against the installed CLI. A tool absent from this map is not a
# mutating file tool this guard claims to understand -- and an unrecognized
# payload shape denies rather than passing through unchecked.
_MUTATING_FILE_TOOL_TARGET_FIELDS: dict[str, str] = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

_PASS_EXIT_CODE = 0
_DENY_EXIT_CODE = 2


def load_guard_config(path_text: str | None) -> tuple[Path, frozenset[str]] | None:
    """The canonical workspace root and exact source allowlist.

    Returns `None` whenever the artifact cannot be safely loaded -- missing env
    var, missing/unreadable file, non-JSON, wrong shape, non-absolute
    workspace, or a non-string/empty entry in the allowlist. Callers must treat
    `None` as fail-closed (deny everything), never as "no restriction".
    """

    if not path_text:
        return None
    try:
        data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    workspace_text = data.get("workspace")
    if not isinstance(workspace_text, str) or not workspace_text:
        return None
    workspace = Path(workspace_text)
    if not workspace.is_absolute():
        return None

    allowed = data.get("allowed_changed_paths")
    if not isinstance(allowed, list):
        return None
    if not all(isinstance(entry, str) and entry for entry in allowed):
        return None
    return workspace, frozenset(allowed)


def _canonical_relative_path(workspace: Path, raw_target: str) -> str | None:
    """The workspace-relative POSIX form of a *lexically* interpreted target.

    Purely lexical: this deliberately does not resolve symlinks (the resolved
    form is checked separately, and both must be authorized). Returns `None`
    when the target is outside the workspace, contains a `.git` component, or
    cannot be interpreted.
    """

    if "\x00" in raw_target:
        return None
    candidate = Path(raw_target)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    # `os.path.normpath` collapses `.`/`..` textually, which is exactly what is
    # needed to catch `allowed/../../escape` before touching the filesystem.
    normalized = Path(os.path.normpath(str(candidate)))
    try:
        relative = normalized.relative_to(workspace)
    except ValueError:
        return None
    parts = relative.parts
    if not parts or any(part == ".git" for part in parts):
        return None
    return relative.as_posix()


def _resolved_relative_path(workspace: Path, raw_target: str) -> str | None:
    """The workspace-relative POSIX form of the fully *resolved* target.

    Follows symlinks (via `realpath`) so an alias cannot escape the workspace
    or reach `.git/**`. Returns `None` when the resolved target leaves the
    workspace, contains a `.git` component, or cannot be resolved. A target
    that does not exist yet is fine -- `realpath` resolves the existing prefix,
    which is what matters for a symlinked parent directory.
    """

    if "\x00" in raw_target:
        return None
    candidate = Path(raw_target)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = Path(os.path.realpath(candidate))
        workspace_real = Path(os.path.realpath(workspace))
        relative = resolved.relative_to(workspace_real)
    except (OSError, ValueError):
        return None
    parts = relative.parts
    if not parts or any(part == ".git" for part in parts):
        return None
    return relative.as_posix()


def evaluate(payload: object, config: tuple[Path, frozenset[str]] | None) -> bool:
    """True means the target is authorized (defer); False means DENY.

    A True result is never itself a permission grant -- see module docstring.
    Every unrecognized shape, missing field, or unauthorized target fails
    closed; there is no path that infers authorization from absence of
    information.
    """

    if config is None:
        return False
    workspace, allowed_changed_paths = config

    if not isinstance(payload, dict):
        return False
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        return False
    target_field = _MUTATING_FILE_TOOL_TARGET_FIELDS.get(tool_name)
    if target_field is None:
        # Not a mutating file tool this guard governs. The matcher should not
        # have routed it here; deny rather than silently pass an unknown
        # mutating shape.
        return False

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    raw_target = tool_input.get(target_field)
    if not isinstance(raw_target, str) or not raw_target:
        return False

    lexical = _canonical_relative_path(workspace, raw_target)
    if lexical is None or lexical not in allowed_changed_paths:
        return False

    # Both halves must be authorized: the lexical request AND what it actually
    # resolves to. This is what stops an alias from widening the contract.
    resolved = _resolved_relative_path(workspace, raw_target)
    if resolved is None or resolved not in allowed_changed_paths:
        return False

    return True


def _run() -> bool:
    """True only for a verified authorized target.

    Free to raise: `main()`'s single boundary converts every exception into the
    identical fail-closed outcome, so "on failure, deny" is proven in exactly
    one place.
    """

    config = load_guard_config(os.environ.get(CONFIG_FILE_ENV))
    raw_stdin = sys.stdin.read()
    payload: object = json.loads(raw_stdin) if raw_stdin else None
    return evaluate(payload, config)


def main() -> int:
    """The single fail-closed boundary: exactly two possible exit codes.

    `BaseException` is caught deliberately rather than `Exception`: an uncaught
    `KeyboardInterrupt` (130) or `SystemExit(1)` would be just as non-blocking
    under Claude Code's PreToolUse contract as a plain crash, so both are
    converted to the same deny. Never prints a traceback, the stdin payload,
    or the rejected path.
    """

    try:
        if _run():
            return _PASS_EXIT_CODE
    except BaseException:  # noqa: BLE001 -- standalone security-hook boundary; see docstring.
        pass

    try:
        print(DENIAL_MESSAGE, file=sys.stderr)
    except BaseException:  # noqa: BLE001 -- a broken stderr must not prevent the blocking exit.
        pass

    return _DENY_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
