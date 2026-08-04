# TOMTIT Coordinator — Official Build and AI Coding Process V1

## 0. Document governance

```yaml
document_name: TOMTIT Coordinator — Official Build and AI Coding Process
document_version: V1
document_status: FINAL_CANDIDATE_FOR_OWNER_APPROVAL

reviewed_against:
  - SYSTEM.md
  - MVP Execution Policy V0.3
  - Dogfood Experiment Protocol V0.2
  - TOMTIT Coordinator — Evaluation Contract V1
```

Owner approval is recorded only in external gate evidence:

```yaml
contract_status: APPROVED
approved_version: V1
approved_by: OWNER
approved_at: <timestamp>
document_sha256: <SHA-256 of exact finalized document bytes>
```

The document must not contain its own approval hash.

After the Owner approves the document SHA-256:

- the document bytes must not be modified;
- any later modification creates a new document candidate;
- the new candidate requires a new hash and new Owner approval.

If an authority source changes version or semantics, this document requires a drift review before further use.

---

# 1. Operating principle

The process exists to build the product. It must not turn prompts, reports, commits and reviews into the product itself.

A task may be opened only when it does at least one of the following:

```text
1. Moves TOMTIT closer to controlling a real coding agent.

2. Closes a reproduced core-correctness defect.
```

The following are deferred by default:

- LOW findings;
- cleanup;
- generalized abstractions;
- production hardening;
- features without expected product evidence;
- future capabilities not observed through a real adapter;
- refactors whose only benefit is code aesthetics;
- theoretical edge cases that do not affect the current gate.

Core rule:

```text
Process serves product progress.

Prompt, report, commit and review are controls,
not the product.
```

---

# 2. Authority

## 2.1 Owner

The Owner is the final authority for:

- policy exceptions;
- roadmap changes;
- product-direction changes;
- accepting unresolved MEDIUM, HIGH or BLOCKER findings;
- accepting risks outside approved policy;
- reopening a frozen gate;
- authorizing corrective cycles beyond the default review budget;
- approving governance document hashes and versions;
- narrowing or changing a gate requirement.

## 2.2 Codex

Codex provides the independent implementation verdict.

Codex may:

- review an exact source commit or explicit source diff range;
- inspect architecture, state and authority boundaries;
- rerun tests and quality commands;
- run bounded independent experiments;
- classify findings as `RESOLVED`, `OPEN`, `DEFERRED` or `NOT_PROVEN`;
- return one of the approved verdicts.

Codex must not:

- decide the roadmap;
- authorize the next phase;
- modify tracked source or tests;
- implement a corrective patch;
- override the Owner or approved policy;
- reopen a frozen gate.

## 2.3 ChatGPT

ChatGPT:

- maintains the Owner-approved roadmap and gates;
- verifies that each task produces product evidence or closes a reproduced core defect;
- selects an appropriate implementer and reviewer;
- writes implementation and review prompts in English;
- evaluates reports and verdicts against policy;
- recommends the gate decision;
- executes already-approved normal workflow without asking for confirmation at every step;
- controls scope, commit lineage and review budget;
- defines and validates gate-evidence content;
- records actual commit hashes in gate evidence after commits exist, when acting as an explicitly authorized repository writer.

ChatGPT must not:

- override policy;
- accept unresolved MEDIUM or higher findings;
- alter the roadmap without Owner authorization;
- reopen a frozen gate;
- accept risks outside policy;
- continue a corrective cycle after budget exhaustion without Owner authorization, except for the BLOCKER exception defined below.

## 2.4 Claude Code

Claude Code is the implementer.

Claude Code may:

- read the repository and approved policies;
- modify authorized files;
- add or update tests;
- run required commands;
- create the source-and-tests commit;
- create the implementation-report-only commit;
- record implementation evidence.

Claude Code must not:

- mark a finding as `RESOLVED`;
- open the next task or gate;
- modify findings outside authorized scope;
- alter the roadmap or policy;
- independently accept risk;
- modify reviewer reports;
- push, merge or deploy;
- use `git add .`;
- amend, squash, rebase or rewrite history.

---

# 3. Model assignment

| Work type                                                                | Default model                     |
| ------------------------------------------------------------------------ | --------------------------------- |
| State, authority, lifecycle, atomicity or multi-boundary contract        | Claude Code Opus                  |
| Bounded patch, tests, serialization or mechanical adapter work           | Claude Code Sonnet                |
| Deep architecture, correctness, trust-boundary or canonical-state review | Codex Sol                         |
| Git, documentation, commit scope, test counts or mechanical evidence     | Codex Terra or a cheaper reviewer |

