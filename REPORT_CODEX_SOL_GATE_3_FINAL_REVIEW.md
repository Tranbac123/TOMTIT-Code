# Final Independent Gate 3 Claude Code CLI Capability Review

## VERDICT

`REQUEST_CHANGES`

The committed real CLI success evidence is authentic and internally consistent, Runtime authority remains separate, timeout cleanup works, secrets are not persisted, and all 347 deterministic tests pass. Gate 3 is not approved because an independent offline reproduction shows that a normally exiting worker can leave a background descendant and both pipe-reader threads alive after `run()` returns. The adapter can report `SUCCEEDED` with an empty unexpected-path set, then the descendant can modify an unexpected workspace file after the final snapshot. A second reproduced contract defect allows an invalid explicit executable path to escape as `FileNotFoundError` instead of returning the immutable result contract.

## TARGET_SOURCE_COMMIT

`6b370c3236f4120e5a917676231e3420055aa950`

## EVIDENCE_REFRESH_COMMIT

`ecfa981bdedf25f31e46bf870aee6fa5be3fc31c`

## REVIEWED_DIFF_RANGES

```text
IMPLEMENTATION=66b6afb6d82934b09d5b8214d2d3e4e6b629111c..6b370c3236f4120e5a917676231e3420055aa950
EVIDENCE_REFRESH=fefaf4e9063f87517d951e0fdeaf76d0ccc2c641..ecfa981bdedf25f31e46bf870aee6fa5be3fc31c
```

## REVIEW_REPORT_EXPECTED_PARENT

`9846ff1771ec4607ec342411d25def662fcf95e4`

Preflight matched exactly: branch `main`; required HEAD through fourth parent; clean tracked worktree, index, and untracked project-file set. Ignored entries were local metadata, caches, and bytecode only.

## FILES_REVIEWED

Required governance and authority material, the Gate 2 closing evidence/review, both Gate 3 reports, adapter source and exports, manual spike script, both offline test files, both current evidence files, and both historical attempt-1 evidence files were read. Production imports and call sites were searched directly rather than relying on the AST test alone.

Commit scopes were exact:

- source commit: nine bounded new Gate 3 adapter/test/script/initial-evidence files;
- original report: only `REPORT_CLAUDE_GATE_3_CLAUDE_CODE_CLI_SPIKE.md`;
- refresh: only the canonical result JSON and patch;
- successful-evidence report: only `REPORT_CLAUDE_GATE_3_REAL_CLI_EVIDENCE_REFRESH.md`.

No Runtime, governance, Gate 1, Gate 2, or prior-report path changed in the refresh.

## COMMANDS_AND_EXIT_CODES

| Command / probe | Exit | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | 347 collected |
| `python3 -m pytest -q` | 0 | 347 passed |
| `python3 -m pytest tests/adapters -q` | 0 | 23 passed |
| Gate 3 offline scenario tests | 0 | 2 passed |
| Scenario 1 tests | 0 | 99 passed |
| Scenario 2 tests | 0 | 137 passed |
| `python3 -m compileall -q src tests scripts` | 0 | pass |
| `ruff check .` | 0 | no findings |
| `git diff --check` and both reviewed-range diff checks | 0 | clean |
| `python3 -m mypy src` | 0 | no issues in 13 source files |
| successful-manifest `jq` invariants | 0 | all required values true |
| stdout/stderr digest reproduction | 0 | both hashes match captured excerpts |
| placeholder/secret-form evidence scans | expected no-match | no credential form found |
| 14 isolated weakened-condition controls | 1 | 14 failed as expected |
| invalid explicit-executable probe | 0 | reproduced `FileNotFoundError` escape |
| normal-exit descendant probe | 0 | reproduced success with live child and two live readers |
| delayed-write scope probe | 0 | reproduced post-success unexpected `README.md` mutation |

No real or billable Claude invocation was executed during this review.

## ARCHITECTURE_BOUNDARY_RESULTS

