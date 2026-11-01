# TARGET-04 Run005 Prior-Evidence Stable-Identity Fix Report

WORK_CLASS=RUNTIME_DEFECT_CORRECTION
CURRENT_GATE=TARGET_04

## Scope

Historical Target-04A Run005 failed with `error_category=PREVIOUS_EVIDENCE_MUTATED`
during the authorized repair round, even though independent forensics
established the protected prior-evidence *contents* never changed -- only
the Review1 evidence root directory's `mtime_ns`/`ctime_ns` differed between
repair start and repair end, with every load-bearing field (recursive
membership, every member file's SHA-256, device, inode, mode, size, nlink)
identical. This is a narrow correction of exactly that defect.

`ROOT_CAUSE_CLASSIFICATION=RUNTIME_FINGERPRINT_SEMANTICS_DEFECT`
(`ROOT_CAUSE_CONFIDENCE=HIGH`, per the forensic result this task was handed).

Run005 remains historically `FAILED`. Its workspace and evidence were not
touched by this task. Target-04A was not rerun; no real product model was
invoked; nothing was pushed or tagged.

## Baseline / lineage

- `BASELINE_COMMIT` (repo `HEAD` at task start): `da37e02734f1efc04dd9da39a1385421f589299e`

## The exact defect

`run_repair.py`'s previous-evidence check (and, separately,
`run_loop.py`'s own outer check of the same contract) compared **raw**
canonical evidence-tree identity snapshots -- `dir:`/`file:` descriptor
strings that embed `mtime_ns`/`ctime_ns` -- with plain dict equality:

```python
previous_evidence_unchanged = (
    previous_impl_hashes_before == previous_impl_hashes_after
    and review_hashes_before == review_hashes_after
)
```

Any external metadata churn on the evidence directory's own entry (a stray
extended-attribute write, filesystem indexing, or similar -- the *specific*
external actor was never identified, and this fix does not assume one) that
changes only `mtime_ns`/`ctime_ns` therefore made this comparison fail,
even though nothing semantically meaningful had changed. This is exactly
the "raw byte-for-byte equality used where a timestamp-tolerant stable
projection was needed" class of defect this Runtime already has one
approved fix for -- `protected_git_metadata_preserved()`'s
`_stable_cross_phase_projection()` -- just not yet applied to this second,
separate "previous evidence" contract.

## The fix: reuse the existing stable projection, in both places

**No new parallel implementation was introduced.** `_stable_cross_phase_projection()`
(already approved and exercised for protected Git metadata) is a fully
generic `Mapping[str, str] -> dict[str, str]` transform over exactly the
same `dir:`/`file:` canonical descriptor grammar that
`snapshot_canonical_evidence()`/`_measure_type_aware_tree()` already
produce for evidence trees -- it needed no modification to be reused here.

### `run_repair.py`

```python
previous_impl_stable_before = run_review._stable_cross_phase_projection(previous_impl_hashes_before)
review_stable_before = run_review._stable_cross_phase_projection(review_hashes_before)
...
previous_evidence_unchanged = (
    previous_impl_stable_before == previous_impl_stable_after
    and review_stable_before == review_stable_after
)
```

The raw dicts are still measured and retained (Section 2: timestamps are
never deleted from measurement) -- only the *comparison* now runs on the
stable projection.

### `run_loop.py` (Section 6: align internal and outer comparisons)

Auditing every comparison that decides preservation of prior Attempt1/
Review1 evidence during repair surfaced a **second, independent** copy of
the same raw-equality defect: `run_loop.py`'s own `_verify_previous_evidence_unchanged()`
closure (used both to gate repair and, separately, to validate the outer
loop-level `previous_evidence_immutable` result field) compared
`hash_directory()` snapshots -- built from the very same raw
`_measure_type_aware_tree` machinery -- with the same blunt `!=`. `run_loop.py`
combines this OUTER check with `run_repair.py`'s own INNER
`previous_evidence_unchanged` receipt field (`repair_immutable_ok =
repair_receipt_immutable_ok and outer_previous_evidence_immutable`), so
fixing only the inner check would **not** have been sufficient -- the outer
check would still have independently rejected on the identical raw-timestamp
mismatch. Both were corrected identically:

```python
if run_review._stable_cross_phase_projection(current) != run_review._stable_cross_phase_projection(snapshot):
    drifted.append(child_id)
```

## New shared building block: `describe_evidence_identity_diff()`

