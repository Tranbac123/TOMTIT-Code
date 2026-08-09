"""Gate 3 capability spike: a bounded Claude Code CLI adapter.

This adapter executes exactly one external `claude` CLI process and returns an
immutable result describing what was observed. It owns no canonical state: it
does not import from `tomtit.runtime` or `tomtit.domain`, appends no event,
persists no decision, and decides nothing about retry, completion, or failure
classification beyond describing the process outcome. Runtime or a future
Coordinator interprets the result; this module only produces it.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

ADAPTER_NAME = "claude-code-cli"
ADAPTER_VERSION = "0.1.0-gate3-spike"

DEFAULT_ENVIRONMENT_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "TERM",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY",
)

_SECRET_NAME_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)

_IGNORED_DIR_NAMES = frozenset({".git", "__pycache__"})
_IGNORED_FILE_NAMES = frozenset({".DS_Store"})
_IGNORED_SUFFIXES = frozenset({".pyc"})

_MAX_SNAPSHOT_FILE_BYTES = 1_000_000
_PROCESS_POLL_INTERVAL_SECONDS = 0.02
_PROCESS_WAIT_GRACE_SECONDS = 5
_PROCESS_CLEANUP_GRACE_SECONDS = 5.0
_READER_JOIN_TIMEOUT_SECONDS = 5.0

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# --- Authorized-command (bounded Bash tool authorization) -----------------
#
# A caller may pass a small, exact allowlist of Bash commands the worker is
# authorized to run without an interactive approval prompt -- e.g.
# `npm install <exact package list> --save-exact`. This exists because
# `--permission-mode acceptEdits` only auto-approves file Write/Edit tool
# calls; Bash tool calls still require approval, and a non-interactive `-p`
# invocation has no channel for that prompt to ever be answered. See
# EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md for the reproduced
# defect and full design rationale.
#
# Commands are never treated as shell strings: each is parsed with
# `shlex.split`, rejected outright if it contains or requires shell syntax,
# and only ever passed to the real CLI as one argv element per rule inside
# `--allowedTools`, never through a shell interpreter.
#
# Authorized-command validation is deliberately stricter than the dogfood
# check-command validator (`run_review.validate_check_command_text`): a
# check command legitimately runs arbitrary read-only CI tooling (e.g.
# `python3 -c "import sys; sys.exit(1)"`, where `;` is data inside one
# quoted argument), so that validator only rejects an operator when it
# appears as its own shlex token. An authorized command instead grants the
# worker unattended Bash execution -- there is no legitimate reason for a
# shell metacharacter to appear anywhere in one, quoted or not, so every
# occurrence is rejected as a raw substring, matching this task's literal
# "reject commands *containing*..." requirement.
_RAW_FORBIDDEN_COMMAND_SUBSTRINGS = (
    "$(", "`", "\n", ";", "&&", "||", "|", ">>", "<<", ">", "<", "&",
)
# Glob/wildcard characters rejected *anywhere* in a token, not merely when a
# token consists entirely of them -- a package/path fragment like "foo*" or
# "*.txt" must be rejected exactly like a bare "*" (Codex corrective review,
# HIGH finding 1: "wildcard and direct-contract validation are incomplete").
_FORBIDDEN_GLOB_CHARACTERS = frozenset({"*", "?", "[", "]"})

# `NAME=value ...` is the shell environment-assignment form; rejected in any
# argv position (not only argv[0]) since an authorized command grants
# unattended execution, and there is no legitimate reason for one of its
# arguments to look like an assignment prefix.
_ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Nested-shell executable basenames: an authorized command must be the exact
# literal program invoked, never a shell that could reinterpret further
# arguments as its own script/command-line syntax. Matched case-foldedly
# against a basename extracted from *either* `/` or `\` separators (Codex
# validator-fix2 re-review, HIGH finding: "Bash", "/bin/BaSh", "./ZsH",
# "C:\tools\BaSh" were all previously accepted).
_FORBIDDEN_SHELL_EXECUTABLE_BASENAMES = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh"}
)
_FORBIDDEN_ENV_WRAPPER_BASENAME = "env"

# Credential-bearing option/header terms rejected wherever they appear inside
# a token (flag name, "--key=value" form, or a "Name: value" header-style
# token) -- case-insensitive, deliberately broad: an authorization allowlist
# is a security boundary, so a false-positive rejection is acceptable but a
# false-negative acceptance of a persisted credential is not.
_CREDENTIAL_TERM_PATTERN = re.compile(
    r"(token|password|passwd|secret|api[-_]?key|authorization|cookie|"
    r"private[-_]?key|bearer)",
    re.IGNORECASE,
)

# `scheme://user:password@host` URL user-info credential form.
_URL_USERINFO_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@]+:[^\s/@]+@")

# Credential-key material split across *adjacent* argv tokens (e.g.
# `--api key SECRET`, `Authorization Bearer SECRET`) is normalized into
# lowercase alphanumeric "words" per token, then scanned for these forbidden
# adjacent word pairs across the whole argv (Codex validator-fix2/fix3
# re-reviews: split credential-key forms, including punctuation-joined
# forms like `--api=key`/`--api,key`/`--api:key`, were accepted).
_SPLIT_CREDENTIAL_BIGRAMS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("access", "token"),
        ("auth", "token"),
        ("bearer", "token"),
        ("client", "secret"),
        ("authorization", "bearer"),
        ("authorization", "header"),
    }
)

# Canonical ASCII word extractor shared by the long-command-flag check and
# the split-credential-word scan: every maximal run of ASCII lowercase
# letters/digits is one "word", and everything else (`-`, `=`, `,`, `:`,
# `.`, `_`, or any other punctuation) is discarded as a separator, rather
# than requiring a fixed separator list (Codex validator-fix3 re-review).
_OPTION_WORD_PATTERN = re.compile(r"[a-z0-9]+")

# Compound shell/interpreter command-mode flags: any single-dash, all-letter
# short-option cluster containing `c` (e.g. `-c`, `-lc`, `-cl`, `-ic`, `-ci`,
# `-abc`, `-lci`). The long-form `--command` variant (in any case,
# repeated-dash, or punctuation-suffixed form) is handled separately by
# `_is_long_form_command_flag`, below.


def _is_forbidden_command_char(ch: str) -> bool:
    """NUL/control characters and non-ASCII/ambiguous whitespace, ASCII space excepted.

    A plain ASCII space is a legitimate character inside one shlex-quoted
    token (e.g. a file path with a space); every other whitespace-like or
    control character is rejected, including the ones (`\\n`, `\\r`, `\\t`)
    that `shlex.split` would otherwise silently treat as ordinary token
    separators if not caught before splitting.
    """

    if ch == " ":
        return False
    if ord(ch) < 0x20 or ord(ch) == 0x7F:
        return True
    return unicodedata.category(ch) in ("Zs", "Zl", "Zp", "Cf", "Cc")


def _token_basename(token: str) -> str:
    """The final path component of a token, for either `/` or `\\` separators."""

    return token.replace("\\", "/").rsplit("/", 1)[-1]


def _is_compound_interpreter_command_flag(token: str) -> bool:
    """A single-dash, all-letter short-option cluster that includes `c`.

    Matches `-c`, `-lc`, `-cl`, `-ic`, `-ci`, `-abc`, `-lci`, etc. Long-form
    (`--`) flags are handled separately by `_is_long_form_command_flag`,
    never merely for containing the letter `c` (that would reject unrelated
    flags like `--cache` or `--config`).
    """

    if len(token) < 2 or token[0] != "-" or token[1] == "-":
        return False
    letters = token[1:]
    if not letters.isalpha() or not letters.isascii():
        return False
    return "c" in letters.casefold()


def _normalize_option_words(token: str) -> list[str]:
    """Canonical option-key normalization for security classification.

    Applies only to a token beginning with one or more ASCII hyphens (an
    option-like token). Strips every leading hyphen, case-folds the rest,
    then extracts every maximal ASCII alphanumeric word via
    `_OPTION_WORD_PATTERN` -- `=`, `,`, `:`, `.`, `_`, `-`, and any other
    non-alphanumeric character all act as separators. Never returns or logs
    the original token; only the extracted lowercase words are ever used in
    a validation decision.
    """

    if not token.startswith("-"):
        return []
    return _OPTION_WORD_PATTERN.findall(token.lstrip("-").casefold())


def _is_long_form_command_flag(token: str) -> bool:
    """A hyphen-prefixed token whose first normalized option word is "command".

    Catches `-command`, `--command`, `--COMMAND`, `---command`,
    `--command=value`, `--COMMAND:value`, `--command,anything`,
    `--command.anything`, `--command_anything`, and any other repeated-dash,
    case, or punctuation-suffixed variant -- without relying on exact
    raw-string equality with `"--command"` (Codex validator-fix3 re-review).
    Deliberately does not flag `--cache`, `--commander`, or `--commandment`:
    the first extracted word must be *exactly* "command", not merely start
    with or contain it.
    """

    words = _normalize_option_words(token)
    return bool(words) and words[0] == "command"


def _credential_scan_words(token: str) -> list[str]:
    """Every maximal ASCII alphanumeric word in a token, case-folded.

    Unlike `_normalize_option_words`, this applies to *every* argv token,
    not only hyphen-prefixed ones -- credential material can appear in a
    bare value token too (e.g. a lone `Bearer` or `Authorization`). Any
    non-alphanumeric character (`-`, `=`, `,`, `:`, `.`, `_`, or anything
    else) is discarded as a separator rather than requiring a fixed
    separator list (Codex validator-fix3 re-review: `--api=key`,
    `--api,key`, `--api:key`, `--api.key` were all previously accepted
    because the prior splitter only recognized `_`/`-`/`.`/`:`, not `=`/`,`).
    """

    return _OPTION_WORD_PATTERN.findall(token.casefold())


def validate_authorized_command_argv(argv: Sequence[str]) -> None:
    """The single canonical validator for one authorized command's argv.

    Every entry path -- raw `--authorized-command` text (after
    `shlex.split`), `normalize_authorized_command`,
    `normalize_authorized_commands`, and `ClaudeCodeCLIRequest.__post_init__`
    -- calls exactly this function. No caller can bypass its checks by
    constructing a request directly: the dataclass re-validates every entry
    unconditionally (Codex corrective review, HIGH finding 1).

    Raises `AuthorizedCommandError` with a stable, bounded message. Error
    messages never include the rejected token or command text, since the
    rejected value could itself be the secret or injection payload being
    rejected (Codex corrective review, required-correction wording).
    """

    if not argv:
        raise AuthorizedCommandError("INVALID_AUTHORIZED_COMMAND", "authorized command must not be empty")
    for token in argv:
        if not isinstance(token, str) or not token:
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND", "authorized command contains an empty argument"
            )
        for ch in token:
            if _is_forbidden_command_char(ch):
                raise AuthorizedCommandError(
                    "INVALID_AUTHORIZED_COMMAND",
                    "authorized command contains a control character or forbidden whitespace",
                )
            # ASCII-only authorization grammar for this MVP trust boundary:
            # no Unicode normalization or confusable-character mapping is
            # attempted, so every character above the ASCII range is
            # rejected outright (Codex validator-fix2 re-review, HIGH
            # finding: ordinary non-ASCII characters and Unicode lookalike
            # executables were previously accepted).
            if ord(ch) > 127:
                raise AuthorizedCommandError(
                    "INVALID_AUTHORIZED_COMMAND", "authorized command contains a non-ASCII character"
                )
        for substring in _RAW_FORBIDDEN_COMMAND_SUBSTRINGS:
            if substring in token:
                raise AuthorizedCommandError(
                    "INVALID_AUTHORIZED_COMMAND",
                    "authorized command contains a rejected shell metacharacter or operator",
                )
        for glob_char in _FORBIDDEN_GLOB_CHARACTERS:
            if glob_char in token:
                raise AuthorizedCommandError(
                    "INVALID_AUTHORIZED_COMMAND",
                    "authorized command contains a rejected wildcard or glob character",
                )
        if _ENV_ASSIGNMENT_PATTERN.match(token):
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND", "authorized command contains an environment-assignment form"
            )
        # Case-folded, both-separator basename extraction: a mixed-case or
        # path-qualified variant (e.g. "Bash", "/bin/BaSh", "./ZsH",
        # "C:\tools\BaSh", "/usr/bin/ENV") must be rejected exactly like the
        # lowercase bare form.
        basename = _token_basename(token).casefold()
        if basename in _FORBIDDEN_SHELL_EXECUTABLE_BASENAMES:
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND", "authorized command references a nested shell interpreter"
            )
        if basename == _FORBIDDEN_ENV_WRAPPER_BASENAME:
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND", "authorized command references an env-based command wrapper"
            )
        if _is_compound_interpreter_command_flag(token) or _is_long_form_command_flag(token):
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND", "authorized command contains a shell interpreter command-mode flag"
            )
        if _CREDENTIAL_TERM_PATTERN.search(token):
            raise AuthorizedCommandError(
                "CREDENTIAL_BEARING_AUTHORIZED_COMMAND",
                "authorized command appears to contain a credential-bearing option or value",
            )
        if _URL_USERINFO_PATTERN.search(token):
            raise AuthorizedCommandError(
                "CREDENTIAL_BEARING_AUTHORIZED_COMMAND",
                "authorized command appears to contain a credential-bearing URL",
            )

    # Credential-key material split across *adjacent* argv tokens (e.g.
    # `--api key SECRET`, `Authorization Bearer SECRET`, or punctuation-joined
    # single-token forms like `--api=key`) cannot always be caught by the
    # single-token scan above; normalize every token into words and scan the
    # whole argv for a forbidden adjacent word pair.
    words: list[str] = []
    for token in argv:
        words.extend(_credential_scan_words(token))
    for first, second in zip(words, words[1:]):
        if (first, second) in _SPLIT_CREDENTIAL_BIGRAMS:
            raise AuthorizedCommandError(
                "CREDENTIAL_BEARING_AUTHORIZED_COMMAND",
                "authorized command appears to contain a credential-bearing option or value split across arguments",
            )

# Whether the installed Claude CLI's bare `Bash(<command>)` permission-rule
# form (no `:*` suffix, no `*` wildcard) matches the given command *raw-exactly*
# -- i.e. whether Claude's own permission engine, on its own, refuses every
# variant of an approved command.
#
# This is False because it has been **experimentally disproven**, not merely
# because it is unverified. An Owner-authorized real probe against Claude Code
# 2.1.223 established that a bare rule is matched *after* a fixed set of
# process-wrapper normalizations are stripped (`command`, `builtin`, `timeout`,
# `time`, `nice`, `nohup`, `stdbuf`, zsh `noglob`, certain env-assignment
# prefixes, bare `xargs`): with only the native rule in place, `command <exact>`
# executed. Native bare-rule matching is therefore provider-*normalized*
# equivalence, never TOMTIT's stricter Owner-approved exact argv.
#
# Keep this False. It is retained as the durable record of a measured provider
# behavior, and it deliberately no longer gates anything: nothing in this module
# reads it to grant authorization. Enablement is governed solely by
# `_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE` below, so there
# is no code path on which Claude's native `--allowedTools` alone can satisfy
# the exact-argv contract.
_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False

# Whether TOMTIT's *layered* exact-argv authorization capability is confirmed
# safe to use. This is the single enablement gate.
#
# It asserts the full contract below, not any one provider behavior. Every layer
# has been independently security-reviewed and, for the execution layer,
# operationally proven against real Claude Code 2.1.223:
#
#   1. Owner-authorized canonical argv validation
#      (`validate_authorized_command_argv`, re-run unconditionally by
#      `ClaudeCodeCLIRequest.__post_init__` so no caller can bypass it);
#   2. the TOMTIT PreToolUse exact-argv fingerprint guard
#      (`exact_bash_guard.py`, sharing this module's canonical validator and
#      SHA-256 fingerprint contract -- never a second, weaker validator);
#   3. hardened guard-process invocation (`-E -s`, see
#      `_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS`);
#   4. guard-file preflight (`_verified_exact_bash_guard_module_path`);
#   5. Claude's native `--allowedTools` bounded rules, retained as an outer
#      layer even though (1)-(4) are what make the grant exact;
#   6. the request's existing `--permission-mode` (never `bypassPermissions`);
#   7. provider-output persistence withholding + typed safe summary
#      (`_summarize_provider_output`), so an authorized run cannot persist
#      provider-controlled free-form text.
#
# Layers 2-4 are not merely asserted by this flag -- they are enforced at
# runtime by `run()`, which fails closed with `EXACT_BASH_GUARD_UNAVAILABLE`
# before starting any process if the guard artifacts cannot be constructed
# (which includes the preflight). Layer 7 is enforced by the persistence
# boundary in `run()`. This flag therefore records an Owner decision that the
# layered design is safe; it does not, and cannot, substitute for any layer.
#
# Setting this False restores the previous dormant behavior with no other code
# change: `resolve_claude_cli_authorization_contract` is the single call site.
_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE = True


class AuthorizedCommandError(Exception):
    """A supplied authorized command could not be safely normalized.

    Raised before any process is started; never surfaced as a raw exception
    to a caller that only expects `ClaudeCodeCLIResult` -- `run()` catches
    this at the trust boundary and returns an immutable FAILED result.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class ClaudeCLIAuthorizationContract(str, Enum):
    """Whether the installed Claude CLI can express bounded exact-command authorization."""

    EXACT_SUPPORTED = "EXACT_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