The static authority boundary is correct. `ClaudeCodeCLIAdapter` imports no Runtime, domain, reporting, or fake Runtime module. It has no `RunRecord`, event, DecisionEvent, attempt progression, retry/escalation, completion, or terminal-report write path. `run()` executes one worker process and otherwise constructs only adapter data. Runtime remains the sole canonical authority.

`RUNTIME_AUTHORITY_PRESERVED=RESOLVED`

The adapter itself remains `OPEN`, however, because its process/evidence boundary can return before descendant activity is finished.

## REQUEST_AND_RESULT_CONTRACT_RESULTS

The request is a frozen, slotted dataclass containing the required identities and bounded execution inputs. It validates nonempty identity/prompt/goal, attempt at least one, positive timeout and output bounds, and validates the workspace as an existing absolute directory before execution. It contains no shell-command field, full RunRecord, canonical state, or decision-authority field. Environment inheritance is explicit allowlist only. Optional model and permission mode remain spike-specific invocation parameters, not routing or Runtime policy.

The result is a frozen, slotted dataclass with every required identity, status, process, bounded stream, digest, timing, workspace, scope, evidence-path, and error field. `_determine_status` correctly refuses success for nonzero exit, timeout, unexpected paths, or no expected change. Discovery failure returns `UNAVAILABLE`.

The explicit `executable_path=` override bypasses `discover_claude_executable(explicit_path)` validation. Passing a nonexistent explicit path reaches `subprocess.Popen` and raises `FileNotFoundError`; `run()` therefore does not always return only `ClaudeCodeCLIResult`. This is `G3-SOL-02`.

## PROCESS_EXECUTION_RESULTS

The normal execution path uses a list argv, no shell, explicit cwd, `DEVNULL` stdin, separately captured stdout/stderr, monotonic deadline, and a new session/process group. Timeout sends group `SIGTERM`, waits a bounded grace period, falls back to group `SIGKILL`, retains partial output, and the existing timeout/grandchild tests pass.

`PROCESS_TIMEOUT_HANDLING=RESOLVED`

`PROCESS_CLEANUP=OPEN`

On a normal parent exit, `_execute` does not check for or terminate surviving group members. It only calls `join(timeout=5)` once per daemon reader and never tests `reader.is_alive()`. Independent reproduction:

```text
normal_parent_status=SUCCEEDED
normal_parent_success=True
normal_parent_elapsed_seconds=10.51
normal_parent_reader_threads_alive=2
normal_parent_background_child_alive=True
```

The temporary probe then killed the process group and joined the readers; no probe process was intentionally left behind.

## ENVIRONMENT_AND_SECRET_RESULTS

`SECRET_REDACTION=RESOLVED`

The child environment contains only present allowlisted variables. Secret-named allowlisted values are collected and replaced in stdout, stderr, and sanitized argv; HOME is replaced with `$HOME` in argv/excerpts. The raw environment is neither logged nor persisted. Independent scans of current and historical manifests found no API key, password, credential, authorization bearer, cookie, or Anthropic key form. Captured hashes reveal no secret value.

The unredacted local `executable_path` and absolute evidence paths disclose a local filesystem layout only. This is LOW privacy/portability exposure, not a credential defect under the specified boundary.

## WORKSPACE_SCOPE_RESULTS

Snapshots deterministically sort relative files and hash content, ignoring only documented `.git`, `__pycache__`, `.DS_Store`, and `.pyc` metadata. Changed paths derive from before/after existence and hashes; unexpected paths take status priority and are not reverted. Missing expected change cannot succeed. Patch rendering is bounded by per-file content capture and represents the derived changed paths.

`SCOPE_CHANGE_DETECTION=OPEN`

The static before/after calculation works only while worker activity has quiesced. A second independent fixture had the parent make the expected `result.txt` change, spawn a child that delayed an unexpected `README.md` write, and exit. The adapter returned after its two timed reader joins:

