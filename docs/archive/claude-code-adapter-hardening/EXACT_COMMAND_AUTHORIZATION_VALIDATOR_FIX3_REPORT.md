# Exact Command Authorization — Validator Fix3 Report

This report documents one narrow, validator-equivalence-only corrective
patch applied after the independent Codex re-review recorded in
`EXACT_COMMAND_AUTHORIZATION_CODEX_FIX2_REREVIEW.md`
(`VERDICT=REQUEST_CHANGES`, `PREVIOUS_HIGH_1=OPEN`,
`PREVIOUS_HIGH_2=RESOLVED`, `COMPOUND_FLAG_REJECTION=FAIL`,
`SPLIT_CREDENTIAL_REJECTION=FAIL`, `DIRECT_REQUEST_BYPASS_CLOSED=FAIL`). It
supersedes nothing in the three prior reports
(`EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md`,
`EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md`,
`EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX2_REPORT.md`), all of which are
left unmodified.

## 1. Status

`PREVIOUS_HIGH_1=CLAIMED_FIXED_PENDING_REVIEW` (the remaining equivalent-form
bypasses the fix2 patch left open: raw-string-equality-based long-command
detection, and a fixed-separator-list-based split-credential scan).
`PREVIOUS_HIGH_2=PRESERVED_RESOLVED` (fingerprint-only evidence persistence
was not touched by this patch).

Neither claim is independently resolved by this report. Only the next Codex
re-review can determine that. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`
remains `False`; exact authorization remains operationally disabled; no real
Claude CLI contract probe was run; no personal-web rerun occurred.

## 2. Starting HEAD

`f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged by this task — no
commit was made; see section 12).

## 3. Remaining Codex findings addressed

From `EXACT_COMMAND_AUTHORIZATION_CODEX_FIX2_REREVIEW.md`'s HIGH finding,
all five confirmed-accepted bypasses:

- `--COMMAND` — the prior long-form check only matched the exact lowercase
  literal `"--command"`.
- `---command` — repeated leading dashes were not stripped before the
  exact-string comparison.
- `--command=<synthetic>` — any suffix (via `=`) broke the exact-string
  comparison.
- `--api=key` — the split-credential scan's separator list (`_`, `-`, `.`,
  `:`) did not include `=` or `,`, so a single punctuation-joined token was
  never split into `api`/`key` words.
- `--api,` followed by `key` (two adjacent argv tokens) — same
  separator-list gap, this time across a token boundary.

Both are structural gaps: the prior checks relied on exact-string matching
and a fixed separator list rather than a canonical word-extraction grammar,
so any case, dash-count, or punctuation variant not literally anticipated
slipped through — including via direct `ClaudeCodeCLIRequest` construction,
since it reaches the identical (incomplete) canonical validator.

## 4. Files changed

- `src/tomtit/adapters/claude_code_cli.py` — the only substantive change:
  two existing checks (long-form command-flag detection, split-credential
  word scanning) were rewritten to use one shared canonical ASCII word
  extractor (`_OPTION_WORD_PATTERN = re.compile(r"[a-z0-9]+")`) instead of
  exact-string equality and a fixed separator list, respectively. No
  fingerprint, evidence, redaction, `run()`, `_build_argv`,
  `_early_result`, or `ClaudeCodeCLIResult` code was touched.
- `tests/adapters/test_claude_code_cli.py` — new raw-input and
  direct-request tests for every required equivalence form, plus
  non-regression tests for the unrelated-word exclusions (`--cache`,
  `--commander`, `--commandment`) and the compact short-option-cluster rule.
- `src/tomtit/adapters/__init__.py` — **not modified**; no new public
  symbol was introduced. All new/changed helpers
  (`_normalize_option_words`, `_is_long_form_command_flag`,
  `_credential_scan_words`, `_OPTION_WORD_PATTERN`) are module-private. The
  residual diff shown by `git diff --stat` for this file is entirely
  carried over from the earlier fix1 corrective patch (fingerprint exports);
  nothing in it changed in *this* task.

No `.tomtit/dogfood/*` file needed to change, as expected: those runners
call `normalize_authorized_commands()` / construct `ClaudeCodeCLIRequest`,
both of which automatically inherit the corrected canonical validator with
no call-site change, and their existing test suites all remain green
unmodified (section 9).

## 5. Canonical option-key normalization