def normalize_authorized_command(raw_command: str) -> tuple[str, ...]:
    """Parse one `--authorized-command` string into a validated, bounded argv tuple.

    Never treats the command as an arbitrary shell string: parsed once with
    `shlex.split`, and rejected if it contains or requires any shell syntax
    (operators, redirection, substitution, background execution, wildcards).
    Raises `AuthorizedCommandError` rather than returning a sentinel, so a
    caller cannot accidentally proceed with an unvalidated command.
    """

    if not raw_command or not raw_command.strip():
        raise AuthorizedCommandError("INVALID_AUTHORIZED_COMMAND", "authorized command must not be empty")
    # Checked on the raw, pre-split string: `shlex.split` treats an embedded
    # newline as an ordinary token separator, which would otherwise let a
    # single-line-looking value silently smuggle extra argv tokens past this
    # validator.
    for ch in raw_command:
        if _is_forbidden_command_char(ch):
            raise AuthorizedCommandError(
                "INVALID_AUTHORIZED_COMMAND",
                "authorized command contains a control character or forbidden whitespace",
            )
    try:
        argv = tuple(shlex.split(raw_command))
    except ValueError as exc:
        raise AuthorizedCommandError(
            "INVALID_AUTHORIZED_COMMAND", f"authorized command could not be parsed: {exc}"
        ) from None
    validate_authorized_command_argv(argv)
    return argv