```text
late_write_status_at_return=SUCCEEDED
late_write_success_at_return=True
late_write_unexpected_paths_at_return=()
late_write_returned_after_seconds=10.62
late_write_readme_at_return=before
late_write_readme_after_return=late unexpected write
```

Thus the result can certify a clean scope snapshot while the same invocation still has authority to mutate the workspace afterward. This is the trust impact of `G3-SOL-01`.

The real spike script did use a fresh temporary Git repository outside TOMTIT; no reviewed real evidence indicates a TOMTIT repository edit.

## OFFLINE_TEST_RESULTS

All expected counts match: 347 total, 23 adapter, 2 Gate 3 scenario, 99 Scenario 1, and 137 Scenario 2. Compileall, Ruff, diff checks, and source Mypy pass. Existing tests cover successful execution, nonzero exit, timeout, timeout-grandchild termination, partial output, stream truncation/digests, static scope checks, environment allowlisting, redaction, shell avoidance, immutable contracts, no Runtime imports, and no adapter retry.

The deterministic suite missed `G3-SOL-01` because its grandchild test exercises only the timeout branch. It has no case where the direct child exits normally while a descendant survives, holds pipes, or performs a delayed post-snapshot write. It missed `G3-SOL-02` because the unavailable test covers failed `shutil.which`, not an invalid explicit override or `Popen` failure.

## HISTORICAL_FAILURE_RESULTS

`HISTORICAL_FAILURE_PRESERVED=PASS`

The original report remains unchanged and records CLI `1.0.85`, two real attempts, API 404 model-resolution failure, no workspace change, empty patches, and Gate objective not proven at that time. The attempt-1 failed manifest and patch remain committed. The later refresh modifies only the canonical result/patch pair, preserving the prior report and historical attempt evidence rather than rewriting the audit trail.

## REAL_EVIDENCE_AUTHENTICITY

`REAL_CLI_INVOCATION=PROVEN`

The refreshed JSON is valid with sorted keys and records `SUCCEEDED`, `success=true`, exit 0, no timeout, attempt 1, only `result.txt`, no unexpected path, differing workspace hashes, null errors, a fresh run ID distinct from the historical attempt, UTC-aware ordered timestamps, positive plausible 18.375-second duration, and valid stream SHA-256 fields. Recomputed stdout and empty-stderr hashes match. Evidence paths identify the canonical Gate 3 files.

The manifest is not a relabeled prior failure: run identity, timestamps, exit/status, output, duration, and workspace hash transition are independently different. It also records real Claude response metadata consistent with a successful invocation.

`claude update` reported `2.1.222`, while the successful binary self-reported `2.1.197 (Claude Code)`. `CLI_INSTALLATION_VERSION_METADATA_MISMATCH=OBSERVED_NONBLOCKING`.

## PATCH_AND_RESULT_FILE_RESULTS

`REAL_CLI_RESULT_FILE=PROVEN`

`REAL_CLI_SCOPE_CONTROL=PROVEN` for the single committed invocation.

The nonempty canonical patch contains exactly `a/result.txt` and `b/result.txt`, replacing `not completed` with `TOMTIT Claude Code CLI adapter smoke test passed`. It has no README, `.git`, TOMTIT source, test, or other unexpected entry. This proves the exact real sample stayed in scope; it does not cure the generic delayed-write defect.

`REAL_CLI_EVIDENCE=PROVEN`

## SCRIPT_ACCEPTANCE_RESULTS

The script's `OVERALL_PASS` requires status `SUCCEEDED`, `success=true`, exact permitted result content, changed paths exactly `("result.txt",)`, and empty unexpected paths. Discovery absence returns 2. CLI failure, timeout, wrong content, missing expected change, or scope violation returns nonzero. The workspace comes from `tempfile.mkdtemp`, is initialized as a separate Git repository, and contains only the fixture. The successful refresh reports one attempt and no retry. The refresh commit changes only generated evidence, so it contains no hidden invocation code or script mutation.

