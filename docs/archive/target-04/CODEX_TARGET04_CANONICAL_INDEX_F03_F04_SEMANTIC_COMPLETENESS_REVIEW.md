# Target-04 F03/F04 Semantic-Completeness Review

Date: 2026-08-17  
Gate: `TARGET_04`  
Work class: `RUNTIME_DESIGN_CORRECTION_REVIEW`  
Verdict: **APPROVE_WITH_NON_BLOCKING_FINDINGS**

## Executive conclusion

The narrow semantic-completeness correction closes every member of the finite, previously proven F03/F04 defect set. The exact relative and traversal role-descriptor reproductions now fail closed. Cached stage and REUC records now require nonzero repository-format object IDs. Porcelain-v2 mode/OID pairs are validated contextually, invalid type-1 whole-record states are rejected, type-2 scores are bounded to the valid percentage range, unmerged stage presence is tied to the exact XY state, and intent-to-add classification occurs only after a fully valid type-1 record.

Independent direct probes, real Git conflict fixtures, all focused regressions, and all final clean serial containing-suite runs pass. F01, F02, and F05 remain closed. `run_loop.py` and `run_repair.py` are byte-unchanged. No Runtime/test, Target-04, tag, remote, or model action was performed by this review.

Two unrelated existing subprocess timing tests each failed once in an initial full-suite pass, then passed immediately in isolation and in fresh clean containing-suite reruns. The final full `run_review.py`, `tests/`, and dogfood-tree runs are clean. These fail-closed timing outcomes are retained below as non-blocking validation observations; they do not contradict the changed F03/F04 logic.

## 1. Lineage and source scope

Resolved lineage:

```text
738ab14b2544007ad7e10ce35288554c64cbacbd
  -> acaf3a8eb35586e0cdf3ff509becde1455072021
  -> 2357603706cc6063295cf05a7cfd858d02aff1a4
```

- Baseline: `738ab14b2544007ad7e10ce35288554c64cbacbd`
- Source: `acaf3a8eb35586e0cdf3ff509becde1455072021`
- Implementation report: `2357603706cc6063295cf05a7cfd858d02aff1a4`
- Actual source parent: `738ab14b2544007ad7e10ce35288554c64cbacbd`
- Actual report parent: `acaf3a8eb35586e0cdf3ff509becde1455072021`
- Lineage: PASS

The source commit modifies only the two authorized files:

- `.tomtit/dogfood/run_review.py`
- `.tomtit/dogfood/test_run_review.py`

The implementation-report commit adds only `TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_REPORT.md`. `run_loop.py` and `run_repair.py` are byte-identical from baseline through the reviewed report commit.

## 2. Prior closed contracts

| Contract | Result | Independent evidence |
|---|---:|---|
| F01 multi-command projection coherence | PASS | Bound remains 3; two consecutive complete non-`None` projections are required; retry remains flat; incomplete state is not reused. |
| F02 canonical authority on every success path | PASS | Non-null canonical equality remains required before raw representation exceptions can authorize preservation. |
| F05 semantic/raw evidence separation | PASS | Canonical semantic identity remains authority evidence and raw descriptor changes remain bounded diagnostics. |
| Coherent protected bootstrap / trusted-content guards | PASS | Focused bootstrap selection passed 9/9. |
| Repair authority / expected Review1 | PASS | Focused authority and expected-ID selection passed 7/7; no repair bootstrap or baseline rollover was introduced. |

## 3. F03 canonical role proof

The new `_canonical_absolute_path_descriptor()` gate is called for both role descriptors before equality or containment is evaluated. It requires the exact `path:` descriptor form, a non-empty absolute path, and no unresolved `..` component. Failure of either operand returns an empty exception set.

Independent direct results:

```text
path:common/worktrees/w vs path:common
  -> empty exception set

path:/common/../elsewhere vs path:/common
  -> empty exception set
```

Positive controls also pass:

- equal ordinary-repository paths allow `git_dir/index` and `git_common_dir/index`;
- a canonical linked-worktree Git directory under the common directory allows `git_dir/index` and only its corresponding `git_common_dir/worktrees/<name>/index` alias;
- a misleading textual prefix produces no exception;
- the distinct common-root `git_common_dir/index` remains strict in linked-worktree tests;
- real linked-worktree representation-only refresh is accepted when canonical identity is preserved.

F03 result: PASS.

## 4. F04 field-context object IDs

The code now separates three concepts:

