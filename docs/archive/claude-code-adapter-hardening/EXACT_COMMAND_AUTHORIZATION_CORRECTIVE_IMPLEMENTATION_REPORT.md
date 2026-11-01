# Exact Command Authorization — Corrective Implementation Report

This report documents one bounded corrective patch applied after the
independent Codex review recorded in `EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md`
(`VERDICT=REQUEST_CHANGES`). It supersedes nothing in
`EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md` — that file is left
unmodified as the record of the original implementation and its known gaps —
but this report's "Tests and exit codes" and "Canonical validator design"
sections are now the accurate description of the validation and evidence
behavior; where the original report's "No wildcard, ever" and "request
construction revalidates the same shell syntax" claims were false, they are
false of the code *as it existed before this patch*, and are corrected here.

## 1. Status

`FINDING_HIGH_1=CLAIMED_FIXED_PENDING_REVIEW`
`FINDING_HIGH_2=CLAIMED_FIXED_PENDING_REVIEW`

Neither finding is resolved by this report's own claim. Only the next
independent Codex review can determine that. This patch also does not enable
the feature: `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`,
exact authorization remains operationally disabled, and a personal-web rerun
remains unauthorized (see sections 12–15).

## 2. Starting HEAD

`f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged by this task — no
commit was made; see section 16).

## 3. Codex findings addressed

Both HIGH findings from `EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md`:

- **HIGH 1 — wildcard and direct-contract validation are incomplete.**
  `normalize_authorized_command()` only rejected wildcard-*only* tokens, and
  `ClaudeCodeCLIRequest.__post_init__` only re-checked non-empty tokens and
  standalone operators, letting a direct construction bypass raw
  normalization once the enable constant is flipped.
- **HIGH 2 — authorized command values can persist credentials.** The
  evidence manifest and every dogfood receipt persisted `authorized_commands`
  and rendered `Bash(...)` rules verbatim, redacted only against known
  environment-secret values, not command-argument secrets.

The MEDIUM finding (CLI exactness unproven) and LOW finding (report
overstated guarantees) are explicitly out of scope for this task and are not
addressed here; `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`
and no real Claude CLI contract probe was run.

## 4. Files changed

- `src/tomtit/adapters/claude_code_cli.py` — canonical validator, credential
  rejection, fingerprinting, evidence redesign.
- `src/tomtit/adapters/__init__.py` — export the new public symbols
  (`compute_authorized_command_fingerprint`,
  `compute_authorized_command_set_fingerprint`,
  `render_authorized_command_evidence_rule`,
  `validate_authorized_command_argv`).
- `tests/adapters/test_claude_code_cli.py` — corrected/expanded tests
  (canonical validator groups, direct-request bypass closure, evidence
  safety).
- `.tomtit/dogfood/run_task.py` — persists fingerprints instead of raw
  commands in `receipt.json`.
- `.tomtit/dogfood/run_repair.py` — inheritance check rewritten to compare
  ordered-set fingerprints instead of raw tuples; explicit old-evidence
  handling; persists fingerprints instead of raw commands in
  `repair-context.json` / `receipt.json`.
- `.tomtit/dogfood/run_loop.py` — persists fingerprints instead of raw
  commands in `task-contract.json` / `loop-result.json`. In-memory
  propagation to children (via `--authorized-command` argv to
  `invoke_implementation()` / `invoke_repair()`) is unchanged and does not
  touch a file.
- `.tomtit/dogfood/test_run_loop.py`, `.tomtit/dogfood/test_run_repair.py` —
  updated to the new fingerprint schema; added old-evidence and
  no-raw-leakage coverage.
- `.tomtit/dogfood/README.md`, `REPAIR_RUNNER.md`, `LOOP_RUNNER.md` —
  updated to describe the canonical validator's full rejection scope and the
  fingerprint-only evidence schema.

No other file was modified. `EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md`
and `EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md` are untouched.

## 5. Canonical validator design

One function, `validate_authorized_command_argv(argv)`, is the single
rejection point for an already-tokenized argv tuple. Every entry path calls
it directly or transitively:

- `normalize_authorized_command(raw_command)` — the raw
  `--authorized-command` text path — first rejects any control character or
  forbidden/ambiguous whitespace character in the *raw, pre-split* string
  (this matters because `shlex.split` treats an embedded newline as an
  ordinary token separator; catching it before splitting prevents a
  single-line-looking value from silently smuggling extra argv tokens),
  then calls `shlex.split`, then calls `validate_authorized_command_argv`.
- `normalize_authorized_commands(raw_commands)` — calls
  `normalize_authorized_command` per entry, inheriting the validator
  automatically.
- `ClaudeCodeCLIRequest.__post_init__` — calls
  `validate_authorized_command_argv` on every entry of
  `self.authorized_commands`, unconditionally, regardless of how the request
  was constructed. This closes the direct-construction bypass: a caller
  cannot skip `normalize_authorized_command` and hand the dataclass a
  pre-tokenized argv containing rejected syntax, because the dataclass
  re-validates every entry through the exact same function.

`validate_authorized_command_argv` rejects (13 categories, matching the
task's numbered list):

1. Empty command (empty argv tuple).
2. Empty argv token.
3. NUL and all ASCII control characters (`ord(ch) < 0x20` or `0x7F`).
4. Newline/CR/tab and ambiguous/non-ASCII whitespace (Unicode categories
   `Zs`, `Zl`, `Zp`, `Cf`, `Cc`; plain ASCII space is exempted, since it is a
   legitimate character inside one shlex-quoted token).
5. Shell operators/metacharacters anywhere in a token (`&&`, `||`, `;`, `|`,
   `>`, `>>`, `<`, `<<`, `&`, backtick, `` $( ``) — substring match, not
   whole-token match.
6. Shell glob characters anywhere in a token (`*`, `?`, `[`, `]`) — this is
   the direct fix for HIGH-1's "wildcard merely embedded in a token" gap.
7. Environment-assignment forms (`^[A-Za-z_][A-Za-z0-9_]*=`), rejected in
   any argv position, not only argv[0].
8. Nested shell execution via shell executable basenames (`sh`, `bash`,
   `zsh`, `dash`, `fish`, `ksh`, `csh`, `tcsh`), matched against every
   token's basename (`token.rsplit("/", 1)[-1]`), not only argv[0].
9. Shell interpreter `-c` form (a token exactly equal to `-c`).
10. `env`-based command wrapping (a token whose basename is `env`).
11. Existing broad Claude permission-rule syntax (`Bash(*)`, `:*`,
    wildcard/prefix forms) — subsumed by category 6, since any such string
    contains a rejected glob character.
12. Credential-bearing option names/headers (case-insensitive substring
    match against `token|password|passwd|secret|api[-_]?key|authorization|
    cookie|private[-_]?key|bearer`), raised as the distinct
    `CREDENTIAL_BEARING_AUTHORIZED_COMMAND` category.
13. URL user-info credential forms (`scheme://user:password@host`), also
    raised as `CREDENTIAL_BEARING_AUTHORIZED_COMMAND`.

