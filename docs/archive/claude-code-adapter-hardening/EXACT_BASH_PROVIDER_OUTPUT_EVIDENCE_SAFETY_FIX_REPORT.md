# Exact-Bash Provider-Output Evidence-Safety Fix Report (M-1 / M-2)

## 1. Status

Both remaining enablement blockers are corrected by inverting the design at
the persistence boundary. When `authorized_commands` is non-empty, TOMTIT no
longer attempts to *redact* provider-controlled text — it does not persist
provider-controlled free-form text **at all**, and instead persists a
summary whose every value is a `bool`, `int`, `float`, or `None`.

The execution authorization layer (operationally proven by the real Claude
Code 2.1.223 probe) was **not** modified. `exact_bash_guard.py` is untouched,
and `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`.

## 2. Starting HEAD

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Unchanged; no commits made.

## 3. Persistence-flow audit

Traced by searching actual consumers, not names supplied in the task.

**Source of provider-controlled data:** `_execute()` returns
`outcome.stdout_bytes` / `stderr_bytes` (already bounded by `_StreamReader`).

| # | Path | Durable? | Protection after this patch |
|---|---|---|---|
| 1 | `ClaudeCodeCLIResult.stdout_excerpt` / `.stderr_excerpt` | yes (serialized downstream) | fixed placeholder when authorized |
| 2 | `build_evidence_manifest()` keys `stdout_excerpt`/`stderr_excerpt` | yes | inherits (1) |
| 3 | `render_evidence_manifest_json()` | yes | inherits (1) |
| 4 | `write_evidence()` → `CLAUDE_CODE_CLI_SPIKE_RESULT.json` | **on disk** | inherits (1) |
| 5 | `run_task.persist_evidence()` → `result.json` | **on disk** | inherits (1); asserted end-to-end |
| 6 | `run_task` `receipt.json` | on disk | never carried excerpts (verified); unchanged |
| 7 | `run_repair.py:788` → manifest | **on disk** | inherits (1) |
| 8 | `run_loop.extract_usage_from_implementation_result()` | reads (5) | now prefers typed safe fields |
| 9 | `run_loop` `loop-result.json` / `loop-receipt.json` | on disk | verified: carries only IDs/counts/aggregated usage — never child result dicts |
| 10 | `scripts/run_claude_code_cli_spike.py` | console + `write_evidence` | never passes `authorized_commands`; unchanged path |
| 11 | `ClaudeCodeCLIResult.error_message` | yes | verified provider-free: every construction site is a fixed string, an `int` exit code, workspace paths, or `exc.__class__.__name__` + `errno` |
| 12 | `ClaudeCodeCLIResult.sanitized_argv` | yes | TOMTIT-constructed argv + fingerprint placeholders; no provider data |
| 13 | `run_review.py` `stdout_excerpt` fields | on disk | **out of scope** — these capture *local check-command* output (`npm run lint`), not provider output; different source, unchanged |

**Concrete confirmation the leak was real:** pre-existing on-disk artifacts
in `.tomtit/dogfood/` (`runs/*/result.json`, `loops/*/children/*/result.json`)
already contain raw `permission_denials[].tool_input.command` values such as
`npm run lint` and multi-command `;`-joined shell strings inside
`stdout_excerpt`. Those runs predate the guard (`authorized_command_count=0`),
so they were never covered by the old redaction pass — but they are direct
evidence that path 1→5 persists raw provider command text verbatim.

## 4. M-1 root cause

`_redact_provider_json_bash_commands` parsed the JSON and replaced any dict
key **literally named `"command"`**. This is a denylist over an open schema.
Provider output is not a stable contract: `result`, `error`, `message`,
`reason`, `hookSpecificOutput.permissionDecisionReason`, nested
`content[].text`, alternate spellings (`cmd`, `Command`), and any key a
future Claude version introduces all bypassed it. The code decided what to
*remove* from attacker-controlled text and kept the remainder — so anything
it failed to anticipate was persisted.

## 5. M-1 correction

Inverted the decision. `_summarize_provider_output()` derives a
`_ProviderOutputSummary` from the raw text; `run()` then replaces the
excerpts with `_PROVIDER_OUTPUT_WITHHELD_PLACEHOLDER` (or `""` for an empty
stream, so an empty output does not gain phantom content).

The invariant is structural, not enumerative:

- `_ProviderOutputSummary` **has no `str` field.** Its fields are `int`,
  `bool`, `bool | None`, `int | None`, `int | float | None`. A command
  string cannot be a float, so unknown keys, unknown nesting, and future
  formats are safe without anyone having to anticipate them.
- Values come from `_SAFE_PROVIDER_NUMERIC_PATHS`, an **allowlist of what to
  derive**, not a denylist of what to strip. Anything not named is never
  read.
- `_extract_safe_number()` type-checks at extraction, so a provider
  returning `{"total_cost_usd": "<command text>"}` yields `None` rather than
  smuggling text through a numeric-looking field. `bool` is excluded
  explicitly (it subclasses `int` in Python) so a flag cannot become a count.