def normalize_authorized_commands(raw_commands: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Normalize, validate, and deterministically deduplicate a list of raw commands.

    Order-preserving: the first occurrence of an exact-argv duplicate wins,
    later duplicates are dropped, and the relative order of distinct commands
    is otherwise unchanged.
    """

    normalized: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for raw_command in raw_commands:
        argv = normalize_authorized_command(raw_command)
        if argv in seen:
            continue
        seen.add(argv)
        normalized.append(argv)
    return tuple(normalized)


def render_authorized_command_display(argv: tuple[str, ...]) -> str:
    """A normalized, shell-safe display string for one authorized command's argv."""

    return shlex.join(argv)


def render_authorized_command_cli_rule(argv: tuple[str, ...]) -> str:
    """The bounded `Bash(...)` permission rule for one authorized command.

    Uses the CLI's documented bare-rule form (no `:*`, no `*`). This rule is
    the *outer* layer only: a real probe measured bare-rule matching to strip
    process wrappers (see `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` above),
    so exactness is supplied by the TOMTIT PreToolUse guard in front of it,
    never by this rule on its own.
    """

    return f"Bash({render_authorized_command_display(argv)})"


def compute_authorized_command_fingerprint(argv: tuple[str, ...]) -> str:
    """A deterministic SHA-256 fingerprint of one authorized command's argv.

    Computed from a canonical JSON encoding of the argv list (order
    preserved, since argument order is part of what makes two commands the
    same command). Persisted evidence must carry this fingerprint instead of
    the raw argv (Codex corrective review, HIGH finding 2).
    """

    canonical = json.dumps(list(argv), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_authorized_command_set_fingerprint(commands: tuple[tuple[str, ...], ...]) -> str:
    """A deterministic SHA-256 fingerprint of an ordered set of authorized commands.

    Order-sensitive: reordering the same commands changes this fingerprint,
    so repair-attempt inheritance can detect a reorder as well as an
    addition, removal, or substitution using only fingerprint equality,
    never the raw commands themselves.
    """

    canonical = json.dumps([list(argv) for argv in commands], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_authorized_command_evidence_rule(argv: tuple[str, ...]) -> str:
    """The safe, persistable placeholder for one authorized command's rule.

    Never the raw `Bash(<command>)` rule -- only its fingerprint. This is
    what evidence (`sanitized_argv`, receipts, manifests) must contain; the
    raw rule from `render_authorized_command_cli_rule` may exist only in
    memory while constructing the real subprocess argv.
    """

    return f"Bash(<AUTHORIZED_COMMAND_SHA256:{compute_authorized_command_fingerprint(argv)}>)"


def resolve_claude_cli_authorization_contract() -> tuple[ClaudeCLIAuthorizationContract, str]:
    """Whether bounded exact-command authorization can be safely used right now.

    Keyed *solely* on `_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE`
    -- the layered TOMTIT capability -- and deliberately **not** on
    `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`, which records a provider
    behavior that was measured to be *non*-exact. There is intentionally no
    branch on which Claude's native `--allowedTools` alone yields
    EXACT_SUPPORTED: native bare-rule matching strips process wrappers, so
    relying on it by itself would grant more than the Owner approved.

    This is the single call site `run()` consults, so the one layered flag
    governs enablement in both directions.
    """

    if _CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE:
        return (
            ClaudeCLIAuthorizationContract.EXACT_SUPPORTED,
            "layered TOMTIT exact-argv authorization is enabled: Owner-authorized canonical "
            "argv validation, the TOMTIT PreToolUse exact-argv fingerprint guard (hardened "
            "-E -s invocation, guard-file preflight), Claude's native --allowedTools rules, "
            "the request's permission mode, and provider-output persistence withholding all "
            "apply together; exactness comes from the TOMTIT guard, never from Claude's own "
            "wrapper-normalized bare-rule matching",
        )
    return (
        ClaudeCLIAuthorizationContract.UNSUPPORTED,
        "layered TOMTIT exact-argv authorization is disabled; failing closed rather than "
        "relying on Claude's native bare Bash(<command>) matching alone, which was measured "
        "to strip process wrappers (`command`, `timeout`, `nice`, ...) and would therefore "
        "grant a silently broader set of commands than the approved argv",
    )


# --- Exact-argv PreToolUse Bash guard (defense-in-depth) ------------------
#
# An isolated real-CLI probe established that Claude Code's own
# `--allowedTools Bash(<command>)` permission rule is matched *after* a
# fixed set of process-wrapper normalizations are stripped (`command`,
# `builtin`, `timeout`, `time`, `nice`, `nohup`, `stdbuf`, zsh `noglob`,
# certain env-assignment prefixes, bare `xargs`) -- so it enforces
# provider-normalized equivalence, not TOMTIT's stricter Owner-approved
# exact argv. This section builds an ephemeral, session-scoped Claude Code
# PreToolUse hook (`exact_bash_guard.py`) that independently re-checks every
# Bash tool call against the exact same canonical validator/fingerprint
# contract used above, closing that gap as a second enforcement layer *in
# front of* Claude's own permission engine -- never in place of it. See
# `EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md`.
_EXACT_BASH_GUARD_MODULE_PATH = Path(__file__).resolve().parent / "exact_bash_guard.py"
_EXACT_BASH_GUARD_SETTINGS_FILENAME = "settings.json"
_EXACT_BASH_GUARD_FINGERPRINTS_FILENAME = "authorized_command_fingerprints.json"

# Matches `exact_bash_guard.FINGERPRINTS_FILE_ENV` -- duplicated as a literal
# (not imported) so this adapter never depends on being able to import the
# hook module, which is designed to run as a standalone subprocess.
_EXACT_BASH_GUARD_FINGERPRINTS_ENV = "TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE"

# Isolated-startup flags for the guard's own interpreter process (H-1/H-2
# re-review, finding N-1: "harden the hook invocation itself ... moves the
# defense to the process boundary, where it actually holds"):
#
#   -E  ignore every `PYTHON*` environment variable, so `PYTHONPATH`,
#       `PYTHONHOME`, and `PYTHONSTARTUP` cannot inject an ambient `tomtit`
#       package -- nor a `sitecustomize.py` -- into the guard's interpreter.
#   -s  disable the per-user site-packages directory, so neither a
#       user-site `tomtit` package nor a user-site `usercustomize.py` is
#       importable by the guard.
#
# These sit *in front of* the guard's own in-process trusted-import boundary
# (`exact_bash_guard._load_trusted_claude_code_cli`), never in place of it:
# the in-process check already blocks every one of these vectors that
# presents an honest `__file__`, and these flags additionally block the
# spoofed-`__file__` variant N-1 documented, which no in-process check can
# survive because it runs before the guard's own code does.
#
# `-S` is deliberately NOT used: it would disable `site` entirely, which the
# required scope excludes absent deterministic proof that the trusted import
# and all required standard behavior still work without it. `-E -s` is the
# approved scope.
#
# Scope limit worth recording honestly: `-E -s` does not neutralize a
# `sitecustomize.py` installed into the *system* site-packages directory,
# which requires privileged write access to the interpreter's own
# installation -- a strictly higher bar than the unprivileged `PYTHONPATH`
# and user-site vectors these flags do close.
_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS: tuple[str, ...] = ("-E", "-s")


# --- Claude native Bash execution sandbox (H-1 enforcement layer) ---------
#
# The workspace snapshot is an ACCOUNTING + DETECTION layer. It observes what
# changed after the fact; it cannot prevent a write. Containment for Bash and
# its descendants must come from an actual OS-level execution boundary.
#
# Claude Code 2.1.223 supports OS-level Bash sandboxing (seatbelt on macOS,
# namespaces/seccomp on Linux; "Sandboxing is currently only supported on
# macOS, Linux, and WSL2"). The setting keys below were verified to exist in
# the installed binary before being used -- they are not guessed. There is no
# `--sandbox` CLI flag in this version; the sandbox is configured through the
# settings JSON TOMTIT already generates.
#
# The four required properties, and why each matters:
#   enabled=true                  -- turn the OS boundary on at all.
#   failIfUnavailable=true        -- if the platform cannot sandbox, FAIL
#                                    rather than silently running unconfined.
#   allowUnsandboxedCommands=false-- remove the retry-outside-the-sandbox
#                                    escape hatch.
#   autoAllowBashIfSandboxed=false-- being sandboxed must NOT auto-approve
#                                    Bash; the exact-argv guard and
#                                    `--allowedTools` remain authoritative
#                                    over *which* commands may start.
#   excludedCommands=[]           -- no command is exempt from the sandbox.
#   filesystem.disabled=false     -- filesystem isolation can never be off.
_SANDBOX_DENY_WRITE_SUBPATHS: tuple[str, ...] = (".git",)


def _build_sandbox_settings(workspace: Path) -> dict[str, object]:
    """The TOMTIT-controlled Claude Bash sandbox contract.

    Write access is scoped to the workspace and then `.git` is explicitly
    denied within it, so Bash and its descendants can neither write outside
    the workspace nor mutate the workspace's own Git metadata. This is the
    enforcement counterpart to the side-effect *accounting* contract -- the
    two are independent and neither implies the other.
    """

    workspace_path = str(Path(os.path.realpath(workspace)))
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": False,
        "excludedCommands": [],
        "filesystem": {
            "disabled": False,
            "allowWrite": [workspace_path],
            "denyWrite": [str(Path(workspace_path) / sub) for sub in _SANDBOX_DENY_WRITE_SUBPATHS],
        },
    }


# TOMTIT's execution boundary must not be widenable by an untrusted workspace.
# `--setting-sources` takes a comma-separated subset of {user, project, local}
# (verified: an invalid value is rejected with exactly that list, and an empty
# value is accepted). Passing an EMPTY value loads none of them, so a target
# repository's `.claude/settings.json` or `.claude/settings.local.json` cannot
# merge in a wider `sandbox.filesystem.allowWrite`, re-add
# `sandbox.excludedCommands`, flip `sandbox.enabled`, or register competing
# permissions/hooks. Only TOMTIT's own `--settings` file applies.
_EXACT_BASH_GUARD_SETTING_SOURCES = ""


class _ExactBashGuardIntegrityError(OSError):
    """The guard script is missing, or is not the trusted TOMTIT-owned regular file.

    Subclasses `OSError` deliberately, so `run()`'s already-reviewed
    `except OSError` fail-closed path (-> `EXACT_BASH_GUARD_UNAVAILABLE`,
    before any process is started) catches it unchanged -- this hardening
    adds a new *reason* to fail closed, never a new outcome or a new
    routing path.
    """


def _verified_exact_bash_guard_module_path() -> Path:
    """Preflight the guard script's identity, or raise `_ExactBashGuardIntegrityError`.

    Deliberately narrow (the required MVP scope): this is not a signing or
    attestation system and creates no persistence. It only proves that the
    single, known, TOMTIT-owned absolute path this adapter is about to hand
    to Claude as a PreToolUse hook command still resolves to a real regular
    file at the location this module itself was loaded from.

    `os.lstat` is used rather than `Path.exists()`/`Path.is_file()` so the
    symlink case is *detected* rather than silently followed:
    `stat.S_ISREG` on an `lstat` result is False for a symlink (the mode is
    `S_IFLNK`), a directory, a FIFO, or a device node -- so one check covers
    symlink substitution and every non-regular-file substitution together.
    The subsequent full-path `.resolve()` comparison is belt-and-braces for
    a parent directory component having become a symlink after this module
    was imported.

    Addresses H-1/H-2 re-review finding N-2 ("guard-file corruption is
    fail-open"): a *missing* guard file already made Python exit 2, but a
    substituted or non-regular path is now rejected before Claude is started
    at all, rather than being discovered only once the hook runs. Note this
    cannot detect a *corrupted-but-still-regular* guard file -- N-2's
    truncation/syntax-error case remains an out-of-band integrity assumption
    that no check inside this process can close.
    """

    expected_path = _EXACT_BASH_GUARD_MODULE_PATH
    try:
        stat_result = os.lstat(expected_path)
    except OSError:
        # Never interpolates the errno/strerror text: the message stays a
        # fixed shape so nothing environment-derived reaches evidence.
        raise _ExactBashGuardIntegrityError(
            "the exact-argv PreToolUse guard script is missing or unreadable at its expected location"
        ) from None
    if not stat.S_ISREG(stat_result.st_mode):
        raise _ExactBashGuardIntegrityError(
            "the exact-argv PreToolUse guard script path is not a regular file"
        )
    if expected_path.resolve() != expected_path:
        raise _ExactBashGuardIntegrityError(
            "the exact-argv PreToolUse guard script path does not resolve to its expected location"
        )
    return expected_path


def _render_exact_bash_guard_hook_command(guard_module_path: Path) -> str:
    """The PreToolUse hook command line: `<sys.executable> -E -s <guard path>`.

    Every element is `shlex.quote`d individually and joined with single
    spaces -- no shell wrapper, no `shell=True` anywhere in this adapter,
    and no environment-dependent interpreter discovery (`sys.executable` is
    TOMTIT's own already-running interpreter, an absolute path, never a
    bare `python3` resolved through `PATH`). The isolation flags always
    precede the script path, as the interpreter requires.
    """

    parts = (sys.executable, *_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS, str(guard_module_path))
    return " ".join(shlex.quote(part) for part in parts)


# --- Built-in mutating file-tool guard (Write/Edit/MultiEdit/NotebookEdit) ---
#
# Claude's OS-level Bash sandbox covers `Bash` and its descendants only. The
# built-in mutating file tools run inside the CLI process itself and are NOT
# sandboxed, so without this guard they could write outside the workspace,
# into `.git/**`, or to in-workspace source files the Owner never authorized --
# and TOMTIT would only notice afterwards via the workspace snapshot. Post-run
# detection is not a boundary.
#
# This guard is therefore required on EVERY worker run, independent of
# `authorized_commands`: file edits are possible whether or not any Bash
# command was authorized. Its authority comes solely from
# `allowed_changed_paths`; `allowed_side_effect_paths` deliberately grants it
# nothing (that contract exists to *account for* generated output, not to
# permit source editing).
_EXACT_FILE_WRITE_GUARD_MODULE_PATH = Path(__file__).resolve().parent / "exact_file_write_guard.py"
_EXACT_FILE_WRITE_GUARD_CONFIG_FILENAME = "file_write_guard_config.json"

# Matches `exact_file_write_guard.CONFIG_FILE_ENV` -- duplicated as a literal
# (not imported) so this adapter never depends on importing the standalone
# hook module.
_EXACT_FILE_WRITE_GUARD_CONFIG_ENV = "TOMTIT_EXACT_FILE_WRITE_GUARD_CONFIG_FILE"

# One matcher entry per tool rather than a single compound pattern: the exact
# compound-matcher semantics of this CLI version were not verified, and an
# unmatched mutating tool would silently bypass the guard. Separate literal
# entries are unambiguous.
_EXACT_FILE_WRITE_GUARD_TOOLS: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")


class _ExactFileWriteGuardIntegrityError(OSError):
    """The file-write guard script is missing or is not the trusted TOMTIT file.

    Subclasses `OSError` so `run()`'s fail-closed path catches it alongside
    any other artifact-construction failure -- a new *reason* to refuse to
    start, never a new outcome.
    """


def _verified_exact_file_write_guard_module_path() -> Path:
    """Preflight the file-write guard script, mirroring the Bash guard's check.

    `os.lstat` (not `exists()`/`is_file()`) so a symlink is *detected* rather
    than silently followed: `stat.S_ISREG` on an `lstat` result is False for a
    symlink, a directory, a FIFO, or a device node, so one check covers every
    substitution. Messages stay a fixed shape and never interpolate the path.
    """

    expected_path = _EXACT_FILE_WRITE_GUARD_MODULE_PATH
    try:
        stat_result = os.lstat(expected_path)
    except OSError:
        raise _ExactFileWriteGuardIntegrityError(
            "the file-write PreToolUse guard script is missing or unreadable at its expected location"
        ) from None
    if not stat.S_ISREG(stat_result.st_mode):
        raise _ExactFileWriteGuardIntegrityError(
            "the file-write PreToolUse guard script path is not a regular file"
        )
    if expected_path.resolve() != expected_path:
        raise _ExactFileWriteGuardIntegrityError(
            "the file-write PreToolUse guard script path does not resolve to its expected location"
        )
    return expected_path


def _render_exact_file_write_guard_hook_command(guard_module_path: Path) -> str:
    """`<sys.executable> -E -s <guard path>` -- same hardening as the Bash guard.

    Every element is `shlex.quote`d individually; no shell wrapper, no
    `shell=True`, and no environment-dependent interpreter discovery.
    """

    parts = (sys.executable, *_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS, str(guard_module_path))
    return " ".join(shlex.quote(part) for part in parts)


def _build_file_write_guard_hook_entries(guard_module_path: Path) -> list[dict[str, object]]:
    """One PreToolUse entry per mutating file tool."""

    hook_command = _render_exact_file_write_guard_hook_command(guard_module_path)
    return [
        {"matcher": tool, "hooks": [{"type": "command", "command": hook_command}]}
        for tool in _EXACT_FILE_WRITE_GUARD_TOOLS
    ]


def _build_file_write_guard_config(
    workspace: Path, allowed_changed_paths: tuple[str, ...]
) -> dict[str, object]:
    """The guard's entire on-disk configuration.

    Deliberately minimal: the canonical workspace root and the exact
    Owner-approved source allowlist, and nothing else. No prompt, no provider
    output, no secrets, no authorized Bash commands, and explicitly **no
    side-effect roots** -- those must never be able to authorize a file write.
    """

    return {
        "workspace": str(Path(os.path.realpath(workspace))),
        "allowed_changed_paths": list(allowed_changed_paths),
    }


@dataclasses.dataclass(frozen=True, slots=True)
class _ExactBashGuardArtifacts:
    """Paths to one invocation's ephemeral PreToolUse guard artifacts.

    One temp directory owns the whole bundle so lifetime is unambiguous: the
    settings file (always), the file-write guard config (always), and the
    Bash-guard fingerprints file (only when Bash commands were authorized).
    """

    directory: Path
    settings_path: Path
    file_write_config_path: Path
    fingerprints_path: Path | None = None


def _write_exact_bash_guard_artifacts(
    authorized_commands: tuple[tuple[str, ...], ...],
    workspace: Path,
    allowed_changed_paths: tuple[str, ...] = (),
) -> _ExactBashGuardArtifacts:
    """Write one invocation's ephemeral, session-scoped guard settings pair.

    Contains only SHA-256 fingerprints of the authorized commands -- never
    their raw argv -- so it is safe for these files to exist on disk, even
    though nothing else in this adapter ever writes authorized-command state
    to disk (Codex corrective review, HIGH finding 2: evidence must carry
    fingerprints only). Raises `OSError` on any failure to construct the
    directory or write either file -- or, via the
    `_ExactBashGuardIntegrityError` subclass, if the guard script itself
    fails its preflight; the caller must fail closed rather than proceed
    without a trustworthy guard installed.
    """

    # Preflight every guard script *first*: nothing is created on disk, and no
    # process is started, if a thing we are about to register as a PreToolUse
    # hook is not the trusted TOMTIT-owned file. The file-write guard is
    # required on every run, so its preflight is unconditional.
    file_write_guard_path = _verified_exact_file_write_guard_module_path()
    bash_guard_path = _verified_exact_bash_guard_module_path() if authorized_commands else None

    directory = Path(tempfile.mkdtemp(prefix="tomtit-exact-bash-guard-"))
    os.chmod(directory, 0o700)

    # Always present: the built-in mutating file tools can run whether or not
    # any Bash command was authorized, so their guard is unconditional.
    file_write_config_path = directory / _EXACT_FILE_WRITE_GUARD_CONFIG_FILENAME
    file_write_config_path.write_text(
        json.dumps(_build_file_write_guard_config(workspace, allowed_changed_paths)), encoding="utf-8"
    )
    os.chmod(file_write_config_path, 0o600)

    pre_tool_use: list[dict[str, object]] = _build_file_write_guard_hook_entries(file_write_guard_path)
    settings: dict[str, object] = {}

    fingerprints_path: Path | None = None
    if authorized_commands and bash_guard_path is not None:
        # Bash-only layers. Deliberately conditional: with no Owner-authorized
        # command, no Bash can run, so a sandbox with `failIfUnavailable`
        # would add availability risk without adding containment.
        fingerprints = [compute_authorized_command_fingerprint(argv) for argv in authorized_commands]
        fingerprints_path = directory / _EXACT_BASH_GUARD_FINGERPRINTS_FILENAME
        fingerprints_path.write_text(json.dumps(fingerprints), encoding="utf-8")
        os.chmod(fingerprints_path, 0o600)

        pre_tool_use.append(
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": _render_exact_bash_guard_hook_command(bash_guard_path)}
                ],
            }
        )
        settings["sandbox"] = _build_sandbox_settings(workspace)

    settings["hooks"] = {"PreToolUse": pre_tool_use}
    settings_path = directory / _EXACT_BASH_GUARD_SETTINGS_FILENAME
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    os.chmod(settings_path, 0o600)

    return _ExactBashGuardArtifacts(
        directory=directory,
        settings_path=settings_path,
        file_write_config_path=file_write_config_path,
        fingerprints_path=fingerprints_path,
    )