The original blocked script permits one documented invocation-correction retry on recognized syntax/model-resolution failure. That historical behavior did not occur on the successful rerun and is nonblocking here.

## NEGATIVE_CONTROLS

All controls were stored only in `/private/tmp`. Each intentional weakened-condition assertion failed against the current guard:

| # | Restored invalid condition | Public/offline path and exact failing test | Load-bearing guard |
|---:|---|---|---|
| 1 | nonzero exit succeeds | `_determine_status`; `test_nc01_nonzero_exit_treated_as_success` | exit-code branch forces failure |
| 2 | timeout succeeds | `_determine_status`; `test_nc02_timeout_treated_as_success` | timeout branch forces failure |
| 3 | unexpected path succeeds | `_determine_status`; `test_nc03_unexpected_path_treated_as_success` | scope violation has priority |
| 4 | missing expected change succeeds | `_determine_status`; `test_nc04_missing_expected_change_treated_as_success` | expected-change requirement |
| 5 | shell execution enabled | source AST; `test_nc05_shell_invocation_enabled` | list argv/no shell |
| 6 | unallowlisted secret inherited | environment builder; `test_nc06_environment_inherits_unallowlisted_secret` | explicit allowlist |
| 7 | redaction removed | redaction helper; `test_nc07_secret_redaction_removed` | secret replacement |
| 8 | group termination removed | offline timeout process; `test_nc08_process_group_termination_removed` | timeout invokes group termination |
| 9 | internal retry enabled | offline invocation counter; `test_nc09_adapter_internal_retry_enabled` | one `Popen` per `run()` |
| 10 | Runtime/domain import introduced | production AST; `test_nc10_runtime_or_domain_import_introduced` | adapter independence |
| 11 | mutable result | public result assignment; `test_nc11_result_dataclass_made_mutable` | frozen dataclass |
| 12 | exact content ignored | script helper; `test_nc12_script_acceptance_ignores_exact_result_content` | content predicate |
| 13 | unexpected path accepted by script | exact `OVERALL_PASS` expression; `test_nc13_script_acceptance_permits_unexpected_path` | exact path and empty-unexpected checks |
| 14 | success paired with empty derived patch | patch renderer; `test_nc14_empty_patch_accepted_while_success_true` | changed-content diff rendering |

Result: `14 failed in 2.35s`, the expected control outcome. These controls validate existing defenses but do not cover the independently reproduced post-return process/scope gap.

## PRESERVATION_RESULTS

Scenario 1: 99 passed. Scenario 2: 137 passed. The Gate 3 source/report/evidence ranges did not modify Runtime, domain, reporting, fakes, either earlier scenario test, or Gate 1/Gate 2 evidence.

```text
GATE_1_PRESERVATION=PRESERVED
GATE_2_PRESERVATION=PRESERVED
```

## FINDING_STATUS

```text
CLAUDE_CODE_CLI_ADAPTER=OPEN
REAL_CLI_INVOCATION=PROVEN
REAL_CLI_RESULT_FILE=PROVEN
REAL_CLI_SCOPE_CONTROL=PROVEN
REAL_CLI_EVIDENCE=PROVEN

PROCESS_TIMEOUT_HANDLING=RESOLVED
PROCESS_CLEANUP=OPEN
SCOPE_CHANGE_DETECTION=OPEN
SECRET_REDACTION=RESOLVED
RUNTIME_AUTHORITY_PRESERVED=RESOLVED

HISTORICAL_FAILURE_PRESERVED=PASS
GATE_1_PRESERVATION=PRESERVED
GATE_2_PRESERVATION=PRESERVED

CLI_INSTALLATION_VERSION_METADATA_MISMATCH=OBSERVED_NONBLOCKING
REAL_WORLD_ADAPTER_STABILITY=NOT_PROVEN
```

