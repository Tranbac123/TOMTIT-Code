# Decision Evaluation V0 Implementation Receipt

- Base tag: `tomtit-cli-v0-v1`
- Source/tests commit: `971fa7dfab996a85fe212ea2b3219eb90e9ee386`
- Additive-only: PASS; no pre-existing file was modified or deleted.
- Objective task need is separate from repair authorization; runtime action is separate from policy compliance.
- Runtime reviewer evidence is separate from independent ground truth. A reviewer-only case evaluates `AMBIGUOUS`.
- `VERIFIED` counterfactual evidence validates canonical provenance, run/decision identity, evaluated attempt, producing attempt, and source type.
- Hindsight isolation is preserved. `retry_value` is separate from original decision correctness.
- Objective metrics use determinate objective need: retry precision is true retries over determinate retries; retry recall is true retries over objectively needed decisions; unnecessary retry rate is false retries over determinate retries; missed retry rate is missed retries over objectively needed decisions; decision accuracy is matching actions over determinate decisions; ambiguous rate is ambiguous decisions over all decisions.
- Repair success rate is improved retries over retries with known retry value. Policy compliance accuracy is compliant actions over all decisions. Authorization-blocked-needed rate is unauthorized but objectively needed decisions over all objectively needed decisions.
- Unauthorized-but-needed decisions remain in objective-needed metric populations.
- Verification: 43 focused tests passed; the full regression passed; compileall, Ruff, and diff checks passed.
- Real model invocations: 0.
- Frozen Runtime unchanged; Product CLI V0 unchanged.
- Independent semantic verdict: APPROVE.

STATUS=READY_FOR_FREEZE
BASE_TAG=tomtit-cli-v0-v1
DECISION_EVAL_SOURCE_TESTS_COMMIT=971fa7dfab996a85fe212ea2b3219eb90e9ee386
ADDITIVE_ONLY=PASS
PRE_EXISTING_FILES_MODIFIED=0
PRE_EXISTING_FILES_DELETED=0
INDEPENDENT_REVIEW_VERDICT=APPROVE