Every raised `AuthorizedCommandError` carries a fixed, category-describing
message that never embeds the rejected token or command text — the
rejected value could itself be the secret or injection payload being
rejected.

## 6. Direct-request trust-boundary enforcement

`ClaudeCodeCLIRequest.__post_init__` previously performed a weaker,
independent check (non-empty tokens, standalone-operator rejection only).
It now calls `validate_authorized_command_argv` per entry — the identical
function the raw-text path uses — so a caller constructing the dataclass
directly (bypassing `normalize_authorized_command`/`normalize_authorized_commands`
entirely) cannot smuggle any of the 13 rejected categories through. This is
covered by `test_direct_request_construction_cannot_bypass_canonical_validator`,
a parametrized test exercising one representative argv per category directly
against `ClaudeCodeCLIRequest` construction (not the raw-text helpers).

## 7. Credential-bearing command rejection

Rather than accepting a credential-shaped command and relying solely on
redaction (one of the two options Codex's review allowed), this patch
**rejects** credential-bearing forms outright at validation time, via the
`CREDENTIAL_BEARING_AUTHORIZED_COMMAND` category (flag-name/header-term
pattern plus URL-userinfo pattern — see section 5, categories 12–13). This
is a stronger guarantee than redaction: such a command can never be
authorized in the first place, so there is nothing to redact later. Evidence
fingerprinting (section 8) independently protects any *non*-credential-shaped
argument value that is nonetheless sensitive for other reasons (e.g. an
internal package name), since fingerprints never expose raw text regardless
of whether the credential-term check fired.

## 8. Evidence fingerprint and redaction design

`ClaudeCodeCLIResult` no longer has an `authorized_commands` field. It now
carries:

- `authorized_command_fingerprints: tuple[str, ...]` — one SHA-256 per
  command, in order.
- `authorized_command_set_fingerprint: str | None` — one SHA-256 over the
  ordered list of all authorized commands (`None` when there are none).
- `authorized_command_count: int`.
- `authorized_command_cli_rules: tuple[str, ...]` — kept for schema
  continuity, but its content changed: it now holds only the safe
  placeholder form `Bash(<AUTHORIZED_COMMAND_SHA256:<hex>>)`, never the raw
  rule.

Both fingerprint functions (`compute_authorized_command_fingerprint`,
`compute_authorized_command_set_fingerprint`) hash a canonical, compact JSON
encoding of the argv list(s) (`json.dumps(..., separators=(",", ":"))`),
so they are deterministic and order-sensitive: reordering either an
individual command's arguments or the authorization set itself changes the
fingerprint (`test_fingerprint_changes_with_argument_order`,
`test_set_fingerprint_changes_with_authorization_order`).

In `ClaudeCodeCLIAdapter.run()`, `sanitized_argv` (the evidence-facing argv)
is built by substituting every raw `Bash(<command>)` rule that was actually
placed in the real subprocess argv with its safe placeholder — this
substitution happens *instead of*, not in addition to, the existing
env-value/HOME redaction pass, which only ever removed known environment
secret values and could not have caught an arbitrary command-argument value.
The raw rule exists only in the local `argv` list passed to `_execute()` /
`Popen` — the one place it is allowed to exist, transiently, to construct
the real invocation — and never in anything returned, logged, or persisted.
`build_evidence_manifest()` emits only the fingerprint fields and the safe
rule placeholders; there is no `authorized_commands` key in the manifest at
all.

The same discipline applies to every dogfood evidence file:
`run_task.py`'s `receipt.json`, `run_repair.py`'s `repair-context.json` and
`receipt.json`, and `run_loop.py`'s `task-contract.json` and
`loop-result.json` all persist `authorized_command_fingerprints`,
`authorized_command_set_fingerprint`, and `authorized_command_count` —
never a raw command list or a `_display` string.

## 9. Repair/loop inheritance

`run_repair.py`'s Attempt-2 authorization check no longer reconstructs or
compares raw tuples (which the original implementation's evidence no longer
even contains). It recomputes `compute_authorized_command_set_fingerprint`
over its own normalized `--authorized-command` set and compares it to
`authorized_command_set_fingerprint` read back from the original
implementation's `receipt.json`. Any addition, removal, substitution, or
reorder changes the fingerprint and fails closed with
`AUTHORIZED_COMMAND_CONTRACT_MISMATCH`.

Old evidence predating this schema (no `authorized_command_set_fingerprint`
key present at all) is handled explicitly: if the repair supplies any
`--authorized-command`, it fails closed with the new
`AUTHORIZED_COMMAND_EVIDENCE_INVALID` category, since there is no safe way
to know what, if anything, Attempt 1 was authorized to run. If the repair
supplies none, it proceeds — there is nothing to inherit or contradict on
either side. This is deliberately conservative: it can only ever narrow what
a stale-schema repair is allowed to do, never broaden it.

`run_loop.py`'s `main()` still binds `authorized_commands` exactly once,
before the state machine runs, and passes the identical in-memory tuple to
both `invoke_implementation()` and `invoke_repair()` — unchanged from the
original implementation and re-verified by the existing
`test_loop_never_expands_authorization_after_creation` AST-walk test. What
changed is only what gets *persisted*: fingerprints, not raw commands.

## 10. Backward compatibility

When no `--authorized-command` is supplied, behavior is unchanged: no
`--allowedTools` argv is emitted, `authorized_command_fingerprints` is `()`,
`authorized_command_set_fingerprint` is `None`, `authorized_command_count`
is `0`, and every existing no-command test remains green
(`test_no_authorized_command_preserves_existing_behavior`,
`test_run_task_no_authorized_command_unchanged_behavior`,
`test_repair_no_authorized_commands_on_both_sides_is_accepted`). Old
evidence without fingerprints is read back explicitly and safely by
`run_repair.py` (section 9) rather than silently trusted or silently
broadened.

## 11. Tests and exit codes

All commands were run from the repository root, after the patch, with zero
real Claude or Codex invocations:

| Command | Exit |
|---|---:|
| `python3 .tomtit/dogfood/test_run_loop.py` | 0 |
| `python3 .tomtit/dogfood/test_run_repair.py` | 0 |
| `python3 .tomtit/dogfood/test_run_review.py` | 0 |
| `python3 -m pytest tests/adapters -q` | 0 |
| `python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q` | 0 |
| `python3 -m pytest -q` | 0 |
| `python3 -m compileall -q src tests scripts .tomtit/dogfood` | 0 |
| `ruff check src tests scripts` | 0 |
| `python3 -m mypy src` | 0 (`Success: no issues found in 13 source files`) |
| `git diff --check` | 0 |

Test coverage added or corrected in this patch (three groups, as required):

- **Canonical validator** (`tests/adapters/test_claude_code_cli.py`):
  wildcard/glob-embedded-in-token rejection (parametrized, 7 cases); nested
  shell / `-c` / `env`-wrapper / environment-assignment rejection
  (parametrized, 9 cases); control-character and ambiguous-whitespace
  rejection (parametrized, 7 cases, including NUL, bell, CR, tab, NBSP,
  zero-width space, line separator); empty-argv-token rejection via direct
  validator call; credential-bearing rejection (parametrized, 7 cases:
  separate-value flag, `--key=value`, `--key_value`, Authorization/Bearer
  header form, URL userinfo form) with an explicit assertion that the
  synthetic secret never appears in the raised exception's message or
  string form; a harmless-exact-argv acceptance sanity check; and a
  parametrized direct-`ClaudeCodeCLIRequest`-construction test proving the
  same rejection classes cannot be bypassed by skipping
  `normalize_authorized_command` (13 representative bad argvs).
- **Evidence safety**: `ClaudeCodeCLIResult` has no `authorized_commands`
  attribute at all; the exact-supported-path test (run under monkeypatch,
  since production always fails closed) asserts the safe placeholder — not
  the raw rule — appears in `sanitized_argv` and `authorized_command_cli_rules`,
  while separately confirming the raw rule *does* reach the real subprocess
  argv (the one permitted transient location); evidence-manifest field
  checks for the new fingerprint keys and the absence of the old
  `authorized_commands` key; a dedicated argument-value test (using a
  non-credential-shaped synthetic placeholder, `SYNTHETIC_ARGUMENT_VALUE`,
  distinct from the credential-rejection tests' `SYNTHETIC_SECRET`, since a
  credential-shaped value would be rejected outright rather than persisted)
  asserting the value is absent from `sanitized_argv`,
  `authorized_command_cli_rules`, the rendered manifest JSON, and every file
  written by `write_evidence()`; fingerprint determinism; fingerprint
  sensitivity to argument order; and set-fingerprint sensitivity to both
  authorization order and membership.
- **Repair and loop integrity** (`.tomtit/dogfood/test_run_repair.py`,
  `test_run_loop.py`): identical ordered fingerprints accepted; add/remove/
  substitute/reorder rejected via `AUTHORIZED_COMMAND_CONTRACT_MISMATCH`
  (pre-existing tests, now exercised against the fingerprint-based check);
  two new old-evidence tests (`legacy_schema_without_fingerprint=True`
  fixture) proving a repair against schema-predating evidence fails closed
  with `AUTHORIZED_COMMAND_EVIDENCE_INVALID` when it requests authorization,
  and succeeds when it requests none; loop-level task-contract, per-child
  receipt, and loop-result fingerprint-field assertions plus an explicit
  scan of every file under the loop's evidence tree for absence of the raw
  argv text and the raw `Bash(...)` rule; the existing
  `test_loop_never_expands_authorization_after_creation` AST-walk test
  (unchanged, still green); and the full existing no-command regression
  suite in both files (unchanged, still green).

## 12. Real Claude invocation count

`REAL_CLAUDE_INVOCATIONS=0`. Every test uses a fake Python script standing
in for the `claude` executable, exactly as in the original implementation
task.

## 13. Real Codex invocation count

`REAL_CODEX_INVOCATIONS=0`. No Codex CLI or model was invoked by this
corrective task; this report is produced directly by the Owner-authorized
coding task, not by an independent review.

## 14. Remaining unproven CLI semantics

Unchanged from the original implementation and the Codex review's MEDIUM
finding: whether the installed Claude CLI's bare `Bash(<command>)`
permission rule is truly exact-match at runtime (rather than some
undocumented prefix or fuzzy match) is still not behaviorally confirmed.
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`, and this task
did not run a real Claude CLI contract probe (explicitly out of scope, per
the task's instructions). `resolve_claude_cli_authorization_contract()`
still returns `UNSUPPORTED` unconditionally in production, so `run()` still
returns `UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` before any worker process
starts whenever `--authorized-command` is supplied.

## 15. Risks and limitations

- The credential-term rejection pattern is intentionally broad (matches
  `token`, `secret`, `password`, etc. as substrings anywhere in a token) and
  will produce false-positive rejections of legitimate commands whose
  arguments happen to contain one of these words (e.g. a package literally
  named `secret-santa-cli`). This is a deliberate trade-off for a security
  allowlist boundary: an over-conservative rejection is recoverable (supply
  a different exact command), a false-negative acceptance of a persisted
  credential is not.
- Fingerprints prove *equality* of an authorization set, not *safety* of
  its contents; a command that was already accepted by the canonical
  validator when Attempt 1 requested it is not re-examined for new
  credential/wildcard patterns during repair inheritance — only its
  fingerprint is compared. This is intentional (repair must never
  re-litigate what was already approved) but is worth noting as a design
  choice, not an oversight.
- The nested-shell and `env`-wrapper basename checks are basename-only
  (`token.rsplit("/", 1)[-1]`), which is POSIX-appropriate for this
  Bash-authorization feature but would not by itself catch every possible
  disguised-shell scenario (e.g. a symlink named something else entirely
  that execs a shell) — that class of risk is out of scope for an argv-level
  static validator and is unaffected by this patch.
- Real-CLI runtime semantics remain unproven (section 14); this patch only
  improves what is validated and what is persisted, not whether the
  eventually-enabled feature will behave as the static evidence suggests.

## 16. Final Git state

No commit, stage, reset, stash, checkout, push, merge, or deploy action was
taken. `git rev-parse HEAD` remains `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d`.
`git status --short` shows exactly the three expected modified tracked
files (`src/tomtit/adapters/__init__.py`, `src/tomtit/adapters/claude_code_cli.py`,
`tests/adapters/test_claude_code_cli.py`) plus this new untracked report and
the two pre-existing untracked reports from the prior tasks; `.tomtit/dogfood/`
changes are untracked/ignored, as before. `git diff --cached` is empty.

## 17. Next gate

`STATUS=READY_FOR_CODEX_CORRECTIVE_REREVIEW`. The next action is an
Owner-triggered independent Codex corrective re-review of this patch. Exact
command authorization remains operationally disabled
(`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT=False`) pending that re-review
and a subsequent, separately Owner-authorized, isolated real-CLI contract
probe (per the Codex review's "Required controlled real-CLI contract probe"
section) — neither of which this task performed. A personal-web rerun
remains unauthorized.
