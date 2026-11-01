# Exact Command Authorization — Validator Fix2 Report

This report documents one narrow, validator-only corrective patch applied
after the independent Codex corrective re-review recorded in
`EXACT_COMMAND_AUTHORIZATION_CODEX_REREVIEW.md`
(`VERDICT=REQUEST_CHANGES`, `PREVIOUS_HIGH_1=OPEN`,
`PREVIOUS_HIGH_2=RESOLVED`). It supersedes nothing in
`EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md` or
`EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md`, both of
which are left unmodified.

## 1. Status

`PREVIOUS_HIGH_1=CLAIMED_FIXED_PENDING_REVIEW` (the remaining canonical
validator edge cases — mixed-case/path-qualified shell names, compound
interpreter flags, non-ASCII tokens, split credential-key forms).
`PREVIOUS_HIGH_2=PRESERVED_RESOLVED` (fingerprint-only evidence persistence
was not touched, except for one new regression test proving it).

Neither claim is independently resolved by this report. Only the next
Codex re-review can determine that. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`
remains `False`; exact authorization remains operationally disabled; no real
Claude CLI contract probe was run; no personal-web rerun occurred.

## 2. Starting HEAD

`f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged by this task — no
commit was made; see section 14).

## 3. Codex findings addressed

From `EXACT_COMMAND_AUTHORIZATION_CODEX_REREVIEW.md`:

- **HIGH (re-review) — mixed-case/path-qualified shell and compound
  interpreter-flag bypass; Unicode lookalikes accepted.** The canonical
  validator compared nested-shell and `env` basenames case-sensitively
  (`"Bash"`, `/bin/BaSh`, `./ZsH`, `C:\tools\BaSh` all passed), only
  rejected the exact token `"-c"` (so `-lc`, `-ic`, `-lci`, `--command` all
  passed), and imposed no restriction on non-ASCII characters at all
  (ordinary non-ASCII text and Unicode-lookalike executable names both
  passed).
- **MEDIUM (re-review) — credential-key material split across argv tokens
  accepted.** The existing single-token credential-term regex could not see
  a credential-shaped flag/value pair spread across two adjacent tokens
  (e.g. `--api key SECRET`, `Authorization Bearer SECRET`).
- **LOW (re-review) — report omission.** Not separately addressed by any
  code change (out of scope: prior reports are not modified); this report's
  own "ASCII-only grammar" section (5) accurately describes the new
  behavior instead.

The MEDIUM finding from the *original* corrective review (CLI exactness
unproven) remains untouched and out of scope, as instructed.

## 4. Files changed

- `src/tomtit/adapters/claude_code_cli.py` — the only substantive change:
  four new checks wired into the existing single canonical validator
  (`validate_authorized_command_argv`), plus their supporting constants and
  helper functions. No fingerprint, evidence, redaction, `run()`,
  `_build_argv`, `_early_result`, or `ClaudeCodeCLIResult` code was touched.
- `tests/adapters/test_claude_code_cli.py` — new raw-input and
  direct-request tests for every required case, plus two explicit
  regression tests proving the fingerprint/evidence design (HIGH-2,
  already Codex-resolved) is unchanged.
- `src/tomtit/adapters/__init__.py` — **not modified**; no new public
  symbol was introduced (all new helpers are private, module-internal —
  `_token_basename`, `_is_compound_interpreter_command_flag`,
  `_split_credential_words` — since only `validate_authorized_command_argv`,
  already exported, needed to change behavior). `git diff --stat` above
  shows this file with a residual diff only because it already carried the
  Fix1 corrective patch's exports (`compute_authorized_command_fingerprint`,
  etc.) relative to the pre-corrective baseline; nothing in it changed in
  *this* task.

No `.tomtit/dogfood/*` file needed to change: those runners call
`normalize_authorized_commands()` / construct `ClaudeCodeCLIRequest`, both
of which automatically inherit the stricter canonical validator with no
call-site change, and their existing test suites (`test_run_loop.py`,
`test_run_repair.py`, `test_run_review.py`) all remain green unmodified
(see section 11).

## 5. ASCII-only grammar

Inside the existing per-token character loop in
`validate_authorized_command_argv` (immediately after the existing
control-character/ambiguous-whitespace check), every character with
`ord(ch) > 127` is now rejected with the fixed category
`INVALID_AUTHORIZED_COMMAND` and the fixed message "authorized command
contains a non-ASCII character" — never the character or token itself. No
Unicode normalization, NFKC folding, or confusable-character mapping is
attempted, per instruction: this is a strict allowlist grammar, not a
best-effort security-through-normalization scheme. An ASCII space inside an
already-tokenized argument remains allowed, unchanged from the prior patch.
This rejects both ordinary non-ASCII text (e.g. `café`) and Unicode
lookalike executables (Cyrillic, full-width Latin, etc.) uniformly, since
neither is ASCII.

## 6. Case-folded basename validation

