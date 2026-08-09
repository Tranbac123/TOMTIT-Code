# Exact Command Authorization — Implementation Report

## 1. Verdict/status

```text
STATUS=READY_FOR_INDEPENDENT_CORRECTNESS_REVIEW
```

Bounded exact-command authorization is implemented end to end: adapter
contract, `run_task.py`, `run_repair.py` (with strict inheritance
enforcement), and `run_loop.py` (with identical Attempt 1/Attempt 2
propagation and no post-creation expansion). It **fails closed by design** at
the one point that could not be safely verified without a real, unauthorized
model invocation: whether the installed Claude CLI's bare `Bash(<command>)`
permission rule is truly exact-match. Every real run that supplies
`--authorized-command` currently returns
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` before any worker process starts,
with no `bypassPermissions`, no broadened rule, and no silent fallback.

## 2. Starting HEAD

```text
f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Preflight confirmed: repository root is TOMTIT-Coordinator, branch `main`,
tracked HEAD matched exactly, tracked worktree and index were clean before
any edit.

## 3. Files changed

Tracked (all within the explicitly authorized scope):

```text
src/tomtit/adapters/claude_code_cli.py   (+231/-2)
src/tomtit/adapters/__init__.py          (+14)
tests/adapters/test_claude_code_cli.py   (+308)
```

Ignored `.tomtit/dogfood/` (propagation + tests + docs only, no product code):

```text
.tomtit/dogfood/run_task.py
.tomtit/dogfood/run_repair.py
.tomtit/dogfood/run_loop.py
.tomtit/dogfood/test_run_loop.py          (+~230 lines of new tests)
.tomtit/dogfood/test_run_repair.py        (+~120 lines of new tests)
.tomtit/dogfood/README.md
.tomtit/dogfood/REPAIR_RUNNER.md
.tomtit/dogfood/LOOP_RUNNER.md
```

No file under `src/tomtit/runtime`, `src/tomtit/domain`, evaluation,
governance, memory, or web/product code was touched. No Git commit was
created.

## 4. Reproduced defect

Real dogfood loop `360e74f8-07cb-4e6d-96ef-cdc499b64394` failed with
`ERROR_CATEGORY=NO_EXPECTED_CHANGE`. Root-cause diagnosis (prior turn,
read-only) found: Claude Code ran non-interactively (`-p`), under
`--permission-mode acceptEdits`, which auto-approves file Write/Edit tool
calls but **not** Bash tool calls. The task legitimately required `npm
install` to add explicitly-authorized new dependencies
(`next-mdx-remote`, `gray-matter`, `tsx`, etc. — all within
`allowed_paths` via `package.json`/`package-lock.json`). The CLI's own
`permission_denials` evidence showed 11 denied Bash calls; Claude explicitly
stopped and asked for approval rather than guessing — correct worker
behavior. Classified `ROOT_CAUSE=ORCHESTRATOR_FAILURE`: the orchestrator had
no mechanism to grant a legitimately in-scope, Owner-approved Bash operation
non-interactively.

## 5. Chosen authorization design

- **Canonical representation**: `authorized_commands: tuple[tuple[str,
  ...], ...]` on `ClaudeCodeCLIRequest`/`ClaudeCodeCLIResult` — never a raw
  shell string. Each `--authorized-command TEXT` is parsed exactly once with
  `shlex.split`, producing a bounded argv tuple.
