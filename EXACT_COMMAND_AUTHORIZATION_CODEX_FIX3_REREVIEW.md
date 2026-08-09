# Fix 3 Exact Command Authorization Re-review

## 1. Verdict

`APPROVE`

The remaining canonical-validator HIGH finding (long-command-flag
equivalence and split/punctuation-joined credential-key forms) is resolved.
The previously resolved evidence-safety HIGH finding shows no regression.
The disabled implementation (`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT =
False`) is safe enough to proceed to a separately Owner-authorized isolated
real Claude CLI contract probe. This verdict does **not** mean exact CLI
matching semantics are proven, does not flip the enable constant, and does
not authorize a personal-web rerun.

## 2. Reviewed HEAD and actual diff

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Matches the expected tracked HEAD. Worktree has uncommitted changes on top,
as expected for this review:

```
 M src/tomtit/adapters/__init__.py        |  22 +
 M src/tomtit/adapters/claude_code_cli.py | 566 +++++++++++++++++++-
 M tests/adapters/test_claude_code_cli.py | 916 +++++++++++++++++++++++++++++++++
```

`git diff --cached` is empty (nothing staged). Full `git diff` was read
directly (not summarized from the Fix3 implementation report).

## 3. Files reviewed

- `src/tomtit/adapters/claude_code_cli.py` (full file, all 1589 lines)
- `src/tomtit/adapters/__init__.py`
- `tests/adapters/test_claude_code_cli.py` (validator-relevant sections,
  including the new "Validator fix3" section)
- `.tomtit/dogfood/run_task.py`, `run_repair.py`, `run_loop.py` (authorized-
  command plumbing, contract/receipt construction, evidence serialization)
- `.tomtit/dogfood/test_run_loop.py`, `test_run_repair.py`
- All prior exact-command review/implementation reports for continuity of
  finding history

## 4. Previous finding status

- **PREVIOUS_HIGH_1** (long-command-flag / split-credential equivalence
  incompleteness): **RESOLVED**. Independently reproduced against the actual
  source, not the implementation report (see §6–8).
- **PREVIOUS_HIGH_2** (raw authorized-command/`Bash(...)` rule persisted in
  evidence): remains **RESOLVED**, no regression (see §11).

## 5. Canonical-validator authority

`validate_authorized_command_argv()` remains the single canonical validator.
Confirmed both entry paths reach it:

- Raw `--authorized-command` text → `shlex.split` → `normalize_authorized_command`
  → `validate_authorized_command_argv` (`claude_code_cli.py:387-416`).
- Direct `ClaudeCodeCLIRequest(...)` construction → `__post_init__` →
  `validate_authorized_command_argv` unconditionally re-validates every
  entry (`claude_code_cli.py:586-598`), regardless of whether the caller
  already normalized it.

No reconstruction, deserialization, or alternate helper path was found that
bypasses either entry point. `src/tomtit/adapters/__init__.py` exports only
`validate_authorized_command_argv`, `normalize_authorized_command(s)`, and
`ClaudeCodeCLIRequest` — no parallel validator or raw-argv passthrough is
exported. `.tomtit/dogfood/run_task.py`, `run_repair.py`, `run_loop.py` all
construct requests via `normalize_authorized_commands()` followed by
`ClaudeCodeCLIRequest(...)`, both of which land on the same function.
`run_loop.py`'s `invoke_implementation()` re-serializes an already-validated
argv back to CLI-string form via `render_authorized_command_display()` only
to pass it through `run_task.main(argv)` as an in-process call, which
re-parses and re-validates it from scratch — no bypass.

## 6. Long-command-equivalence analysis

Independently probed both `normalize_authorized_command` (raw path) and
direct `ClaudeCodeCLIRequest` construction (source-code execution against
this worktree, not the implementation report):

| Input | Result |
|---|---|
| `-command` | REJECT |
| `--command` | REJECT |
| `--COMMAND` | REJECT |
| `---command` | REJECT |
| `----COMMAND` | REJECT |
| `--command=value` | REJECT |
| `--COMMAND:value` | REJECT |
| `--command,anything` | REJECT |
| `--command.anything` | REJECT |
| `--command_anything` | REJECT |
| `--command-anything` | REJECT |
| `--cache` | ACCEPT (correct: unrelated word) |
| `--commander` | ACCEPT (correct: first word ≠ "command") |
| `--commandment` | ACCEPT (correct: first word ≠ "command") |
| `--my-command` | ACCEPT — see note below |