`_token_basename(token)` replaces `\` with `/` before taking the final
path component, so both POSIX and Windows-style path-qualified tokens
resolve to the same basename. The basename is compared via `.casefold()`
against `_FORBIDDEN_SHELL_EXECUTABLE_BASENAMES` (`sh`, `bash`, `zsh`,
`dash`, `fish`, `ksh`, `csh`, `tcsh`) and separately against the literal
`env` wrapper name. This rejects `Bash`, `/bin/BaSh`, `./ZsH`,
`/usr/bin/ENV`, and `C:\tools\BaSh` exactly as it already rejected their
lowercase bare forms. The match remains basename-*equality*, not a
substring scan: a token like `myzshrc-plugin` (containing `zsh` as a
substring but not equal to it as a basename) is correctly still accepted
(`test_basename_match_is_exact_not_substring`). This check applies to every
token in argv, not only argv[0] — unchanged from the original corrective
patch's design, and necessary since a forbidden basename can appear in any
position.

## 7. Compound interpreter flag validation

`_is_compound_interpreter_command_flag(token)` rejects:

- The literal long-form flag `--command`.
- Any single-dash, all-ASCII-letter short-option cluster that contains the
  letter `c` (case-insensitively), e.g. `-c`, `-lc`, `-cl`, `-ic`, `-ci`,
  `-abc`, `-lci`.

Long-form (`--`) flags are rejected *only* via the exact `--command`
literal — a long-form flag that happens to contain `c` (e.g. `--cache`,
`--config`) is deliberately **not** rejected, since sweeping up every
long-form flag containing the letter `c` would be far too broad for an MVP
allowlist and was not what Codex's finding asked for
(`test_long_form_flag_containing_c_is_not_rejected`). Ordinary short flags
without `c` (e.g. `-p`, `-x`) also remain accepted
(`test_unrelated_short_flags_without_c_still_accepted`). This is
deliberately conservative for compact short-option clusters, per
instruction, and replaces the old exact-`"-c"`-only check (which the
re-review showed `-lc` bypassed).

## 8. Split credential-key detection

`_split_credential_words(token)` case-folds the token, strips a leading
`-` (so `--api` and `-api` both normalize to `api`), and splits on
`_`, `-`, `.`, `:` into a list of alphanumeric words. `validate_authorized_command_argv`
builds one flat word list for the whole argv (concatenating every token's
words in argv order) and rejects if any *adjacent* pair matches one of the
required bigrams: `(api, key)`, `(private, key)`, `(access, token)`,
`(auth, token)`, `(bearer, token)`, `(client, secret)`,
`(authorization, bearer)`, `(authorization, header)`. This catches
credential material spread across two argv tokens (`--api key SECRET`,
`Authorization Bearer SECRET`) that the single-token
`_CREDENTIAL_TERM_PATTERN` regex could not see, without weakening that
existing single-token check (both remain active; either can fire). Category
is the existing `CREDENTIAL_BEARING_AUTHORIZED_COMMAND`, and the error
message is the fixed string "...split across arguments" — never the
matched words or the surrounding secret value.

## 9. Direct-request trust-boundary proof

No second validator was introduced anywhere. `ClaudeCodeCLIRequest.__post_init__`
still calls exactly `validate_authorized_command_argv` per entry — the same
function `normalize_authorized_command` calls after `shlex.split` — so every
new check in sections 5–8 is automatically enforced on direct construction
with zero additional wiring. `test_direct_request_construction_cannot_bypass_canonical_validator`
was extended with one representative bad argv per new category (mixed-case
shell, path-qualified mixed-case shell, compound flags, non-ASCII tokens,
each split-credential form) — all constructed as pre-tokenized tuples
passed straight to `ClaudeCodeCLIRequest`, bypassing `normalize_authorized_command`
entirely, and all still raise `AuthorizedCommandError`.

## 10. Evidence-regression proof

Two new tests assert the fingerprint/evidence design Codex already accepted
as HIGH-2 RESOLVED is unaffected by this patch:

- `test_validator_fix2_preserves_fingerprint_only_result_schema` — asserts
  `ClaudeCodeCLIResult`'s dataclass fields still exclude `authorized_commands`
  and still include `authorized_command_fingerprints`,
  `authorized_command_set_fingerprint`, `authorized_command_count`, and
  `authorized_command_cli_rules` (and that the *request* dataclass's
  in-memory `authorized_commands` field, which is not evidence, is
  unaffected).
- `test_validator_fix2_preserves_no_raw_evidence_for_accepted_command` —
  runs a full adapter invocation (under the same existing monkeypatch used
  elsewhere to exercise the otherwise-fail-closed exact-supported path) with
  a valid, ASCII, non-credential-shaped authorized command, and asserts the
  raw `Bash(...)` rule is absent from `sanitized_argv`, the safe fingerprint
  placeholder is present in `authorized_command_cli_rules`, and the
  argument's synthetic placeholder value never appears in `sanitized_argv`
  or the rendered evidence manifest JSON.

Both pass unchanged against the untouched fingerprinting/redaction code
(section 4), confirming this patch is validation-only.

## 11. Tests and exit codes

All commands run from the repository root, after the patch, with zero real
Claude or Codex invocations:

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

New/updated test coverage, matching the 22 required items:

1–5. Mixed-case/path-qualified shell rejection: `Bash`, `/bin/BaSh`,
`./ZsH`, `/usr/bin/ENV`, `C:\tools\BaSh` — raw-input parametrized test plus
one direct-request case each.
6–9. Compound flags: `-lc`, `-ic`, `-lci`, `--command` — raw-input
parametrized test (plus `-cl`, `-abc`) and direct-request cases; two
negative tests (`-p`/`-x` accepted, `--cache` accepted) prove the rule is
not over-broad.
10–11. Unicode/non-ASCII: a Cyrillic lookalike, a full-width Latin
lookalike, and an ordinary accented character (`café`) — raw-input
parametrized test plus direct-request cases.
12–16. Split credential forms: `api key`, `private key`, `access token`,
`client secret`, `authorization bearer` (plus `authorization header`) — raw
and direct-request coverage, each asserting category
`CREDENTIAL_BEARING_AUTHORIZED_COMMAND`.
17. Direct-request rejection for every case above:
`test_direct_request_construction_cannot_bypass_canonical_validator`,
extended with one tuple per new category.
18. Error messages contain none of the supplied synthetic values:
`test_validator_fix2_error_messages_never_contain_synthetic_values` plus
per-case assertions on every new parametrized test and on the extended
direct-request test.
19. Valid harmless ASCII command still passes:
`test_valid_harmless_exact_argv_still_accepted` (pre-existing, still
green), plus new `test_basename_match_is_exact_not_substring`,
`test_unrelated_short_flags_without_c_still_accepted`,
`test_long_form_flag_containing_c_is_not_rejected`.
20. Fingerprint/evidence behavior unchanged: section 10's two new tests.
21. No authorized command still preserves existing behavior:
`test_no_authorized_command_preserves_existing_behavior` (pre-existing,
unmodified, still green).
22. Full regression suite remains green: all rows in the table above.

## 12. Real invocation counts

`REAL_CLAUDE_INVOCATIONS=0`. `REAL_CODEX_INVOCATIONS=0`. Every test uses a
fake Python script standing in for the `claude` executable; this report is
produced directly by the Owner-authorized coding task, not by an
independent review.

## 13. Remaining unproven CLI semantics

Unchanged: whether the installed Claude CLI's bare `Bash(<command>)`
permission rule is truly exact-match at runtime remains behaviorally
unconfirmed. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`;
this task did not run a real Claude CLI contract probe (explicitly out of
scope). `resolve_claude_cli_authorization_contract()` still returns
`UNSUPPORTED` unconditionally in production, so `run()` still returns
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` before any worker process starts
whenever `--authorized-command` is supplied.

## 14. Final Git state

No commit, stage, reset, stash, checkout, push, merge, or deploy action was
taken.

```
git rev-parse HEAD          -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
git status --short          -> M src/tomtit/adapters/__init__.py
                                M src/tomtit/adapters/claude_code_cli.py
                                M tests/adapters/test_claude_code_cli.py
                                ?? EXACT_COMMAND_AUTHORIZATION_CODEX_REREVIEW.md
                                ?? EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md
                                ?? EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md
                                ?? EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md
