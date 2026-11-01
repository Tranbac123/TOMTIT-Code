# Decision Evaluation V0 — Narrow Independent Semantic Rereview

## Verdict

**APPROVE**

The corrected implementation resolves B-01, B-02, and B-03. Independent
in-memory probes reproduced the required task-need, policy, provenance,
hindsight, integrity, and metric behavior. No remaining blocker was found
within the deliberately narrow rereview scope.

## Baseline and additive-only boundary

- Base: `tomtit-cli-v0-v1`
- Base commit: `226a2e04fa1f8302c509464cf0ffe7505d148f0b`
- Rereview-time `HEAD`: `226a2e04fa1f8302c509464cf0ffe7505d148f0b`
- Tracked and staged diffs: empty
- Baseline-tree lookup for the design, package, and focused test paths: empty
- Pre-existing files modified: 0
- Pre-existing files deleted: 0
- Frozen Runtime and Product CLI V0: unchanged

Decision Evaluation V0 remains additive-only. This rereview created only this
report and the requested ignored `.log` artifact; it did not edit design,
implementation, tests, Runtime, CLI, personal-web, or evidence.

## B-01 — Objective need versus authorization

**RESOLVED**

`objective_task_need`, `repair_authorization`, `decision_label`, and
`policy_compliance` are separate result fields. Objective need is computed
without reading authorization (`src/tomtit_decision_eval_v0/evaluator.py:105-283`);
authorization and policy are projected separately
(`evaluator.py:303-325`).

Independent results:

| Case | Action | Authorization | Objective need | Task/action label | Policy |
|---|---|---|---|---|---|
| A | RETRY | AUTHORIZED | RETRY_NEEDED | CORRECT | COMPLIANT |
| B | STOP | AUTHORIZED | RETRY_NEEDED | INCORRECT | COMPLIANT |
| C | STOP | NOT_AUTHORIZED | RETRY_NEEDED | INCORRECT | COMPLIANT |
| D | RETRY | NOT_AUTHORIZED | RETRY_NEEDED | CORRECT | NON_COMPLIANT |

The implementation's exact policy semantics are authorization-specific: an
unauthorized RETRY is non-compliant; STOP is compliant with that authorization
policy. Policy compliance does not rewrite task need or task/action alignment.
Most importantly, case C remains objectively needed and case D can match task
need while independently violating policy.

## B-02 — Circular Runtime reviewer ground truth

**RESOLVED**

The evidence model distinguishes these provenance roles explicitly:

- `RUNTIME_REVIEWER`
- `DETERMINISTIC_ACCEPTANCE_CHECK`
- `OWNER_ADJUDICATION`
- `INDEPENDENT_EVALUATOR`
- `COUNTERFACTUAL_REPLAY`
- `RUNTIME_OUTCOME`

