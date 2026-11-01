# TOMTIT M-1 Changed-Path Independent Rereview

## 1. Verdict

**REQUEST_CHANGES**

The three-way mutation model, source-only review patch, repair reconciliation, full-workspace hash, and Attempt-2 side-effect contract inheritance are implemented correctly. Canonical identity propagation from `run_loop` is also correct. Approval is blocked by two independently reproduced fail-closed identity defects:

1. implementation evidence with a present-but-falsy `workspace` value bypasses workspace equality in the shared loader and can supply authority-bearing side-effect roots to both review and repair;
2. repair accepts review evidence whose `implementation_attempt` is absent or wrong because it cross-checks only `implementation_run_id`.

These are trust-boundary defects even though all prescribed suites pass.

## 2. Source inspection

I directly inspected `.tomtit/dogfood/run_task.py`, `run_review.py`, `run_repair.py`, `run_loop.py`, their three test files, and the canonical classifier in `src/tomtit/adapters/claude_code_cli.py`.

The traced flow is: Owner contract → Attempt 1 request/receipt → identity-bound review preflight → canonical three-way classification → source-only reviewer patch → reviewed-state proof → exact Attempt-2 contract inheritance → repair receipt → Attempt-2 review. The classification/state portions are coherent; the two evidence-binding gaps are detailed below.

## 3. Canonical identity schema

Both implementation receipt shapes expose canonical `run_id` and `attempt`:

- `run_task` persists the adapter's actual implementation `run_id` and fixed attempt 1.
- `run_repair` persists the actual repair ID as both `run_id` and compatibility field `repair_run_id`, with fixed attempt 2.

The executable regression asserts `repair_receipt["run_id"] == repair_receipt["repair_run_id"]`. Review loading uses only canonical `run_id`; it does not fall back to `repair_run_id`.

## 4. Review identity binding

`run_review` requires independent CLI flags `--implementation-run-id`, `--implementation-attempt`, and `--review-run-id`. It loads implementation evidence before reading `allowed_side_effect_paths`, then compares canonical `run_id` and `attempt`. Wrong/missing/malformed run IDs and attempts fail closed in existing tests.

However, workspace binding is not fail closed. `load_implementation_evidence()` requires the `workspace` key but checks mismatch only under `if receipt_workspace`. Consequently `workspace: ""` or `workspace: null` skips equality. An isolated proof supplied `workspace: ""`, matching run/attempt, and `allowed_side_effect_paths: ["dist"]`; review preflight accepted `dist/bundle.js` as an authorized side effect. The required `(workspace, run_id, attempt)` binding is therefore incomplete.

## 5. Review 1 / Review 2 propagation

`run_loop` passes the correct identities:

- Review 1 receives `impl1_id`, attempt 1, and its independent `review1_id`.
- Repair receives the original `impl1_id`, attempt 1.
- Review 2 receives `repair2_id`, attempt 2, and its independent `review2_id`.

Review 2 does not reuse `impl1_id`. The end-to-end loop regression reads both review receipts and proves these values.

## 6. Three-way mutation semantics

All live Git-visible paths are classified by the adapter's canonical `classify_workspace_changes()` function into `SOURCE`, `AUTHORIZED_SIDE_EFFECT`, or `UNEXPECTED`. Neither review nor repair duplicates path-prefix logic.

Classification is ordered: exact source membership first, component-aware side-effect-root containment second, and unexpected otherwise. There is no `startswith()` containment. Thus source authorization wins when scopes overlap, and `dist-evil` does not match `dist`.

## 7. run_review classification

The required valid case passes: live `package.json` plus `dist/bundle.js`, with `package.json` source-authorized and `dist` taken from identity-bound implementation evidence, yields:

- source: `package.json`;
- authorized side effect: `dist/bundle.js`;
- unexpected: none.

Preflight passes and returns `changed_paths` as the source-only tuple. The classification is correct when the evidence workspace field is valid; section 4 explains why malformed workspace evidence can improperly reach it.

## 8. Source-only patch semantics

The review prompt builds tracked diff and untracked-file content only from the classified source paths. The executable test confirms `package.json` appears in the patch-driving section while `dist/bundle.js` and its contents appear in neither the tracked diff nor the untracked-file list. Authorized generated files do not enter the product patch.

The raw status section remains unfiltered for transparent workspace state, but it does not provide generated-file content as product patch material.

