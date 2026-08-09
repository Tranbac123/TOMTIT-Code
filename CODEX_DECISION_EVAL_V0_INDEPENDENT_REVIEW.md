# Decision Evaluation V0 — Independent Semantic Correctness Review

## Verdict

**BLOCKER**

The implementation is cleanly additive, operationally offline/read-only, and
its focused and full test suites pass. It is not yet a valid independent
measurement of TOMTIT decision quality. Two required semantic boundaries are
absent: objective task retry necessity is collapsed into repair authorization,
and the runtime reviewer's own finding can become the evaluator's ground truth.
The counterfactual contract does not repair that independence failure.

## Scope and baseline

- Review base: `tomtit-cli-v0-v1`
- Base commit: `226a2e04fa1f8302c509464cf0ffe7505d148f0b`
- Review-time `HEAD`: `226a2e04fa1f8302c509464cf0ffe7505d148f0b`
- `git diff --name-status`: empty
- `git diff --cached --name-status`: empty
- Baseline-tree lookup for the design, evaluator package, and focused tests:
  empty; all are new paths
- Pre-existing files modified: 0
- Pre-existing files deleted: 0
- Frozen runtime and Product CLI V0: unchanged

The implementation consists only of the new design document,
`src/tomtit_decision_eval_v0/`, and `tests/decision_eval_v0/`. This review added
only this report and the requested full log; it did not edit implementation,
test, runtime, CLI, or personal-web files.

## Blocking findings

### B-01 — Objective retry necessity is incorrectly gated by authorization

**Severity: BLOCKER**

The design defines a retry as needed only when repair is authorized
(`DECISION_EVALUATION_V0_DESIGN.md:23-29`). The implementation repeats that
conflation: after it has established a repairable problem, it returns
`RETRY_NEEDED` only when `repair_authorized` is true; otherwise it returns
`RETRY_NOT_NEEDED` (`src/tomtit_decision_eval_v0/evaluator.py:130-161`). The
focused test at `tests/decision_eval_v0/test_evaluator.py:224-241` explicitly
codifies this behavior.

The adversarial case used independent deterministic evidence, avoiding any
reviewer-ground-truth ambiguity:

| Fact | Value |
|---|---|
| Required deterministic check | `FAIL` |
| Required acceptance criterion | `UNSATISFIED` |
| Problem repairability | `YES` |
| `repair_authorized` | `false` |
| TOMTIT action | `STOP` |
| Required conceptual result | task retry needed = yes; retry authorized = no |
| Actual evaluator result | `RETRY_NOT_NEEDED`; `CORRECT` |

This destroys the distinction between a fact about task completion and a policy
constraint on runtime action. STOP scenario A (satisfied) is correctly labeled;
scenario B (repairable and authorized) is correctly recognized as a missed
retry; scenario C (repairable but unauthorized) is incorrectly collapsed into
"no retry needed."

Downstream effects reproduced by the probe:

- The objectively needed repair is omitted from the retry-recall and
  missed-retry denominators; both become zero/undefined for a population
  containing only this case.
- The same STOP is counted as `correct_stop`, producing decision accuracy 1/1.
- A RETRY in the equivalent state would be put in the false/unnecessary-retry
  population because the evaluator has changed the underlying task fact.
- Future learning or routing would learn that policy-denied work was already
  unnecessary, preventing it from distinguishing task difficulty/need from
  budget, ownership, or authorization constraints.

Required correction: represent objective task need and repair authorization as
orthogonal facts, and evaluate runtime policy compliance separately. Decision
quality metrics about need must use the objective task-need fact.

### B-02 — Runtime reviewer evidence can self-validate the retry decision

**Severity: BLOCKER**

`EvidenceReference.kind` is an unconstrained string and carries no trust role or
adjudication provenance (`src/tomtit_decision_eval_v0/models.py:122-128`). A
`ReviewerFinding` supplies its own impact, repairability, and criterion links
(`models.py:156-163`). The classifier then treats any positive self-declared
impact, or merely a criterion ID, as `MATERIAL`
(`src/tomtit_decision_eval_v0/evaluator.py:24-32`). A repairable material finding
enters the retry-needed branch directly (`evaluator.py:62-88,130-154`).

The explicit independence-failure probe contained:

- one runtime reviewer `MEDIUM` finding;
- no deterministic failure;
- an acceptance criterion whose status was `UNKNOWN`;
- no Owner/evaluator label, replay, or independent adjudication;
- `counterfactual_evidence=NOT_AVAILABLE`.

Actual result: `RETRY_NEEDED`, `CORRECT`, with
`MATERIAL_REPAIRABLE_FINDING`. The evaluator therefore implements the circular
chain the review was required to prevent:

`runtime reviewer says retry → TOMTIT retries → evaluator trusts the same
reviewer assertion → TOMTIT receives CORRECT`.

The operational code boundary is separate from Runtime, but the evidence model
is not epistemically independent. In the probed state, `AMBIGUOUS` is the
defensible result. Strong `RETRY_NEEDED` requires an independently grounded
acceptance fact, deterministic check, replay, Owner label, or adjudication whose
provenance is distinct from the runtime decision source.

### B-03 — `VERIFIED` counterfactual evidence is only asserted, not verified

**Severity: BLOCKER**

For both `VERIFIED` and `INFERRED`, validation only requires a non-empty source
tuple and matching run/decision IDs
(`src/tomtit_decision_eval_v0/models.py:340-364`). It does not bind
`CounterfactualSource.evidence_ref` to canonical evidence, validate the claimed
source artifact, or impose a stronger rule for `VERIFIED` than `INFERRED`.