Opus is not the default for every task involving state.

Model selection considers:

- ambiguity;
- number of authority boundaries affected;
- risk of invalid canonical state;
- diff width and coupling;
- validation complexity;
- failure-recovery complexity;
- whether the task can be expressed mechanically and narrowly.

Every task must state:

```text
IMPLEMENTER_MODEL:
MODEL_REASON:
```

Every review must state:

```text
REVIEWER_MODEL:
MODEL_REASON:
```

---

# 4. Standard verdicts

Only four verdicts are allowed.

## 4.1 APPROVE

- No required finding remains.
- Expected product evidence is present.
- The gate is closed.
- The gate may be frozen.
- Work may proceed to the next approved gate.

## 4.2 APPROVE_WITH_NON_BLOCKING_FINDINGS

- Only LOW, documentation or cleanup findings remain.
- Expected product evidence is present.
- The gate is closed.
- Findings move to backlog.
- No corrective patch is opened solely for these findings.

## 4.3 REQUEST_CHANGES

- At least one reproduced BLOCKER, HIGH or MEDIUM finding requires correction.
- The gate is not approved.
- A bounded corrective patch may be opened only within the available review budget or with Owner authorization.

## 4.4 BLOCKED

The review cannot complete because of missing:

- source;
- policy;
- environment;
- required evidence;
- reproducible repository state;
- Owner decision;
- access or capability;
- an implementable contract within authorized scope.

Do not use:

```text
APPROVE_WITH_MINOR_PATCHES
```

A required patch always corresponds to:

```text
REQUEST_CHANGES
```

---

# 5. Review budget

The default budget for each gate is:

```text
1 independent review
1 bounded corrective patch
1 corrective rereview
```

## 5.1 Budget exhaustion semantics

When the default review budget is exhausted:

- if the latest verdict is `APPROVE` or `APPROVE_WITH_NON_BLOCKING_FINDINGS`, the gate remains closed;
- if the latest verdict is `REQUEST_CHANGES` or `BLOCKED`, the gate remains `NOT_APPROVED`.

The gate may then proceed only when one of the following occurs:

1. The Owner explicitly accepts the reproduced risk.
2. The Owner authorizes another bounded corrective cycle.
3. The Owner changes or narrows the gate requirement.

Deferring a blocking finding does not convert `REQUEST_CHANGES` into approval.

Before requesting another corrective cycle, ChatGPT must present:

```text
REPRODUCED_DEFECT:
USER_VISIBLE_OR_CORE_CORRECTNESS_IMPACT:
PROPOSED_BOUNDED_SCOPE:
WHY_IT_CANNOT_BE_DEFERRED_UNTIL_REAL_ADAPTER_VALIDATION:
```

## 5.2 Automatic BLOCKER exception

The only automatic exception is:

```text
A reproduced BLOCKER proving that the current gate
cannot produce its stated product evidence.
```

This exception may block the gate without prior Owner authorization, but any additional implementation cycle still requires an explicit bounded task under approved authority.

## 5.3 Frozen gates

After approval:

```text
FREEZE GATE
```

A frozen gate may be reopened only when:

- a real adapter reproduces a defect; or
- the Owner explicitly authorizes reopening.

---

# 6. Product evidence expected

Every gate must define exactly one:

```text
PRODUCT_EVIDENCE_EXPECTED:
```

A task that does not produce that evidence and does not close a reproduced core defect must not be opened.

## Gate 1 — Scenario 1

```text
The Runtime completes or rolls back consistently without leaving
a partial terminal state, and the operation can be retried.
```

## Gate 2 — Scenario 2

```text
The Runtime can perform one bounded revision from deterministic
failure to successful completion without owner input in the
controlled fake loop.
```

Scenario 2 does not prove real-world reduction in human coordination.

## Gate 3 — Claude Code CLI capability spike

```text
The official Claude Code CLI supports enough of the user's existing
authenticated workflow to build a bounded TOMTIT adapter without
unofficial workarounds.
```

## Gate 4 — Minimal real adapter

```text
TOMTIT can assign one bounded coding task to Claude Code CLI and
collect a usable result, status and evidence through the user's
existing authenticated workflow.
```

## Gate 5 — Real-task validation

```text
The current TOMTIT contracts and Runtime can execute a small set of
real bounded coding tasks without critical contract mismatch.
```

