# TARGET-04 Canonical Index F03/F04 Semantic Completeness Report

WORK_CLASS=RUNTIME_DESIGN_CORRECTION
CURRENT_GATE=TARGET_04

## Scope

This is a narrow correction of exactly the two remaining blocking findings
from the latest independent Codex review
(`/Users/tranvanbac/Documents/AI/ai-agent/TOMTIT-E2E-Evidence/CODEX_TARGET04_CANONICAL_INDEX_F03_F04_FINAL_FIX_REVIEW.md`,
verdict `REQUEST_CHANGES`):

- **F03 role-proof gap**: `_index_metadata_exception_keys()`'s containment
  helper performed lexical `Path.relative_to()` on the raw descriptor
  strings without first proving both were absolute, normalized/resolved
  paths -- relative and traversal-bearing (`..`) descriptors could still
  receive exception keys.
- **F04 cross-field semantic gaps**: individual field *shapes* were valid
  but the *complete records* were not proven to be states Git can actually
  emit -- all-zero object IDs were accepted for cached stage/REUC entries,
  a type-1 record's mode-absence and its associated object id were
  validated independently rather than as one coherent presence/absence
  relationship, `XY=".."`/unmerged-only `XY` pairs were accepted on type-1
  records, rename/copy scores above 100 were accepted, and unmerged (type
  "u") records did not validate that their per-stage mode/OID
  presence pattern actually matched their own XY state.

`F01`, `F02`, and `F05` were already `PASS`/closed and are **not**
redesigned here. Only `.tomtit/dogfood/run_review.py` and
`.tomtit/dogfood/test_run_review.py` change; `run_loop.py`, `run_repair.py`,
and `test_run_loop.py` remain byte-identical (confirmed via `git diff
--stat`) -- no mechanical adjustment was needed.

## Baseline / lineage

- `BASELINE_COMMIT` (repo `HEAD` at task start): `738ab14b2544007ad7e10ce35288554c64cbacbd`
- `REVIEWED_SOURCE_COMMIT`: `3597f4b47b37e94fdb37194131466e8ab99fb8c0`
- `REVIEWED_IMPLEMENTATION_REPORT_COMMIT`: `738ab14b2544007ad7e10ce35288554c64cbacbd`

## F01/F02/F05: remain closed, unmodified

- `F01_REMAINS_CLOSED=PASS` -- `_MAX_CANONICAL_INDEX_OBSERVATIONS = 3` and
  the two-consecutive-complete-match algorithm are byte-unchanged.
- `F02_REMAINS_CLOSED=PASS` -- `protected_git_metadata_preserved()`'s
  unconditional non-`None`/equal canonical-identity gate is unchanged.
- `F05_REMAINS_CLOSED=PASS` -- the semantic/raw evidence contract in
  `run_loop.py` is entirely untouched (that file was not edited).

All three sets of focused regressions pass unmodified (see validation
matrix below).

## F03 role-proof gap -- fixed

**Exact defect**: `_common_dir_relative_alias_for_nested_git_dir()` stripped
the `"path:"` prefix and called `Path(git_dir).relative_to(Path(common_dir))`
directly on the raw strings -- a purely lexical, non-filesystem-touching
comparison. Codex's two exact reproductions:

```text
{"@git_dir": "path:common/worktrees/w", "@git_common_dir": "path:common"}
-> {"git_dir/index", "git_common_dir/worktrees/w/index"}   # WRONG: neither path is absolute

{"@git_dir": "path:/common/../elsewhere", "@git_common_dir": "path:/common"}
-> {"git_dir/index", "git_common_dir/../elsewhere/index"}   # WRONG: ".." never resolved
```

**Fix**: a new `_canonical_absolute_path_descriptor(path_descriptor) -> Path
| None` gate validates a role descriptor as eligible to participate in role
reasoning AT ALL -- exact `"path:"` prefix, absolute path portion, no
unresolved `".."` segment. `_index_metadata_exception_keys()` now calls this
for BOTH `@git_dir` and `@git_common_dir` FIRST; if either is ineligible, it
returns the empty set immediately, before any equality or nesting proof is
even attempted:

```python
canonical_git_dir = _canonical_absolute_path_descriptor(git_dir_path)
canonical_git_common_dir = _canonical_absolute_path_descriptor(git_common_dir_path)
if canonical_git_dir is None or canonical_git_common_dir is None:
    return frozenset()
if canonical_git_dir == canonical_git_common_dir:
    return frozenset({_GIT_DIR_INDEX_KEY, _GIT_COMMON_DIR_INDEX_KEY})
try:
    nested_relative = canonical_git_dir.relative_to(canonical_git_common_dir).as_posix()
except ValueError:
    return frozenset()
return frozenset({_GIT_DIR_INDEX_KEY, f"git_common_dir/{nested_relative}/index"})
```

Both exact reproductions now yield `frozenset()`. A `"."` segment is
deliberately NOT checked for separately: unlike `".."`, it is never
context-dependent or ambiguous under symlinks -- `pathlib.Path` itself
unconditionally drops it while parsing (`Path("/a/./b").parts == ("/", "a",
"b")`, with no literal `"."` ever surviving), so it normalizes away
transparently rather than needing rejection; this was verified directly
(`_canonical_absolute_path_descriptor("path:/a/./b") == Path("/a/b")`).

The already-passing ordinary-repository, valid-linked-worktree,
distinct-common-root-strict, and misleading-textual-prefix cases from the
prior round are all unaffected (all their real-Git and synthetic
regressions pass unmodified).

`F03_LINKED_WORKTREE_ROLE_HANDLING=PASS`
`ROLE_DESCRIPTOR_ABSOLUTE_REQUIRED=PASS`
`ROLE_DESCRIPTOR_CANONICAL_REQUIRED=PASS`
`ROLE_TRAVERSAL_REJECTED=PASS`

## F04 cross-field semantic gaps -- fixed

### 1. Zero object IDs (Section 7)

**Fix**: split the old single `_valid_object_id` into three field-context-
aware predicates:

- `_valid_object_id_shape(oid, n)` -- shape only (width + hex); never called
  by a record parser directly.
- `_valid_real_object_id(oid, n)` -- shape-valid AND not all-zero.
- `_valid_zero_placeholder_object_id(oid, n)` -- exactly all-zero at width `n`.

`_parse_canonical_index_stage_record` and `_parse_canonical_resolve_undo_record`
now require `_valid_real_object_id` -- a cached index entry or REUC record
always names a real object, never the absence placeholder. (The empty-blob
hash used for `add -N` intent-to-add entries is a real, well-known,
non-zero object id -- e.g. `e69de29b...` for SHA-1 -- so this does not
affect intent-to-add handling at all.)

`STAGE_ZERO_OID_REJECTED=PASS`
`REUC_ZERO_OID_REJECTED=PASS`

### 2. Mode/OID presence relationship (Section 8)

**Fix**: `_valid_status_mode_oid_pair(mode, oid, n)` validates one
HEAD/index mode+OID pair (or one type-u per-stage pair) as ONE coherent
presence/absence state: `mode == "000000"` requires `oid` to be the
zero placeholder; any other (real) mode requires `oid` to be
`_valid_real_object_id`. Codex's exact reproduced "present HEAD/index modes
but all-zero hH/hI" and the symmetric "absent mode + nonzero OID" are both
now rejected. Applied to `hH`/`mH` and `hI`/`mI` in the shared type-1/type-2
validator, and to each of `h1`/`m1`, `h2`/`m2`, `h3`/`m3` in the type-u
parser. `mW` (worktree mode) has no associated OID field in either record
shape and is validated only as a shape-valid status mode.

`STATUS_MODE_OID_RELATIONSHIP_VALIDATED=PASS`

### 3. Type-1 complete semantics (Section 9)

**Fix**: `_STATUS_TYPE1_TYPE2_FORBIDDEN_XY = {".."}  | {the seven unmerged
pairs}`, checked first in the shared fixed-field validator. Codex's exact
reproduced `XY=".."` (git never emits a changed-path record for an
unchanged path) and `XY="AA"` (an unmerged state, always emitted as type
`"u"`, never type `"1"`) are both rejected. Combined with the mode/OID
relationship fix above, a type-1 record is now validated as ONE coherent
state, not independently-valid fields.

