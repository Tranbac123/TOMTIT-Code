# TOMTIT Decision Evaluation V0

## Offline boundary

Decision Evaluation V0 reads persisted evidence and evaluates `RETRY` and
`STOP` decisions offline. It does not modify evidence, repositories, Runtime,
Product CLI V0, or routing. Its versioned inputs and outputs are strict and
immutable.

## Three independent decision dimensions

The evaluator preserves three facts rather than collapsing them:

1. `objective_task_need`: `RETRY_NEEDED`, `RETRY_NOT_NEEDED`, or `AMBIGUOUS`;
2. `repair_authorization`: `AUTHORIZED` or `NOT_AUTHORIZED`;
3. `policy_compliance`: whether the Runtime action obeyed authorization.

Authorization never changes objective need. For example, a proven repairable
defect remains `RETRY_NEEDED` when repair is unauthorized. `STOP` is then
policy-compliant but does not match objective task need. Conversely, an
unauthorized `RETRY` can match objective need while violating policy.

`decision_label` remains the task-need/action alignment label: RETRY/needed and
STOP/not-needed are `CORRECT`; opposite determinate pairings are `INCORRECT`;
insufficient ground truth is `AMBIGUOUS`. Policy compliance is never folded into
that label.

## Runtime evidence and independent ground truth

Every canonical evidence reference has typed provenance and is bound to a run,
decision, evaluated attempt, producing attempt, and sequence. Supported roles
distinguish Runtime reviewer output, deterministic acceptance checks, Owner
adjudication, independent evaluation, explicit counterfactual replay, and normal
Runtime outcomes.

A `ReviewerFinding` contains raw Runtime claims, including claimed impact and
severity. These claims cannot establish materiality. A separate
`FindingAdjudication`, backed by accepted canonical provenance, classifies impact
and repairability. Missing independent substantiation normally yields
`AMBIGUOUS`. A grounded failed deterministic acceptance check may establish need
directly without trusting a reviewer opinion.

Severity remains metadata: a cosmetic `BLOCKER` can be non-material, while an
independently grounded `LOW` acceptance violation can be material.

## Counterfactual provenance

Counterfactual evidence is `VERIFIED`, `INFERRED`, or `NOT_AVAILABLE`.
All sources must resolve to canonical evidence and match its run, decision,
attempt, and provenance. `VERIFIED` accepts only deterministic acceptance
evidence, Owner adjudication, independent evaluator adjudication, or explicit
decision-point replay. A dangling reference, mismatched identity, or ordinary
Attempt 2 outcome presented as verified fails closed. Runtime outcomes may be
recorded as inferred evidence but cannot prove Attempt 1 needed retry. V0 never
runs a replay automatically.

## Hindsight isolation and retry value

Objective task need reads the decision-point snapshot plus explicitly verified
adjudication of that same snapshot. Normal later success or failure is excluded.
`PostRetryEvidence` affects only `retry_value`: `IMPROVED`,
`NO_MATERIAL_CHANGE`, `HARMFUL`, or `UNKNOWN`. Thus an unnecessary retry may
improve unrelated quality, and a necessary retry may fail, without rewriting the
original decision assessment.

## Metric populations

Ambiguous task need is excluded from correctness denominators and never counted
as correct or incorrect. Undefined ratios use `value=null` and an explicit
zero-denominator flag.

- retry precision = objective-needed RETRY actions / determinate RETRY actions;
- retry recall = objective-needed RETRY actions / all objective-needed decisions;
- unnecessary retry rate = objective-not-needed RETRY actions / determinate RETRY actions;
- missed retry rate = objective-needed STOP actions / all objective-needed decisions;
- repair success rate = improved retries / retries with known retry value;
- decision accuracy = task/action matches / determinate task-need decisions;
- ambiguous rate = ambiguous task-need decisions / all decisions;
- policy compliance accuracy = policy-compliant actions / all decisions;
- authorization-blocked-needed rate = objectively needed but unauthorized decisions /
  all objective-needed decisions.

Unauthorized-but-needed decisions remain in retry recall and missed-retry
populations. Their separate policy result prevents authorization compliance from
silently rewriting task need.

## Integrity

The read-only loader rejects unknown provenance, dangling references, duplicate
evidence IDs, mismatched run/decision/attempt identity, invalid verified-source
provenance, future evidence smuggled into the decision snapshot, and contradictory
required check/acceptance evidence. Missing independent ground truth that is not
itself contradictory evaluates to `AMBIGUOUS` rather than being invented.