## 9. run_review fail-closed cases

The implementation correctly denies:

- unapproved source `src/unapproved.ts`;
- prefix-confused `dist-evil/bundle.js` under a `dist` contract;
- `dist/bundle.js` with no side-effect authority;
- wrong-run evidence carrying useful roots;
- wrong-attempt implementation evidence carrying useful roots;
- reviewer-output or Git-derived attempts to introduce a new root.

These checks are insufficient for approval because the falsy-workspace proof reaches and trusts the same authority-bearing field without a valid workspace equality proof.

## 10. run_repair identity binding

`run_repair` now receives the actual Attempt 1 implementation ID and attempt from `run_loop` and uses the shared loader before reading Attempt 1 side-effect roots. Wrong implementation run/attempt values fail closed. Review-run identity is not substituted for implementation identity.

Two gaps remain:

- the shared falsy-workspace bypass also accepts Attempt 1 implementation evidence with `workspace: ""` during repair preflight;
- `bind_review_to_implementation()` validates the review workspace and `implementation_run_id` but never requires or compares `review_receipt["implementation_attempt"]`. Independent fixtures proved both a missing value and value 2 are accepted while repairing Attempt 1.

Production review receipts include the field, but legacy/malformed evidence must not be silently accepted at this authority boundary.

## 11. run_repair source-state comparison

The previous raw equality between all live Git paths and source-only receipt paths has been corrected semantically. `prove_reviewed_state()` classifies the live Git set, rejects every `UNEXPECTED` path, and compares only the `SOURCE` portion to the reviewed implementation receipt's source-only `changed_paths`.

The valid `package.json` plus authorized `dist/bundle.js` case passes. The fingerprint retains the full live path set and separately records source, authorized side effect, and unexpected tuples.

## 12. Workspace hash preservation

The full workspace hash remains load-bearing and is evaluated before Git-path classification. `snapshot_workspace_hashes()` and `workspace_hash()` are reused from the adapter, and the fresh value must exactly equal the implementation result's `workspace_after_hash`.

Changing `dist/bundle.js` after review fails with `WORKSPACE_STATE_MISMATCH` even though its path still classifies as authorized. Adding `unexpected.txt` likewise fails at the hash proof before classification. Classification success cannot bypass content/state identity.

## 13. Side-effect contract immutability

Attempt 2 normalizes its Owner-supplied roots and compares their ordered list to the now-bound Attempt 1 receipt. Addition, removal, substitution, reordering, and introducing roots where Attempt 1 had none all fail. No roots are inferred from Git, `.gitignore`, reviewer output, filenames, or evidence-directory names.

Legacy implementation evidence missing `allowed_side_effect_paths` can authorize only the empty set. The reviewer has no parameter or output field that can mutate the side-effect contract.

## 14. NO_EXPECTED_CHANGE

The adapter defines product success using `bool(source_changed_paths)`. Authorized generated side effects are excluded from `changed_paths` and cannot satisfy expected product mutation. The generated-only repair regression returns `NO_EXPECTED_CHANGE`.

## 15. Source precedence

For source authorization `dist/bundle.js`, side-effect root `dist`, and changed path `dist/bundle.js`, classification yields `SOURCE`; authorized-side-effect and unexpected tuples are empty. This is covered at adapter, review, and repair layers.

## 16. Backward compatibility

With `allowed_side_effect_paths=()`, ordinary source-only review and repair behavior remains intact, and non-source generated paths fail closed. Missing canonical `run_id` or `attempt` in implementation receipts is rejected when the required expected identity is supplied.

Backward compatibility is not fully safe for review evidence: the repair fixture's legacy-shaped review receipt omits `implementation_attempt`, and repair accepts it. This silently treats missing canonical identity as sufficient. That behavior must be replaced with a required integer attempt equal to the bound Attempt 1 receipt.

## 17. Security regressions

No M-1 regression was found in the existing guards or independent authorization layers:

- exact file-write guard behavior and tests remain green;
- exact Bash guard SHA-256 remains `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`;
- exact argv authorization, provider-output withholding, settings-source lockdown, sandbox composition, and source/side-effect/Bash independence remain covered by the passing adapter suite;
- generated side-effect scope still grants neither built-in file permission nor Bash execution.

The identity findings are confined to evidence consumption in review/repair; they do not indicate a weakening of the already-approved guard implementations.