The old `_redact_provider_json_bash_commands` / `_redact_command_values` /
`_REDACTED_PROVIDER_COMMAND_PLACEHOLDER` were **deleted**, not left as dead
code — verified: `grep` finds zero remaining references anywhere in
`src`, `tests`, `scripts`, `.tomtit`. Leaving them would have implied a
protection that is no longer the mechanism.

## 6. M-2 root cause

`_StreamReader` truncates at `max_stdout_bytes` (default 65536) **before**
redaction ran. A truncated `--output-format json` payload is invalid JSON, so
`json.loads` raised and the redactor's `except ValueError: return text`
branch returned the raw prefix unchanged — every command in the surviving
64 KB landed in `stdout_excerpt` and on disk. Confidentiality depended on
parser success, which is exactly backwards: the case with the *most*
provider output to protect was the case that got no protection.

One of the pre-existing tests
(`test_redact_provider_json_bash_commands_non_json_text_unchanged`) asserted
`redact(non_json) == non_json` — encoding the M-2 defect as intended
behavior. It was removed along with the helper.

## 7. M-2 correction

Parsing is now used **only to enrich** the summary, never to decide whether
raw text is safe to keep. A parse failure yields `stdout_json_parsed=False`
and leaves the derived numbers `None`, while byte counts, truncation flags,
and the full-stream SHA-256 remain accurate. Degradation is toward *less
metadata*, never toward *more raw text*. Because the excerpt is replaced
unconditionally when authorization is active, truncation is simply
irrelevant to confidentiality — there is no code path where a parser outcome
can reintroduce provider text.

## 8. Safe persistence representation

Added to `ClaudeCodeCLIResult` and `build_evidence_manifest()`:

| Field | Type | Meaning |
|---|---|---|
| `provider_output_withheld` | `bool` | whether the boundary was applied |
| `provider_stdout_bytes` / `provider_stderr_bytes` | `int \| None` | retained-stream byte counts |
| `provider_stdout_json_parsed` | `bool \| None` | whether stdout parsed as a JSON object |
| `provider_is_error` | `bool \| None` | only if genuinely a bool |
| `provider_permission_denial_count` | `int \| None` | `len()` of the list, never its contents |
| `provider_num_turns` | `int \| float \| None` | |
| `provider_total_cost_usd` | `int \| float \| None` | |
| `provider_input_tokens` / `provider_output_tokens` | `int \| float \| None` | |

Pre-existing safe fields retained: `stdout_sha256`, `stderr_sha256`,
`stdout_truncated`, `stderr_truncated`, `exit_code`, `duration_ms`, status
and error *categories* (fixed vocabulary).

`test_m2_result_provider_fields_are_never_string_typed` asserts none of the
`provider_*` fields (other than the boolean flag) is string-typed, so the
guarantee is checkable by annotation rather than by auditing call sites.

## 9. Runtime-vs-persistence boundary

Raw provider text exists transiently in `run()`'s local `stdout_text` /
`stderr_text` — it is what the summary is derived from — and is discarded
when those locals are rebound to the placeholder. **No field carrying it is
placed on the result**, so no serialization path can reach it: the boundary
is enforced by what the result object is allowed to hold, not by every
consumer remembering to sanitize.

One runtime consumer genuinely needed the text: `run_loop`'s usage/cost
extraction parsed `stdout_excerpt` back out of the persisted `result.json`.
Rather than lose telemetry for exactly the runs that carry authorized
commands, `extract_usage_from_implementation_result` now prefers the typed
safe fields when `provider_output_withheld` is `True`, with the same
numeric type-checking, and keeps the legacy excerpt-parsing path unchanged
otherwise. This is the only dogfood change, and it is justified by audit
path 8.

## 10. Unknown-provider-shape behavior

Unknown keys are never read, so they cannot leak. Proven for
`a_field_invented_in_2027`, deeply nested list/dict combinations, alternate
`cmd`/`Command` spellings, and all six previously-leaking known shapes —
each asserted against the **serialized manifest and the on-disk evidence
files**, not against a helper's return value.

## 11. Truncated / non-JSON behavior

All ten M-2 boundary cases (below/at/one-over the limit, command before /
crossing / entirely within the retained prefix, closing syntax truncated
away, very large prose, very large `permission_denials`, non-JSON larger
than the limit) persist the fixed placeholder and no raw text.
`test_m2_truncated_json_falls_back_to_safe_representation` asserts the exact
defect condition — `stdout_truncated is True`, `provider_stdout_json_parsed
is False` — together with a clean evidence surface and a retained
full-stream hash.

## 12. No-command compatibility

With `authorized_commands` empty: no withholding, no summary, `stdout_excerpt`
passes through exactly as before (only the pre-existing env-secret and HOME
redaction applies), all `provider_*` fields are `None`/`False`, and
`run_loop` uses the unchanged legacy parsing path. Asserted by
`test_no_authorized_commands_provider_output_behavior_unchanged`,
`..._existing_secret_redaction_still_works`,
`..._home_redaction_still_works`, and
`test_dogfood_loop_usage_extraction_legacy_path_unchanged`.

## 13. Execution-layer regression check