- `_valid_object_id_shape`: exact repository-format width and lowercase hexadecimal syntax;
- `_valid_real_object_id`: valid shape and not all-zero;
- `_valid_zero_placeholder_object_id`: exactly the repository-width all-zero absence placeholder.

Every record parser uses the context-sensitive predicates rather than accepting shape alone:

- stage records require a real nonzero object ID;
- REUC records require a real nonzero object ID;
- present porcelain HEAD/index or unmerged-stage state requires a supported nonzero mode and real nonzero OID;
- absent porcelain state requires mode `000000` and the exact repository-width zero placeholder.

Independent probes rejected all-zero stage entries at stages 0, 1, 2, and 3, all-zero REUC entries at stages 1, 2, and 3, present-mode/zero-OID pairs, and absent-mode/nonzero-OID pairs. Each invalid single-shot projection returned unavailable.

## 5. Type-1, type-2, intent, and unknown-state semantics

Type 1:

- `XY=..` is rejected because an unchanged path cannot be a changed type-1 record.
- `XY=AA` and the other unmerged-only XY pairs are rejected as type 1.
- HEAD/index mode and object ID are validated as coherent pairs.
- Valid real modified and intent-to-add output remains accepted.

Type 2:

- fixed fields inherit the same contextual mode/OID validation;
- the score letter must match XY.X;
- the numeric similarity percentage is constrained to 0 through 100;
- `R101`, `R999`, and `Xbad` are rejected;
- real `R100` remains accepted with the required NUL-framed original-path continuation.

Intent-to-add:

- type-1 fixed-field validation completes before a path can be classified;
- absent HEAD/index modes paired with nonzero object IDs now fail before classification;
- the existing stage-0, repository-format empty-blob cross-check remains required;
- genuine `git add -N` remains accepted.

Every member of the locked invalid-specimen list makes the single-shot projection unavailable and therefore cannot establish authority through the 2-of-3 consecutive-match algorithm.

## 6. Unmerged stage-presence proof

Independent disposable repositories generated four real conflict classes and confirmed both porcelain-v2 XY and `ls-files --stage` presence:

| XY | Real stages observed | Expected table | Result |
|---|---|---|---:|
| `UU` | 1, 2, 3 | 1, 2, 3 | PASS |
| `AA` | 2, 3 | 2, 3 | PASS |
| `DU` | 1, 3 | 1, 3 | PASS |
| `UD` | 1, 2 | 1, 2 | PASS |

All four real conflict repositories produced a non-`None` single-shot and coherent canonical projection. Negative controls rejected `UU` with all stages absent and `DD` with all stages present.

## 7. Real-Git positive matrix

The requested positive states all remain accepted:

- normal tracked entry;
- assume-unchanged;
- skip-worktree;
- combined assume-unchanged plus skip-worktree, independently confirmed to project both flags;
- real unmerged entries;
- genuine intent-to-add;
- real resolve-undo;
- split-index and sparse-checkout normalization;
- valid linked worktree;
- genuine SHA-256 repository;
- ordinary index representation refresh;
- linked-worktree representation refresh;
- Run-004-class prospective lifecycle.

`REAL_GIT_POSITIVE_MATRIX=PASS` and `RUN004_CLASS_REPRODUCTION=PASS`.

## 8. Validation record

Focused serial validation:

| Selection | Result |
|---|---:|
| New F03 locked cases | 3/PASS |
| New F04 locked semantic cases | 11/PASS |
| Prior F03/F04 regression selection | 57/PASS |
| F01 exact coherence selection | 6/PASS |
| F02 canonical-preservation selection | 9/PASS |
| F05 semantic/raw evidence selection | 4/PASS |
| Real-Git canonical-index selection | 10/PASS |
| Independent combined assume+skip fixture | PASS |
| Independent real UU/AA/DU/UD conflict fixture | PASS |
| Run-004-class lifecycle | 1/PASS |
| Coherent protected bootstrap | 9/PASS |
| Repair authority / expected Review1 | 7/PASS |

Final clean serial validation:

| Suite/check | Result |
|---|---:|
| `.tomtit/dogfood/test_run_review.py` | 243/PASS |
| `.tomtit/dogfood/test_run_loop.py` | 92/PASS |
| `.tomtit/dogfood/test_run_repair.py` | 129/PASS |
| `tests/adapters/test_claude_code_cli.py` | 436/PASS |
| `tests/` | 985/PASS |
| `.tomtit/dogfood/` | 464/PASS |
| compileall for changed Runtime/test files | PASS |
| Ruff for changed Runtime/test files | PASS |
| `git diff --check`, worktree and baseline-to-report range | PASS |