## 18. Verification receipt

All prescribed commands passed on their first runs; none was retried:

| Command | Result |
|---|---:|
| `test_run_review.py` | 63 tests; exit 0 |
| `test_run_repair.py` | 77 tests; exit 0 |
| `test_run_loop.py` | 58 tests; exit 0 |
| `pytest tests/adapters -q` | 558 tests; exit 0 |
| Claude spike scenario | 4 tests; exit 0 |
| Full pytest | 884 tests; exit 0 |
| `compileall` | exit 0 |
| Ruff | exit 0 |
| mypy | exit 0; 15 source files clean |
| `git diff --check` | exit 0 |

Independent negative probes ran only in temporary Git fixtures. They reproduced the three acceptance observations described in sections 4 and 10. The first repair-attempt probe invocation omitted a required test-helper argument and recorded two `TypeError` results; that diagnostic mistake is retained in the full log. The corrected probe was then run once with the required source allowlist and reproduced acceptance of both missing and wrong review attempts. No prescribed product test failed or flaked.

No real Claude/Codex child invocation, TOMTIT self-run, Git write operation, or personal-web access occurred.

## 19. Findings by severity

### High

- **H-1 — falsy implementation workspace bypasses canonical binding.** `load_implementation_evidence()` accepts an empty/null `workspace` because equality is conditional on truthiness. Both review and repair then trust authority-bearing fields from evidence that was never proven to belong to the target workspace. Fix by requiring a non-empty string/path and unconditional canonical equality before any other receipt authority is consumed. Add empty-string, null, malformed-type, missing, and wrong-workspace regressions for both callers.

### Medium

- **M-1 — repair does not bind review evidence to implementation attempt.** `bind_review_to_implementation()` checks workspace and implementation run ID but ignores `implementation_attempt`. Missing and wrong values are accepted. Require a non-bool integer and exact equality with the bound Attempt 1 receipt; update legacy fixtures and add missing/malformed/wrong-attempt tests.

### Low

- None.

## 20. Remaining assumptions

- Evidence directories are Owner-selected and remain outside the target workspace, but every authority-bearing identity field must still be validated fail closed.
- The full-workspace hash continues to use the adapter's canonical snapshot semantics.
- M-1 does not change the separately reviewed symlink/TOCTOU assumptions of the file-write and side-effect classifiers.
- This rereview does not authorize personal-web access or a real model invocation.

## 21. Next action

Correct the shared implementation-workspace validation and bind repair's review receipt to the exact implementation attempt, add focused regressions, then request another narrow M-1 identity rereview. Do not rerun personal-web yet.

VERDICT=REQUEST_CHANGES
CANONICAL_RUN_ID_ATTEMPT_SCHEMA=PASS
RUN_REVIEW_IDENTITY_BINDING=FAIL
RUN_REPAIR_IDENTITY_BINDING=FAIL
REVIEW1_BINDS_ATTEMPT1=PASS
REVIEW2_BINDS_ATTEMPT2=PASS
REVIEW2_BINDS_REPAIR_RUN_ID=PASS
THREE_WAY_MUTATION_MODEL=PASS
NONIGNORED_SIDE_EFFECT_REVIEW=PASS
NONIGNORED_SIDE_EFFECT_REPAIR=PASS
SOURCE_PATCH_ONLY=PASS
SOURCE_PRECEDENCE=PASS
UNEXPECTED_SOURCE_FAILS_CLOSED=PASS
PREFIX_CONFUSION_DENIED=PASS
NO_SIDE_EFFECT_AUTH_FAILS_CLOSED=PASS
REVIEWER_CAN_BROADEN_SIDE_EFFECT_SCOPE=NO
REPAIR_CAN_BROADEN_SIDE_EFFECT_SCOPE=NO
REPAIR_CAN_DROP_SIDE_EFFECT_SCOPE=NO
WORKSPACE_HASH_VERIFICATION=PRESERVED
GENERATED_ONLY_COUNTS_AS_PRODUCT_CHANGE=NO
FILE_WRITE_GUARD=PRESERVED
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
FULL_LOG=M1_CHANGED_PATH_INDEPENDENT_REREVIEW_FULL.log
PERSONAL_WEB_TOUCHED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=FIX_CANONICAL_WORKSPACE_AND_REVIEW_ATTEMPT_BINDING