Section 9's observability gap ("the repair-end map itself was not
persisted") is closed with one new function in `run_review.py`, reusing the
canonical descriptor grammar (the existing `_CANONICAL_DIR_DESCRIPTOR_RE`/
`_CANONICAL_FILE_DESCRIPTOR_RE`, additively extended with named
`mtime_ns`/`ctime_ns` capture groups -- purely additive, no match/reject
behavior changed) to report, for a bounded sample of changed paths, exactly
which named fields differ: `device`, `inode`, `mode`, `size`, `nlink`,
`mtime_ns`, `ctime_ns`, `content_sha256`, or `type`; plus `appeared`/
`disappeared` for membership changes. Never an unbounded dump (bounded to
`_EVIDENCE_DIFF_BOUNDED_SAMPLE_SIZE = 10` sampled paths).

`run_repair.py` now persists, on every repair run (whether or not a
mismatch occurs):

- `prior_evidence_stable_hash_start` / `prior_evidence_stable_hash_end`
  (in `receipt.json`; `_start` also in `repair-context.json`) -- one
  combined `workspace_hash` over both evidence roots' stable projections,
  namespaced (`impl/...`, `review/...`) so a mismatch in either root is
  distinguishable from the combined hash alone changing.
- `prior_evidence_diagnostic` -- `None` when nothing changed; otherwise the
  bounded `describe_evidence_identity_diff()` output for each evidence
  root, built from the **raw** (not stable-stripped) descriptors so the
  field-level parser can actually report which field changed, including
  `mtime_ns`/`ctime_ns` when that is genuinely part of what differed
  (caught and fixed during test-writing -- see below).

