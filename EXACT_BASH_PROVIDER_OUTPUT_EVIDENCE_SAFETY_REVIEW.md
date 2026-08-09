# Exact-Bash Provider-Output Evidence Safety (M-1 / M-2) — Independent Security Review

**Reviewer:** fresh Claude Code Opus session, Owner-coordinated.
**HEAD at review:** `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no git write operation performed).
**Scope:** M-1 and M-2 only. The execution-authorization layer was checked for regression, not re-litigated.
**Method:** source tracing plus independently authored probes that drive the **real `ClaudeCodeCLIAdapter.run()`** and the **real dogfood `run_task.persist_evidence()`** path, then grep the actual on-disk `result.json` / `receipt.json` / `diff.patch` bytes. My probes use synthetic markers **deliberately different** from the implementer's, so a marker-specific special case could not pass them. The fix report was read for claims but not treated as evidence.

**Disclosure on method:** the enable constant gates this code path, so exercising it required patching `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` to `True` **in memory only, inside my probe process** — the same mechanism the existing suite uses. The on-disk constant was asserted `False` before patching and is still `False`. No implementation file was modified.

## 1. Verdict

**APPROVE_WITH_NON_BLOCKING_FINDINGS.**

M-1 and M-2 are independently verified resolved. The fix is structural rather
than enumerative, and that structure holds: across 40 adversarial cases —
including six provider shapes I invented that appear nowhere in the
implementation or its tests — **no raw provider command text or synthetic secret
reached any durable surface**, including real on-disk dogfood evidence.

Two non-blocking findings (N-1, N-2) and one Owner decision item (historical
evidence) are recorded. Neither finding is a defect in this patch; N-1 is a
pre-existing bounded channel that the patch neither introduces nor widens.

## 2. Review area 1 — persistence-flow audit

Traced from `_execute()` outward by searching actual consumers repo-wide, not
from the report's list. **Only two `ClaudeCodeCLIResult(...)` construction sites
exist** (`claude_code_cli.py:1909` and `:2004`), which is what makes the boundary
auditable at all: the withholding at L1877-1883 sits above the first, and the
second (`_early_result`) hardcodes `stdout_excerpt=""` / `stderr_excerpt=""`.

| # | Path | Durable? | Classification | Post-fix state |
|---|---|---|---|---|
| 1 | `ClaudeCodeCLIResult.stdout_excerpt` / `.stderr_excerpt` | yes | PROVIDER_CONTROLLED | fixed placeholder when authorized |
| 2 | `build_evidence_manifest()` (L1255-1303) | yes | inherits (1) | verified clean |
| 3 | `render_evidence_manifest_json()` | yes | inherits (1) | verified clean |
| 4 | `write_evidence()` → `CLAUDE_CODE_CLI_SPIKE_RESULT.json` + `.patch` | **on disk** | inherits (1) | verified clean on disk |
| 5 | `run_task.persist_evidence()` → `result.json` | **on disk** | inherits (1) | verified clean on disk (my probe drives this directly) |
| 6 | `run_task.persist_evidence()` → `receipt.json` | **on disk** | TOMTIT_CONTROLLED | carries no excerpt; source-verified (L190-208) |
| 7 | `run_repair.py` → manifest | on disk | inherits (1) | inherits |
| 8 | `run_loop.extract_usage_from_implementation_result()` | reads (5) | TOMTIT_CONTROLLED after fix | typed-field path; see §6 |
| 9 | `run_loop` `loop-result.json` / `loop-receipt.json` | on disk | TOMTIT_CONTROLLED | aggregated scalars/IDs only; no child result dicts |
| 10 | `scripts/run_claude_code_cli_spike.py` | console + `write_evidence` | PROVIDER_CONTROLLED (transient) | never passes `authorized_commands`; unchanged |
| 11 | `ClaudeCodeCLIResult.error_message` | yes | TOMTIT_CONTROLLED **with one exception** | see N-1 |
| 12 | `ClaudeCodeCLIResult.sanitized_argv` | yes | TOMTIT_CONTROLLED | TOMTIT argv + fingerprint placeholders |
| 13 | `run_review.py` `CheckResult.stdout_excerpt` | on disk | **LOCAL_CHECK_OUTPUT** | source-traced, not provider — see below |
| 14 | `run_review.extract_adapter_result_fields()` | prompt → model | inherits (1) | placeholder when authorized; **relevant to historical evidence, §9** |
| 15 | raw `stdout_text` / `stderr_text` locals in `run()` | no | TRANSIENT_ONLY | rebound to placeholder; never stored |

**Path 13 independently confirmed, not assumed.** `run_review.py:468` populates
`stdout_excerpt` from `completed.stdout` where `completed = subprocess.run(argv,
...)` inside `run_check_command()` (L446-458), and `argv` comes from
`validate_check_command_text()` — a locally configured check command such as
`npm run lint`. This is local check output, a different source from adapter
provider output. The report's classification is correct.

**Path 14 is one the report does not list.** `_ADAPTER_RESULT_PROMPT_FIELDS`
(`run_review.py:760-771`) includes `stdout_excerpt`, and
`extract_adapter_result_fields()` copies it (truncated to 2000 chars) into the
review prompt sent to a reviewing model. With the fix active this is the fixed
placeholder, so it is safe going forward — but it is an **onward-transmission
path**, which matters for the historical artifacts in §9. Not a defect in this
patch; recorded because the audit was required to be independent of the report's
list.

## 3. Review area 2 — structural persistence boundary

| # | Requirement | Result |
|---|---|---|
| 1 | No raw provider text on the result when authorization is active | PASS — L1877-1883 rebinds both locals before the single construction site at L1909 |
| 2 | No string-bearing channel on persisted provider-derived fields | PASS — `_ProviderOutputSummary` (L796-815) declares `int`, `bool`, `bool \| None`, `int \| None`, `int \| float \| None`. **No `str` field exists.** The nine `provider_*` result fields (L1004-1013) carry the same types |
| 3 | Unknown provider keys ignored | PASS — only `_SAFE_PROVIDER_NUMERIC_PATHS`, `is_error`, and `permission_denials` are ever read; everything else is never touched |
| 4 | Unknown nested structures ignored | PASS — `_extract_safe_number` walks only the four allowlisted paths and bails on any non-`Mapping` en route |
| 5 | Numeric extraction allowlisted | PASS — `_SAFE_PROVIDER_NUMERIC_PATHS` is an allowlist of what to *derive*, not a denylist of what to *strip*. This is the inversion that makes the fix structural |
| 6 | Numeric extraction type-checks | PASS — `isinstance(current, (int, float))` or `None` |
| 7 | `bool` not accepted as numeric | PASS — explicitly rejected before the `int` check (`bool` subclasses `int`). Verified empirically: `total_cost_usd: true` → `None` |
| 8 | Strings cannot enter typed fields by coercion | PASS — no `int()`/`float()`/`str()` coercion anywhere; the value is returned as-is or `None`. Verified: `{"total_cost_usd": "<command>"}` → `None` |
| 9 | Provider text cannot enter `error_message` | PASS **for stdout/stderr** — every `_determine_status` message is a fixed string, an `int` exit code, or a path list; start-failure messages are `exc.__class__.__name__` + `errno`. One bounded exception: see **N-1** |
| 10 | Serialized manifest preserves the invariant | PASS — `build_evidence_manifest` copies only the typed fields; verified against the serialized JSON and on-disk files, not the dict alone |

The old `_redact_provider_json_bash_commands` / `_redact_command_values` /
`_REDACTED_PROVIDER_COMMAND_PLACEHOLDER` are **fully deleted** — `grep` across
`src`, `tests`, `scripts`, `.tomtit` returns zero references. Removing them
rather than leaving them dead is correct: a retained helper would imply a
protection that is no longer the mechanism.

## 4. Review area 3 — independent M-1 reproduction

Every case: real `run()` with `authorized_commands=(("python3","writer.py","exact.txt"),)`,
a fake `claude` emitting the payload, then `run_task.persist_evidence()` writing
real `result.json` / `receipt.json` / `diff.patch`, then a byte-level grep of
those files for my markers (`curl http://evil.example/rev-shell-9f3a2b`,
`REVIEWER-SECRET-e7c1d4a9-DO-NOT-PERSIST`, `rev-shell-9f3a2b`, `evil.example`).
Surfaces checked per case: `stdout_excerpt`, `stderr_excerpt`, `error_message`,
`sanitized_argv`, manifest dict, serialized manifest JSON, **full dataclass
`repr()`**, and every on-disk file.