`ReviewerFinding` contains only claimed impact/repairability. Materiality is
computed from a separate `FindingAdjudication`, whose evidence must have trusted
provenance (`models.py:203-236,507-524`). A circular adjudication citing the
Runtime reviewer's own evidence was rejected with `finding adjudication lacks
trusted provenance`.

Independent results:

| Grounding | Result |
|---|---|
| Runtime reviewer only; check and acceptance UNKNOWN | AMBIGUOUS |
| Same reviewer plus required deterministic FAIL | RETRY_NEEDED |
| Same reviewer plus VERIFIED independent-evaluator adjudication | RETRY_NEEDED |

The reviewer-only result also contains
`RUNTIME_REVIEWER_NOT_INDEPENDENTLY_SUBSTANTIATED`, does not mark a material
finding present, and cannot enter determinate correctness populations.

## Materiality provenance

**PASS**

- `BLOCKER` reviewer severity plus independently adjudicated cosmetic impact:
  `NON_MATERIAL`.
- `LOW` reviewer severity plus independently adjudicated acceptance violation:
  `MATERIAL`.
- Claimed Runtime correctness impact without independent substantiation:
  objective need remains `AMBIGUOUS`.

Severity and raw Runtime claims are therefore not materiality ground truth.

## B-03 — VERIFIED counterfactual evidence

**RESOLVED**

All counterfactual sources resolve to a canonical evidence ID and must match the
run, decision, evaluated attempt, producing attempt, source type, and canonical
provenance (`models.py:431-493`). VERIFIED accepts only deterministic acceptance,
Owner adjudication, independent evaluator, or explicit replay provenance.

Independent results:

| Probe | Result |
|---|---|
| Dangling evidence reference | Rejected |
| Wrong run | Rejected |
| Wrong decision | Rejected |
| Wrong evaluated attempt | Rejected |
| Wrong producing/source attempt | Rejected |
| Ordinary Attempt 2 Runtime outcome as VERIFIED | Rejected |
| Canonical deterministic source | VERIFIED accepted |
| Canonical Owner adjudication | VERIFIED accepted |
| Canonical independent-evaluator adjudication | VERIFIED accepted |
| Canonical explicit replay | VERIFIED accepted |

The same ordinary Runtime outcome may be recorded as `INFERRED`, but it left the
reviewer-only objective need `AMBIGUOUS`. VERIFIED is therefore materially
stronger than INFERRED and is no longer a free caller label.

## Hindsight isolation

**PASS**

- Attempt 1 satisfied acceptance; unnecessary RETRY; unrelated Attempt 2
  improvement; final `COMPLETED`: objective need stayed `RETRY_NOT_NEEDED`, the
  decision stayed `INCORRECT`, and retry value was `IMPROVED`.
- Attempt 1 required repair; RETRY; Attempt 2 did not repair; final `FAILED`:
  objective need stayed `RETRY_NEEDED`, the decision stayed `CORRECT`, and retry
  value was `NO_MATERIAL_CHANGE`.

Post-retry observations affect retry value only.

## Metric population audit

The requested six-case population contained:

1. needed + authorized + RETRY;
2. needed + authorized + STOP;
3. needed + unauthorized + STOP;
4. not-needed + RETRY;
5. not-needed + STOP;
6. ambiguous reviewer-only + RETRY.

Resulting counts were: total 6, determinate 5, ambiguous 1, true retry 1,
false retry 1, missed retry 2, correct stop 1, policy compliant 6, policy
violations 0, and authorization-blocked-needed 1.

| Metric | Manual | Evaluator | Result |
|---|---:|---:|---|
| retry precision | 1/2 | 0.5 | PASS |
| retry recall | 1/3 | 0.3333333333333333 | PASS |
| unnecessary retry rate | 1/2 | 0.5 | PASS |
| missed retry rate | 2/3 | 0.6666666666666666 | PASS |
| decision accuracy | 2/5 | 0.4 | PASS |
| ambiguous rate | 1/6 | 0.16666666666666666 | PASS |
| policy compliance accuracy | 6/6 | 1.0 | PASS |
| authorization-blocked-needed rate | 1/3 | 0.3333333333333333 | PASS |

Case 3 remains in the three-decision objective-needed denominator and is one of
the two missed retries. Case 6 is excluded from determinate correctness and
contributes to ambiguous rate. Policy compliance does not affect objective-need
membership.

For a separate two-RETRY population, an unnecessary retry with post-value
`IMPROVED` and a necessary retry with `NO_MATERIAL_CHANGE` produced repair
success 1/2. This confirms repair success follows only known post-retry value,
not original need or decision correctness. Every metric returns explicit
`value=null` and `zero_denominator=true` for an empty denominator.

## Integrity and architecture

**PASS**

Independent probes rejected unknown provenance, dangling references, duplicate
canonical IDs, mismatched run/decision/evaluated/producing-attempt identity,
future decision-point evidence, contradictory required check/acceptance facts,
invalid VERIFIED provenance, and circular reviewer adjudication. Missing
independent ground truth produced `AMBIGUOUS`.

The package is strict, immutable, and versioned (`decision-eval-input.v0.1`,
`decision-eval.v0.2`). Its imports are limited to the standard library,
Pydantic, and package-local modules. The loader only reads the supplied JSON
artifact. There is no Runtime, CLI, routing, provider, network, or model
integration.

## Verification

| Check | Result |
|---|---|
| Focused Decision Evaluation tests | PASS (43 indicators; exit 0) |
| Full repository regression | PASS (exit 0) |
| `compileall` | PASS |
| `ruff check .` | PASS |
| `git diff --check` | PASS |
| Real model invocations | 0 |

Both pytest commands first aborted before collection inside the execution
sandbox because OpenMP could not open shared memory. Those first-run outputs are
preserved in the full log. Each exact pytest command was then rerun outside the
process sandbox and passed; no code was changed to accommodate the environment.
The compile cache was directed to `/private/tmp` to preserve repository state.

## Terminal result

```text
VERDICT=APPROVE

B01_NECESSITY_AUTHORIZATION=RESOLVED
OBJECTIVE_NEED_SEPARATED=YES
UNAUTHORIZED_NEEDED_REMAINS_NEEDED=YES
POLICY_COMPLIANCE_SEPARATE=YES

B02_CIRCULAR_GROUND_TRUTH=RESOLVED
RUNTIME_REVIEWER_ONLY_CASE=AMBIGUOUS
DETERMINISTIC_GROUNDING=PASS
INDEPENDENT_ADJUDICATION_GROUNDING=PASS

B03_COUNTERFACTUAL=RESOLVED
DANGLING_REF_REJECTED=YES
IDENTITY_MISMATCH_REJECTED=YES
INVALID_PROVENANCE_REJECTED=YES
VALID_VERIFIED_SOURCE_ACCEPTED=YES

HINDSIGHT_ISOLATION=PASS
MATERIALITY_PROVENANCE=PASS
AMBIGUOUS_FAIL_CLOSED=PASS

RETRY_PRECISION=PASS
RETRY_RECALL=PASS
UNNECESSARY_RETRY_RATE=PASS
MISSED_RETRY_RATE=PASS
REPAIR_SUCCESS_RATE=PASS
DECISION_ACCURACY=PASS
AMBIGUOUS_RATE=PASS
POLICY_COMPLIANCE_ACCURACY=PASS
AUTHORIZATION_BLOCKED_NEEDED_RATE=PASS

FOCUSED_TESTS=PASS
FULL_REGRESSION=PASS

ADDITIVE_ONLY=PASS
PRE_EXISTING_FILES_MODIFIED=0
PRE_EXISTING_FILES_DELETED=0

REAL_MODEL_INVOCATIONS=0
FROZEN_RUNTIME_MODIFIED=NO
CLI_V0_MODIFIED=NO
COMMIT_CREATED=NO

NEXT_ACTION=Proceed with Owner acceptance of corrected Decision Evaluation V0.
```