Section 10's raw/stable diagnostic separation:
`previous_evidence_unchanged` (existing field name, preserved for backward
compatibility with `run_loop.py`'s own reading of it) now reflects the
*semantic* decision; a new sibling `previous_evidence_raw_representation_unchanged`
reports pure raw byte/timestamp equality, kept strictly separate and never
itself authoritative -- the same naming pattern already established for
`protected_git_metadata_preserved` / `protected_git_metadata_raw_representation_preserved`
elsewhere in this Runtime.

## A real bug caught while writing tests

The first draft wired `describe_evidence_identity_diff()` to the
already-stable-stripped projections (`previous_impl_stable_before/after`)
rather than the raw ones. Since stable-projected descriptors no longer
contain the `mtime_ns=...:ctime_ns=...` segment, they no longer
`fullmatch()` the canonical (with-timestamp) grammar the field parser
expects -- every diagnosed change silently fell back to the coarse `"type"`
bucket instead of naming the real field. Caught immediately by a failing
new test (`test_repair_diagnostic_populated_on_real_mutation`) asserting
the diagnostic actually named `content_sha256`; fixed by passing the raw
(`previous_impl_hashes_before/after`) dicts to the diagnostic function
instead, which is also more informative for debugging (it can report a
genuine `mtime_ns`/`ctime_ns`-only change too, not just semantic ones).

A second test (`test_repair_rejects_prior_evidence_member_disappearance`,
now split into two: `..._member_disappearance` for a non-required member
and `..._raises_integrity_failure_when_required_evidence_file_disappears`
for one of the three `REPAIR_EVIDENCE_FILES`) initially assumed every
membership change surfaces as a graceful `PREVIOUS_EVIDENCE_MUTATED`.
Deleting one of the three *required* evidence files instead trips the
pre-existing, stricter `read_canonical_evidence()` required-file check
(`EvidenceIntegrityFailure`, exit code 3) -- a harder failure than a
graceful mismatch, but still correctly fail-closed and unrelated to this
fix. The test matrix now covers both paths explicitly rather than asserting
an incorrect uniform expectation.

## No baseline rollover (Section 8)

`previous_impl_stable_before`/`review_stable_before` are computed exactly
once, at repair start, from the raw measurement taken at that moment, and
persisted as the frozen authority baseline (`prior_evidence_stable_hash_start`).
Repair-end is always validated *against* that frozen baseline; nothing ever
re-derives or replaces it from a later reading. This is unchanged from the
already-approved before/after measurement shape -- only the *comparison
semantics* changed.

## Regression tests added

+26 tests total (`test_run_review.py` 243->263, `test_run_repair.py`
129->134, `test_run_loop.py` 92->93).

**`test_run_review.py` (+20):** the full Section 13 positive
timestamp-only matrix (directory mtime-only, ctime-only, both; file
mtime-only, ctime-only) and the full Section 12 negative matrix (content
SHA-256 change, size change, mode change, inode/device change, nlink
change, member appears, member disappears, nested membership change,
object-type change, timestamp-change-plus-content-change), all as direct
unit tests against `_stable_cross_phase_projection` equality on synthetic
descriptor strings; plus five tests of `describe_evidence_identity_diff()`
itself (bounded changed-field reporting, timestamp-only fields,
appeared/disappeared, type change, empty-diff-on-no-change).

**`test_run_repair.py` (+5):** the exact Run005-class reproduction
(`test_repair_tolerates_prior_evidence_root_timestamp_only_change` -- a
real fake-`claude` executable that performs the ordinary repair write and
then `os.utime`-touches the real Attempt1/Review1 evidence directories
mid-run, confirming repair still succeeds and
`previous_evidence_raw_representation_unchanged` honestly reports `False`
while `previous_evidence_unchanged` is `True`); observability-fields
persistence on both the happy path and a genuine-mutation path; the
member-disappearance split described above.

**`test_run_loop.py` (+1):** `test_full_repair_cycle_tolerates_prior_evidence_timestamp_only_change`
-- the same Run005-class reproduction one layer up, through the real
`run_loop.py` orchestrator end-to-end (Attempt1 -> Review1 REQUEST_CHANGES
-> repair, with the fake `claude` executable touching the *actual*
`children/<id>-impl-1`/`children/<id>-review-1` evidence directories mid-repair
-> Review2 APPROVE), confirming the loop reaches `ACCEPTED` with
`previous_evidence_immutable=True` -- proving the outer (`run_loop.py`) and
inner (`run_repair.py`) alignment fix together, not just in isolation.

## Full validation matrix (Section 16)

Full log: `TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_FULL.log`
(this task's log; prior rounds' logs were not rerun; Target-04A was not
rerun).

| Check | Result |
|---|---|
| Timestamp-only stable identity + negative matrix + observability (focused) | PASS (20) |
| Run005-class repair integration cases (focused) | PASS (5) |
| Run005-class outer alignment (focused) | PASS (2) |
| Repair authority / expected-Review1 binding regressions | PASS (7) |
| Canonical Git-index F01-F05/F03-F04 regressions | PASS (81) |
| Coherent protected-metadata bootstrap / trusted-content guards | PASS (9) |
| Full suite `test_run_review.py` | PASS, 263 |
| Full suite `test_run_repair.py` | PASS, 134 |
| Full suite `test_run_loop.py` | PASS, 93 |
| Full suite `tests/adapters/test_claude_code_cli.py` | PASS, 436 (unchanged) |
| Full repository regression `pytest tests/ -q` | PASS, 985 (unchanged root) |
| `.tomtit/dogfood/` full tree | PASS, 490 (93 + 134 + 263) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

**Honest note on a transient failure:** during the matrix's concurrent
dogfood-tree run, `test_capability_probe_delayed_descendant_is_quiesced`
(a pre-existing real-subprocess process-group quiescence timing test,
entirely unrelated to any file this task touched -- the same class of
flakiness that has appeared once each in every prior Target-04 round's
concurrent matrix run) failed once. It was rerun in isolation (pass) and
the full dogfood tree was rerun cleanly with nothing else running
concurrently (pass, recorded in the log's section 16). This is disclosed
here and not erased.

## Out of scope / unchanged

- `F01`, `F02`, `F03`, `F04`, `F05` canonical-index behavior: unmodified
  (confirmed by the unchanged focused regression selection above).
- Coherent protected-metadata bootstrap, trusted-content guards, Review1
  authority binding, repair's exact Review1 ID binding, no-repair-bootstrap,
  no-baseline-rollover, bounded repair: all unmodified.
- Target-04A was not rerun; Target-04B was not started; no Run006 was
  created.
- No real product/Claude/Codex model was invoked.
- Run001-005 historical workspaces and evidence: untouched. Run005 remains
  historically `FAILED`.
- No push, no tag.

`RAW_MEASUREMENT_RETAINS_TIMESTAMPS=PASS`
`STABLE_CROSS_PHASE_EXCLUDES_MTIME=PASS`
`STABLE_CROSS_PHASE_EXCLUDES_CTIME=PASS`
`FILE_SHA_REMAINS_LOAD_BEARING=PASS`
`DIRECTORY_MEMBERSHIP_REMAINS_LOAD_BEARING=PASS`
`DEVICE_INODE_MODE_SIZE_NLINK_REMAIN_LOAD_BEARING=PASS`
`RUN005_CLASS_TIMESTAMP_ONLY_CHANGE_ACCEPTED=PASS`
`REAL_CONTENT_CHANGE_REJECTED=PASS`
`MEMBERSHIP_CHANGE_REJECTED=PASS`
`REPAIR_INTERNAL_COMPARISON_ALIGNED=PASS`
`OUTER_COMPARISON_ALIGNED=PASS`
`REPAIR_END_STABLE_HASH_PERSISTED=PASS`
`BOUNDED_CHANGED_PATHS_PERSISTED=PASS`
`BOUNDED_CHANGED_FIELDS_PERSISTED=PASS`
`RUN005_RETROACTIVELY_RECLASSIFIED=NO`