All 24 M-1 cases clean:

| Injection channel | Result |
|---|---|
| `permission_denials[].tool_input.command` | clean |
| `result` / `error` / `message` / `reason` | clean |
| `hookSpecificOutput.permissionDecisionReason` | clean |
| nested `content[].text` | clean |
| `cmd` / `Command` | clean |
| arbitrary unknown key (`totally_new_key_2031`) | clean |
| deeply nested list/dict (5 levels, mixed) | clean |
| multiple commands incl. wrapper + compound forms | clean |
| Unicode (`café … 日本語 🚀 …`) | clean |
| **secret in usage-shaped field** (`usage.input_tokens` = secret) | clean, extracted as `None` |
| **secret in cost field** (`total_cost_usd` = command) | clean, extracted as `None` |
| **`toolUseResult.{stdout,stderr}`** (shape I invented) | clean |
| **top-level JSON array / string / bare number** | clean |
| stdout prose / stderr prose / both streams | clean |
| mixed JSON + prose | clean |
| malformed JSON | clean |

`provider_output_withheld is True` in every authorized case. Empty stream stays
`""` rather than gaining a phantom placeholder (verified in the stderr-only case).

```
RAW_PROVIDER_COMMAND_ON_DISK=NO
RAW_PROVIDER_SECRET_ON_DISK=NO
```