- **Rejection is stricter than the existing `--check-command` validator by
  deliberate design**: `run_review.validate_check_command_text()` only
  rejects a shell operator when it appears as its own `shlex` token (so
  `python3 -c "import sys; sys.exit(1)"` is legitimately allowed — the `;`
  is data inside one quoted argument). An authorized command instead grants
  the worker *unattended Bash execution*; there is no legitimate reason for
  a shell metacharacter to appear anywhere in one, quoted or not. So
  `normalize_authorized_command()` rejects `$(`, `` ` ``, newline, `;`,
  `&&`, `||`, `|`, `>`, `>>`, `<`, `<<`, `&` as **raw substrings**, matching
  the task's literal "reject commands *containing*..." wording. This was
  caught by an offline test (`test_shell_syntax_rejected[semicolon]`) that
  initially failed under the token-only approach.
- **Determinism**: `normalize_authorized_commands()` deduplicates
  order-preserving (first occurrence wins), so the same input always
  produces the same canonical tuple and the same rendered rules.
- **No wildcard, ever**: a lone `*` token, or any all-`*` token, is rejected
  outright — the module can structurally never construct `Bash(*)`,
  `Bash(npm:*)`, or any prefix/wildcard form; it only ever renders one
  `Bash(<exact literal command>)` rule per authorized command via
  `render_authorized_command_cli_rule()` (`shlex.join`-based, round-trips
  exactly through `shlex.split`).
- **Purely additive to the adapter's argv**: `--allowedTools "Bash(...)"
  ...` is appended only when at least one authorized command is present;
  `--permission-mode` and every other existing argument is unchanged.
- **Single enable switch**: `resolve_claude_cli_authorization_contract()` is
  the one function `run()` consults. It currently always returns
  `UNSUPPORTED` because `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False`
  — flipping that one module-level constant (after real verification) is
  sufficient to enable the feature with no other code change.

## 6. Local Claude CLI contract evidence

Installed CLI: `2.1.223 (Claude Code)` at
`/Users/tranvanbac/.nvm/versions/node/v20.19.4/bin/claude`.

`claude --help` documents `--allowedTools, --allowed-tools <tools...>` with
example `"Bash(git *)"` — a wildcard example only; the help text alone does
not establish bare-rule semantics.

**Decisive evidence** came from `strings` on the compiled CLI binary
(`@anthropic-ai/claude-code`'s native executable), which contains the actual
permission-rule validator's literal error strings:

```text
"The :* pattern must be at the end"
suggestion: "Move :* to the end for prefix matching, or use * for wildcard matching"
examples: ["Bash(npm run:*) - prefix matching (legacy)", "Bash(npm run *) - wildcard matching"]
"Prefix cannot be empty before :*"
'The ":*" syntax is only for Bash prefix rules'
```

This shows the CLI's own validator explicitly recognizes **three distinct
rule shapes**: `:*`-suffixed ("prefix matching (legacy)"), embedded-`*`
("wildcard matching"), and — by exclusion — a bare rule with neither marker.
The consistent framing of `:*` and `*` as the *only* named mechanisms for
non-exact matching is strong circumstantial evidence that a bare
`Bash(<command>)` rule is exact-match.

The settings JSON schema (`claude-code-settings.schema.json`, found in the
locally installed VS Code extension) only types `permissions.allow`/`deny`/
`ask` as opaque `string[]`, with no further matching-semantics
documentation — consistent with, but not additional confirmation of, the
binary-string evidence above.

**What was explicitly not done**: no real `claude` invocation was made to
behaviorally confirm this (not authorized by this task). The evidence above
is parsing/validation-layer evidence, not runtime-matching-layer proof.

## 7. Exactness guarantees

- No rule is ever constructed with `*` or `:*` — enforced structurally (the
  renderer only ever emits `Bash(<shlex.join(argv)>)`, and
  `normalize_authorized_command()` rejects any wildcard-only token before a
  command ever reaches the renderer).
- `sanitized_argv` redaction (existing HOME/secret substitution) applies
  uniformly to the rendered rules, since they are literal argv elements —
  no separate redaction path needed, verified by
  `test_evidence_credential_safe_for_authorized_commands`.
- `ClaudeCodeCLIRequest.__post_init__` re-validates every authorized command
  a second time (structural + forbidden-token check), independent of
  whatever validation the caller already performed — matching `SYSTEM.md`
  §4's "revalidate canonical data at trust boundaries."

## 8. Unsupported cases and fail-closed behavior

`resolve_claude_cli_authorization_contract()` always returns
`UNSUPPORTED` today. When `request.authorized_commands` is non-empty and the
contract is unsupported, `ClaudeCodeCLIAdapter.run()` returns an immutable
`ClaudeCodeCLIResult` with `status=FAILED`, `success=False`,
`error_category="UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"`, `exit_code=None`
— **before any subprocess is started** (verified by
`test_unsupported_cli_contract_fails_before_model_invocation`, which plants
an invocation marker file the fake worker would create and asserts it never
appears). This propagates through `run_task.py` (`AuthorizedCommandError` →
`ConfigurationError` → exit 2, or an unsupported-contract failure surfaced
via the adapter's normal receipt/exit-1 path since the CLI *itself* is
available — only the authorization-rule emission is refused), and through
`run_repair.py`/`run_loop.py` identically, since they all share the same
adapter.

Invalid command syntax (`;`, `&&`, wildcards, empty, etc.) is rejected even
earlier — at CLI/config preflight in every runner, before the adapter is
even constructed, and produces zero model invocations
(`test_run_task_invalid_authorized_command_fails_before_invocation`,
`test_loop_invalid_authorized_command_causes_zero_model_invocations`).

## 9. Task/repair/loop propagation

- **`run_task.py`**: `--authorized-command` (repeatable) → validated via
  `normalize_authorized_commands()` before the adapter is invoked → passed
  into `ClaudeCodeCLIRequest` → persisted in `receipt.json` as
  `authorized_commands` (argv arrays), `authorized_commands_display`
  (normalized strings), and `authorized_command_cli_rules` (from the
  adapter result).
- **`run_repair.py`**: same flag, same validation, **plus** a strict
  equality check against the original implementation's persisted
  `authorized_commands` (read from its `receipt.json`). Any mismatch —
  addition, removal, or substitution — raises
  `IntegrityFailure("AUTHORIZED_COMMAND_CONTRACT_MISMATCH", ...)`, exit 3,
  before any Claude invocation. A dedicated test
  (`test_repair_findings_cannot_introduce_bash_authorization`) proves that
  even a HIGH-severity finding whose `required_change` literally says "run
  `npm install zod`" is still rejected if it wasn't in the original
  authorized set — findings are never grounds to broaden Bash
  authorization.
- **`run_loop.py`**: same flag, validated once at `_config_preflight()`
  (exit 4 on invalid syntax, no loop evidence written), persisted in
  `task-contract.json` (`authorized_commands` +
  `authorized_commands_display`), and bound to a single local variable
  inside `main()` that is passed byte-identically to both
  `invoke_implementation()` (Attempt 1) and `invoke_repair()` (Attempt 2) —
  proven never reassigned by an AST-based test
  (`test_loop_never_expands_authorization_after_creation`) that walks
  `main()`'s own syntax tree and asserts exactly one binding.

`--evidence-directory`/CLI plumbing quirks already documented in
`REPAIR_RUNNER.md`/`LOOP_RUNNER.md` were reused unchanged; no new
inconsistency was introduced.

## 10. Evidence schema changes

`ClaudeCodeCLIResult` gained `authorized_commands: tuple[tuple[str, ...],
...] = ()` and `authorized_command_cli_rules: tuple[str, ...] = ()`, both
included in `build_evidence_manifest()`/`result.json`. `run_task.py`'s
`receipt.json` gained `authorized_commands`, `authorized_commands_display`,
`authorized_command_cli_rules`. `run_repair.py`'s `receipt.json` and
`repair-context.json` gained the same three fields. `run_loop.py`'s
`task-contract.json` and `loop-result.json` gained `authorized_commands`
(+`authorized_commands_display` on the contract). All additive, all
default to `()`/`[]`, all backward compatible — no existing evidence
consumer breaks. No credentials, environment variables, or unrestricted
output are persisted anywhere in this evidence (verified by
`test_evidence_credential_safe_for_authorized_commands`).

## 11. Tests and commands with exit codes

| Command | Exit | Result |
|---|---:|---|
| `python3 .tomtit/dogfood/test_run_loop.py` | 0 | 52 passed |
| `python3 .tomtit/dogfood/test_run_repair.py` | 0 | 49 passed |
| `python3 .tomtit/dogfood/test_run_review.py` | 0 | 45 passed (unchanged) |
| `python3 -m pytest tests/adapters -q` | 0 | 88 passed (58 existing + 30 new) |
| `python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q` | 0 | 4 passed (one pre-existing, unrelated timing-based test flaked once during this session and passed cleanly on 4 subsequent reruns — not a regression from this change; the failing test involves a 3-second delayed-subprocess-write race unrelated to authorized commands) |
| `python3 -m pytest -q` | 0 | 414 passed (384 + 30 new) |
| `python3 -m compileall -q src tests scripts .tomtit/dogfood` | 0 | pass |
| `ruff check src tests scripts` | 0 | no findings |
| `python3 -m mypy src` | 0 | no issues in 13 source files |
| `git diff --check` | 0 | clean |

`test_run_task.py` does not exist and was **not** created solely to satisfy
the verification-command list, per this task's own instruction. `run_task.py`
has never had a dedicated test file (predates every other dogfood test
suite). Its new `--authorized-command` behavior is instead covered directly
in `test_run_loop.py` (which already imports `run_task` as a module and
exercises it via `invoke_implementation()`), in a clearly delineated
section with its own `run_task_cli()` helper: `test_run_task_propagates_
exact_authorized_commands`, `test_run_task_no_authorized_command_unchanged_
behavior`, `test_run_task_invalid_authorized_command_fails_before_
invocation`.

## 12. Real Claude invocation count

```text
REAL_CLAUDE_INVOCATIONS=0
```

## 13. Real Codex invocation count

```text
REAL_CODEX_INVOCATIONS=0
```

## 14. Risks and limitations

- **The central open question remains genuinely open**: bare-rule
  exactness is well-evidenced but not behaviorally confirmed. Until an
  Owner-authorized real invocation flips
  `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`, the feature is inert in
  production (fails closed every time), which is the deliberately safe
  default but means the reproduced defect (dependency-installation tasks
  blocked under `acceptEdits`) is not yet actually fixed for real runs.
- **Multiple authorized commands share one `--allowedTools` invocation**:
  `_build_argv` appends all rendered rules after a single `--allowedTools`
  flag (`--allowedTools "Bash(a)" "Bash(b)"`). This matches the CLI's
  documented variadic syntax but was not behaviorally verified either.
- **`shlex.join` quoting**: an authorized command argument containing a
  literal `*` embedded within a token that is not itself a bare `*` (e.g.
  `"file*.txt"` as one argument to some other command) would currently be
  accepted by `normalize_authorized_command` (only whole-token `*`/all-`*`
  tokens are rejected) and rendered verbatim into the `Bash(...)` rule.
  Given the feature is fully fail-closed today, this has no live exposure,
  but is worth Owner awareness before the feature is ever enabled.
- **Stricter-than-check-command rejection is a deliberate divergence**,
  documented in section 5 above and in source comments, but it does mean
  an authorized command can never legitimately contain a literal `;`/`&`/
  etc. even as harmless argument data (e.g. a URL query string) — judged an
  acceptable, conservative trade-off for a Bash-authorization allowlist.

## 15. Scope and non-goals

Not implemented, per explicit Owner authorization boundaries: an approval
UI, an MCP permission server, a cloud permission service, a generalized
policy engine, `bypassPermissions` as a solution, wildcard or broad-prefix
Bash authorization (`Bash(*)`, `Bash(npm:*)`, `Bash(npm install:*)`, or any
unrestricted rule), and any automatic Git action. No Runtime, evaluation,
governance, memory, or web/product code was touched. No unrelated
refactoring was performed.

## 16. Final Git state

```text
HEAD=f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d   (unchanged)
tracked worktree: 3 modified files (src/tomtit/adapters/claude_code_cli.py,
                   src/tomtit/adapters/__init__.py,
                   tests/adapters/test_claude_code_cli.py)
index: clean (nothing staged)
no commit created
```

## 17. Next gate

None authorized by this task. The immediate next action belongs to the
Owner: run one bounded, explicitly-authorized real verification invocation
to confirm (or refute) bare-rule Bash exactness, then flip
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` accordingly. No further
product gate is opened by this corrective task.
