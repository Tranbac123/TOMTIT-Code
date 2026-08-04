# Dogfood Experiment Protocol V0

**Status:** Accepted for Implementation
**Version:** 0.2
**Product:** TOMTIT
**Purpose:** Compare direct coding-agent usage with TOMTIT coordination while preserving information parity and separating contract inference from orchestration value.

---

## 1. Hypotheses

Primary hypothesis:

> TOMTIT preserves or improves accepted result quality while reducing user coordination effort compared with using the same coding agent directly.

Secondary hypotheses:

1. TOMTIT can infer a usable TaskContract from the same request shown to the direct-agent baseline.
2. Deterministic evidence adds value beyond guided self-check.
3. LLM review adds measurable marginal value only for some task classes.

---

## 2. Comparison Arms

### Baseline

```text
Identical agent-visible request
→ coding agent directly
→ user coordinates, checks, and accepts or edits result
```

### Treatment

```text
Identical agent-visible request
→ TOMTIT infers TaskContract
→ same coding agent
→ bounded execution and evidence collection
→ user accepts, edits, or rejects result
```

The same worker/provider and version must be used in both arms for a paired comparison.

---

## 3. Information Parity

Each experiment uses two separate artifacts.

### 3.1 Agent-visible request

This is the exact request shown to both arms.

It must remain identical, except for unavoidable surface-specific wrapper text.

```yaml
agent_visible_request:
  text: string
```

The baseline must not be given TOMTIT-only structured fields.

The treatment must not receive extra hidden requirements beyond the agent-visible request.

### 3.2 Hidden evaluation contract

This contract is used only by the evaluator to score both final patches.

```yaml
hidden_evaluation_contract:
  goal:
  acceptance_criteria:
  allowed_scope:
  forbidden_actions:
  required_checks:
```

The hidden contract must not be injected into either coding agent during the run.

TOMTIT may infer its own internal TaskContract from the agent-visible request. After the run, compare it with the hidden evaluation contract and record:

```yaml
contract_inference_accuracy:
  goal_alignment:
  criteria_recall:
  scope_alignment:
  required_check_alignment:
  material_misunderstanding: true | false
```

This separates:

- task-understanding quality;
- orchestration value;
- final patch quality.

---

## 4. Fixed Variables

For each valid comparison, keep fixed:

- repository fixture;
- base commit;
- agent-visible request;
- hidden evaluation contract;
- worker/provider;
- provider CLI version;
- model configuration when observable;
- time limit;
- owner evaluation rubric.

Use separate disposable workspaces created from the same base commit.

Do not change both the worker and orchestration workflow in one comparison.

---

## 5. Carryover and Order Bias

Running the same task twice can create carryover through human memory.

Preferred controls, in order:

### 5.1 Equivalent-task pairs

Use two tasks with comparable complexity and the same hidden rubric:

```text
Task A1 → Baseline
Task A2 → TOMTIT
```

Reverse the assignment in the next pair.

### 5.2 Blinded patch review

Where practical:

- the person running the agents is not the final evaluator; or
- final patches are assigned anonymous IDs;
- the evaluator does not know which arm produced each patch.

### 5.3 Same-task paired runs

When the exact same task is used:

- alternate run order;
- use a fresh provider session where supported;
- record discoveries exposed by the first run;
- record:

```yaml
carryover_risk: low | medium | high
```

Pairs with `carryover_risk: high` must not drive the primary conclusion.

Suggested alternation:

```text
Pair 1: Baseline → TOMTIT
Pair 2: TOMTIT → Baseline
Pair 3: Baseline → TOMTIT
...
```

---

## 6. Task Cohort

Start with 15–30 comparisons across:

- small bug fixes;
- test additions;
- narrow refactors;
- small features;
- multi-file features;
- scope-sensitive changes.

A personal profile website may be used as the first controlled fixture.

Do not use production repositories, sensitive code, or production credentials during early dogfood.

### 6.1 Initial verification allocation

Because 15–30 comparisons are too few for a dense matrix:

- Mode B is the default TOMTIT treatment.
- Mode A is used for 3–5 lightweight baseline tasks.
- Mode C is used for a 5–10 task medium-risk subset.
- Category breakdowns are descriptive.
- Do not claim one mode is superior from a subgroup with fewer than approximately five comparable tasks.

---

## 7. Verification Modes

### Mode A

Guided self-check. Requires owner acceptance before canonical completion.

### Mode B

Deterministic checks. Default treatment mode.

### Mode C

Deterministic checks plus LLM review.

Mode C is not assumed better than Mode B. Measure its marginal benefit and additional cost or latency.

---

## 8. Metrics

### 8.1 Primary metrics

1. User result:
   - accepted;
   - edited;
   - rejected;
   - unknown.
2. Accepted without manual code edit.
3. Human intervention time.
4. Scope violations.
5. Required-check result.
6. Elapsed time.
7. Escaped-defect status.
8. Cost when observable.

### 8.2 Diagnostic interaction metrics

Record separately:

```yaml
agent_execution_rounds:
user_reprompts:
user_clarifications:
manual_commands:
human_intervention_seconds:
```

Do not compare TOMTIT attempts directly with direct-agent re-prompts as equivalent units.

Primary conclusions must rely on:

- acceptance;
- human intervention;
- scope violations;
- elapsed time;
- escaped defects;
- cost when available.

Cost fields remain null when unavailable.

---

## 9. Human Intervention Measurement

Preferred sources:

1. explicit user report;
2. interaction timestamps;
3. unavailable.

Record:

```yaml
human_intervention_seconds:
human_intervention_source:
  explicit_user_report | interaction_timestamps | unavailable
```

Do not infer precise manual effort from wall-clock time alone.

---

## 10. Owner Evaluation Rubric

Use the hidden evaluation contract and the same rubric for both arms:

```yaml
goal_satisfied: yes | partial | no

criteria:
  - criterion_id:
    status: satisfied | unsatisfied | unresolved
    evidence:

scope_respected: yes | no
required_checks_passed: yes | no | unknown
manual_edit_required: yes | no
remaining_blocking_issue: yes | no
overall_result: accepted | edited | rejected
```

Evaluation is based on the final patch and observed checks, not provider self-report.

---

## 11. Escaped-Defect Lifecycle

Escaped defects are updated through append-only events:

```text
ESCAPED_DEFECT_REPORTED
ESCAPED_DEFECT_CLEARED
OUTCOME_SNAPSHOT_GENERATED
```

Immediately after execution:

```yaml
escaped_defect_status: unknown
```

A later derived outcome snapshot may change the current status to:

```text
reported
cleared
none_reported
```

Historical events are never overwritten.

---

## 12. Invalid or Excluded Comparisons

Mark a comparison invalid or excluded when:

- base commits differ;
- worker/provider changes;
- agent-visible request changes materially;
- hidden evaluation contract changes after either arm starts;
- provider quota interrupts only one arm;
- repository fixture is contaminated;
- one arm receives extra task information;
- TOMTIT receives hidden evaluator requirements;
- carryover risk is high enough to invalidate the comparison.

Keep invalid records; do not silently delete them.

---

## 13. Analysis

After at least 10 comparable results, report:

- acceptance rate by arm;
- accepted-without-edit rate;
- median human intervention;
- scope violation rate;
- required-check pass rate;
- median elapsed time;
- cost per accepted task when available;
- contract inference accuracy;
- carryover-risk distribution;
- Mode B versus Mode C marginal value for the eligible subset.

Do not change routing or policy from a single comparison.

Do not make strong category or verification-mode conclusions from subgroups with fewer than approximately five comparable results.

---

## 14. Initial GO Criteria

Continue toward a small pilot when:

- TOMTIT acceptance is not lower than baseline;
- TOMTIT human intervention is not higher than baseline;
- no increase in scope violations;
- no uncontrolled attempt loops;
- every TOMTIT run has a complete trace and terminal report;
- contract inference accuracy is measurable;
- failure causes can be attributed to contract, worker, verification, runtime, or policy.

These are initial product-learning thresholds, not permanent production SLAs.