def _cleanup_exact_bash_guard_artifacts(artifacts: _ExactBashGuardArtifacts | None) -> None:
    """Best-effort removal of one invocation's ephemeral guard files.

    Never raises: cleanup failure must not mask or replace the real
    invocation's outcome. Leaving a stray temp directory behind is a
    containment concern only (fingerprints, not secrets, not raw commands),
    not a correctness one.
    """

    if artifacts is None:
        return
    shutil.rmtree(artifacts.directory, ignore_errors=True)


# --- Provider-output persistence boundary (M-1 / M-2) ---------------------
#
# Claude's own stdout/stderr is provider-controlled data with an OPEN
# schema. It can echo a raw Bash command -- one this adapter authorized, or
# one it rejected -- through `permission_denials[].tool_input.command`, a
# free-form `result`/`error`/`message`/`reason` string, a nested
# `content[].text` array, an alternate key spelling (`cmd`, `Command`), a
# key that does not exist yet in any Claude version, or plain unstructured
# prose that is not JSON at all.
#
# The previous design tried to *redact* known-bad shapes: parse the JSON,
# replace any dict key literally named "command". Two independent defects
# followed, both reproduced in review:
#
#   M-1  A denylist of provider field names cannot be complete against an
#        open schema. Every key above except `tool_input.command` leaked.
#   M-2  Redaction depended on `json.loads` succeeding. `_StreamReader`
#        truncates at `max_stdout_bytes` (default 64 KB) *before* redaction
#        runs, so a truncated payload is invalid JSON, the redactor's
#        `except ValueError: return text` path returned the raw prefix
#        unchanged, and every command in it was persisted verbatim.
#
# Neither is fixable by adding more field names, because the failure mode is
# structural: the code decided what to *remove* from attacker-controlled
# text and kept the remainder. This module now inverts that decision at the
# persistence boundary. When exact-command authorization is active, TOMTIT
# does not persist provider-controlled free-form text AT ALL -- it derives a
# summary whose every value is a bool, an int, a float, or None, and
# persists that instead. A raw command string cannot be a float, so the
# invariant holds by construction for unknown keys, unknown nesting, invalid
# JSON, truncation, and any future provider format, with no parser success
# required for confidentiality.
#
# The raw text still exists transiently in process memory for the duration
# of `run()` (it is what the summary is derived from) -- see
# `_summarize_provider_output` -- but no field carrying it is placed on the
# result, and therefore no serialization path can reach it.
_PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER = "[TOMTIT_PROVIDER_OUTPUT_WITHHELD_EXACT_AUTHORIZATION]"

# Numeric paths lifted into the safe summary. This is an ALLOWLIST of values
# TOMTIT chooses to derive, not a denylist of values to strip -- anything not
# named here is simply never looked at. Each is additionally type-checked at
# extraction time, so a provider that returns a *string* where a number is
# expected (`"total_cost_usd": "<command text>"`) yields None rather than
# smuggling text through a numeric-looking field.
_SAFE_PROVIDER_NUMERIC_PATHS: tuple[tuple[str, ...], ...] = (
    ("num_turns",),
    ("total_cost_usd",),
    ("usage", "input_tokens"),
    ("usage", "output_tokens"),
)


def _extract_safe_number(payload: Mapping[str, object], path: tuple[str, ...]) -> int | float | None:
    """The value at `path`, only if it is a real number. Never a string.

    `bool` is excluded explicitly: it is a subclass of `int` in Python, and
    letting it through would blur a flag into a count. Anything else --
    string, list, dict, None, missing key, or a non-mapping on the way down
    -- yields None.
    """

    current: object = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, bool):
        return None
    if isinstance(current, (int, float)):
        return current
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class _ProviderOutputSummary:
    """Persist-safe metadata derived from provider stdout/stderr.

    Every field is a bool, an int, a float, or None -- there is deliberately
    no `str` field on this type, which is what makes "no provider text can
    be persisted" checkable by reading the annotations rather than by
    auditing every call site. Byte counts and truncation flags describe the
    stream; the derived numbers come only from `_SAFE_PROVIDER_NUMERIC_PATHS`.
    """

    stdout_bytes: int
    stderr_bytes: int
    stdout_json_parsed: bool
    is_error: bool | None
    permission_denial_count: int | None
    num_turns: int | float | None
    total_cost_usd: int | float | None
    input_tokens: int | float | None
    output_tokens: int | float | None


def _summarize_provider_output(stdout_text: str, stderr_text: str) -> _ProviderOutputSummary:
    """Derive persist-safe metadata from raw provider output.

    Parsing is best-effort and used ONLY to enrich the summary, never to
    decide whether the raw text is safe to keep: a parse failure (truncated,
    mixed prose, or plain non-JSON) simply leaves the derived fields None
    while the byte counts and flags remain accurate. That is the M-2 fix --
    an unparseable payload degrades to *less* metadata, never to "return the
    original text".
    """

    stdout_bytes = len(stdout_text.encode("utf-8"))
    stderr_bytes = len(stderr_text.encode("utf-8"))

    parsed: object
    try:
        parsed = json.loads(stdout_text) if stdout_text.strip() else None
    except ValueError:
        parsed = None

    if not isinstance(parsed, Mapping):
        return _ProviderOutputSummary(
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout_json_parsed=False,
            is_error=None,
            permission_denial_count=None,
            num_turns=None,
            total_cost_usd=None,
            input_tokens=None,
            output_tokens=None,
        )

    raw_is_error = parsed.get("is_error")
    denials = parsed.get("permission_denials")
    numbers = {path[-1]: _extract_safe_number(parsed, path) for path in _SAFE_PROVIDER_NUMERIC_PATHS}

    return _ProviderOutputSummary(
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_json_parsed=True,
        is_error=raw_is_error if isinstance(raw_is_error, bool) else None,
        permission_denial_count=len(denials) if isinstance(denials, list) else None,
        num_turns=numbers["num_turns"],
        total_cost_usd=numbers["total_cost_usd"],
        input_tokens=numbers["input_tokens"],
        output_tokens=numbers["output_tokens"],
    )


