# Historical Provider-Evidence Sanitization Report

One-time local sanitization of pre-fix dogfood evidence artifacts under
`.tomtit/dogfood/`. This report deliberately contains **no** raw Bash command
text — only paths, hashes, identifiers, counts, and fixed categories — so it
preserves proof that the historical defect existed without retaining the
leaked provider text itself.

## 1. Status

Complete. All historical provider-output excerpts containing raw provider
Bash command text have been replaced with
`[HISTORICAL_PROVIDER_OUTPUT_REDACTED]`. No dogfood run was deleted; all
audit metadata was preserved.

Starting HEAD: `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no
commits).

## 2. Independent identification

Detection was performed independently rather than by trusting the review's
count. Method: parse every `.tomtit/dogfood/**/*.json`, walk to every
`stdout_excerpt` / `stderr_excerpt` field at any depth, attempt to parse each
excerpt's *content* as JSON, and count recoverable `*.command` keys inside it.

**Result: exactly 3 JSON files affected — matching the review's finding.**

| # | Relative path | Excerpt field | Recoverable commands |
|---|---|---|---|
| 1 | `loops/360e74f8-07cb-4e6d-96ef-cdc499b64394/children/360e74f8-07cb-4e6d-96ef-cdc499b64394-impl-1/result.json` | `.stdout_excerpt` | 11 |
| 2 | `runs/5badc6b0-30b7-4af8-82b3-8766c9f3e520/result.json` | `.stdout_excerpt` | 5 |
| 3 | `runs/c04cda2d-4e40-431a-811a-daf9bf403d19/result.json` | `.stdout_excerpt` | 5 |

Total: 21 raw provider command occurrences across 13 distinct command
strings (values withheld from this report).

### Additional finding beyond the review's JSON-only scope

Extending the sweep to **non-JSON** files found the forwarding vector the
task description itself names — `run_review.extract_adapter_result_fields()`
copying `stdout_excerpt` into a reviewing-model prompt — had already
materialized on disk:

| # | Relative path | Affected line | Nature |
|---|---|---|---|
| 4 | `reviews/c70a278e-32dc-4618-b9d3-7beb20da5437/review-prompt.txt` | 1 line (indent 2, carries run_review's `(truncated for review prompt)` marker) | forwarded provider excerpt |
| 5 | `reviews/fb63a946-80bf-4937-872b-d3b1c241f4d3/review-prompt.txt` | 1 line (same shape) | forwarded provider excerpt |

Sanitizing only the 3 JSON files would have left 4 distinctive provider
command occurrences on disk and made
`RAW_PROVIDER_COMMAND_OCCURRENCES_AFTER=0` unreachable. Both files were
therefore included.

**Total affected files sanitized: 5** (3 JSON + 2 forwarded-prompt TXT).

### Deliberately NOT sanitized (verified not a provider leak)

- `reviews/*/checks.json` and `reviews/*/review-receipt.json`
  `stdout_excerpt` fields — these capture **local check-command output**
  (lint/build console output), not provider-controlled Bash command text.
  Different source; preserved.
- `run_loop.py`, `run_repair.py`, `run_review.py`, their `__pycache__`
  bytecode, `LOOP_RUNNER.md`, `REPAIR_RUNNER.md`, `REVIEW_RUNNER.md`,
  `tasks/*.md` — these contain one short, generic command string that is
  **TOMTIT's own documented example check command**
  (`--check-command "npm run lint"` in usage docs), not provider-echoed
  output. Distinguished by an evidence-only test: a command appearing
  outside the `runs/`, `loops/`, `reviews/` evidence directories is
  documentation, not a leak. 1 of the 13 command strings was generic by
  this test; the other 12 were evidence-only (distinctive).

## 3. Pre-sanitization fingerprints and preserved metadata

| Relative path | SHA-256 (before) | Bytes |
|---|---|---|
| `loops/360e74f8-.../children/360e74f8-...-impl-1/result.json` | `e1537a04b021777ab39943631dfd45d821f61dddad428a0d2241582dc4435b69` | 12525 |
| `runs/5badc6b0-30b7-4af8-82b3-8766c9f3e520/result.json` | `ba536127b7b41de48a57fffe1c96abbf8b6e420516dacfce07201b9a1a002149` | 16634 |
| `runs/c04cda2d-4e40-431a-811a-daf9bf403d19/result.json` | `2eef268eb4227adcf699fd40848e69caa87ca5f4423881eaf143b3a9cdfb6edd` | 6873 |
| `reviews/c70a278e-32dc-4618-b9d3-7beb20da5437/review-prompt.txt` | `305190cdd960ae738bedfc5da282f26e5ab02c5d295d9510d0d106e303c7a763` | 11588 |
| `reviews/fb63a946-80bf-4937-872b-d3b1c241f4d3/review-prompt.txt` | `7ce2a83b56aa1fcf3cfd07b21628da4f8219271ea9bfe0179e32a267f4ef3e28` | 32885 |

Run identifiers recorded before modification (all preserved unchanged):

| Relative path | run_id | task_id | attempt | status |
|---|---|---|---|---|
| `loops/.../impl-1/result.json` | `360e74f8-07cb-4e6d-96ef-cdc499b64394-impl-1` | `360e74f8-07cb-4e6d-96ef-cdc499b64394-impl-1` | 1 | `FAILED` |
| `runs/5badc6b0-.../result.json` | `5badc6b0-30b7-4af8-82b3-8766c9f3e520` | `manual-dogfood-5badc6b0-30b7-4af8-82b3-8766c9f3e520` | 1 | `SUCCEEDED` |
| `runs/c04cda2d-.../result.json` | `c04cda2d-4e40-431a-811a-daf9bf403d19` | `manual-dogfood-c04cda2d-4e40-431a-811a-daf9bf403d19` | 1 | `SUCCEEDED` |

## 4. Sanitization method

**JSON files.** Each file was first verified to round-trip byte-identically
under its original writer format (`json.dumps(..., indent=2, sort_keys=True,
ensure_ascii=False) + "\n"`), proving that rewriting it changes only the
value deliberately replaced. Only excerpt fields from which provider commands
were actually recoverable were replaced; `stderr_excerpt` was empty (`""`) in
all three and was left untouched.

**TXT files.** A single line per file was replaced — identified structurally
as indent-2 + `"stdout_excerpt": ` + carrying run_review's
`(truncated for review prompt)` marker, which uniquely distinguishes the
forwarded *provider* excerpt from the indent-4 *local check-command*
excerpts in the same file. Key, indentation, trailing comma, and line ending
were preserved.

No backup copies were made anywhere: a backup would have retained exactly the
text being removed, defeating the purpose. Modification was in place.

## 5. Post-sanitization fingerprints

| Relative path | SHA-256 (after) | Bytes | Changed |
|---|---|---|---|
| `loops/360e74f8-.../children/360e74f8-...-impl-1/result.json` | `785123588d42546f25586fac674a13cc2b1e50726f28766f568b6702200ce712` | 7284 | yes |
| `runs/5badc6b0-30b7-4af8-82b3-8766c9f3e520/result.json` | `5e8bd6fd1782cf9abee160ed390b25aecbdc713b64dcb85e1098312aecf7dd31` | 14093 | yes |
| `runs/c04cda2d-4e40-431a-811a-daf9bf403d19/result.json` | `809863b58ebb221ae96c6c4e6bed61b686462b646bba7c12ed2ff20ed85f292c` | 4027 | yes |
| `reviews/c70a278e-32dc-4618-b9d3-7beb20da5437/review-prompt.txt` | `14d6009c53a73926b7ad44edd8bb2edb737e5771e77602682430816568aaa460` | 9378 | yes |
| `reviews/fb63a946-80bf-4937-872b-d3b1c241f4d3/review-prompt.txt` | `26324c777348bc313e1befa43bc7c87bb3f48ea8726140dbace48cf030438526` | 30679 | yes |

## 6. Preserved audit metadata

Verified programmatically unchanged in all 3 JSON files: `run_id`, `task_id`,
`attempt`, `status`, `success`, `exit_code`, `started_at`, `completed_at`,
`stdout_sha256`, `stderr_sha256`.

`stdout_sha256` is especially significant: it is the hash of the **original
full provider stream**, so the sanitized artifact still proves what the
provider actually produced without retaining it — the audit value the Owner
asked to preserve. All patches, receipts, loop results, task contracts,
evaluation records, and review verdicts were left untouched. No run was
deleted.

**Accepted trade-off, recorded honestly:** replacing the whole excerpt value
(the instructed "preferred replacement") also discards the non-sensitive
usage/cost/session telemetry that happened to live inside the same
provider-controlled blob. No new fields were invented to compensate —
fabricating fields in historical evidence would compromise its integrity as a
record. The byte-count reduction in §5 reflects this.

## 7. Post-sanitization verification

Every remaining excerpt across all 15 dogfood JSON files was enumerated and
classified. Every one is now `EMPTY`, `REDACTED-PLACEHOLDER`, or
`LOCAL-CHECK-OUTPUT (plain text)`. **None parses as provider-shaped JSON.**

Structural signature sweep across all non-bytecode dogfood files for
`permission_denials` and `tool_use_id`: **0 files**.

**run_review forwarding check (step 7).** `run_review.extract_adapter_result_fields()`
was imported and executed against each sanitized `result.json`. Each returned
`stdout_excerpt = '[HISTORICAL_PROVIDER_OUTPUT_REDACTED]'` with **0**
recoverable commands — a reviewing model can no longer be handed historical
provider command text.

All 3 sanitized JSON files remain valid JSON and parseable by the runners.
Dogfood suites re-run after sanitization: `test_run_review.py` EXIT=0 (45
tests), `test_run_loop.py` EXIT=0 (52 tests).

### Metrics

```
HISTORICAL_JSON_FILES_SCANNED            = 15
AFFECTED_BEFORE                          = 3     (JSON, matches independent review)
AFFECTED_FILES_SANITIZED_TOTAL           = 5     (3 JSON + 2 forwarded-prompt TXT)
AFFECTED_AFTER                           = 0
RAW_PROVIDER_COMMAND_OCCURRENCES_AFTER   = 0
UNSANITIZED_FORWARDED_TXT_EXCERPTS_AFTER = 0
PROVIDER_DENIAL_SIGNATURES_AFTER         = 0
```

### Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git status --short (tracked):
  M src/tomtit/adapters/__init__.py         (predates this task, untouched by it)
  M src/tomtit/adapters/claude_code_cli.py  (predates this task, untouched by it)
  M tests/adapters/test_claude_code_cli.py  (predates this task, untouched by it)
  ?? src/tomtit/adapters/exact_bash_guard.py     (untouched by this task)
  ?? tests/adapters/test_exact_bash_guard.py     (untouched by this task)
  ?? (report .md files, including this one)
git diff --check -> EXIT=0
git diff --cached -> (empty; nothing staged)
```

All sanitized artifacts live under `.tomtit/`, which is gitignored, so they
do not appear in `git status`. No tracked implementation file was modified by
this task. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False` at
`claude_code_cli.py:366`, unchanged.

## 8. Residual risk

Sanitization covers this working copy only. As the task noted, ordinary
filesystem backups, Time Machine snapshots, editor undo history, or shell
scratch copies taken before now may still retain the original bytes. That is
outside this repository's control and is recorded here rather than claimed
resolved. The pre-sanitization SHA-256 values in §3 allow any such copy to be
positively identified.

## 9. Constraint compliance

No dogfood run deleted. No TOMTIT implementation modified. No real Claude or
Codex invocation. No TOMTIT self-run. Enable constant not flipped. No
personal-web rerun. No commit, stage, reset, stash, checkout, push, merge, or
deploy. No raw command text appears anywhere in this report.

---

STATUS=HISTORICAL_PROVIDER_EVIDENCE_SANITIZED
HISTORICAL_LEAK_CONFIRMED=YES
HISTORICAL_RAW_PROVIDER_TEXT_RETAINED=NO
AUDIT_METADATA_PRESERVED=YES
TRACKED_IMPLEMENTATION_MODIFIED=NO
ENABLE_CONSTANT=False
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_AUTHORIZES_NARROW_EXACT_AUTHORIZATION_ENABLEMENT_PATCH