## 5. Review area 4 — independent M-2 reproduction

Run against the **actual configured** stream limit (`max_stdout_bytes=4096` in my
harness, so the boundary is exercised rather than approximated). All 12 cases
persist the fixed placeholder with no raw text:

| Case | `stdout_truncated` | `json_parsed` | Evidence |
|---|---|---|---|
| below limit | False | True | clean |
| exactly at limit | False | True | clean |
| one byte over | True | False | clean |
| truncated mid-string | True | False | clean |
| truncated mid-command | True | False | clean |
| command fully inside retained prefix | True | False | clean |
| closing JSON syntax truncated away | True | False | clean |
| very large result prose | True | False | clean |
| very large `permission_denials` | True | False | clean |
| non-JSON larger than limit | True | False | clean |
| malformed JSON larger than limit | True | False | clean |
| mixed JSON/prose larger than limit | True | False | clean |

The rows that matter are "command fully inside retained prefix" and "closing
JSON syntax truncated away" — these are precisely the conditions under which the
old code returned the raw prefix verbatim. Parse failure now reduces metadata
(`provider_stdout_json_parsed=False`, derived numbers `None`) while byte counts
and the full-stream SHA-256 stay accurate.

**Confidentiality no longer has any dependency on parser success**, because the
excerpt is replaced *unconditionally* when authorization is active — the
replacement happens before parsing is even consulted, and parsing feeds only the
summary.

```
TRUNCATION_CONFIDENTIALITY_DEPENDENCY=NONE
```

## 6. Review area 5 — telemetry regression

| Check | Result |
|---|---|
| Valid telemetry preserved when output withheld | PASS — `num_turns=7`, `total_cost_usd=0.4231`, `input_tokens=1234`, `output_tokens=567`, `is_error=False`, `permission_denial_count=1` |
| `run_loop` typed extraction | PASS — `{'cost_usd': 0.4231, 'input_tokens': 1234, 'output_tokens': 567}` |
| Invalid types do not become valid telemetry | PASS — string `num_turns`, `bool` cost, list/dict token values, string `is_error`, dict `permission_denials` all → `None`, never a coerced value |
| `bool` rejected as a number | PASS — `total_cost_usd: true` → `None` |
| Precedence unambiguous | PASS — with `provider_output_withheld=True` **and** a legacy excerpt containing conflicting numbers (999/888/777), extraction returns the typed values (0.4231/1234/567), never the excerpt's |
| No double counting | PASS — the two paths are mutually exclusive via a single `if result.get("provider_output_withheld") is True:` early return; `aggregate_usage` sees exactly one contribution per child |
| Legacy path unchanged | PASS — with `provider_output_withheld=False`, extraction uses the excerpt (999/888/777) exactly as before |

Note `provider_num_turns` and `provider_is_error` are persisted but not consumed
by `run_loop`; that is additive metadata, not a gap.

## 7. Review area 6 — no-command compatibility

Independently verified with `authorized_commands=()`:

| Check | Result |
|---|---|
| Provider output behavior unchanged | PASS — `stdout_excerpt` is byte-identical to the emitted payload |
| Withholding not accidentally enabled | PASS — `provider_output_withheld is False` |
| All `provider_*` fields inert | PASS — all nine are `None` |
| Env-secret redaction retained | PASS — `GITHUB_TOKEN=<secret>` echoed by the child persists as `token=[REDACTED]` |
| HOME redaction retained | PASS — persists as `home=$HOME`, raw HOME absent |
| Legacy telemetry parsing functional | PASS — `{'cost_usd': 1.5, 'input_tokens': 10, 'output_tokens': 20}` |

No global behavior change is hidden behind this patch. The withholding is gated
strictly on `if request.authorized_commands:`.

## 8. Review area 7 — execution-layer regression: PRESERVED

| Property | Evidence |
|---|---|
| `exact_bash_guard.py` | SHA-256 `b35053af5660…b448e15` — **byte-identical** to the file I reviewed in both prior reviews |
| Guard test suite | 70/70 pass, unmodified |
| Exact argv validator / fingerprint algorithm | unchanged; fingerprint recomputed and matched |
| H-1 import boundary / H-2 exit-0/2 boundary | guard file unmodified; suites green |
| `-E -s` invocation | hook command still `<sys.executable> -E -s <absolute guard path>` |
| Guard-file preflight | `_verified_exact_bash_guard_module_path()` returns the expected path |
| `--allowedTools` layering, wrapper deny, `run_in_background` deny | unchanged; covered by the green adapter suite |
| Enable constant | **`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False`** (`claude_code_cli.py:366`); contract resolves `UNSUPPORTED` |