class ClaudeCodeCLIStatus(str, Enum):
    """Closed outcome vocabulary. Only SUCCEEDED means the invocation is trustworthy."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNAVAILABLE = "UNAVAILABLE"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


class _ProcessGroupCleanup(str, Enum):
    """Outcome of bounded worker process-group cleanup.

    Only QUIESCENT and CLEANED permit a successful result: both mean no member
    of the worker group can still mutate the workspace. FAILED means quiescence
    could not be proven, so every snapshot-derived claim is untrustworthy.
    """

    QUIESCENT = "PROCESS_GROUP_QUIESCENT"
    CLEANED = "PROCESS_GROUP_CLEANED"
    FAILED = "PROCESS_GROUP_CLEANUP_FAILED"


@dataclasses.dataclass(frozen=True, slots=True)
class ClaudeCodeCLIRequest:
    """A bounded, immutable invocation request.

    Carries no Runtime-authority field and no full canonical record: only the
    identifiers needed to correlate a result with its caller, and the bounded
    execution parameters the adapter needs to run one process safely.
    """

    task_id: str
    run_id: str
    attempt: int
    goal: str
    prompt: str
    working_directory: Path
    timeout_seconds: int
    allowed_changed_paths: tuple[str, ...]
    environment_allowlist: tuple[str, ...] = DEFAULT_ENVIRONMENT_ALLOWLIST
    max_stdout_bytes: int = 65536
    max_stderr_bytes: int = 65536
    model: str | None = None
    permission_mode: str = "acceptEdits"
    authorized_commands: tuple[tuple[str, ...], ...] = ()
    # Owner-approved roots beneath which generated/side-effect filesystem
    # mutations are expected (e.g. `node_modules`, `.next`). Independent of
    # `authorized_commands`: neither contract implies the other.
    allowed_side_effect_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if not self.goal:
            raise ValueError("goal must be non-empty")
        if not self.prompt:
            raise ValueError("prompt must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_stdout_bytes <= 0:
            raise ValueError("max_stdout_bytes must be positive")
        if self.max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")
        object.__setattr__(self, "working_directory", Path(self.working_directory))
        object.__setattr__(self, "allowed_changed_paths", tuple(self.allowed_changed_paths))
        object.__setattr__(self, "environment_allowlist", tuple(self.environment_allowlist))
        # Trust boundary (SYSTEM.md §4: "revalidate canonical data at trust
        # boundaries"): `authorized_commands` is expected to already be the
        # output of `normalize_authorized_commands()`, but every entry is
        # re-validated here through the exact same canonical validator used
        # by the raw-text path, so a caller cannot bypass any rejected
        # syntax by constructing this request directly (Codex corrective
        # review, HIGH finding 1).
        validated_commands: list[tuple[str, ...]] = []
        for raw_entry in self.authorized_commands:
            argv = tuple(raw_entry)
            validate_authorized_command_argv(argv)
            validated_commands.append(argv)
        object.__setattr__(self, "authorized_commands", tuple(validated_commands))
        # Same trust-boundary treatment for the side-effect contract: even a
        # caller that constructs this request directly cannot smuggle an
        # absolute path, a `..` traversal, or a `.git` root past validation.
        object.__setattr__(
            self,
            "allowed_side_effect_paths",
            normalize_allowed_side_effect_paths(tuple(self.allowed_side_effect_paths)),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ClaudeCodeCLIResult:
    """An immutable observation of one Claude Code CLI invocation.

    This is data only. Nothing about it writes canonical state; the caller
    decides what the result means.
    """

    adapter_name: str
    adapter_version: str
    task_id: str
    run_id: str
    attempt: int
    status: ClaudeCodeCLIStatus
    success: bool
    executable_path: str | None
    sanitized_argv: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    exit_code: int | None
    timed_out: bool
    stdout_excerpt: str
    stderr_excerpt: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    workspace_before_hash: str | None
    workspace_after_hash: str | None
    changed_paths: tuple[str, ...]
    unexpected_changed_paths: tuple[str, ...]
    patch_path: str | None
    evidence_manifest_path: str | None
    error_category: str | None
    error_message: str | None
    # Fingerprints only, never the raw authorized-command argv or rendered
    # Bash rule (Codex corrective review, HIGH finding 2): the raw values
    # may exist only in memory while constructing the real subprocess argv,
    # never in this result or anything derived from it for persistence.
    authorized_command_fingerprints: tuple[str, ...] = ()
    authorized_command_set_fingerprint: str | None = None
    authorized_command_count: int = 0
    authorized_command_cli_rules: tuple[str, ...] = ()
    # Whether the TOMTIT-controlled exact-argv PreToolUse guard (defense in
    # depth in front of Claude's native `--allowedTools` matching) was
    # installed for this invocation. Never carries the hook's raw decisions
    # or the raw command it evaluated -- only this boolean.
    exact_bash_guard_enabled: bool = False
    # Provider-output persistence boundary (M-1 / M-2). When True,
    # `stdout_excerpt`/`stderr_excerpt` carry a fixed placeholder instead of
    # provider-controlled text, and the `provider_*` fields below carry the
    # persist-safe summary derived from it. Every one of those fields is a
    # bool/int/float/None by type -- none is a `str` -- so no provider text
    # can reach any serialization path through them.
    provider_output_withheld: bool = False
    provider_stdout_bytes: int | None = None
    provider_stderr_bytes: int | None = None
    provider_stdout_json_parsed: bool | None = None
    provider_is_error: bool | None = None
    provider_permission_denial_count: int | None = None
    provider_num_turns: int | float | None = None
    provider_total_cost_usd: int | float | None = None
    provider_input_tokens: int | float | None = None
    provider_output_tokens: int | float | None = None
    # Authorized generated side effects. `changed_paths` above deliberately
    # excludes these (it stays the product change set), so downstream
    # scope checks keep working unchanged. Visibility is preserved here as
    # bounded evidence -- the Owner's approved roots, a total count, a count
    # per root, and a small sample -- never the full generated path list,
    # which can run to thousands of entries.
    allowed_side_effect_paths: tuple[str, ...] = ()
    side_effect_changed_path_count: int = 0
    side_effect_changed_path_counts_by_root: tuple[tuple[str, int], ...] = ()
    side_effect_changed_paths_sample: tuple[str, ...] = ()


def discover_claude_executable(explicit_path: str | None = None) -> str | None:
    """Locate the `claude` executable without shell interpolation.

    This is the single validation boundary for executables: an explicit
    override is honored only if it resolves to an executable regular file, so
    a caller-supplied path can never reach `subprocess.Popen` unchecked. A
    relative override is resolved explicitly rather than left ambiguous.
    Otherwise discovery uses `shutil.which`, which never invokes a shell.
    """

    if explicit_path:
        candidate = Path(explicit_path)
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            return None
        return None
    return shutil.which("claude")


def build_child_environment(allowlist: Sequence[str]) -> dict[str, str]:
    """Build a child environment containing only allowlisted, present variables."""

    return {name: os.environ[name] for name in allowlist if name in os.environ}


def _collect_secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    values = {value for name, value in env.items() if value and _SECRET_NAME_PATTERN.search(name)}
    # Longest first, so a short secret cannot mask part of a longer one during replacement.
    return tuple(sorted(values, key=len, reverse=True))


def _redact(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for value in secret_values:
        redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _redact_home(text: str, home: str | None) -> str:
    if not home or len(home) < 4:
        return text
    return text.replace(home, "$HOME")


def _iter_workspace_relative_paths(root: Path) -> Iterator[str]:
    """Walk the workspace WITHOUT ever dereferencing or descending a symlink.

    H-1: `rglob("*")` + `is_dir()` both follow symlinks. That made the
    observer (a) hash the *resolved target bytes* of a file symlink under its
    lexical workspace alias -- so a `node_modules/x -> /outside/secret` alias
    read external content and was then classified as an ordinary authorized
    side effect -- and (b) skip directory symlinks entirely via `is_dir()`,
    so anything written through `node_modules/dir-link -> /outside/dir` was
    wholly invisible.

    This walk is explicitly `lstat`-based: symlinks are yielded as their own
    observable entries (never followed, never read), and directory symlinks
    terminate traversal instead of being descended. `os.walk(followlinks=False)`
    is used rather than `rglob` because it lets the symlink decision be made
    per entry, before any stat that would resolve it.
    """

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        rel_parts = () if rel_dir == Path(".") else rel_dir.parts
        if any(part in _IGNORED_DIR_NAMES for part in rel_parts):
            dirnames[:] = []
            continue
        # Never descend into an ignored directory, and never descend through a
        # directory symlink -- but still surface the symlink itself below, so
        # its creation/removal/retargeting remains observable.
        kept_dirnames: list[str] = []
        for name in dirnames:
            if name in _IGNORED_DIR_NAMES:
                continue
            if (current / name).is_symlink():
                # Observed as a symlink entry, not traversed.
                continue
            kept_dirnames.append(name)
        symlinked_dirs = [
            name
            for name in dirnames
            if name not in _IGNORED_DIR_NAMES and (current / name).is_symlink()
        ]
        dirnames[:] = sorted(kept_dirnames)

        for name in sorted(filenames) + sorted(symlinked_dirs):
            path = current / name
            if name in _IGNORED_FILE_NAMES:
                continue
            # Suffix filtering must not hide a symlink: an alias named
            # `x.pyc -> /outside/secret` would otherwise vanish.
            if not path.is_symlink() and Path(name).suffix in _IGNORED_SUFFIXES:
                continue
            yield (rel_dir / name).as_posix() if rel_parts else name


def describe_symlink(root: Path, rel: str) -> str:
    """A bounded, deterministic identity for a symlink -- never its target bytes.

    Encodes the raw link text plus whether the resolved target stays inside
    the workspace and outside `.git`. Retargeting the link changes this
    string even when the new target's *content* is identical, so a retarget
    is always observable as a change. The target is never opened or read.
    """

    link = root / rel
    try:
        raw_target = os.readlink(link)
    except OSError:
        return "symlink:unreadable"
    try:
        resolved = Path(os.path.realpath(link))
        workspace = Path(os.path.realpath(root))
        inside = resolved == workspace or workspace in resolved.parents
        rel_resolved = resolved.relative_to(workspace).as_posix() if inside else None
    except (OSError, ValueError):
        inside, rel_resolved = False, None
    if not inside:
        location = "external"
    elif rel_resolved is not None and (rel_resolved == ".git" or rel_resolved.startswith(".git/")):
        location = "git-metadata"
    else:
        location = "internal"
    digest = hashlib.sha256(raw_target.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"symlink:{location}:{digest}"


def symlink_target_is_safe(root: Path, rel: str) -> bool:
    """True only when a symlink resolves inside the workspace and outside `.git`.

    Used by classification so a lexically-authorized alias (e.g. under
    `node_modules`) cannot launder an external or Git-metadata target into an
    ordinary authorized side effect. Legitimate npm-internal symlinks -- which
    resolve to real paths inside the workspace -- remain compatible.
    """

    return describe_symlink(root, rel).startswith("symlink:internal:")


def snapshot_workspace_hashes(root: Path) -> dict[str, str]:
    """Hash every tracked-relevant entry, for change detection and scope proof.

    A symlink is recorded by its *link identity* (see `describe_symlink`) and
    is never dereferenced: reading a symlink's target here would both hash
    content that lives outside the workspace and let an external target
    masquerade as an ordinary in-workspace file (H-1).
    """

    hashes: dict[str, str] = {}
    for rel in _iter_workspace_relative_paths(root):
        path = root / rel
        if path.is_symlink():
            hashes[rel] = describe_symlink(root, rel)
            continue
        try:
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            hashes[rel] = "unreadable"
    return hashes


def workspace_hash(file_hashes: Mapping[str, str]) -> str:
    """A single deterministic hash summarizing an entire workspace snapshot."""

    canonical = json.dumps(dict(sorted(file_hashes.items())), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_workspace_contents(
    root: Path,
    *,
    max_file_bytes: int = _MAX_SNAPSHOT_FILE_BYTES,
) -> dict[str, bytes | None]:
    """Read file contents for later patch rendering. `None` marks an omitted file."""

    contents: dict[str, bytes | None] = {}
    for rel in _iter_workspace_relative_paths(root):
        path = root / rel
        try:
            if path.stat().st_size > max_file_bytes:
                contents[rel] = None
            else:
                contents[rel] = path.read_bytes()
        except OSError:
            contents[rel] = None
    return contents


def _diff_changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    keys = set(before) | set(after)
    return tuple(sorted(path for path in keys if before.get(path) != after.get(path)))


# --- Authorized generated side-effect paths -------------------------------
#
# A real dogfood run (personal-web-content-exactauth-rerun-002) was rejected
# with 2709 "unexpected" changed paths that were entirely `node_modules/**`
# (2517) and `.next/**` (192) -- the generated output of the Owner-approved
# `npm install` / `npm run build` commands. The actual product change set was
# exactly 17 paths, all inside the approved source allowlist. TOMTIT was
# classifying authorized commands' generated filesystem output as
# unauthorized source changes.
#
# The fix is a SECOND, INDEPENDENT Owner contract -- not a relaxation of the
# first. Two properties matter and are deliberately kept separate:
#
#   * Authorizing a Bash command (`--authorized-command "npm run build"`)
#     does NOT grant that command arbitrary filesystem scope.
#   * Authorizing a side-effect root (`--allowed-side-effect-path .next`)
#     does NOT grant any Bash execution.
#
# Both gates must independently pass. Classification is strictly ordered --
# source allowlist first, then side-effect authorization, then fail closed --
# so a path can never be laundered from "unexpected" to "fine" by anything
# other than an explicit Owner decision.
#
# This is deliberately NOT derived from `.gitignore`: ignored files routinely
# include `.env`, credentials, and private configuration, so treating "git
# ignores it" as "TOMTIT may write it" would silently widen the trust
# boundary. Ignored-but-unauthorized mutations still fail closed.
_MAX_SIDE_EFFECT_SAMPLE_PATHS = 10


class AllowedSideEffectPathError(Exception):
    """A supplied allowed-side-effect path could not be safely normalized.

    Raised before any process is started. Carries a stable category/message
    pair mirroring `AuthorizedCommandError`, and never echoes back the
    rejected value (it could itself be an injection payload).
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def normalize_allowed_side_effect_path(raw_path: str) -> str:
    """Canonicalize one Owner-approved generated/side-effect path prefix.

    Fail-closed. Rejects, at minimum: non-strings, empty values, absolute
    paths, the repository root (`.` / `./` / `/`), any `..` traversal,
    backslash separators (a Windows-style alias that would defeat POSIX
    component checks), `.git` and anything beneath it, and any non-normalized
    alias (`a//b`, `a/./b`, `foo/`) that could otherwise be used to smuggle a
    different effective path past containment. Returns the canonical
    slash-joined relative form used for all later comparisons.
    """

    if not isinstance(raw_path, str) or not raw_path:
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must be a non-empty string"
        )
    if "\\" in raw_path:
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must use POSIX separators"
        )
    if "\x00" in raw_path:
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path contains a NUL character"
        )
    if raw_path.startswith("/"):
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must be relative, not absolute"
        )

    parts = raw_path.split("/")
    # Reject every non-normalized alias explicitly rather than silently
    # collapsing it: silently normalizing `a/./b` would mean the value the
    # Owner approved and the value TOMTIT enforces are not the same string.
    for part in parts:
        if part == "":
            raise AllowedSideEffectPathError(
                "INVALID_SIDE_EFFECT_PATH",
                "allowed side-effect path must not contain empty components or a trailing slash",
            )
        if part == ".":
            raise AllowedSideEffectPathError(
                "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must not contain '.' components"
            )
        if part == "..":
            raise AllowedSideEffectPathError(
                "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must not contain '..' traversal"
            )

    canonical = "/".join(parts)
    if canonical in (".", ""):
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path must not be the workspace root"
        )
    # Git metadata can never be authorized through this feature, at ANY depth.
    # M-2: the previous implementation checked only `parts[0]`, so `a/.git` and
    # `a/.git/config` were accepted despite the stated contract. Nested Git
    # directories are real (submodules, vendored checkouts under
    # `node_modules`), so this must be a component-wise check.
    if any(part == ".git" for part in parts):
        raise AllowedSideEffectPathError(
            "INVALID_SIDE_EFFECT_PATH", "allowed side-effect path may not target Git metadata at any depth"
        )
    return canonical


