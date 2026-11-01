# M-1 Identity Corrective Patch — Narrow Independent Rereview

## 1. Verdict

**APPROVE_WITH_NON_BLOCKING_FINDINGS**

Both prior blocking identity findings are resolved. The corrective patch fails closed for malformed implementation workspaces and malformed/mismatched review attempts without regressing the previously approved M-1 behavior. The only finding is the pre-existing adapter timeout-test race recorded by the implementer; it is not caused by this patch.

## 2. H-1 workspace-binding result

**RESOLVED.** The shared implementation-evidence loader now requires `workspace` to be a non-empty string, resolves it under an `OSError`/`ValueError` boundary, and unconditionally compares the canonical path to the expected workspace. There is no truthiness-gated equality bypass.

The trust order is correct: structural receipt validation occurs first; workspace equality is proven next; canonical `run_id` and `attempt` are then bound; only afterward do review/repair consume `allowed_side_effect_paths`. Missing or invalid workspace evidence cannot authorize `dist/bundle.js`. Valid matching workspaces still pass both consumers.

## 3. M-1 review-attempt-binding result

**RESOLVED.** `run_repair.bind_review_to_implementation()` now requires `review_receipt["implementation_attempt"]` to be an integer, not a bool, positive, and exactly equal to the already-bound implementation receipt attempt.

No directory, run-ID suffix, review ID, workspace, Git state, or default supplies the value. Missing/null/string/bool/zero/negative/2 all fail with `REVIEW_IMPLEMENTATION_ATTEMPT_MISMATCH`; value 1 passes. Existing review workspace and implementation-run-ID comparisons remain load-bearing.

## 4. Independent negative probes

Focused probes ran first in isolated temporary Git repositories:

- Both `run_review` and `run_repair` rejected missing, empty, null, non-string, NUL/unresolvable, and wrong implementation workspaces.
- The negative receipts carried `allowed_side_effect_paths=["dist"]` with live `dist/bundle.js`; rejection occurred at workspace binding before that authority was consumed.
- Both consumers accepted a canonical matching workspace and classified `dist/bundle.js` as authorized.
- Repair rejected missing, null, `"1"`, `True`, `0`, `-1`, and `2` review attempts, and accepted `1`.
- Independent wrong review run-ID and wrong review-workspace probes remained rejected.

The focused probe process exited 0 with all assertions satisfied.

## 5. Regression check

No regression was found in the listed M-1 invariants. Canonical `run_id + attempt`, Review 1/Review 2 propagation, canonical three-way classification, source precedence, component-aware prefix denial, source-only review patching, SOURCE-only repair comparison, full workspace hashing, ordered side-effect inheritance, and generated-only `NO_EXPECTED_CHANGE` semantics remain covered by the passing dogfood suites.

The exact file-write and Bash guards remain unchanged in behavior; the Bash guard retains SHA-256 `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`. Adapter regressions covering settings-source lockdown and provider-output withholding passed.

## 6. Flake attribution

The implementer’s first full-suite run failed `test_no_child_process_survives_a_timeout` because the fake worker had not created `child.pid` before its one-second timeout; the immediate complete rerun passed. The failure is preserved verbatim in the full rereview log.

The identity patch did not cause it. The failing test invokes `ClaudeCodeCLIAdapter` directly and does not execute `run_review` or `run_repair`; the corrective logic is confined to dogfood evidence parsing/binding. The same process-start/timing flake family is documented in prior reports. This rereview’s full suite passed on its first and only run.

## 7. Tests

| Check | Result |
|---|---:|
| Independent identity matrix | PASS; exit 0 |
| `test_run_review.py` | 70 tests; exit 0 |
| `test_run_repair.py` | 92 tests; exit 0 |
| `test_run_loop.py` | 58 tests; exit 0 |
| Adapter suite | 558 tests; exit 0 |
| Claude spike scenarios | 4 tests; exit 0 |
| Full pytest, first/only rereview run | 884 tests; exit 0 |
| `compileall` | exit 0 |
| Ruff | exit 0 |
| mypy | exit 0; 15 source files clean |
| `git diff --check` | exit 0 |

Full commands, stdout/stderr, exit codes, source anchors, hashes, repository receipt, and the implementer’s first-run flake are retained in `M1_IDENTITY_NARROW_REREVIEW_FULL.log`. No real model was invoked, no Git write operation was performed, and personal-web was not touched.

## 8. Findings

### N-1

- **ID:** N-1
- **SEVERITY:** LOW / non-blocking
- **FILE/LOCATION:** `tests/adapters/test_claude_code_cli.py`, `test_no_child_process_survives_a_timeout`
- **PROBLEM:** The test assumes the fake worker will start and write `child.pid` within the adapter’s one-second timeout. Under scheduling delay, timeout can occur before that write.
- **IMPACT:** Intermittent false-negative full-suite failures; no demonstrated product, identity-binding, or process-cleanup regression.
- **REQUIRED_FIX:** Separately harden the test with deterministic startup synchronization or a bounded readiness handshake before exercising timeout cleanup.

No MEDIUM or HIGH finding remains.

## 9. Final recommendation

Close the two M-1 identity findings and permit the Owner-coordinated personal-web rerun. Track N-1 separately as test reliability work; it does not block this identity correction.

VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
H1_FALSY_WORKSPACE_BYPASS=RESOLVED
WORKSPACE_MISSING_REJECTED=YES
WORKSPACE_EMPTY_REJECTED=YES
WORKSPACE_NULL_REJECTED=YES
WORKSPACE_MALFORMED_REJECTED=YES
WORKSPACE_WRONG_REJECTED=YES
AUTHORITY_BEFORE_WORKSPACE_BINDING=NO
M1_REVIEW_ATTEMPT_BINDING=RESOLVED
MISSING_REVIEW_ATTEMPT_REJECTED=YES
MALFORMED_REVIEW_ATTEMPT_REJECTED=YES
WRONG_REVIEW_ATTEMPT_REJECTED=YES
MATCHING_REVIEW_ATTEMPT_ACCEPTED=YES
RUN_ID_BINDING_PRESERVED=YES
THREE_WAY_MUTATION_MODEL=PRESERVED
SOURCE_ONLY_REVIEW_SCOPE=PRESERVED
RUN_REPAIR_RECONCILIATION=PRESERVED
WORKSPACE_HASH=PRESERVED
SIDE_EFFECT_AUTHORITY_IMMUTABLE=YES
GENERATED_ONLY_PRODUCT_SEMANTICS=PASS
IDENTITY_PATCH_CAUSED_PROCESS_FLAKE=NO
FULL_TEST_SUITE=PASS_WITH_FLAKE_NOTE
PERSONAL_WEB_RERUN_AUTHORIZED=YES
NEXT_ACTION=OWNER_RERUNS_PERSONAL_WEB_DOGFOOD