**Dormancy note:** because the enable constant is `False`, `run()` returns
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` before reaching the withholding block.
The M-1/M-2 fix is therefore itself dormant in the shipped configuration, exactly
like the H-1/H-2 and process-hardening work. My verification drove it directly.

## 9. Historical on-disk evidence — Owner decision required

**Independently confirmed.** Scanning all 15 JSON files under `.tomtit/dogfood/`,
**3 files** carry raw provider command text inside `stdout_excerpt`:

```
.tomtit/dogfood/loops/360e74f8-…/children/…-impl-1/result.json   excerpt_len=4895
.tomtit/dogfood/runs/5badc6b0-…/result.json                       excerpt_len=2341
.tomtit/dogfood/runs/c04cda2d-…/result.json                       excerpt_len=2626
```

Raw values recovered include `npm ping 2>&1 | head -20`,
`npm ping 2>&1; echo EXIT:$?`, `ls node_modules | grep -iE …`, and
`cat node_modules/js-yaml/package.json | grep …` — i.e. shell strings with pipes,
redirects, and command separators, verbatim.

All three predate the fields entirely (`authorized_command_count` and
`provider_output_withheld` are absent, not merely `0`/`False`), so they predate
the guard and are **not** a failure of this patch.

**Exposure assessment:**

| Vector | Status |
|---|---|
| Source control | **Low** — `/.gitignore:115` ignores `/.tomtit/*`; `git ls-files .tomtit` returns **0** tracked files. Reachable only via an explicit `git add -f` |
| CI artifacts | **None** — the repository has no `.github/workflows/` directory at all; nothing uploads artifacts |
| Local backups / snapshots | **Present** — ordinary filesystem files under the working tree; any Time Machine or backup sweep retains them |
| Onward transmission to a model | **Present** — `run_review.extract_adapter_result_fields()` copies `stdout_excerpt` (first 2000 chars) into the review prompt. For these legacy files that is raw command text |

**Classification:** remediation need is **separate from, and does not affect, the
correctness of the new fix**. The commands involved are benign local build
commands, not credentials — but the containment question (immutable historical
record vs. scrub) is an Owner policy decision, not a reviewer's. I did not modify
or delete any artifact.

`HISTORICAL_CLEANUP_DECISION_REQUIRED=YES`.

## 10. Non-blocking findings

**N-1 — provider-chosen filenames reach `error_message` and the manifest (LOW,
pre-existing, arguably required).** `_determine_status` builds
`f"changed paths outside the allowed set: {list(unexpected_changed_paths)}"`
(`claude_code_cli.py:1667`). Reproduced: a provider that creates
`LEAK-curl-evil-example-rev-shell.txt` in the workspace gets that name persisted
verbatim into `error_message`, `receipt.json`, and the manifest, **even with
authorization active and output withheld**.

This is outside the stated invariant, which covers provider-controlled *stdout/stderr
text*; a filename is neither. It is also bounded (filesystem name rules, ~255
bytes, must be a really-created file) and is arguably necessary evidence — naming
the offending path is the entire point of `SCOPE_VIOLATION`. It is unchanged by
this patch. Recorded because review area 2 item 9 asks specifically about
`error_message`, and answering it honestly requires naming this channel rather
than reporting a clean pass. If the Owner wants the invariant tightened to
"no provider-*influenced* strings", this is the remaining one to address —
plausibly by persisting path hashes or a count alongside a bounded, sanitized
name.

**N-2 — legacy telemetry path still type-unchecked (LOW, pre-existing, no-command
path only).** `extract_usage_from_implementation_result`'s legacy branch returns
`parsed.get("total_cost_usd")` with no `isinstance` check, so a provider string
could flow into `aggregate_usage`, where `sum()` would raise `TypeError`. This is
an availability nit, not a confidentiality one, it applies only to runs *without*
authorized commands, and it is explicitly the behavior the task required be left
unchanged. The new typed path does check. Worth aligning whenever the legacy path
is next touched.

## 11. Verification commands — exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests, unmodified)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (390 tests; was 351, +39)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q            (FIRST RUN)                         -> EXIT=1   (1 failed, 715 passed — see flake note)
python3 -m pytest -q            (rerun)                             -> EXIT=0   (716 tests; was 677, +39)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Flake note — recorded honestly, and correcting the fix report.** The fix report
§15 states "None observed in this task." In **my** first full-suite run the known
F-1 flake *did* fire:
`tests/adapters/test_claude_code_cli.py::test_normal_exit_with_surviving_descendant_kills_descendant`
failed with `assert result.status is SUCCEEDED` getting `FAILED`. This does not
contradict the report (which describes its own runs) and it is not attributable to
this patch: the failing result's own repr shows `provider_output_withheld=False`
and all `provider_*` fields `None`, i.e. it uses `base_request(workspace)` with no
authorized commands and exercises none of the changed code. It is the pre-existing
process-group cleanup timing test. Isolated rerun passed **5/5**; the full-suite
rerun passed clean at 716. The first result is recorded, not erased.

Test counts match the report exactly, including the +39 delta.

## 12. Constraint compliance

- No implementation file, test file, or prior report modified. The enable constant
  was patched **in memory only** inside my probe process (disclosed in the header);
  the on-disk value is `False`.
- No git write operation. `HEAD` unchanged. Working tree identical to session start
  apart from this new report.
- TOMTIT not run against itself. No real Claude or Codex child model invoked. No
  personal-web rerun. No historical evidence modified or deleted.

## 13. Recommended next action

Owner decision on the historical `.tomtit/dogfood/` artifacts (§9), which is
independent of and does not block the enablement question. With H-1, H-2, M-1 and
M-2 now all closed and the execution layer already operationally proven, the
remaining step is the separate narrow enablement patch flipping
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` — which this review does not
authorize.

---

VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
M1_PROVIDER_OUTPUT_REDACTION=RESOLVED
M2_TRUNCATION_BEFORE_REDACTION=RESOLVED
RAW_PROVIDER_COMMAND_NEW_EVIDENCE=NO
RAW_PROVIDER_SECRET_NEW_EVIDENCE=NO
UNKNOWN_PROVIDER_FIELDS_SAFE=PASS
NON_JSON_PROVIDER_OUTPUT_SAFE=PASS
TRUNCATED_PROVIDER_OUTPUT_SAFE=PASS
SAFE_PERSISTENCE_BOUNDARY=PASS
TYPED_USAGE_TELEMETRY=PASS
NO_COMMAND_COMPATIBILITY=PASS
EXECUTION_AUTHORIZATION_LAYER=PRESERVED
EXACT_ARGV_OPERATIONALLY_PROVEN=YES
ENABLE_CONSTANT=False
EXACT_AUTHORIZATION_ENABLED=NO
HISTORICAL_LEAK_CONFIRMED=YES
HISTORICAL_CLEANUP_DECISION_REQUIRED=YES
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=OWNER_DECIDES_HISTORICAL_EVIDENCE_DISPOSITION_THEN_NARROW_ENABLEMENT_PATCH

Scope of this APPROVE_WITH_NON_BLOCKING_FINDINGS: M-1 and M-2 are verified
resolved and the system is ready for a separate narrow enablement patch. It does
not authorize flipping the enable constant, a personal-web rerun, TOMTIT
self-modification, deleting historical evidence, or real user workloads.
