# Target-04 Run005 Prior-Evidence Stable-Identity Fix — Independent Review

Date: 2026-08-18
Gate: `TARGET_04`
Work class: `RUNTIME_DEFECT_CORRECTION_REVIEW`
Verdict: `APPROVE`

## 1. Executive conclusion

The narrow Run005 correction is approved. The source commit fixes the exact
prospective false-positive class established by the independent Run005
forensics: prior Attempt1/Review1 evidence whose member bytes, recursive
membership, device, inode, mode, size, and link count remain identical no
longer fails merely because the evidence-root directory's `mtime_ns` and
`ctime_ns` changed between repair phases.

The correction does not weaken real evidence-integrity enforcement. Raw
canonical measurements still retain timestamps. A single shared stable
cross-phase projection removes only `mtime_ns` and `ctime_ns` after strict
full-grammar recognition, while retaining path/membership, object type,
device, inode, mode, size, link count, and file-content SHA-256. The repair
runner's internal comparison and the loop runner's outer comparison both use
that same projection. Content, membership, required-file, and non-timestamp
metadata mutations remain fail-closed.

All requested focused and full serial validation passed. No Target-04 run was
started, no real model was invoked, and no tag, remote, or product workspace
was touched.

## 2. Exact lineage and scope

Resolved lineage:

```text
da37e02734f1efc04dd9da39a1385421f589299e
  -> bbff54b914f95edc01d299567bf177bbeb2e8659
  -> 76b577db16306dfa77ab97e19d86bbdfbb3269e1
```

- Source parent: `da37e02734f1efc04dd9da39a1385421f589299e`
- Implementation-report parent: `bbff54b914f95edc01d299567bf177bbeb2e8659`
- Lineage result: `PASS`

The source commit changes exactly these six files:

- `.tomtit/dogfood/run_review.py`
- `.tomtit/dogfood/run_repair.py`
- `.tomtit/dogfood/run_loop.py`
- `.tomtit/dogfood/test_run_review.py`
- `.tomtit/dogfood/test_run_repair.py`
- `.tomtit/dogfood/test_run_loop.py`

The implementation-report commit adds only
`TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REPORT.md`. Review of the
source diff found the source scope limited to the stable prior-evidence
comparison, bounded diagnostic observability, and directly associated tests.
No unrelated gate requirement or product behavior was introduced.

## 3. Historical Run005 disposition

This review is prospective only. It does not reclassify or overwrite the
historical Run005 outcome. The historical state remains:

```text
TASK_OUTCOME=FAILED
REAL_REPAIR_SEGMENT_PROOF=FAIL
CONTROL_LOOP_PROOF=NOT_FULLY_EXERCISED
ROOT_CAUSE_CLASSIFICATION=RUNTIME_FINGERPRINT_SEMANTICS_DEFECT
```

`RUN005_HISTORICAL_RESULT_PRESERVED=PASS`.

## 4. Raw measurement and stable projection

`snapshot_canonical_evidence()` and `read_canonical_evidence()` continue to
delegate to the type-aware evidence-tree measurement. The raw directory and
regular-file descriptors still include both `mtime_ns` and `ctime_ns`.
Regular-file descriptors also retain content SHA-256. The patch therefore
does not globally remove timestamps from canonical evidence measurement or
from same-window race detection.

`_stable_cross_phase_projection()` applies
`_strip_volatile_timestamp_fields()` only for strict full matches of the
canonical `dir:` and `file:` descriptor grammars. Canonical reconstruction
removes only the two timestamp fields. Unknown, malformed, reordered,
truncated, path, symlink, or extended descriptor text is preserved byte for
byte rather than being partially normalized. The retained stable form binds:

- every map key, hence path and recursive membership;
- object type;
- device and inode;
- mode;
- size;
- link count; and
- regular-file content SHA-256.

This gives the requested boundary: timestamps are raw diagnostic/race
evidence but are not cross-phase authority; all other established identity
fields remain authoritative.