Direct-request construction with `--COMMAND`, `---command`, `--command=x`
(the exact three previously-accepted bypasses from the Fix2 re-review) now
all REJECT via the same code path.

**Note on `--my-command`:** the first normalized word is `my`, not
`command`, so it is not flagged. This is intentional and consistent with
the design (`_is_long_form_command_flag` only flags a token whose *first*
extracted word is exactly `command`) and is not a real equivalence to a
shell's `-c`/`--command` execution flag — no interpreter treats `--my-command`
as a command-mode switch. Not a finding.

Mechanism: `_normalize_option_words` strips leading hyphens, case-folds, and
extracts maximal `[a-z0-9]+` runs via the shared `_OPTION_WORD_PATTERN`,
treating every other character (`=`, `,`, `:`, `.`, `_`, `-`, arbitrary
repeated leading dashes) as a separator. Case-folding, arbitrary leading-
dash count, and punctuation are all handled uniformly by this single
extractor, closing the gap the Fix2 re-review found (exact raw-string
equality with `"--command"`).

## 7. Compound-short-flag analysis

Verified rejection remains intact for `-c`, `-lc`, `-cl`, `-ic`, `-ci`,
`-abc`, `-lci`, `-C`, `-LC` (case-fold via `.casefold()` on the letters
before the `"c" in letters` check).

Placement: `validate_authorized_command_argv` iterates every token in argv
regardless of position, so a compound flag anywhere in the argv (including
after other tokens, or after a `--` option terminator) is still caught —
verified by testing `-c` in a non-leading argv position. No terminator or
placement bypass found.

Boundary case investigated: `--c` and `--lc` (double-dash) are **accepted**,
since `_is_compound_interpreter_command_flag` only matches single-dash
clusters by design (`token[1] == "-"` short-circuits to `False`), and
`_is_long_form_command_flag`'s first extracted word is `c`/`lc`, not
`command`. Verified against real `bash` on this host that `bash --lc ...`
and `bash --c ...` are both rejected by bash itself as invalid options
(`bash: --lc: invalid option`) — GNU bash's long-option grammar does not
treat `--lc` as a bundled `-l -c`, so this is not an actual authorization
bypass, only an accepted-but-meaningless flag string. Not a finding.

Also investigated whether an attached-argument short-cluster (`-c1`,
`-cvalue`, `-cecho_hi`) could smuggle a command past the cluster check: for
letters-only clusters (`-cvalue`) the existing `"c" in letters` check still
catches it. For clusters with a non-letter suffix (`-c1`), the compound
check's `letters.isalpha()` guard fails and the long-form check's first
word is `c1` (not `command`), so this one *is* left unmatched at the
validator level. Verified against real `bash` on this host, however, that
`bash -c1 ...` and `bash -cecho_hi ...` do **not** execute an attached
command — bash's own option parser processes short-option clusters
character-by-character and reports `-1`/`-e`/etc. as an invalid option,
never reinterpreting the remainder as `-c`'s argument. This is a validator
grammar gap but not an exploitable authorization bypass against the actual
shells this check defends against (`sh`, `bash`, `zsh`, `dash`, etc., all
of which use the same POSIX-style short-cluster parsing). Reporting as a
non-blocking LOW finding for completeness rather than a blocker.

## 8. Credential-equivalence analysis

Independently probed (raw path and direct-request path):

| Input | Result |
|---|---|
| `--api=key` | REJECT |
| `--api,key` (single token) | REJECT |
| `--api,` + `key` (two tokens) | REJECT |
| `--api:key` | REJECT |
| `--api.key` | REJECT |
| `--api_key` | REJECT |
| `--api-key` | REJECT |
| `--api` + `key` (two tokens) | REJECT |
| `--private=key` | REJECT |
| `--private,key` | REJECT |
| `--access.token` | REJECT |
| `--auth-token` | REJECT |
| `--bearer-token` | REJECT |
| `--client:secret` | REJECT |
| `Authorization=Bearer` | REJECT (env-assignment pattern; still rejected, different category) |
| `Authorization,Bearer` (single token) | REJECT |
| `Authorization,` + `Bearer` (two tokens) | REJECT |
| `Authorization:Header` | REJECT |
| Mixed-case split (`AUTHORIZATION` `BEARER`) | REJECT |
| `--api`, unrelated word, `key` (non-adjacent) | ACCEPT — expected, see below |

Direct-request construction with `--api=key` and `--api,`+`key` (the exact
two previously-accepted bypasses from the Fix2 re-review) now REJECT via the
same code path.