`exact_bash_guard.py` not modified (`git status` shows it unchanged since the
prior task). All 70 guard tests, all 25 process-hardening tests, and the
H-1/H-2 suites pass unmodified. Exact argv semantics, canonical validator,
fingerprint algorithm, PreToolUse PASS/DENY, `-E -s`, guard-file preflight,
native `--allowedTools` layering, and `run_in_background` rejection are all
untouched. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False` at
`claude_code_cli.py:366`, not flipped, not renamed.

## 14. Tests and exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests, unmodified)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (390 tests; was 351, +39)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=0   (716 tests; was 677, +39)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

Net test delta: +39 (43 new provider-output/persistence tests, 4 removed
redaction-helper tests). All 20 required M-1 cases, all 10 required M-2
boundary cases, the persistence-path end-to-end tests, and the regression set
are covered. Every M-1/M-2 assertion runs `render_evidence_manifest_json`
**and** `write_evidence`, then greps the actual on-disk files — not a
redaction helper's return value.

## 15. Known flakes

**None observed in this task.** The process-cleanup timing flake recorded in
earlier reports (`test_normal_exit_with_surviving_descendant_kills_descendant`
/ `test_delayed_descendant_write_is_prevented_before_final_snapshot`) did not
reproduce: `tests/adapters` and the full suite each passed clean on their
first attempt, both before and after the change. No result was discarded and
no rerun was needed.

One self-caught issue during implementation, recorded for honesty: my first
edit accidentally duplicated the `ClaudeCodeCLIStatus` enum header, which
`mypy` caught immediately (5 errors); fixed before any test run. A second
issue — `ClaudeCodeCLIResult` used in test annotations without being
imported — passed tests silently (deferred annotations) but was caught by
`ruff` F821 and fixed.

## 16. Scope / non-goals

Modified: `src/tomtit/adapters/claude_code_cli.py`,
`tests/adapters/test_claude_code_cli.py`, and
`.tomtit/dogfood/run_loop.py` (audit-justified consumer fix, path 8 —
`.tomtit/` is gitignored so it does not appear in `git status`).

Not modified: `exact_bash_guard.py`, `tests/adapters/test_exact_bash_guard.py`,
Runtime state model, Coordinator architecture, evaluation contracts,
governance, memory, routing, personal-web. `run_review.py`'s own
`stdout_excerpt` fields were deliberately left alone — they capture local
check-command output, not provider output, and changing them would be
scope creep with no security benefit for this finding.

Explicit non-goal: this patch does not enable the capability, and does not
address provider output for runs *without* authorized commands (unchanged by
design, per the no-command compatibility requirement).

## 17. Final Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git status --short (tracked):
  M src/tomtit/adapters/__init__.py        (predates this task, untouched by it)
  M src/tomtit/adapters/claude_code_cli.py
  M tests/adapters/test_claude_code_cli.py
  ?? src/tomtit/adapters/exact_bash_guard.py    (unchanged by this task)
  ?? tests/adapters/test_exact_bash_guard.py    (unchanged by this task)
  ?? (report .md files)
git diff --cached -> (empty; nothing staged)
git diff --check  -> EXIT=0
```

No commit, stage, reset, stash, checkout, push, merge, or deploy. No real
Claude or Codex invocation. No TOMTIT self-run. No `bypassPermissions`. No
personal-web rerun. Enable constant not flipped.

## 18. Next gate

Owner-coordinated fresh evidence-safety review of M-1/M-2 (§3–§11),
independently reproducing the persistence audit rather than relying on this
report. If that review passes, the remaining enablement question is a policy
decision — all four previously recorded blockers (H-1, H-2, M-1, M-2) would
then be closed, with the execution layer already operationally proven.

Worth flagging for that review: the **pre-existing** `.tomtit/dogfood/`
artifacts described in §3 still contain raw command text on disk from runs
that predate the guard. This patch prevents new leakage; it does not
retroactively sanitize historical evidence. Whether those artifacts should be
scrubbed or left as an immutable record is an Owner decision outside this
task's scope.

---

STATUS=READY_FOR_PROVIDER_OUTPUT_EVIDENCE_SAFETY_REVIEW
M1_PROVIDER_OUTPUT_REDACTION=CLAIMED_RESOLVED_PENDING_REVIEW
M2_TRUNCATION_BEFORE_REDACTION=CLAIMED_RESOLVED_PENDING_REVIEW
UNKNOWN_PROVIDER_FIELDS_SAFE=CLAIMED_YES_PENDING_REVIEW
NON_JSON_PROVIDER_OUTPUT_SAFE=CLAIMED_YES_PENDING_REVIEW
TRUNCATED_PROVIDER_OUTPUT_SAFE=CLAIMED_YES_PENDING_REVIEW
RAW_PROVIDER_BASH_COMMAND_PERSISTED=NO
RAW_PROVIDER_SECRET_PERSISTED=NO
NO_COMMAND_COMPATIBILITY=PRESERVED
EXACT_ARGV_OPERATIONALLY_PROVEN=YES
EXECUTION_AUTHORIZATION_LAYER=PRESERVED
ENABLE_CONSTANT=False
EXACT_AUTHORIZATION_ENABLED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_FRESH_EVIDENCE_SAFETY_REVIEW