Observed first-pass timing failures, retained rather than erased:

1. The first full `test_run_review.py` run ended `1 failed, 242 passed`. `test_deterministic_check_delayed_descendant_is_quiesced` returned the deliberately fail-closed `PREFLIGHT_FAILURE:SUBPROCESS_QUIESCENCE_UNVERIFIED` because process-group state became indeterminate. It passed immediately in isolation, the fresh full 243-test containing-suite rerun passed, and the final 464-test dogfood tree passed.
2. The first full `tests/` run ended `1 failed, 984 passed`. `test_delayed_descendant_write_is_prevented_before_final_snapshot` observed a fail-closed adapter result rather than `SUCCEEDED`. It passed immediately in isolation, the complete 436-test adapter suite passed, and a fresh full 985-test `tests/` rerun passed.

Neither failing test exercises a file changed by this source commit, and neither failure reproduced in a clean containing-suite rerun. They are non-blocking timing observations, not F03/F04 semantic failures.

## 9. Preservation and disposition

- Reviewed Runtime/test bytes modified by Codex: NO
- Source bytes unchanged from reviewed HEAD before report creation: YES
- Target-04 modified: NO
- Target-04A reruns: 0
- Target-04B started: NO
- Real product models invoked: NO
- Tags created: NO
- Pushes/remotes modified: NO

Required results:

```text
STATUS=APPROVED
VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS

SOURCE_COMMIT=acaf3a8eb35586e0cdf3ff509becde1455072021
IMPLEMENTATION_REPORT_COMMIT=2357603706cc6063295cf05a7cfd858d02aff1a4

ACTUAL_SOURCE_PARENT=738ab14b2544007ad7e10ce35288554c64cbacbd
ACTUAL_REPORT_PARENT=acaf3a8eb35586e0cdf3ff509becde1455072021
LINEAGE=PASS

F01_REMAINS_CLOSED=PASS
F02_REMAINS_CLOSED=PASS
F03_LINKED_WORKTREE_ROLE_HANDLING=PASS
F04_COMPLETE_INDEX_OUTPUT_VALIDATION=PASS
F05_REMAINS_CLOSED=PASS

F03_RELATIVE_ROLE_DESCRIPTOR_REJECTED=PASS
F03_TRAVERSAL_ROLE_DESCRIPTOR_REJECTED=PASS
F03_ROLE_PROOF_BEFORE_EXCEPTION=PASS
CURRENT_WORKTREE_INDEX_ALIASES_CORRECT=PASS
DISTINCT_COMMON_INDEX_STRICT=PASS

STAGE_ZERO_OID_REJECTED=PASS
REUC_ZERO_OID_REJECTED=PASS
FIELD_CONTEXT_OID_VALIDATION=PASS

STATUS_MODE_OID_RELATIONSHIP_VALIDATED=PASS
STATUS_TYPE1_RECORD_SEMANTICS_VALIDATED=PASS
STATUS_TYPE2_RECORD_SEMANTICS_VALIDATED=PASS
STATUS_U_STAGE_PATTERN_VALIDATED=PASS
TYPE2_SCORE_RANGE_VALIDATED=PASS

INTENT_REQUIRES_FULLY_VALID_TYPE1=PASS
INTENT_STAGE_CROSS_CHECKED=PASS
UNKNOWN_INDEX_STATE_BLOCKS=PASS

MAX_CANONICAL_INDEX_OBSERVATIONS=3
TWO_CONSECUTIVE_MATCHES=PASS
NO_NESTED_RETRY=PASS

REAL_GIT_POSITIVE_MATRIX=PASS
RUN004_CLASS_REPRODUCTION=PASS

COHERENT_BOOTSTRAP_UNCHANGED=PASS
REPAIR_AUTHORITY_UNCHANGED=PASS

RUN_REVIEW_SUITE=243/PASS
RUN_LOOP_SUITE=92/PASS
RUN_REPAIR_SUITE=129/PASS
CLAUDE_ADAPTER_SUITE=436/PASS
FULL_TESTS_TREE=985/PASS
DOGFOOD_TREE=464/PASS

COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=OWNER_BINDS_RUNTIME_FOR_TARGET04A_RUN005
```

The review-report commit is intentionally created after this content is finalized; its hash is reported in the review return rather than embedded self-referentially in this file.