Mechanism: two independent layers. (1) `_CREDENTIAL_TERM_PATTERN` still
substring-matches `token`, `password`, `secret`, `authorization`, `cookie`,
`bearer`, and the compact `api[-_]?key`/`private[-_]?key` forms anywhere in
a single token — this alone catches most listed cases immediately, since
"token"/"secret"/"bearer"/"authorization" alone are credential terms. (2)
The new adjacent-bigram scan over `_credential_scan_words()` (same
`_OPTION_WORD_PATTERN` word extractor, applied to *every* token, not only
hyphen-prefixed ones) catches the residual cases where neither word alone is
a credential term but the pair is — specifically `("api","key")` and
`("private","key")` — regardless of whether the words are punctuation-joined
in one token (`--api=key`) or split across adjacent argv tokens
(`--api,` `key`). Word extraction discards `=`, `,`, `:`, `.`, `_`, `-`, and
any other non-alphanumeric character uniformly, closing the fixed-separator-
list gap the Fix2 re-review found.

The non-adjacent case (`--api`, `totally`, `key`) is correctly accepted:
the bigram scan is deliberately adjacency-only per the task's own
specification ("credential-key material split across *adjacent* argv
tokens"), and a real credential-bearing invocation would place the value
immediately after its flag, not separated by an unrelated argument. Not a
finding.

## 9. ASCII/shell-regression analysis

Re-verified no regression on all previously-passing cases: non-ASCII code
points (`café`), Unicode lookalikes (Cyrillic, full-width Latin), `Bash`,
`/bin/BaSh`, `./ZsH`, `/usr/bin/ENV`, and a Windows-style mixed-case path
(`C:\tools\BaSh`) — all REJECT, unchanged from Fix2.

## 10. Direct-request bypass analysis

Constructed `ClaudeCodeCLIRequest` directly (bypassing `normalize_authorized_command`
entirely) with all five previously-accepted Fix2 bypasses
(`--COMMAND`, `---command`, `--command=<synthetic>`, `--api=key`, `--api,`+`key`)
as pre-built argv tuples. All five now raise `AuthorizedCommandError` from
`__post_init__`, confirming the fix applies uniformly at the trust boundary
and is not merely a raw-text-parsing patch. The test suite's
`test_direct_request_construction_cannot_bypass_canonical_validator`
(`tests/adapters/test_claude_code_cli.py:2242`) covers 44 parametrized cases
including all Fix3-relevant forms, matching independent probing.

## 11. Evidence-regression analysis

No regression from the previously-resolved HIGH-2 finding:

- `ClaudeCodeCLIResult` carries only `authorized_command_fingerprints`,
  `authorized_command_set_fingerprint`, `authorized_command_count`, and
  `authorized_command_cli_rules` (fingerprint-placeholder strings via
  `render_authorized_command_evidence_rule`, never the raw rule) —
  `claude_code_cli.py:637-644`.
- `ClaudeCodeCLIAdapter.run()` swaps every raw `Bash(<command>)` rule
  actually placed in the real subprocess argv for its fingerprint
  placeholder before it reaches `sanitized_argv`
  (`claude_code_cli.py:1453-1470`) — the raw rule exists only transiently
  in-memory while building the real `Popen` argv.
- `.tomtit/dogfood/run_task.py`, `run_repair.py`, `run_loop.py` all persist
  only fingerprints/counts/set-fingerprints in receipts and the task
  contract (`build_task_contract` at `run_loop.py:265-293` explicitly
  comments that only fingerprints are recorded); grepped for any
  `authorized_commands` key landing in a `json.dumps`/`write_text` call and
  found none — the only `write_text(json.dumps(...))` calls in `run_loop.py`
  are `task-contract.json`, `loop-result.json`, `loop-receipt.json`, none of
  which include the raw tuple.
- `invoke_implementation()`'s re-serialized `--authorized-command` string
  (via `render_authorized_command_display`) is used only to build an
  in-memory `argv` list passed to `run_task.main(argv)` as a same-process
  function call — never written to disk or logged.
- Validation error messages: confirmed via both direct probing and the
  existing `test_validator_fix3_error_messages_never_contain_synthetic_values`
  test that no rejected command value or synthetic secret ever appears in
  an `AuthorizedCommandError` message.

## 12. Repair/loop integrity

`run_repair.py`'s preflight (`run_preflight`, lines ~630-700) is unchanged
in its authorization-inheritance logic from the previously-approved state:
Attempt 2 recomputes its own `authorized_command_set_fingerprint` from
freshly-normalized `--authorized-command` arguments and compares it by
exact equality against the fingerprint persisted in Attempt 1's evidence —
addition, removal, substitution, and reordering all change the fingerprint
and fail closed with `AUTHORIZED_COMMAND_CONTRACT_MISMATCH`. Evidence
lacking the fingerprint field entirely (legacy/malformed) rejects any
non-empty `--authorized-command` outright rather than trusting it. Findings
from a review never introduce new authorization — the repair prompt is
built from the original task prompt plus review findings, and
`authorized_commands` is threaded through unchanged from preflight. This is
orthogonal to the Fix3 change (which only affects what individual argv
tokens the shared validator accepts) and shows no regression; confirmed by
a clean `test_run_repair.py` run.

## 13. Tests and exit codes

```
python3 .tomtit/dogfood/test_run_loop.py            -> EXIT=0 (52 tests)
python3 .tomtit/dogfood/test_run_repair.py           -> EXIT=0 (51 tests)
python3 .tomtit/dogfood/test_run_review.py           -> EXIT=0 (45 tests)
python3 -m pytest tests/adapters -q                  -> EXIT=0
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q -> EXIT=0
python3 -m pytest -q                                 -> EXIT=0 (full suite)
python3 -m compileall -q src tests scripts .tomtit/dogfood -> EXIT=0
ruff check src tests scripts                         -> EXIT=0
python3 -m mypy src                                  -> EXIT=0 ("Success: no issues found in 13 source files")
git diff --check                                     -> EXIT=0
```

All commands ran; none were skipped or marked NOT_RUN.

## 14. HIGH/MEDIUM/LOW findings

No HIGH or MEDIUM findings remain open.

**LOW (non-blocking):** the compound-short-flag detector's `letters.isalpha()`
guard means an attached-argument short-option cluster with a non-letter
suffix immediately after `c` (e.g. `-c1`, `-cecho_hi`) is not caught by
either the compound-flag check or the long-form-flag check. Verified against
real `bash` on this host that this is not an exploitable bypass — bash's own
short-option parser does not support attached-argument syntax for `-c` and
rejects the trailing characters as invalid options — but it is a validator
grammar gap relative to the stated intent ("any single-dash... short-option
cluster containing `c`"). Recommend tightening `_is_compound_interpreter_command_flag`
to flag on `"c" in letters` matching a stricter alnum check, or add a
regression test asserting `-c1`/`-cX` are rejected, in a future pass. Not a
gate blocker: no real Bash/sh/zsh/dash invocation is affected today given
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False` blocks all real use
regardless.

## 15. Remaining limitations

- Exact Claude CLI bare-`Bash(<command>)` rule-matching semantics remain
  unproven by real invocation (unchanged; static-inspection-only evidence).
- The validator is a syntactic denylist, not an exhaustive secret scanner;
  it targets the specific equivalence classes named in the Fix2/Fix3
  re-reviews (case, dash-count, punctuation-separator, split-adjacent-word
  forms) rather than every conceivable credential-bearing phrasing.
- The LOW finding in §14 is an accepted, non-exploitable grammar gap, not a
  security bypass against real shells.

## 16. Whether isolated CLI probe is ready

Yes — the disabled implementation is now safe enough that a separately
Owner-authorized, isolated real Claude CLI contract probe may proceed. This
does not itself authorize that probe; it only removes the code-review
blocker that was previously withholding approval.

## 17. Whether personal-web rerun is authorized

No. Not requested, not evaluated, not authorized by this review.

## 18. Next gate

Owner decision on whether to schedule the isolated, bounded real Claude CLI
contract probe (separately authorized, separately scoped) to confirm
bare-`Bash(<command>)` rule exactness before flipping
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`. Optionally, a low-priority
follow-up to close the `-c1`/attached-argument grammar gap noted in §14.

---

VERDICT=APPROVE
PREVIOUS_HIGH_1=RESOLVED
PREVIOUS_HIGH_2=RESOLVED
FAIL_CLOSED_INTERMEDIATE_STATE=PASS
LONG_COMMAND_EQUIVALENCE_REJECTION=PASS
COMPOUND_FLAG_REJECTION=PASS
SPLIT_CREDENTIAL_REJECTION=PASS
ASCII_ONLY_GRAMMAR=PASS
CASEFOLDED_SHELL_REJECTION=PASS
DIRECT_REQUEST_BYPASS_CLOSED=PASS
RAW_AUTHORIZED_COMMAND_PERSISTED=NO
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_CLI_PROBE_READY=YES
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=OWNER_DECISION_ON_ISOLATED_CLI_PROBE_SCHEDULING