def normalize_allowed_side_effect_paths(raw_paths: Sequence[str]) -> tuple[str, ...]:
    """Normalize, validate, and deterministically deduplicate side-effect roots.

    Order-preserving on first occurrence, matching
    `normalize_authorized_commands`, so the Owner-approved set has one stable
    canonical representation for fingerprinting and inheritance checks.
    """

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        value = normalize_allowed_side_effect_path(raw_path)
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _path_is_within_side_effect_root(rel_path: str, root: str) -> bool:
    """Component-aware containment: is `rel_path` at or beneath `root`?

    Deliberately not a string-prefix test. `node_modules-evil/foo` shares the
    textual prefix `node_modules` but is a different top-level directory, so
    it must NOT match an authorization for `node_modules`. Comparing split
    component tuples makes that structural rather than incidental.
    """

    path_parts = rel_path.split("/")
    root_parts = root.split("/")
    if len(path_parts) < len(root_parts):
        return False
    return path_parts[: len(root_parts)] == root_parts


def classify_workspace_changes(
    changed_paths: Sequence[str],
    allowed_source_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str] = (),
    workspace_root: Path | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split observed mutations into (source, authorized side effect, unexpected).

    Strictly ordered, so authorization can only ever be granted by an
    explicit Owner decision and never inferred:

        in the exact source allowlist        -> SOURCE_CHANGE
        else under an authorized root        -> AUTHORIZED_SIDE_EFFECT
        else                                 -> UNEXPECTED_CHANGE (fail closed)

    With no authorized side-effect roots this reduces exactly to the previous
    two-way split, so existing callers are unaffected.
    """

    source_allowed = set(allowed_source_paths)
    source: list[str] = []
    side_effects: list[str] = []
    unexpected: list[str] = []
    for rel_path in changed_paths:
        if rel_path in source_allowed:
            source.append(rel_path)
        elif any(_path_is_within_side_effect_root(rel_path, root) for root in allowed_side_effect_paths):
            # H-1: lexical containment alone is NOT sufficient. If the entry is
            # a symlink, its resolved target must also stay inside the
            # workspace and outside `.git`; otherwise an alias under an
            # authorized root would launder an external / Git-metadata write
            # into an "ordinary authorized side effect". Fail closed when the
            # workspace root is unknown and the entry cannot be checked.
            if workspace_root is not None and (workspace_root / rel_path).is_symlink():
                if symlink_target_is_safe(workspace_root, rel_path):
                    side_effects.append(rel_path)
                else:
                    unexpected.append(rel_path)
            else:
                side_effects.append(rel_path)
        else:
            unexpected.append(rel_path)
    return tuple(source), tuple(side_effects), tuple(unexpected)


def summarize_side_effect_paths(
    side_effect_paths: Sequence[str],
    allowed_side_effect_paths: Sequence[str],
) -> tuple[tuple[tuple[str, int], ...], tuple[str, ...]]:
    """Bounded evidence for observed side effects: per-root counts + a sample.

    A real run produced 2709 generated paths. Persisting or printing all of
    them would drown the evidence and the terminal, so visibility is kept via
    a count per authorized root (bounded by the Owner's own approval list)
    plus a small fixed-size sample. The full set is never materialized into
    evidence.
    """

    counts: dict[str, int] = {root: 0 for root in allowed_side_effect_paths}
    for rel_path in side_effect_paths:
        for root in allowed_side_effect_paths:
            if _path_is_within_side_effect_root(rel_path, root):
                counts[root] += 1
                break
    by_root = tuple((root, counts[root]) for root in allowed_side_effect_paths)
    sample = tuple(side_effect_paths[:_MAX_SIDE_EFFECT_SAMPLE_PATHS])
    return by_root, sample


_NO_NEWLINE_MARKER = "\\ No newline at end of file"
_DIFF_CONTEXT_LINES = 3


def _split_lines_for_diff(data: bytes) -> tuple[list[str], bool]:
    """Decode to line content (no line-ending characters) plus a missing-final-newline flag.

    `str.splitlines()` already discards a trailing newline without producing a
    spurious empty final element, so the returned lines never carry their own
    line terminator: each hunk line built from them gets exactly one `-`/`+`/`
    ` prefix and nothing else, which is what keeps the rendered patch valid.
    The separate flag preserves the one piece of information `splitlines()`
    throws away — whether the file's last line was itself terminated — so the
    standard `\\ No newline at end of file` marker can still be emitted.
    """

    text = data.decode("utf-8", errors="replace")
    missing_final_newline = bool(text) and not text.endswith("\n")
    return text.splitlines(), missing_final_newline


def _format_range_unified(start: int, stop: int) -> str:
    """Hunk range formatting, matching `difflib`'s own `_format_range_unified`."""

    beginning = start + 1
    length = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1
    return f"{beginning},{length}"


def _render_file_hunks(
    before_lines: Sequence[str],
    after_lines: Sequence[str],
    *,
    before_missing_newline: bool,
    after_missing_newline: bool,
    from_label: str,
    to_label: str,
) -> str:
    """Render one file's unified-diff hunks from index-tracked opcodes.

    Built directly on `difflib.SequenceMatcher` (the stdlib diff engine)
    rather than `difflib.unified_diff`, so each emitted line can be checked
    against the true last index of its source file before deciding whether a
    `No newline at end of file` marker belongs after it. `difflib.unified_diff`
    does not expose that index, which is what made the previous
    keepends-based renderer unable to place the marker at all.
    """

    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(n=_DIFF_CONTEXT_LINES))
    if not groups:
        return ""

    out: list[str] = [f"--- {from_label}", f"+++ {to_label}"]
    for group in groups:
        a_start, a_end = group[0][1], group[-1][2]
        b_start, b_end = group[0][3], group[-1][4]
        out.append(
            f"@@ -{_format_range_unified(a_start, a_end)} "
            f"+{_format_range_unified(b_start, b_end)} @@"
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for idx in range(i1, i2):
                    out.append(" " + before_lines[idx])
                    if idx == len(before_lines) - 1 and before_missing_newline:
                        out.append(_NO_NEWLINE_MARKER)
                continue
            if tag in ("replace", "delete"):
                for idx in range(i1, i2):
                    out.append("-" + before_lines[idx])
                    if idx == len(before_lines) - 1 and before_missing_newline:
                        out.append(_NO_NEWLINE_MARKER)
            if tag in ("replace", "insert"):
                for idx in range(j1, j2):
                    out.append("+" + after_lines[idx])
                    if idx == len(after_lines) - 1 and after_missing_newline:
                        out.append(_NO_NEWLINE_MARKER)
    return "\n".join(out) + "\n"


def render_unified_diff(
    before_contents: Mapping[str, bytes | None],
    after_contents: Mapping[str, bytes | None],
    changed_paths: Sequence[str],
) -> str:
    """A bounded unified diff over exactly the changed paths. Git-independent."""

    sections: list[str] = []
    for rel_path in changed_paths:
        before_present = rel_path in before_contents
        after_present = rel_path in after_contents
        before_bytes = before_contents.get(rel_path)
        after_bytes = after_contents.get(rel_path)

        omitted = (before_present and before_bytes is None) or (after_present and after_bytes is None)
        if omitted:
            sections.append(
                f"--- a/{rel_path}\n+++ b/{rel_path}\n"
                "@@ content omitted: file exceeds the diff size bound or was unreadable @@\n"
            )
            continue

        if before_present and before_bytes is not None:
            before_lines, before_missing_newline = _split_lines_for_diff(before_bytes)
        else:
            before_lines, before_missing_newline = [], False
        if after_present and after_bytes is not None:
            after_lines, after_missing_newline = _split_lines_for_diff(after_bytes)
        else:
            after_lines, after_missing_newline = [], False

        from_label = f"a/{rel_path}" if before_present else "/dev/null"
        to_label = f"b/{rel_path}" if after_present else "/dev/null"
        section = _render_file_hunks(
            before_lines,
            after_lines,
            before_missing_newline=before_missing_newline,
            after_missing_newline=after_missing_newline,
            from_label=from_label,
            to_label=to_label,
        )
        if section:
            sections.append(section)
    return "".join(sections)


def build_evidence_manifest(result: ClaudeCodeCLIResult) -> dict[str, object]:
    """The plain-dict form of a result, ready for deterministic JSON rendering."""

    return {
        "adapter_name": result.adapter_name,
        "adapter_version": result.adapter_version,
        "task_id": result.task_id,
        "run_id": result.run_id,
        "attempt": result.attempt,
        "status": result.status.value,
        "success": result.success,
        "executable_path": result.executable_path,
        "sanitized_argv": list(result.sanitized_argv),
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stdout_excerpt": result.stdout_excerpt,
        "stderr_excerpt": result.stderr_excerpt,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "workspace_before_hash": result.workspace_before_hash,
        "workspace_after_hash": result.workspace_after_hash,
        "changed_paths": list(result.changed_paths),
        "unexpected_changed_paths": list(result.unexpected_changed_paths),
        "patch_path": result.patch_path,
        "evidence_manifest_path": result.evidence_manifest_path,
        "error_category": result.error_category,
        "error_message": result.error_message,
        "authorized_command_fingerprints": list(result.authorized_command_fingerprints),
        "authorized_command_set_fingerprint": result.authorized_command_set_fingerprint,
        "authorized_command_count": result.authorized_command_count,
        "authorized_command_cli_rules": list(result.authorized_command_cli_rules),
        "exact_bash_guard_enabled": result.exact_bash_guard_enabled,
        # Persist-safe provider-output summary (M-1 / M-2). `stdout_excerpt`
        # and `stderr_excerpt` above already carry only a fixed placeholder
        # whenever `provider_output_withheld` is True.
        "provider_output_withheld": result.provider_output_withheld,
        "provider_stdout_bytes": result.provider_stdout_bytes,
        "provider_stderr_bytes": result.provider_stderr_bytes,
        "provider_stdout_json_parsed": result.provider_stdout_json_parsed,
        "provider_is_error": result.provider_is_error,
        "provider_permission_denial_count": result.provider_permission_denial_count,
        "provider_num_turns": result.provider_num_turns,
        "provider_total_cost_usd": result.provider_total_cost_usd,
        "provider_input_tokens": result.provider_input_tokens,
        "provider_output_tokens": result.provider_output_tokens,
        # Authorized generated side effects: bounded evidence only (counts,
        # per-root counts, small sample) -- never the full generated list.
        "allowed_side_effect_paths": list(result.allowed_side_effect_paths),
        "allowed_side_effect_path_count": len(result.allowed_side_effect_paths),
        "observed_authorized_side_effect_count": result.side_effect_changed_path_count,
        "observed_authorized_side_effect_counts_by_root": {
            root: count for root, count in result.side_effect_changed_path_counts_by_root
        },
        "observed_authorized_side_effect_sample": list(result.side_effect_changed_paths_sample),
    }


def render_evidence_manifest_json(result: ClaudeCodeCLIResult) -> str:
    """Stable, sorted-key JSON so the same result always renders identically."""

    manifest = build_evidence_manifest(result)
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_evidence(
    result: ClaudeCodeCLIResult,
    *,
    before_contents: Mapping[str, bytes | None],
    after_contents: Mapping[str, bytes | None],
    output_dir: Path,
) -> ClaudeCodeCLIResult:
    """Persist a sanitized patch and manifest, returning the result with their paths filled in."""

    output_dir.mkdir(parents=True, exist_ok=True)
    patch_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_DIFF.patch"
    patch_path.write_text(
        render_unified_diff(before_contents, after_contents, result.changed_paths),
        encoding="utf-8",
    )

    manifest_path = output_dir / "CLAUDE_CODE_CLI_SPIKE_RESULT.json"
    finalized = dataclasses.replace(
        result,
        patch_path=str(patch_path),
        evidence_manifest_path=str(manifest_path),
    )
    manifest_path.write_text(render_evidence_manifest_json(finalized), encoding="utf-8")
    return finalized


class _StreamReader(threading.Thread):
    """Drains a pipe into a bounded buffer while hashing the full stream.

    The hash covers everything read, even past the excerpt bound, so evidence
    proves what was actually produced rather than just what was retained.
    """

    def __init__(self, stream: object, max_bytes: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._max_bytes = max_bytes
        self._hasher = hashlib.sha256()
        self.buffer = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(4096)  # type: ignore[attr-defined]
                if not chunk:
                    break
                self._hasher.update(chunk)
                if len(self.buffer) < self._max_bytes:
                    remaining = self._max_bytes - len(self.buffer)
                    self.buffer.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.truncated = True
                else:
                    self.truncated = True
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()  # type: ignore[attr-defined]
            except OSError:
                pass

    @property
    def digest(self) -> str:
        return self._hasher.hexdigest()


def _signal_process_group(pgid: int, sig: int) -> bool:
    """Signal an entire worker process group.

    Returns True when the signal was delivered or the group was already gone,
    and False when the group exists but cannot be safely controlled. Only the
    group created by this adapter's own `start_new_session=True` child is ever
    signalled; no unrelated system process is scanned or targeted.
    """

    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _process_group_alive(pgid: int) -> bool | None:
    """Probe group membership: True alive, False absent, None indeterminate.

    A POSIX process group exists exactly while it still has a member, so a
    signal-0 probe answers "can anything in this invocation still run?".
    `PermissionError` means the group exists but is not ours to control, which
    is treated as indeterminate rather than quiescent.
    """

    if os.name != "posix":
        return None
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return None
    return True


def _await_process_group_absence(pgid: int, *, deadline: float) -> bool | None:
    """Poll until the group is absent or the bounded monotonic deadline passes."""

    while True:
        alive = _process_group_alive(pgid)
        if alive is not True:
            return alive
        if time.monotonic() >= deadline:
            return True
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)


def ensure_process_group_quiescent(
    pgid: int,
    *,
    grace_seconds: float | None = None,
) -> _ProcessGroupCleanup:
    """Prove the worker group is finished, terminating survivors if necessary.

    Escalates SIGTERM, bounded grace, then SIGKILL, and verifies absence after
    each step. Every wait is bounded by a monotonic deadline. Anything that
    cannot be positively verified — an indeterminate probe, a group that
    outlives SIGKILL, or a platform without process-group verification — fails
    closed as FAILED rather than being reported as quiescent.
    """

    if grace_seconds is None:
        grace_seconds = _PROCESS_CLEANUP_GRACE_SECONDS

    if os.name != "posix":
        return _ProcessGroupCleanup.FAILED
    if pgid <= 1:
        return _ProcessGroupCleanup.FAILED
    try:
        if pgid == os.getpgrp():
            # Never signal TOMTIT's own process group.
            return _ProcessGroupCleanup.FAILED
    except OSError:
        return _ProcessGroupCleanup.FAILED

    alive = _process_group_alive(pgid)
    if alive is False:
        return _ProcessGroupCleanup.QUIESCENT
    if alive is None:
        return _ProcessGroupCleanup.FAILED

    if not _signal_process_group(pgid, signal.SIGTERM):
        return _ProcessGroupCleanup.FAILED
    alive = _await_process_group_absence(pgid, deadline=time.monotonic() + grace_seconds)
    if alive is False:
        return _ProcessGroupCleanup.CLEANED
    if alive is None:
        return _ProcessGroupCleanup.FAILED

    if not _signal_process_group(pgid, signal.SIGKILL):
        return _ProcessGroupCleanup.FAILED
    alive = _await_process_group_absence(pgid, deadline=time.monotonic() + grace_seconds)
    if alive is False:
        return _ProcessGroupCleanup.CLEANED
    return _ProcessGroupCleanup.FAILED


@dataclasses.dataclass(frozen=True, slots=True)
class _ExecutionOutcome:
    """Frozen observation of one subprocess, captured after group quiescence.

    Stream evidence is copied out of the reader buffers only once the readers
    have finished, so no live thread can mutate what the result reports.
    """

    exit_code: int | None
    timed_out: bool
    stdout_bytes: bytes
    stderr_bytes: bytes
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    cleanup: _ProcessGroupCleanup
    readers_alive: bool
    start_failure: tuple[ClaudeCodeCLIStatus, str, str] | None = None


def _start_failure_outcome(
    status: ClaudeCodeCLIStatus,
    error_category: str,
    error_message: str,
) -> _ExecutionOutcome:
    """A normalized outcome for a process that never started. Nothing to clean up."""

    return _ExecutionOutcome(
        exit_code=None,
        timed_out=False,
        stdout_bytes=b"",
        stderr_bytes=b"",
        stdout_sha256=_EMPTY_SHA256,
        stderr_sha256=_EMPTY_SHA256,
        stdout_truncated=False,
        stderr_truncated=False,
        cleanup=_ProcessGroupCleanup.QUIESCENT,
        readers_alive=False,
        start_failure=(status, error_category, error_message),
    )


def _execute(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> _ExecutionOutcome:
    """Run one bounded subprocess. No shell, no retry, no background survivor.

    Returns only after the entire worker process group is proven quiescent (or
    cleanup is reported as failed) and both readers have finished, so a caller
    that snapshots the workspace afterwards cannot be raced by a descendant.
    """

    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # POSIX: proc.pid becomes the new process group id.
        )
    except FileNotFoundError as exc:
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.UNAVAILABLE,
            "EXECUTABLE_UNAVAILABLE",
            f"executable could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )
    except PermissionError as exc:
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.UNAVAILABLE,
            "EXECUTABLE_NOT_EXECUTABLE",
            f"executable could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )
    except OSError as exc:
        # Deliberately excludes str(exc): the message stays a fixed shape so no
        # environment-derived text can reach persisted evidence.
        return _start_failure_outcome(
            ClaudeCodeCLIStatus.FAILED,
            "PROCESS_START_FAILED",
            f"process could not be started: {exc.__class__.__name__} errno={exc.errno}",
        )

    pgid = proc.pid
    stdout_reader = _StreamReader(proc.stdout, max_stdout_bytes)
    stderr_reader = _StreamReader(proc.stderr, max_stderr_bytes)
    stdout_reader.start()
    stderr_reader.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)

    if timed_out:
        # Stop the group first, then reap the direct parent so its zombie
        # cannot keep the group alive for the quiescence probe below.
        if not _signal_process_group(pgid, signal.SIGTERM):
            proc.terminate()
        try:
            proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            if not _signal_process_group(pgid, signal.SIGKILL):
                proc.kill()
            try:
                proc.wait(timeout=_PROCESS_WAIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass

    # Every return path, normal exit included: a descendant that outlives its
    # parent still belongs to this invocation and must not be able to write
    # after the result is formed.
    cleanup = ensure_process_group_quiescent(pgid)

    # Only now can the pipes reach EOF, because the group holding their write
    # ends is gone. Joining before cleanup is what let readers survive.
    join_timeout = _READER_JOIN_TIMEOUT_SECONDS
    stdout_reader.join(timeout=join_timeout)
    stderr_reader.join(timeout=join_timeout)
    readers_alive = stdout_reader.is_alive() or stderr_reader.is_alive()

    return _ExecutionOutcome(
        exit_code=proc.returncode,
        timed_out=timed_out,
        stdout_bytes=bytes(stdout_reader.buffer),
        stderr_bytes=bytes(stderr_reader.buffer),
        stdout_sha256=stdout_reader.digest,
        stderr_sha256=stderr_reader.digest,
        stdout_truncated=stdout_reader.truncated,
        stderr_truncated=stderr_reader.truncated,
        cleanup=cleanup,
        readers_alive=readers_alive,
    )


def _determine_status(
    *,
    exit_code: int | None,
    timed_out: bool,
    unexpected_changed_paths: Sequence[str],
    expected_changed: bool,
    cleanup: _ProcessGroupCleanup = _ProcessGroupCleanup.QUIESCENT,
    readers_alive: bool = False,
) -> tuple[ClaudeCodeCLIStatus, bool, str | None, str | None]:
    # Fail closed before anything else. If the worker group or its readers
    # cannot be proven finished, the workspace snapshot behind every check
    # below may already be stale, so no other classification can be reported
    # as though it were reliable. The observed paths are still returned on the
    # result, so this ordering hides no data — only the trust claim changes.
    if cleanup is _ProcessGroupCleanup.FAILED:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "PROCESS_CLEANUP_FAILED",
            "worker process group could not be proven quiescent before evidence capture",
        )
    if readers_alive:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "PROCESS_CLEANUP_FAILED",
            "a worker output reader was still running after process cleanup",
        )
    # An unexpected write is a safety-relevant condition, checked next: even a
    # timed-out or nonzero-exit process may already have written outside scope.
    if unexpected_changed_paths:
        return (
            ClaudeCodeCLIStatus.SCOPE_VIOLATION,
            False,
            "UNEXPECTED_CHANGED_PATHS",
            f"changed paths outside the allowed set: {list(unexpected_changed_paths)}",
        )
    if timed_out:
        return (
            ClaudeCodeCLIStatus.TIMED_OUT,
            False,
            "TIMEOUT",
            "process exceeded the configured timeout and was terminated",
        )
    if exit_code != 0:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "NONZERO_EXIT",
            f"process exited with code {exit_code}",
        )
    if not expected_changed:
        return (
            ClaudeCodeCLIStatus.FAILED,
            False,
            "NO_EXPECTED_CHANGE",
            "process exited 0 but no allowed path was changed",
        )
    return ClaudeCodeCLIStatus.SUCCEEDED, True, None, None