## Gate 6 — WAITING_OWNER and NO_PROGRESS

```text
TOMTIT can distinguish when owner input is required and when further
revision should stop, based on failures observed from the real adapter.
```

## Gate 7 — Product CLI

```text
A user can start a run, inspect its status and read its report
without interacting directly with Runtime internals.
```

## Gate 8 — Dogfood

```text
TOMTIT does not reduce acceptance quality and shows a useful reduction
in human intervention compared with using Claude Code directly.
```

---

# 7. Linear Git and audit history

The canonical history for one implementation and review cycle is:

```text
Starting HEAD
→ source + tests commit
→ implementation report commit
→ independent review report commit
```

Branches that omit the implementation-report commit from review-report ancestry are not allowed.

## 7.1 Implementation prompt fields

Every implementation prompt must specify:

```text
STARTING_HEAD:
EXPECTED_PARENT:
IMPLEMENTATION_COMMIT_MESSAGE:
IMPLEMENTATION_REPORT_FILENAME:
GATE_EVIDENCE_PATH:
GATE_EVIDENCE_WRITER:
```

`IMPLEMENTATION_COMMIT_MESSAGE` is a commit message, not a hash.

## 7.2 Implementation commits

### Commit 1

Contains only:

```text
source + tests
```

### Commit 2

Contains only:

```text
implementation report
```

The report-only commit must not contain:

- source changes;
- test changes;
- policy changes;
- configuration changes;
- modifications to previous reports.

Do not use:

```bash
git add .
```

## 7.3 Implementation report contract

The implementation report must contain:

```text
STATUS
STARTING_HEAD
SOURCE_COMMIT_HASH
IMPLEMENTED_DIFF_RANGE
REPORT_COMMIT_EXPECTED_PARENT
FILES_CHANGED
COMMANDS_AND_EXIT_CODES
EVIDENCE
CLAIMED_FIXED_PENDING_REVIEW
RISKS
SCOPE_AND_NON_GOALS
UNPROVEN_ITEMS
NEXT_GATE
```

Rules:

```text
REPORT_COMMIT_EXPECTED_PARENT = SOURCE_COMMIT_HASH
```

The implementation report must not contain the hash of its own future report commit.

It must not state:

```text
FINDING=RESOLVED
```

It may state only:

```text
FINDING=CLAIMED_FIXED_PENDING_REVIEW
```

After the report-only commit is created, its actual hash is read from Git and recorded in gate evidence.

Do not amend the report to insert that hash.

## 7.4 Review prompt fields

Every review prompt must specify:

```text
TARGET_SOURCE_COMMIT:
REVIEWED_DIFF_RANGE:
IMPLEMENTATION_REPORT_COMMIT:
REVIEW_REPORT_EXPECTED_PARENT:
REVIEW_REPORT_FILENAME:
GATE_EVIDENCE_PATH:
GATE_EVIDENCE_WRITER:
```

Required relationship:

```text
REVIEW_REPORT_EXPECTED_PARENT
=
IMPLEMENTATION_REPORT_COMMIT
```

`TARGET_SOURCE_COMMIT` and `REVIEWED_DIFF_RANGE` identify the implementation source being reviewed.

The implementation-report commit remains in the linear ancestry but is not part of the reviewed source diff.

## 7.5 Review report contract

The review report must contain:

```text
VERDICT
TARGET_SOURCE_COMMIT
REVIEWED_DIFF_RANGE
REVIEW_REPORT_EXPECTED_PARENT
FILES_REVIEWED
COMMANDS_AND_EXIT_CODES
INDEPENDENT_REPRODUCTIONS
FINDING_STATUS
SCOPE_AND_NON_GOALS
UNPROVEN_ITEMS
NEXT_GATE_RECOMMENDATION
```

The review report must not contain the hash of its own future review-report commit.

The review-report commit:

- must have the implementation-report commit as its direct parent;
- must contain only the review report;
- must not modify source;
- must not modify tests;
- must not modify the implementation report;
- must not modify prior audit reports.

Its actual hash is read from Git after commit creation and recorded in gate evidence.

## 7.6 History discipline

Do not:

- amend;
- squash;
- rebase;
- rewrite history;
- change previous reports to alter conclusions;
- delete old audit evidence.

All corrections are forward-only.

---

# 8. Gate-evidence persistence

Gate evidence has one declared persistence location and one authorized writer.

ChatGPT defines and validates gate-evidence content.