The probe successfully constructed `VERIFIED` evidence whose only source had
`source_attempt=2` and `evidence_ref="does-not-exist"`. More importantly,
`evaluate_decision` does not consult the source or the classification when
forming the independent judgment; it merely copies the classification to the
result (`src/tomtit_decision_eval_v0/evaluator.py:275-304`). Thus these fields
cannot prevent finding B-02 and cannot establish genuinely stronger ground
truth.

The current temporal separation does correctly prevent Attempt 2 success or a
final failure from directly rewriting the decision label. The defect is the
unvalidated strength/provenance of the claimed counterfactual source, not the
post-retry value separation.

## Confirmed semantics

### Hindsight isolation — PASS

- Attempt 1 satisfied acceptance, RETRY was unnecessary, Attempt 2 improved
  unrelated quality, and the final outcome was `COMPLETED`: the original label
  remained `INCORRECT`; `retry_value` was `IMPROVED`.
- Attempt 1 had a proven repairable defect, RETRY was appropriate, Attempt 2 did
  not repair it, and the final outcome was `FAILED`: the original label remained
  `CORRECT`; `retry_value` was `NO_MATERIAL_CHANGE`.

`PostRetryEvidence` is structurally separate and `_retry_value` is computed
after the decision judgment. Decision correctness and retry value are not
mechanically conflated.

### Severity independence — partial pass; overall materiality semantics FAIL

A `BLOCKER` cosmetic finding with all impact claims `NO` classified as
`NON_MATERIAL`, while a `LOW` finding with an explicit required-behavior impact
classified as `MATERIAL`. Severity is correctly ignored. Overall materiality
semantics still fail because those impact/criterion facts may be copied directly
from the runtime reviewer without independent substantiation (finding B-02).

### Operational architecture boundary — PASS

The evaluator is an additive, strict, versioned Python package. The JSON loader
only reads, canonical records are frozen, and the evaluator has no Runtime/CLI
integration or model invocation. Its epistemic independence fails, as described
above, but its filesystem/runtime boundary is appropriately offline and
read-only.

## Metric verification

For a synthetic population with one true retry, one false retry, one missed
retry, one correct stop, and one ambiguous decision, the raw formulas produced:

| Metric | Manual value | Evaluator value | Formula behavior |
|---|---:|---:|---|
| retry precision | 1 / 2 = 0.5 | 0.5 | arithmetic correct |
| retry recall | 1 / 2 = 0.5 | 0.5 | arithmetic correct |
| unnecessary retry rate | 1 / 2 = 0.5 | 0.5 | arithmetic correct |
| missed retry rate | 1 / 2 = 0.5 | 0.5 | arithmetic correct |
| decision accuracy | 2 / 4 = 0.5 | 0.5 | arithmetic correct |
| ambiguous rate | 1 / 5 = 0.2 | 0.2 | arithmetic correct |

Zero denominators are explicit (`value=null`, `zero_denominator=true`), and
ambiguous records are not silently counted as correct. Repair success uses only
RETRY records with known retry value and remains structurally separate from
decision correctness.

The terminal metric verdicts are nevertheless FAIL because their populations
are semantically corrupted before aggregation:

- B-01 removes an unauthorized-but-objectively-needed STOP from the needed and
  missed populations and counts it as a correct stop.
- B-02 can turn a self-asserted runtime reviewer opinion into a true retry,
  inflating precision/recall/accuracy and depressing unnecessary-retry rate.
- The reviewer-only case that should be ambiguous is instead included as a
  determinate success.

In the single-case B-01 population, the evaluator reproduced the failure
directly: `correct_stop=1`, `missed_retry=0`, accuracy `1/1`, and zero
denominators for retry recall and missed-retry rate.

## Verification results

| Check | Result |
|---|---|
| Focused Decision Evaluation tests | PASS (exit 0) |
| Full repository regression | PASS (exit 0) |
| `compileall` | PASS |
| `ruff check .` | PASS |
| `git diff --check` | PASS |
| Real model invocations | 0 |

The focused command first aborted inside the execution sandbox before test
collection because OpenMP could not open shared memory. The exact command was
rerun outside that process sandbox and passed. Both outputs are retained in
`CODEX_DECISION_EVAL_V0_INDEPENDENT_REVIEW_FULL.log`.

## Terminal result

```text
VERDICT=BLOCKER

ADDITIVE_ONLY=PASS
PRE_EXISTING_FILES_MODIFIED=0
PRE_EXISTING_FILES_DELETED=0

NECESSITY_AUTHORIZATION_SEPARATED=FAIL
UNAUTHORIZED_BUT_NEEDED_CASE=FAIL

RUNTIME_EVIDENCE_VS_GROUND_TRUTH=FAIL
CIRCULAR_EVALUATION_PREVENTED=FAIL

HINDSIGHT_ISOLATION=PASS
STOP_SEMANTICS=FAIL
MATERIALITY_SEMANTICS=FAIL
COUNTERFACTUAL_SEMANTICS=FAIL

RETRY_PRECISION_MATH=FAIL
RETRY_RECALL_MATH=FAIL
MISSED_RETRY_MATH=FAIL
UNNECESSARY_RETRY_MATH=FAIL
DECISION_ACCURACY_MATH=FAIL
AMBIGUOUS_HANDLING=FAIL

FOCUSED_TESTS=PASS
FULL_REGRESSION=PASS

REAL_MODEL_INVOCATIONS=0
FROZEN_RUNTIME_MODIFIED=NO
CLI_V0_MODIFIED=NO
COMMIT_CREATED=NO

NEXT_ACTION=Separate objective task necessity from authorization and require independent adjudication provenance before computing decision metrics.
```
