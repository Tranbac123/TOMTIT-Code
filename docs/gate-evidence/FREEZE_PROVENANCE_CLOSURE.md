# Gate Evidence — Freeze Provenance Closure

## Gate decision

```yaml
gate_id: FREEZE_PROVENANCE_CLOSURE
gate_status: CLOSED
product_evidence_status: NOT_APPLICABLE_PROVENANCE_GATE
owner_authorization_ref: OWNER_DECISION_AUTHORIZE_FREEZE_PROVENANCE_CLOSURE_SEPARATE_POLICY_COMMIT_2026-08-13
next_gate: TARGET_04
```

This is a provenance-binding gate, not a new general correctness review. It
closes the gap between an earlier working-tree-bytes correctness review
(`APPROVE`) and the commit identity that now carries those exact bytes.

## Reviewed artifact and lineage

```yaml
baseline_commit: a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee
policy_commit: 771b8bca67779c4e0e6ba18e3d3dfe7e59f2f686
target_source_commit: 2e0560886b60870267fea25c20ef6f2466338680
reviewed_diff_range: a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee..2e0560886b60870267fea25c20ef6f2466338680
implementation_report_commit: febc6c0b7180b78ff10eb26ae5dbef592007e01f
review_report_commit: 94a9c36d68b84fd71c1b3825554a2691c69a5519
review_verdict: APPROVE
```

Direct-parent lineage verified:

```text
a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee   (BASELINE_COMMIT)
→ 771b8bca67779c4e0e6ba18e3d3dfe7e59f2f686 (POLICY_COMMIT — docs/MVP_EXECUTION_POLICY_V0.md only)
→ 2e0560886b60870267fea25c20ef6f2466338680 (TARGET_SOURCE_COMMIT — nine authorized Runtime source/test files only)
→ febc6c0b7180b78ff10eb26ae5dbef592007e01f (IMPLEMENTATION_REPORT_COMMIT — RUNTIME_FINAL_BOUNDED_CORRECTIVE_REPORT.md only)
→ 94a9c36d68b84fd71c1b3825554a2691c69a5519 (REVIEW_REPORT_COMMIT — CODEX_RUNTIME_FINAL_NARROW_REVIEW.md only)
```

The Gate-evidence commit and any commit that follows it are forward-only Git
history that persists and records this decision; they are not part of the
reviewed implementation diff and are not self-referenced from within this
file, consistent with the convention established in
`docs/gate-evidence/GATE_3_CLAUDE_CODE_CLI_CAPABILITY.md`.

## Approved source hash manifest

```text
9e851ec49543af06f91c155bea5fe27e72524d94a7165ad78ceaeab8588b21b3  .tomtit/dogfood/run_loop.py
1fb12f4e0fecc848dbfb329ff6a0ae6c6cb8d26bb0766ecfe199b52a0838813d  .tomtit/dogfood/run_repair.py
3a809730e332e8038d55815b7ae3df8582a70dc7200c4a2ac5a2c26087e32831  .tomtit/dogfood/run_review.py
a966a2712dfa226f8a959a4bc3e7ac5c016dfeaa81a29230b78043ce756895c6  .tomtit/dogfood/run_task.py
91f95a01a05db79a355904d85b62d4f5c3d89a2b39c9f98e27b9b36289931e82  .tomtit/dogfood/test_run_loop.py
95ff5a8c6bf175b8dce0f3d36d3413ed7b51feca88058f50f86805782fbfbf08  .tomtit/dogfood/test_run_repair.py
b4824626a0d819f2f81b8ecfbaa16e4b13111323a2833485ecb7c353a112d583  .tomtit/dogfood/test_run_review.py
f339c329ef24a35c6209ef217246b7dc482b2b369bb934dfcd626ca70f520861  docs/MVP_EXECUTION_POLICY_V0.md
4970beabd05cf1fc34c1a54447e7c90e6813869134b6b445be1ca6e323be457e  src/tomtit/adapters/claude_code_cli.py
7659adbfb7064936e95ad3360074872262b4b8b508d7f345d9a1b7ca15e40460  tests/adapters/test_claude_code_cli.py
```

```yaml
approved_source_hash_manifest: PASS
```

All ten blobs at `target_source_commit` match this manifest exactly. The
policy blob is identical at `policy_commit` and `target_source_commit`. The
nine source/test paths at `policy_commit` are unchanged from
`baseline_commit`. Verified independently at both the implementation stage
and the review stage.

## Validation result

```yaml
validation_result: PASS
validation_log_hash: 6c746fe915a39a969dfa66da4988b6a1422c072804636e2afc2675c80660e993

focused_validation:
  linked_worktree_gitfile_correctness: 14/PASS
  canonical_evidence_reads: 14/PASS
  authorized_side_effect_review_repair: 12/PASS
  r01_r04_lifecycle: 19/PASS

affected_tests: 728/PASS
full_regression: 985/PASS
compileall: PASS
ruff: PASS
diff_check: PASS
```

The implementation-report stage (`febc6c0b7180b78ff10eb26ae5dbef592007e01f`)
ran and passed the affected-suite (728) and full-regression (985) counts,
compileall, ruff, and both `git diff --check` invocations, but honestly
recorded the four previously-accepted focused-selector groups as unproven
because their exact node selectors could not be reconstructed from repository
state alone. The review stage
(`94a9c36d68b84fd71c1b3825554a2691c69a5519`) independently reconstructed and
ran those exact four selections, closing that gap with 14/14/12/19 passing,
and re-confirmed byte-equivalence, lineage, and protected-scope preservation
afterward. Neither stage weakened, skipped, or altered any test.

## Durable evidence artifacts

```yaml
artifacts:
  - artifact_path: evidence/freeze-provenance-closure/CODEX_RUNTIME_FINAL_NARROW_REVIEW_FULL.log
    sha256: eb85f74ae1b1bad51dc57cb2b1447a623df8ddd10040ed67c0fa016a1c9cb774
    size: 8891
    retention_location: GIT_BLOB_IN_GATE_EVIDENCE_COMMIT

  - artifact_path: evidence/freeze-provenance-closure/RUNTIME_COMMITTED_STATE_VALIDATION_FULL.log
    sha256: 6c746fe915a39a969dfa66da4988b6a1422c072804636e2afc2675c80660e993
    size: 3737
    retention_location: GIT_BLOB_IN_GATE_EVIDENCE_COMMIT
```

A third focused-run log
(`/private/tmp/CODEX_RUNTIME_FREEZE_BINDING_FOCUSED_FULL.log`, SHA-256
`18f170872f7dde1a199863ea5b6d2edeb78a12cb7d965ebcb4fde4d80addcc9e`, 320
bytes) exists outside the repository. It is not part of this commit or
canonical durable gate evidence — its focused results are already bound by
`review_report_commit` above, and the durable evidence content contract for
this gate is exactly the two files listed above.

## Scope audit

```yaml
protected_scope_preserved: YES
decision_eval_modified: NO
cli_v0_modified: NO
target03_modified: NO
personal_web_touched: NO
real_product_provider_invocations: 0
real_e2e_runs: 0
pushed: NO
```

## Tag

```yaml
tag: tomtit-runtime-repair-v1
tag_target: 94a9c36d68b84fd71c1b3825554a2691c69a5519
```

The tag targets `review_report_commit`, not the gate-evidence commit, per
Owner decision (`TAG_TARGET=REVIEW_REPORT_COMMIT`).

## Recorded by

```yaml
gate_evidence_writer: CLAUDE_CODE_SONNET
verifier_mode: MECHANICAL
```