The Owner or an explicitly authorized repository writer persists it.

Every task or gate prompt must specify:

```text
GATE_EVIDENCE_PATH:
GATE_EVIDENCE_WRITER:
```

Once declared for a gate, the path must remain stable unless the Owner authorizes a change.

Gate evidence may be stored as JSON or Markdown.

Minimum fields:

```text
gate_id
gate_status
product_evidence_status
target_source_commit
implementation_report_commit
review_report_commit
review_verdict
review_budget_status
owner_authorization_ref
recorded_at
```

Optional fields may include:

```text
open_findings
deferred_findings
approved_document_versions
approved_document_hashes
next_gate
notes
```

Writer authority:

- ChatGPT may draft and validate the content.
- Only the declared `GATE_EVIDENCE_WRITER` may persist or modify the repository record.
- Claude Code or Codex may write gate evidence only when explicitly named as the writer in the task prompt.
- A model must not silently choose a different path or evidence format.

Gate evidence changes must follow forward-only history.

---

# 9. Codex temporary experiments

Codex must not modify tracked source or tests.

Codex may:

- run temporary scripts;
- construct domain objects;
- create temporary files;
- use an external review workspace;
- inject failures through existing dependencies;
- run representative negative cases;
- use an isolated copied worktree.

These experiments:

- must not be committed;
- must not change tracked repository content;
- must not turn Codex into the implementer.

If a finding requires a persistent regression test:

```text
Claude Code implements the regression test later
inside an authorized corrective patch.
```

---

# 10. Per-task workflow

## Step 1 — Define the gate

ChatGPT records:

```text
CURRENT_GATE:
TARGET_FINDINGS:
PRODUCT_EVIDENCE_EXPECTED:
NON_GOALS:
NEXT_GATE_IF_APPROVED:
REVIEW_BUDGET_STATUS:
OWNER_AUTHORIZATION_IF_REQUIRED:
GATE_EVIDENCE_PATH:
GATE_EVIDENCE_WRITER:
```

## Step 2 — Select implementer

```text
IMPLEMENTER_MODEL:
MODEL_REASON:
```

## Step 3 — Write the implementation prompt

The English implementation prompt must contain:

1. Role and model.
2. Required reading.
3. Starting HEAD and expected parent.
4. Current gate.
5. Product evidence expected.
6. Objective.
7. Allowed files.
8. Forbidden files.
9. Required behavior.
10. Required tests.
11. Required commands.
12. Non-goals.
13. Commit lineage.
14. Exact report filename.
15. Gate-evidence path and writer.
16. Implementation-report contract.
17. Final implementation status.

## Step 4 — Claude Code implementation

```text
read policy
→ verify clean repository
→ inspect source
→ implement bounded scope
→ add tests
→ run focused tests
→ run full regression
→ inspect diff
→ commit source/tests
→ create implementation report
→ commit report only
→ verify clean repository
```

## Step 5 — ChatGPT implementation review

ChatGPT checks:

- authorized scope;
- commit lineage;
- report completeness;
- test evidence;
- policy conflicts;
- product evidence;
- unapproved assumptions;
- risk claims;
- report-only commit purity.

Result:

```text
ACCEPTED_FOR_INDEPENDENT_REVIEW
```

or:

```text
IMPLEMENTATION_REPORT_INSUFFICIENT
```

ChatGPT does not mark implementation findings as resolved.

## Step 6 — Select reviewer

```text
Codex Sol:
architecture, state, authority, correctness and atomicity

Codex Terra:
Git, documentation, commit scope, test counts and mechanical evidence
```

## Step 7 — Write the review prompt

The English review prompt must include:

```text
TARGET_SOURCE_COMMIT:
REVIEWED_DIFF_RANGE:
IMPLEMENTATION_REPORT_COMMIT:
REVIEW_REPORT_EXPECTED_PARENT:
REVIEW_REPORT_FILENAME:
GATE_EVIDENCE_PATH:
GATE_EVIDENCE_WRITER:
```

Codex must:

- read approved policy;
- inspect the exact source diff;
- verify linear commit ancestry;
- verify commit scope;
- rerun required tests;
- run bounded independent experiments;
- avoid modifying tracked source or tests;
- create the review report;
- return one standard verdict.

## Step 8 — Gate decision

ChatGPT evaluates the verdict against policy, roadmap and review budget.

### APPROVE

```text
close gate
record gate evidence
freeze when required
move to next approved gate
```