## 5. Shared semantics and inner/outer alignment

There is one stable projection implementation for this contract:
`run_review._stable_cross_phase_projection()`.

At repair start, `run_repair.py` measures the raw Attempt1 and Review1
evidence maps, projects each once, and freezes those projected maps and their
combined start hash. At repair end it independently remeasures, projects, and
compares against the frozen start values. It does not replace the baseline,
rebootstrap from the end state, or retry until a new state becomes accepted.

`run_loop.py` records raw child-evidence snapshots and, when checking prior
children later, projects both the current measurement and the original raw
snapshot with the same shared helper. An evidence-integrity exception remains
a drift result. Thus the repair-internal and outer-loop decisions cannot
disagree merely because one compares raw timestamp-bearing maps and the other
compares stable maps.

No competing prior-evidence timestamp-tolerance helper was found.

## 6. Exact Run005-class reproduction

The focused repair integration uses disposable evidence and a fake local
Claude-shaped executable. During repair, it changes only the timestamps on
the real Attempt1/Review1 evidence roots. The repair succeeds, semantic
preservation remains true, raw representation equality is false, and no
`PREVIOUS_EVIDENCE_MUTATED` result is emitted.

The focused outer-loop integration exercises the complete fake lifecycle:

```text
Attempt1
  -> Review1 REQUEST_CHANGES
  -> bounded repair
  -> prior-evidence root timestamp churn
  -> Review2 APPROVE
  -> terminal accepted result
```

It reaches `COMPLETED`, `accepted=true`, `repair_rounds=1`, and
`previous_evidence_immutable=true`. Both integrations passed alone and again
inside their full containing suites.

## 7. Negative integrity matrix

The direct stable-projection matrix and integration cases establish that the
fix is narrow:

| Mutation | Result |
|---|---|
| File content SHA-256 | Rejected |
| Content plus timestamp change | Rejected |
| File size | Rejected |
| File mode | Rejected |
| Device | Rejected |
| Inode | Rejected |
| Link count | Rejected |
| Object type | Rejected |
| Member appears | Rejected |
| Member disappears | Rejected |
| Nested membership changes | Rejected |
| Required evidence file disappears | Stronger canonical evidence-integrity failure |

The required-file deletion case exits through the existing required-file
integrity path rather than being converted into timestamp tolerance. A
non-required member disappearance takes the graceful
`PREVIOUS_EVIDENCE_MUTATED` path and persists a disappearance diagnostic.

The positive timestamp matrix separately confirms preservation for file
mtime-only, file ctime-only, directory mtime-only, directory ctime-only, and
directory mtime-plus-ctime changes when every retained field and membership
is identical.

## 8. Receipt semantics and observability

The repair receipt separates authority from representation:

- `previous_evidence_unchanged` is the stable semantic decision;
- `previous_evidence_raw_representation_unchanged` is raw descriptor equality;
- `prior_evidence_stable_hash_start` records the frozen start authority;
- `prior_evidence_stable_hash_end` records the independently measured end;
- `prior_evidence_diagnostic` is populated only for a stable semantic
  mismatch and is never used to decide authority.

For timestamp-only Run005-class churn, semantic preservation is true while
raw representation equality is false. Raw equality does not participate in
the success predicate.

`describe_evidence_identity_diff()` is fed the raw timestamp-bearing maps,
not their stripped projections. It reports a total changed-path count and a
sample bounded to ten paths per evidence tree. For a path present on both
sides, it identifies the changed named fields and includes before/after raw
descriptors. It distinguishes `device`, `inode`, `mode`, `size`, `nlink`,
`mtime_ns`, `ctime_ns`, `content_sha256`, and `type`; separate booleans classify
appeared and disappeared paths. Direct tests verify content mutations report
`content_sha256` rather than a coarse type change, timestamp fields remain
observable, and unchanged inputs produce an empty sample.

## 9. Prior Target-04 contracts

The previously established Target-04 boundaries remain intact:

- canonical Git-index F01-F05/F03-F04 selection: 81/81 passed;
- canonical index semantic identity remains separate from raw index
  representation diagnostics;
- coherent protected-metadata bootstrap selection: 9/9 passed;
- two additional no-untrusted-subprocess-before-baseline/index-establishment
  guards passed in a broader 11-test check;
- exact expected Review1 ID and repair-authority selection: 7/7 passed;
- repair does not call coherent bootstrap establishment;
- repair remains limited to Attempt2; no Attempt3 path was introduced;
- real prior-evidence mutations and required-file disappearance remain
  fail-closed.

The source diff does not alter canonical index projection, coherent bootstrap,
review authority selection, allowed repair count, model contract, or Git
mutation policy.

## 10. Independent validation record

All pytest commands were run serially with fake/local providers only and with
bytecode cache redirected or disabled. The first focused invocation inside
the restricted filesystem sandbox exited before pytest collection with the
environmental OpenMP error `Function Can't open SHM failed`. The identical
command was rerun outside that sandbox boundary and passed 20/20. This was an
execution-environment restriction, not a collected test failure, and is
preserved here rather than omitted.

| Validation | Result |
|---|---:|
| Timestamp projection, negative matrix, diagnostic units | 20/PASS |
| Run005-class repair integrations and observability | 5/PASS |
| Run005-class outer alignment plus prior mutation negative | 2/PASS |
| Repair authority / expected Review1 binding | 7/PASS |
| Canonical Git-index regressions | 81/PASS |
| Coherent protected-metadata bootstrap | 9/PASS |
| Full `test_run_review.py` | 263/PASS |
| Full `test_run_repair.py` | 134/PASS |
| Full `test_run_loop.py` | 93/PASS |
| Full Claude adapter suite | 436/PASS |
| Full `tests/` tree | 985/PASS |
| Full `.tomtit/dogfood/` tree | 490/PASS |
| `compileall` on the six reviewed files | PASS |
| Ruff on the six reviewed files | PASS |
| Worktree and baseline-to-report `git diff --check` | PASS |
| Reviewed source/test bytes equal `HEAD` after validation | YES |

The known subprocess-quiescence timing test did not fail in this independent,
clean serial run. No failure was hidden or replaced by a containing-suite
result.

## 11. Safety and non-actions

- Runtime source/tests modified by reviewer: `NO`
- Source bytes changed during review: `NO`
- Target-04A reruns: `0`
- Run006 created: `NO`
- Target-04B started: `NO`
- Real Claude/Codex product models invoked: `NO`
- Git tags created or changed: `NO`
- Remotes pushed: `NO`
- Target-04 product workspaces/evidence modified: `NO`

Four unrelated, pre-existing untracked Markdown reports were present before
this review and were left untouched. They are not part of this review commit.

## 12. Final disposition

`APPROVE`. The Run005 false positive is prospectively closed at both the
repair and outer-loop boundaries without weakening detection of real prior
evidence mutation. The owner may bind this approved Runtime state for the
owner-authorized Target-04A Run006.

```text
STATUS=APPROVED
VERDICT=APPROVE
RUN005_HISTORICAL_RESULT_PRESERVED=PASS
ROOT_CAUSE_CONTRACT_MATCH=PASS
RAW_MEASUREMENT_RETAINS_TIMESTAMPS=PASS
ONLY_TIMESTAMPS_REMOVED=PASS
SHARED_STABLE_PROJECTION_REUSED=PASS
REPAIR_INTERNAL_COMPARISON_ALIGNED=PASS
OUTER_COMPARISON_ALIGNED=PASS
NO_BASELINE_ROLLOVER=PASS
CANONICAL_INDEX_CONTRACTS_PRESERVED=PASS
COHERENT_BOOTSTRAP_UNCHANGED=PASS
REPAIR_AUTHORITY_UNCHANGED=PASS
NEXT_ACTION=OWNER_BINDS_RUNTIME_FOR_TARGET04A_RUN006
```