`STATUS_TYPE1_RECORD_SEMANTICS_VALIDATED=PASS`

### 4. Type-2 score range (Section 10)

**Fix**: `_valid_type2_score(score_field, expected_x)` additionally bounds
the regex-matched numeric percentage to `0 <= score <= 100` (a similarity
score cannot exceed 100%). Codex's exact reproduced `R101` and `R999`
(lexically valid, semantically impossible) are rejected; real Git-produced
`R100` remains valid (confirmed by the existing rename regression). Type-2
records inherit the same mode/OID relationship and forbidden-XY checks as
type-1 through the shared validator.

`STATUS_TYPE2_RECORD_SEMANTICS_VALIDATED=PASS`
`TYPE2_SCORE_RANGE_VALIDATED=PASS`

### 5. Type-u stage-presence pattern (Section 11)

**Fix**: `_UNMERGED_STAGE_PRESENCE`, a lookup table mapping each of the
seven documented unmerged XY pairs to `(stage1_present, stage2_present,
stage3_present)`. Four of the seven entries were confirmed by direct real
Git conflict reproduction: `UU` (all three present -- both sides modified),
`AA` (stage 1 absent -- both sides independently added the same new path,
so no common-ancestor entry exists), `DU` (stage 2 absent -- a real
`modify/delete` conflict, deleted by us), and `UD` (stage 3 absent -- the
same conflict from the other branch, deleted by them). The remaining three
(`DD`, `AU`, `UA`) are derived from the single consistent positional rule
those four confirmations establish (documented inline in the source): `A`
at a position means no common-ancestor entry; `D` at a position means that
side's own stage is absent; `U` at a position means that side's own stage
is present. For each stage, `_parse` now requires: present -> valid mode +
`_valid_real_object_id`; absent -> mode `"000000"` + zero placeholder.
Codex's exact reproduced `u UU` with all four modes `"000000"` and all
three OIDs the zero placeholder is rejected (UU requires all three stages
present). A second impossible combination (`DD` with all three stages
presented as real) is also covered.

`STATUS_U_STAGE_PATTERN_VALIDATED=PASS`

### 6. Intent-to-add requires a fully valid type-1 record (Section 12)

**Confirmed automatic**, no separate code change needed: the intent-to-add
classification line (`mode_index == "000000" and mode_head == "000000" ->
paths.add(...)`) already runs strictly AFTER
`_valid_status_type1_or_type2_fixed_fields(...)` -- which now includes the
mode/OID relationship check from item 2 above. Codex's exact reproduced
case (absent HEAD/index modes paired with NONZERO HEAD/index object IDs,
plus a matching stage-0 empty-blob entry) is rejected at the fixed-field
validation step, before the classification line is ever reached -- verified
by a dedicated new regression test.

`INTENT_REQUIRES_FULLY_VALID_TYPE1=PASS`
`INTENT_STAGE_CROSS_CHECKED=PASS` (unchanged two-source cross-check,
now gated by a fully valid record)

### Unknown-state invariant

Every new rejection returns `None` from the single-shot builder, exactly
like every pre-existing rejection path.

`UNKNOWN_INDEX_STATE_BLOCKS=PASS`

## A subtle correctness note caught while writing tests

An initial draft of `_canonical_absolute_path_descriptor` rejected any
`Path` whose `.parts` contained `"."` OR `".."`. A test asserting
`"path:/a/./b"` should be rejected then failed: `Path("/a/./b").parts`
is `("/", "a", "b")` -- pathlib silently and unconditionally drops `"."`
segments during parsing; no literal `"."` ever survives into `.parts`,
so that half of the check was dead code. On reflection this is the
*correct* behavior, not a bug to route around: unlike `".."` (whose
resolved target depends on what the parent directory actually is, and can
differ under symlinks), a `"."` segment is never ambiguous -- `/a/./b` and
`/a/b` are unconditionally the same path. The implementation was corrected
to check only for `".."`, and the docstring and test were updated to
assert the correct behavior (`"path:/a/./b"` normalizes to `Path("/a/b")`,
it does not get rejected) rather than forcing an incorrect rejection to
make a wrong test assumption pass.

## Regression tests added

`test_run_review.py`: +15 tests (228 -> 243).