### APPROVE_WITH_NON_BLOCKING_FINDINGS

```text
close gate
record deferred LOW/docs/cleanup findings
do not create a corrective patch
```

### REQUEST_CHANGES

```text
keep gate NOT_APPROVED
open one bounded corrective patch only if budget or Owner authorization permits
```

### BLOCKED

```text
keep gate NOT_APPROVED
resolve missing source, policy, environment,
evidence, access or owner decision
```

Deferral never converts `REQUEST_CHANGES` or `BLOCKED` into approval.

---

# 11. Official roadmap

## Gate 1 — Bounded atomic completion

```text
Claude Code Opus
→ final Codex Sol review
→ APPROVE or APPROVE_WITH_NON_BLOCKING_FINDINGS
→ FREEZE SCENARIO 1
```

After freeze:

- do not fix LOW findings;
- do not continue production hardening;
- reopen only when a real adapter reproduces a defect or the Owner authorizes it.

## Gate 2 — Minimal Scenario 2

```text
Attempt 1 fails
→ one repairable finding
→ Runtime commits REVISE decision
→ Attempt 2
→ fresh evidence
→ successful completion
```

Do not yet build:

- Attempt 3;
- complex scoring;
- repeated-finding engine;
- full WAITING_OWNER workflow.

## Gate 3 — Claude Code CLI capability spike

Verify the official CLI supports:

```text
non-interactive invocation
working-directory control
exit-code behavior
stdout capture
stderr capture
structured-output availability
session-resume capability
command visibility
timeout behavior
cancellation behavior
```

If a capability is missing:

```text
ADAPTER_CAPABILITY=LIMITED
```

Do not use:

- browser automation;
- token extraction;
- private endpoints;
- unofficial authentication workarounds.

## Gate 4 — Minimal adapter and development harness

Build exactly one adapter:

```text
ClaudeCodeCLIAdapter
```

Build a development harness that can:

- invoke the adapter;
- provide a task;
- observe raw output;
- collect status;
- collect exit code;
- collect `WorkerResult`;
- inspect evidence.

Do not build the product CLI yet.

## Gate 5 — Run 3–5 bounded real tasks

Example tasks:

- edit text;
- add a test;
- fix CSS;
- add a small component;
- fix a bounded bug.

Use these tasks to discover:

- contract mismatch;
- output parsing problems;
- revision-context problems;
- session behavior;
- subscription-first feasibility;
- Runtime defects exposed by real evidence.

Do not use 3–5 tasks to claim product effectiveness.

## Gate 6 — WAITING_OWNER and NO_PROGRESS

Build only from observed real-adapter failures.

Default:

```text
max_attempts = 2
```

Do not add Attempt 3 yet.

## Gate 7 — Product CLI

Minimum commands:

```text
tomtit run
tomtit status
tomtit report
```

Add:

```text
tomtit resume
```

only after `WAITING_OWNER` works.

## Gate 8 — Dogfood

Run 15–30 real tasks.

Measure:

```text
acceptance
human intervention
scope violations
escaped defects
attempt count
duration
cost availability
review findings
stop reasons
coordination effort
```

This is exploratory evidence, not statistical proof.

## Gate 9 — Product decisions

Only after dogfood decide whether to add:

- Attempt 3;
- verifier policy;
- a second adapter;
- simple routing;
- Memory;
- broader product architecture.

---

# 12. Anti-bureaucracy guardrail

The process must be shortened or stopped when:

- a report does not help the gate decision;
- a reviewer finds only LOW cleanup;
- a task creates no product evidence;
- a task closes no reproduced core defect;
- commit separation no longer improves auditability;
- a prompt is longer than required to maintain scope;
- a review repeats an accepted or deferred finding;
- fake-runtime hardening no longer improves real-adapter readiness.

Gate stopping condition:

```text
core correctness closed
+ independent approval
+ product evidence produced
→ freeze and move forward
```

Do not continue review merely because additional theoretical edge cases can be imagined.

---

# 13. Final operating rule

```text
Build only what proves product value
or closes a reproduced core-correctness defect.

After a gate reaches its evidence target,
freeze it and move closer to a real coding agent.
```

## Document status

```text
TOMTIT_COORDINATOR_OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1=
FINAL_CANDIDATE_FOR_OWNER_APPROVAL
```

Owner approval is recorded only in external gate evidence.

The document bytes must not be modified after their SHA-256 is approved.

Any later modification creates a new document candidate requiring a new hash and new Owner approval.