git diff --stat              -> src/tomtit/adapters/__init__.py        |  22 +
                                 src/tomtit/adapters/claude_code_cli.py | 519 +++++++++++++++++++++-
                                 tests/adapters/test_claude_code_cli.py | 762 +++++++++++++++++++++++++++++++++
                                 3 files changed, 1300 insertions(+), 3 deletions(-)
git diff --cached             -> (empty)
```

`.tomtit/dogfood/` has no tracked changes from this task (git-ignored, as
before). No previous report was modified.

## 15. Next gate

`STATUS=READY_FOR_CODEX_VALIDATOR_FIX2_REREVIEW`. The next action is an
Owner-triggered independent Codex re-review of this narrow validator patch.
Exact command authorization remains operationally disabled
(`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT=False`) pending that re-review
and a subsequent, separately Owner-authorized, isolated real-CLI contract
probe — neither of which this task performed. A personal-web rerun remains
unauthorized.

---

```
STATUS=READY_FOR_CODEX_VALIDATOR_FIX2_REREVIEW
PREVIOUS_HIGH_1=CLAIMED_FIXED_PENDING_REVIEW
PREVIOUS_HIGH_2=PRESERVED_RESOLVED
ASCII_ONLY_AUTHORIZATION_GRAMMAR=PASS
CASEFOLDED_SHELL_REJECTION=PASS
COMPOUND_INTERPRETER_FLAG_REJECTION=PASS
SPLIT_CREDENTIAL_KEY_REJECTION=PASS
DIRECT_REQUEST_BYPASS_CLOSED=CLAIMED_PENDING_REVIEW
RAW_AUTHORIZED_COMMAND_PERSISTED=NO
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_CLI_PROBE_AUTHORIZED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_CODEX_VALIDATOR_FIX2_REREVIEW
```