F03-R2 (3): the exact relative-descriptor reproduction, the exact
traversal-bearing reproduction, and a direct unit test of
`_canonical_absolute_path_descriptor` covering every case (relative,
traversal, non-`path:`, empty, valid, and the `"."`-normalizes-transparently
case).

F04-R2 (11): stage entry with a valid mode but all-zero OID; REUC record
with a valid mode but all-zero OID; type-1 `XY=".."`; type-1 `XY="AA"`;
type-1 present mode + zero OID; type-1 absent mode + nonzero OID; type-2
score `R101`; type-2 score `R999`; unmerged `UU` with everything
absent/zero; unmerged `DD` with everything present (a second impossible
XY/stage-presence combination); and the intent-to-add-from-invalid-status-
record case (absent modes + nonzero OIDs + a matching stage-0 empty blob).

Plus one real-Git positive test (1) explicitly exercising split-index and
sparse-checkout together on a real repository (`git update-index
--split-index` + `git sparse-checkout` cone mode), confirming
`git ls-files --stage` (no `--sparse` flag) always resolves both physical
layouts into the same full per-path logical listing this Runtime already
canonicalizes -- no dedicated handling was ever needed, and this is now
directly demonstrated rather than only reasoned about.

All required "real Git positive matrix" states from prior rounds (Section
18/19) were already covered and are reconfirmed passing unmodified: normal/
assume-unchanged/skip-worktree/assume+skip entries, real unmerged stages
1/2/3, genuine intent-to-add, resolve-undo, ordinary and linked-worktree
index-representation refreshes, a genuine SHA-256 repository, and the
Run-004-class prospective lifecycle.

## Full validation matrix (Section 19)

Full log: `TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_FULL.log`
(this task's log; prior rounds' logs were not rerun; Target-04A was not
rerun).

| Check | Result |
|---|---|
| Focused new F03-R2 cases | PASS (3) |
| Focused new F04-R2 cross-field semantic cases | PASS (12) |
| Focused prior F03/F04 regressions (F03-R1/F04-R1 rounds) | PASS (42) |
| F01 regressions | PASS (50) |
| F02 regressions | PASS (10) |
| F05 regressions | PASS (4) |
| Real Git positive matrix | PASS (16) |
| Run-004-class prospective lifecycle | PASS (3) |
| Coherent protected-metadata bootstrap / prior trusted-content guards | PASS (9) |
| Repair authority / expected-Review1 binding regressions | PASS (7) |
| Full suite `test_run_review.py` | PASS, 243 |
| Full suite `test_run_loop.py` | PASS, 92 (unchanged) |
| Full suite `test_run_repair.py` | PASS, 129 (unchanged) |
| Full suite `tests/adapters/test_claude_code_cli.py` | PASS, 436 (unchanged) |
| Full repository regression `pytest tests/ -q` | PASS, 985 (unchanged root) |
| `.tomtit/dogfood/` full tree | PASS, 464 (92 + 129 + 243) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

**Honest note on transient failures:** during the matrix's concurrent run,
the SAME two unrelated, pre-existing, real-subprocess/real-filesystem
timing tests that have now flaked once each in every Target-04 F03/F04
round's concurrent matrix run --
`test_canonical_index_identity_real_linked_worktree_common_dir_change_rejected`
(its setup expects a `git status` invocation to physically rewrite a
disposable worktree's index; occasionally does not, under heavy concurrent
load) and `test_normal_exit_with_surviving_descendant_kills_descendant` (a
real child-process-timeout test) -- failed again. Neither touches any file
this task modified. Both were rerun in isolation (pass) and both containing
full suites were rerun cleanly with nothing else running concurrently
(pass, recorded in the log's section 20) -- this initial concurrent-run
failure is disclosed here and not erased.

## Out of scope / unchanged

- `F01`, `F02`, `F05` behavior: unmodified.
- `run_loop.py`, `run_repair.py`, `test_run_loop.py`: byte-unchanged (no
  mechanical adjustment was needed).
- Target-04A was not rerun; Target-04B was not started.
- No real product/Claude/Codex model was invoked.
- No push, no tag.

`RUN004_RETROACTIVELY_RECLASSIFIED=NO`