### G3-SOL-01 — surviving descendants bypass final scope evidence

```text
SEVERITY=HIGH
STATUS=OPEN
REPRODUCTION=Offline parent exits 0 after expected write and spawning a delayed descendant; adapter returns SUCCEEDED with two readers and descendant alive; descendant changes README.md after the final snapshot.
CORRECTNESS_IMPACT=A worker invocation can continue mutating the workspace after success and after evidence canonicalization, so unexpected changes may be absent from changed_paths/patch despite being caused by that invocation.
AFFECTED_SCOPE=Process lifecycle, cleanup, workspace snapshot authority, and result trustworthiness in ClaudeCodeCLIAdapter._execute/run.
WHY_TESTS_MISSED=Existing descendant coverage only exercises timeout; no normal-exit descendant or delayed post-snapshot write test exists.
BOUNDED_CORRECTION=Ensure the entire spawned process group is quiescent or terminated on every return path, not only timeout; require reader threads to finish; fail closed if cleanup cannot be proven; take the final workspace snapshot only after cleanup; add normal-exit descendant and delayed-write regression tests.
```

### G3-SOL-02 — explicit executable failure escapes normalization

```text
SEVERITY=MEDIUM
STATUS=OPEN
REPRODUCTION=ClaudeCodeCLIAdapter.run(request, executable_path=<nonexistent absolute path>) raises FileNotFoundError.
CORRECTNESS_IMPACT=The advertised single immutable result boundary is not total for a supported argument; callers receive an unstructured exception rather than UNAVAILABLE/FAILED evidence.
AFFECTED_SCOPE=Explicit executable resolution and subprocess-start error normalization.
WHY_TESTS_MISSED=The unavailable test mocks shutil.which only; it does not exercise an invalid explicit override or Popen OSError.
BOUNDED_CORRECTION=Validate the explicit path through the existing discovery helper and normalize subprocess creation OSError into an immutable non-success result; add regression coverage.
```

## GATE_STATEMENT_ASSESSMENT

`FAILED`

The repository proves one authentic real invocation and a correctly captured exact sample, but it does not prove the full statement that TOMTIT detects unexpected path changes while preserving a bounded trustworthy worker result: a surviving descendant can mutate an unexpected path after the success snapshot. Runtime authority itself remains preserved.

## RISKS

Nonblocking risks remain in addition to the two blocking findings: one successful sample does not prove stability; absolute local paths disclose local layout; installation/update version metadata differs; persistent Runtime integration, production sandboxing, cost controls, cross-process recovery, general retry policy, and multi-provider support are absent or out of scope.

## SCOPE_AND_NON_GOALS

This review covers the bounded adapter contracts, external-process trust boundary, evidence authenticity, workspace scope, script acceptance, historical evidence, and Gate 1/Gate 2 preservation. It does not claim production reliability, cross-version or multi-task stability, intervention reduction, OS sandbox security, persistent recovery, cost optimization, or routing. It did not execute the real CLI, modify tracked implementation/evidence, or start another gate.

## UNPROVEN_ITEMS

- Quiescence and cleanup after a normally exiting parent that leaves descendants.
- Trustworthy final workspace evidence in the presence of delayed descendant writes.
- Total result normalization for all subprocess-start failures.
- Production reliability, persistent integration/recovery, sandbox security, multi-task/cross-version behavior, and product value.

## REVIEW_BUDGET_CONSEQUENCE

The first independent Gate 3 correctness review reproduced one HIGH and one MEDIUM same-scope defect. Gate 3 remains not approved. A bounded corrective patch may address only process-group quiescence/cleanup, post-cleanup snapshot ordering, reader termination/fail-closed behavior, explicit-executable/Popen error normalization, and their offline regressions. No new real CLI invocation is needed to correct or rereview these defects.

## NEXT_ACTION

```text
GATE_3_STATUS=NOT_APPROVED
NO_NEXT_GATE_AUTHORIZED
```