class ClaudeCodeCLIAdapter:
    """Executes one Claude Code CLI process and returns an immutable result.

    This class owns no canonical state. It does not import Runtime or domain
    modules, and `run()` never mutates anything outside the child process's own
    workspace and the value it returns.
    """

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def run(
        self,
        request: ClaudeCodeCLIRequest,
        *,
        executable_path: str | None = None,
    ) -> ClaudeCodeCLIResult:
        started_at = datetime.now(timezone.utc)
        start_monotonic = time.monotonic()

        working_directory = request.working_directory
        if not working_directory.is_absolute():
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category="INVALID_WORKSPACE",
                error_message="working_directory must be an absolute path",
            )
        if not working_directory.exists() or not working_directory.is_dir():
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category="INVALID_WORKSPACE",
                error_message="working_directory does not exist or is not a directory",
            )

        # Explicit overrides go through the same validation boundary as
        # discovery; an unchecked caller path must never reach Popen.
        resolved_executable = discover_claude_executable(executable_path)
        if resolved_executable is None:
            explicit = bool(executable_path)
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.UNAVAILABLE,
                error_category="EXECUTABLE_UNAVAILABLE" if explicit else "CLI_NOT_FOUND",
                error_message=(
                    "explicit executable_path is not an executable regular file"
                    if explicit
                    else "claude executable was not discovered"
                ),
            )

        # Authorization-contract check happens before any process is started
        # ("validated before process invocation") -- an authorized command
        # that cannot be safely bounded must never silently fall back to no
        # authorization at all (the worker would just hit the same denied
        # prompt) nor silently escalate to a broader grant.
        #
        # Fingerprints are computed up front, from the request's own argv,
        # never from anything persisted: they are the only authorized-
        # command-derived values that ever leave this method as evidence.
        authorized_fingerprints = tuple(
            compute_authorized_command_fingerprint(argv) for argv in request.authorized_commands
        )
        authorized_set_fingerprint = (
            compute_authorized_command_set_fingerprint(request.authorized_commands)
            if request.authorized_commands
            else None
        )
        authorized_rules: tuple[str, ...] = ()
        exact_bash_guard_artifacts: _ExactBashGuardArtifacts | None = None
        if request.authorized_commands:
            contract, rationale = resolve_claude_cli_authorization_contract()
            if contract is not ClaudeCLIAuthorizationContract.EXACT_SUPPORTED:
                return self._early_result(
                    request,
                    started_at,
                    start_monotonic,
                    status=ClaudeCodeCLIStatus.FAILED,
                    error_category="UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION",
                    error_message=rationale,
                    executable_path=resolved_executable,
                    authorized_command_fingerprints=authorized_fingerprints,
                    authorized_command_set_fingerprint=authorized_set_fingerprint,
                    authorized_command_count=len(request.authorized_commands),
                )
            authorized_rules = tuple(render_authorized_command_cli_rule(argv) for argv in request.authorized_commands)

        # Guard artifacts are built on EVERY run, not only Bash-authorized
        # ones: the built-in mutating file tools (Write/Edit/MultiEdit/
        # NotebookEdit) are not covered by Claude's Bash sandbox and can run
        # regardless of `authorized_commands`, so their PreToolUse guard is
        # mandatory. If any guard layer cannot be safely constructed --
        # including the file-write guard's own script preflight and config --
        # fail closed before starting the worker rather than proceed with an
        # unenforced file-tool boundary.
        try:
            exact_bash_guard_artifacts = _write_exact_bash_guard_artifacts(
                request.authorized_commands, working_directory, request.allowed_changed_paths
            )
        except _ExactFileWriteGuardIntegrityError:
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category="FILE_WRITE_GUARD_UNAVAILABLE",
                error_message="the file-write PreToolUse guard could not be safely constructed",
                executable_path=resolved_executable,
                authorized_command_fingerprints=authorized_fingerprints,
                authorized_command_set_fingerprint=authorized_set_fingerprint,
                authorized_command_count=len(request.authorized_commands),
            )
        except OSError:
            return self._early_result(
                request,
                started_at,
                start_monotonic,
                status=ClaudeCodeCLIStatus.FAILED,
                error_category=(
                    "EXACT_BASH_GUARD_UNAVAILABLE"
                    if request.authorized_commands
                    else "FILE_WRITE_GUARD_UNAVAILABLE"
                ),
                error_message="a required PreToolUse guard could not be safely constructed",
                executable_path=resolved_executable,
                authorized_command_fingerprints=authorized_fingerprints,
                authorized_command_set_fingerprint=authorized_set_fingerprint,
                authorized_command_count=len(request.authorized_commands),
            )

        child_env = build_child_environment(request.environment_allowlist)
        if exact_bash_guard_artifacts is not None:
            child_env[_EXACT_FILE_WRITE_GUARD_CONFIG_ENV] = str(
                exact_bash_guard_artifacts.file_write_config_path
            )
            if exact_bash_guard_artifacts.fingerprints_path is not None:
                child_env[_EXACT_BASH_GUARD_FINGERPRINTS_ENV] = str(
                    exact_bash_guard_artifacts.fingerprints_path
                )
        secret_values = _collect_secret_values(child_env)
        home_value = child_env.get("HOME")

        before_hashes = snapshot_workspace_hashes(working_directory)
        argv = self._build_argv(resolved_executable, request, authorized_rules, exact_bash_guard_artifacts)

        try:
            outcome = _execute(
                argv,
                cwd=working_directory,
                env=child_env,
                timeout_seconds=request.timeout_seconds,
                max_stdout_bytes=request.max_stdout_bytes,
                max_stderr_bytes=request.max_stderr_bytes,
            )
        finally:
            # Cleanup happens as soon as the real subprocess (and everything
            # it spawned, including any hook invocation) is proven quiescent
            # -- never before, and never skipped even on an unexpected
            # exception path.
            _cleanup_exact_bash_guard_artifacts(exact_bash_guard_artifacts)

        # Ordering invariant: `_execute` has already proven the worker group
        # quiescent and joined both readers, so this snapshot cannot be raced
        # by a descendant of the invocation it is meant to describe.
        after_hashes = snapshot_workspace_hashes(working_directory)
        observed_changed_paths = _diff_changed_paths(before_hashes, after_hashes)
        # Full filesystem observation is retained -- nothing is skipped or
        # filtered out of the snapshot. What changed is only how an observed
        # mutation is *classified*: source, explicitly Owner-authorized
        # generated side effect, or unexpected (fail closed).
        source_changed_paths, side_effect_changed_paths, unexpected_changed_paths = classify_workspace_changes(
            observed_changed_paths,
            request.allowed_changed_paths,
            request.allowed_side_effect_paths,
            workspace_root=working_directory,
        )
        side_effect_counts_by_root, side_effect_sample = summarize_side_effect_paths(
            side_effect_changed_paths, request.allowed_side_effect_paths
        )
        # `changed_paths` remains the product change set: source changes plus
        # anything unexpected. Authorized generated output is reported
        # separately (bounded) rather than inflating this list to thousands
        # of entries, which would also break downstream scope checks that
        # compare it against the source allowlist.
        changed_paths = tuple(sorted(source_changed_paths + unexpected_changed_paths))
        expected_changed = bool(source_changed_paths)

        status: ClaudeCodeCLIStatus
        success: bool
        error_category: str | None
        error_message: str | None
        if outcome.start_failure is not None:
            status, error_category, error_message = outcome.start_failure
            success = False
        else:
            status, success, error_category, error_message = _determine_status(
                exit_code=outcome.exit_code,
                timed_out=outcome.timed_out,
                unexpected_changed_paths=unexpected_changed_paths,
                expected_changed=expected_changed,
                cleanup=outcome.cleanup,
                readers_alive=outcome.readers_alive,
            )

        stdout_text = _redact_home(
            _redact(outcome.stdout_bytes.decode("utf-8", errors="replace"), secret_values),
            home_value,
        )
        stderr_text = _redact_home(
            _redact(outcome.stderr_bytes.decode("utf-8", errors="replace"), secret_values),
            home_value,
        )
        # --- Provider-output persistence boundary (M-1 / M-2) ---
        #
        # Above this line, `stdout_text`/`stderr_text` are raw
        # provider-controlled data (env-secret- and HOME-redacted, but those
        # passes only know about *known* values, not arbitrary command
        # text). Below it, when exact-command authorization is active, no
        # provider-controlled text survives onto the result at all: the
        # excerpts become a fixed placeholder and everything TOMTIT still
        # needs is carried by the typed, numbers-and-booleans-only summary.
        # This is the single point where transient runtime data stops and
        # durable evidence begins.
        provider_summary: _ProviderOutputSummary | None = None
        if request.authorized_commands:
            provider_summary = _summarize_provider_output(stdout_text, stderr_text)
            # A placeholder only where there was actually output, so an
            # empty stream stays empty rather than gaining phantom content.
            stdout_text = _PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER if stdout_text else ""
            stderr_text = _PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER if stderr_text else ""
        # Every raw authorized-command rule that was actually placed in the
        # real subprocess argv is swapped for its safe fingerprint placeholder
        # *instead of* the normal env/HOME redaction pass, which only removes
        # known environment-secret values, not arbitrary command-argument
        # text -- `sanitized_argv` must never carry the raw rule (Codex
        # corrective review, HIGH finding 2).
        raw_rule_to_placeholder = {
            render_authorized_command_cli_rule(command_argv): render_authorized_command_evidence_rule(command_argv)
            for command_argv in request.authorized_commands
        }

        def _sanitize_argv_element(arg: str) -> str:
            placeholder = raw_rule_to_placeholder.get(arg)
            if placeholder is not None:
                return placeholder
            return _redact_home(_redact(arg, secret_values), home_value)

        sanitized_argv = tuple(_sanitize_argv_element(arg) for arg in argv)
        sanitized_authorized_rules = tuple(
            render_authorized_command_evidence_rule(command_argv) for command_argv in request.authorized_commands
        )

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)

        return ClaudeCodeCLIResult(
            adapter_name=self.name,
            adapter_version=self.version,
            task_id=request.task_id,
            run_id=request.run_id,
            attempt=request.attempt,
            status=status,
            success=success,
            executable_path=resolved_executable,
            sanitized_argv=sanitized_argv,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            stdout_sha256=outcome.stdout_sha256,
            stderr_sha256=outcome.stderr_sha256,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            workspace_before_hash=workspace_hash(before_hashes),
            workspace_after_hash=workspace_hash(after_hashes),
            changed_paths=changed_paths,
            unexpected_changed_paths=unexpected_changed_paths,
            patch_path=None,
            evidence_manifest_path=None,
            error_category=error_category,
            error_message=error_message,
            authorized_command_fingerprints=authorized_fingerprints,
            authorized_command_set_fingerprint=authorized_set_fingerprint,
            authorized_command_count=len(request.authorized_commands),
            authorized_command_cli_rules=sanitized_authorized_rules,
            exact_bash_guard_enabled=(
                exact_bash_guard_artifacts is not None
                and exact_bash_guard_artifacts.fingerprints_path is not None
            ),
            provider_output_withheld=provider_summary is not None,
            provider_stdout_bytes=provider_summary.stdout_bytes if provider_summary else None,
            provider_stderr_bytes=provider_summary.stderr_bytes if provider_summary else None,
            provider_stdout_json_parsed=provider_summary.stdout_json_parsed if provider_summary else None,
            provider_is_error=provider_summary.is_error if provider_summary else None,
            provider_permission_denial_count=(
                provider_summary.permission_denial_count if provider_summary else None
            ),
            provider_num_turns=provider_summary.num_turns if provider_summary else None,
            provider_total_cost_usd=provider_summary.total_cost_usd if provider_summary else None,
            provider_input_tokens=provider_summary.input_tokens if provider_summary else None,
            provider_output_tokens=provider_summary.output_tokens if provider_summary else None,
            allowed_side_effect_paths=request.allowed_side_effect_paths,
            side_effect_changed_path_count=len(side_effect_changed_paths),
            side_effect_changed_path_counts_by_root=side_effect_counts_by_root,
            side_effect_changed_paths_sample=side_effect_sample,
        )

    def _build_argv(
        self,
        executable: str,
        request: ClaudeCodeCLIRequest,
        authorized_rules: tuple[str, ...] = (),
        exact_bash_guard_artifacts: _ExactBashGuardArtifacts | None = None,
    ) -> list[str]:
        # The task prompt occupies the unambiguous positional slot for `-p`
        # immediately, before any variadic option. `--allowedTools` accepts
        # an arbitrary number of positional rule values with no closing
        # delimiter, so a prompt placed *after* it (as this argv used to do)
        # is silently consumed as one more allowedTools rule instead of
        # reaching `-p` -- Claude then sees no prompt at all and exits 1
        # ("Input must be provided either through stdin or as a prompt
        # argument when using --print"). Reproduced against a real
        # personal-web dogfood run; see
        # CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md. Every other flag below
        # is added *after* the prompt, so nothing variadic can ever precede
        # and swallow it again.
        argv = [
            executable,
            "-p",
            request.prompt,
            "--output-format",
            "json",
            "--permission-mode",
            request.permission_mode,
        ]
        # Purely additive: only present when there is at least one authorized
        # command, and never replaces or narrows the existing permission-mode
        # or --allowedTools behavior -- the TOMTIT PreToolUse guard is a
        # second gate in front of Claude's native permission engine, not a
        # substitute for it.
        if exact_bash_guard_artifacts is not None:
            # Load ONLY TOMTIT's settings: an empty --setting-sources means
            # user/project/local settings files are not loaded at all, so an
            # untrusted workspace cannot widen the sandbox, permissions, or
            # hooks that form this execution boundary.
            argv.extend(["--setting-sources", _EXACT_BASH_GUARD_SETTING_SOURCES])
            argv.extend(["--settings", str(exact_bash_guard_artifacts.settings_path)])
        if authorized_rules:
            argv.append("--allowedTools")
            argv.extend(authorized_rules)
        if request.model:
            argv.extend(["--model", request.model])
        return argv

    def _early_result(
        self,
        request: ClaudeCodeCLIRequest,
        started_at: datetime,
        start_monotonic: float,
        *,
        status: ClaudeCodeCLIStatus,
        error_category: str,
        error_message: str,
        executable_path: str | None = None,
        authorized_command_fingerprints: tuple[str, ...] = (),
        authorized_command_set_fingerprint: str | None = None,
        authorized_command_count: int = 0,
    ) -> ClaudeCodeCLIResult:
        completed_at = datetime.now(timezone.utc)
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        empty_digest = _EMPTY_SHA256
        return ClaudeCodeCLIResult(
            adapter_name=self.name,
            adapter_version=self.version,
            task_id=request.task_id,
            run_id=request.run_id,
            attempt=request.attempt,
            status=status,
            success=False,
            executable_path=executable_path,
            sanitized_argv=(),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            exit_code=None,
            timed_out=False,
            stdout_excerpt="",
            stderr_excerpt="",
            stdout_sha256=empty_digest,
            stderr_sha256=empty_digest,
            stdout_truncated=False,
            stderr_truncated=False,
            workspace_before_hash=None,
            workspace_after_hash=None,
            changed_paths=(),
            unexpected_changed_paths=(),
            patch_path=None,
            evidence_manifest_path=None,
            error_category=error_category,
            error_message=error_message,
            authorized_command_fingerprints=authorized_command_fingerprints,
            authorized_command_set_fingerprint=authorized_command_set_fingerprint,
            authorized_command_count=authorized_command_count,
            authorized_command_cli_rules=(),
            # M-3: the request was already validated by this point, so the
            # Owner-approved side-effect contract is known and must survive
            # into durable evidence even though execution ended early. Only
            # the *observed* counts stay zero -- nothing is fabricated.
            allowed_side_effect_paths=request.allowed_side_effect_paths,
            side_effect_changed_path_count=0,
            side_effect_changed_path_counts_by_root=tuple(
                (root, 0) for root in request.allowed_side_effect_paths
            ),
            side_effect_changed_paths_sample=(),
        )