`_normalize_option_words(token)` implements the exact recipe: if the token
does not start with `-`, it is out of scope (returns `[]`, mirroring "for
any ASCII token beginning with one or more hyphens"); otherwise every
leading hyphen is stripped (`token.lstrip("-")`), the remainder is
case-folded, and `_OPTION_WORD_PATTERN.findall(...)` extracts every maximal
run of ASCII `[a-z0-9]` as one word — any other character (`=`, `,`, `:`,
`.`, `_`, `-`, or anything else) is discarded as a separator by construction
of the regex, with no separate separator list to keep in sync. The function
never returns the original token, only the extracted lowercase words.

`_is_long_form_command_flag(token)` rejects when the *first* extracted word
is exactly `"command"` — not a substring match — which is why `--cache`,
`--commander`, and `--commandment` remain accepted by this specific check
(each produces one whole word — `cache`, `commander`, `commandment` — none
equal to `"command"`), while `-command`, `--command`, `--COMMAND`,
`---command`, `--command=value`, `--COMMAND:value`, `--command,anything`,
`--command.anything`, and `--command_anything` are all rejected (each
reduces to a first word of exactly `command`, regardless of case,
dash-count, or trailing punctuation). This is wired into
`validate_authorized_command_argv` as `_is_compound_interpreter_command_flag(token)
or _is_long_form_command_flag(token)` — the compact short-option-cluster
rule (`-lc`, `-ic`, `-abc`, etc.) is unchanged and still independently
enforced (`test_compact_short_option_clusters_still_rejected_after_fix3`).

## 6. Canonical ASCII credential-word tokenization

`_credential_scan_words(token)` applies the same shared
`_OPTION_WORD_PATTERN` to *every* argv token (not only hyphen-prefixed
ones, since bare credential-shaped values like a lone `Bearer` must also be
caught), after case-folding — again with no separator list, since any
non-`[a-z0-9]` character is automatically a boundary. `validate_authorized_command_argv`
flattens every token's words across the whole argv, in order, and rejects
if any adjacent pair matches one of the required bigrams (`api key`,
`private key`, `access token`, `auth token`, `bearer token`, `client
secret`, `authorization bearer`, `authorization header`). This now
correctly detects `--api=key`, `--api,key`, `--api:key`, `--api.key`,
`--api_key` (all as one token) and `--api,` + `key` / `Authorization,` +
`Bearer` (as two adjacent tokens) uniformly — the same extractor handles
punctuation-joined and space-separated forms identically, closing the
`=`/`,` gap the fix2 patch's fixed separator list left open. Mixed-case
equivalents (`--API=KEY`, `--PRIVATE.KEY`, `--Access:Token`) are rejected
via the same case-fold step.

## 7. Raw-input and direct-request enforcement

No second validator was introduced. Both rewritten checks live entirely
inside `validate_authorized_command_argv`, the single canonical validator
already called by `normalize_authorized_command` (after `shlex.split`),
`normalize_authorized_commands`, and `ClaudeCodeCLIRequest.__post_init__`.
`test_direct_request_construction_cannot_bypass_canonical_validator` was
extended with one direct-tuple case per newly-fixed equivalence form (all
eight long-command variants and all eleven credential-equivalence forms,
including the two-token `("curl", "--api,", "key")` and
`("curl", "Authorization,", "Bearer")` cases), each constructed as a
pre-tokenized tuple passed straight to `ClaudeCodeCLIRequest`, bypassing
`normalize_authorized_command` entirely — all still raise
`AuthorizedCommandError`.

## 8. Evidence non-regression

No test or source change in this patch touches
`ClaudeCodeCLIResult`, `build_evidence_manifest`, `write_evidence`, `run()`,
`_build_argv`, `_early_result`, or any dogfood evidence-writing code. The
fix2 patch's own evidence-regression tests
(`test_validator_fix2_preserves_fingerprint_only_result_schema`,
`test_validator_fix2_preserves_no_raw_evidence_for_accepted_command`) were
re-run unmodified and remain green (section 9), confirming: no raw command
argv in `ClaudeCodeCLIResult`; no raw `Bash(...)` rule in `sanitized_argv`;
fingerprint-only task/repair/loop evidence (dogfood test suites, section 9,
unmodified and green); deterministic per-command and ordered-set
fingerprints; safe `Bash(<AUTHORIZED_COMMAND_SHA256:...>)` placeholders;
stale-evidence fail-closed behavior in `run_repair.py` (unmodified,
`test_run_repair.py` green).

## 9. Tests with exact exit codes or NOT_RUN

All commands were actually run to completion; none are `NOT_RUN`.

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

New/updated test coverage, matching the 30 required items:

1–8. Long-command equivalence forms (`--COMMAND`, `---command`,
`-command`, `--command=value`, `--COMMAND:value`, `--command,anything`,
`--command.anything`, `--command_anything`) —
`test_long_command_flag_equivalence_forms_rejected` (parametrized,
raw-input) plus matching direct-tuple cases in
`test_direct_request_construction_cannot_bypass_canonical_validator`.
9–20. Credential equivalence forms (`--api=key`, `--api,key`,
`--api,`+`key` as adjacent tokens, `--api:key`, `--api.key`, `--api_key`,
`--private=key`, `--access.token`, `Authorization=Bearer`,
`Authorization,`+`Bearer` as adjacent tokens, `--client:secret`, and
mixed-case equivalents `--API=KEY`/`--PRIVATE.KEY`/`--Access:Token`) —
`test_split_or_punctuation_joined_credential_forms_rejected`
(parametrized), `test_api_key_as_adjacent_argv_tokens_rejected`,
`test_authorization_comma_bearer_as_adjacent_argv_tokens_rejected`, plus
matching direct-tuple cases.
21. Direct-request rejection for every case above: the extended
`test_direct_request_construction_cannot_bypass_canonical_validator`
parametrize list.
22. Exceptions contain no synthetic secret or rejected raw value:
`test_validator_fix3_error_messages_never_contain_synthetic_values`, plus a
`SYNTHETIC_SECRET not in str(excinfo.value)` assertion on every new
parametrized test and on the extended direct-request test.
23–25. `--cache`/`--commander`/`--commandment` remain accepted by the
long-command-specific check:
`test_long_command_flag_check_does_not_reject_unrelated_words`
(parametrized).
26. Existing compact short flags containing `c` remain rejected:
`test_compact_short_option_clusters_still_rejected_after_fix3`, plus the
pre-existing `test_compound_interpreter_command_flags_rejected` (unmodified,
still green).
27. Existing ASCII-only and case-folded shell tests remain green:
`test_non_ascii_tokens_rejected`, `test_mixed_case_and_path_qualified_shell_names`
(both pre-existing, unmodified, still green).
28. Fingerprint-only evidence tests remain green: both fix2 evidence-
regression tests (section 8), unmodified, still green.
29. No-command behavior remains unchanged:
`test_no_authorized_command_preserves_existing_behavior` (pre-existing,
unmodified, still green).
30. Full regression suite remains green: every row in the table above.

## 10. Real invocation counts

`REAL_CLAUDE_INVOCATIONS=0`. `REAL_CODEX_INVOCATIONS=0`. Every test uses a
fake Python script standing in for the `claude` executable; this report is
produced directly by the Owner-authorized coding task, not by an
independent review.

## 11. Remaining unproven CLI semantics

Unchanged: whether the installed Claude CLI's bare `Bash(<command>)`
permission rule is truly exact-match at runtime remains behaviorally
unconfirmed. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`;
this task did not run a real Claude CLI contract probe (explicitly out of
scope). `resolve_claude_cli_authorization_contract()` still returns
`UNSUPPORTED` unconditionally in production, so `run()` still returns
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` before any worker process starts
whenever `--authorized-command` is supplied.

## 12. Final Git state

No commit, stage, reset, stash, checkout, push, merge, or deploy action was
taken.

```
git rev-parse HEAD          -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
git status --short          -> M src/tomtit/adapters/__init__.py
                                M src/tomtit/adapters/claude_code_cli.py
                                M tests/adapters/test_claude_code_cli.py
                                ?? EXACT_COMMAND_AUTHORIZATION_CODEX_FIX2_REREVIEW.md
                                ?? EXACT_COMMAND_AUTHORIZATION_CODEX_REREVIEW.md
                                ?? EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md
                                ?? EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md
                                ?? EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md
                                ?? EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX2_REPORT.md
git diff --stat              -> src/tomtit/adapters/__init__.py        |  22 +
                                 src/tomtit/adapters/claude_code_cli.py | 566 +++++++++++++++++++-
                                 tests/adapters/test_claude_code_cli.py | 916 +++++++++++++++++++++++++++++++++
                                 3 files changed, 1501 insertions(+), 3 deletions(-)
git diff --cached             -> (empty)
```

`.tomtit/dogfood/` has no tracked changes from this task (git-ignored, as
before). No previous report was modified.

## 13. Next gate

`STATUS=READY_FOR_CODEX_VALIDATOR_FIX3_REREVIEW`. The next action is an
Owner-triggered independent Codex re-review of this narrow validator patch.
Exact command authorization remains operationally disabled
(`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT=False`) pending that re-review
and a subsequent, separately Owner-authorized, isolated real-CLI contract
probe — neither of which this task performed. A personal-web rerun remains
unauthorized.

---

```
STATUS=READY_FOR_CODEX_VALIDATOR_FIX3_REREVIEW
PREVIOUS_HIGH_1=CLAIMED_FIXED_PENDING_REVIEW
PREVIOUS_HIGH_2=PRESERVED_RESOLVED
LONG_COMMAND_EQUIVALENCE_REJECTION=PASS
SPLIT_CREDENTIAL_EQUIVALENCE_REJECTION=PASS
DIRECT_REQUEST_BYPASS_CLOSED=CLAIMED_PENDING_REVIEW
RAW_AUTHORIZED_COMMAND_PERSISTED=NO
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_CLI_PROBE_AUTHORIZED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_CODEX_VALIDATOR_FIX3_REREVIEW
```
