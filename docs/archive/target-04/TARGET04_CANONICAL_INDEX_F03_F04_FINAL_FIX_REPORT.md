# TARGET-04 Canonical Index F03/F04 Final Fix Report

WORK_CLASS=RUNTIME_DESIGN_CORRECTION
CURRENT_GATE=TARGET_04

## Scope

This is a narrow correction of exactly the two remaining blocking findings
from the latest independent Codex review
(`/Users/tranvanbac/Documents/AI/ai-agent/TOMTIT-E2E-Evidence/CODEX_TARGET04_CANONICAL_INDEX_AUTHORITY_CORRECTION_REVIEW.md`,
verdict `REQUEST_CHANGES`):

- `F03-R1` -- inconsistent linked-worktree role paths still relax `git_dir/index`
- `F04-R1` -- recognized Git output remains incompletely validated

`F01`, `F02`, and `F05` were already `PASS`/closed per that review and are
**not** redesigned here; only `.tomtit/dogfood/run_review.py` and
`.tomtit/dogfood/test_run_review.py` change. `run_loop.py`, `run_repair.py`,
and `test_run_loop.py` are byte-unchanged.

## Baseline / lineage

- `BASELINE_COMMIT` (repo `HEAD` at task start): `97ae3eabff6ecdb11d402ad978a6cf03e184310f`
- `REVIEWED_SOURCE_COMMIT`: `bb48a29005ae6a4d4fb35967a11b658a57226d30`
- `REVIEWED_IMPLEMENTATION_REPORT_COMMIT`: `97ae3eabff6ecdb11d402ad978a6cf03e184310f`

## F01/F02/F05: remain closed, unmodified

- `F01_REMAINS_CLOSED=PASS` -- `_MAX_CANONICAL_INDEX_OBSERVATIONS = 3` and the
  two-consecutive-complete-match algorithm in `canonical_index_projection()`
  are byte-unchanged. All existing F01 focused tests pass unmodified.
- `F02_REMAINS_CLOSED=PASS` -- `protected_git_metadata_preserved()`'s
  unconditional `canonical_index_identity_before is not None and ... after is
  not None and before == after` gate, evaluated before any raw-equality
  return, is unchanged. All existing F02 focused tests pass unmodified.
- `F05_REMAINS_CLOSED=PASS` -- the `protected_git_metadata_preserved` /
  `protected_git_metadata_raw_representation_preserved` /
  `index_representation_diagnostic` evidence contract in `run_loop.py` is
  entirely untouched (that file was not edited this round). All existing F05
  focused tests pass unmodified.

## F03-R1 -- inconsistent role paths still relax `git_dir/index`

**Exact defect** (`_index_metadata_exception_keys`,
`.tomtit/dogfood/run_review.py`): for any two *present, unequal* role paths,
the function unconditionally initialized `exception_keys = {_GIT_DIR_INDEX_KEY}`
*before* determining whether `git_dir` was actually nested inside
`git_common_dir`. When the nesting proof failed, the nested-alias key was
correctly withheld, but `git_dir/index` itself leaked through regardless.
Reproduced exactly as Codex described:

```text
metadata = {"@git_dir": "path:/x", "@git_common_dir": "path:/y"}
_index_metadata_exception_keys(metadata) -> frozenset({"git_dir/index"})   # WRONG
```

**Fix:** the function now computes the nesting proof
(`_common_dir_relative_alias_for_nested_git_dir`, using
`pathlib.Path.relative_to` on the already-`.resolve()`d role paths -- real
path containment, never string-prefix matching) *first*, and returns the
empty set immediately if that proof fails, before adding any key at all:

```python
if git_dir_path == git_common_dir_path:
    return frozenset({_GIT_DIR_INDEX_KEY, _GIT_COMMON_DIR_INDEX_KEY})
nested_relative = _common_dir_relative_alias_for_nested_git_dir(git_dir_path, git_common_dir_path)
if nested_relative is None:
    return frozenset()
return frozenset({_GIT_DIR_INDEX_KEY, f"git_common_dir/{nested_relative}/index"})
```

Now: `path:/x` vs `path:/y` -> `frozenset()`. A misleading textual prefix
(`/repo/.git-other/...` vs `/repo/.git`) also correctly yields `frozenset()`,
since `Path.relative_to` compares resolved path *components*, not string
prefixes. The ordinary-repository case (equal paths) and the valid
linked-worktree case (proven strict nesting, granting both `git_dir/index`
and its common-dir-tree-walk alias) are unchanged from the prior round --
this was a narrow reordering of an existing correct proof, not a redesign.

`F03_LINKED_WORKTREE_ROLE_HANDLING=PASS`
`INCONSISTENT_ROLE_PATHS_FAIL_CLOSED=PASS`
`CURRENT_INDEX_EXCEPTION_ONLY_AFTER_ROLE_PROOF=PASS`
`DISTINCT_COMMON_INDEX_STRICT=PASS` (unchanged behavior, reconfirmed by the
existing real-linked-worktree regression)

## F04-R1 -- recognized Git output remains incompletely validated

Three sub-defects, each fixed narrowly within the existing single-shot
projection builder (`_build_canonical_index_projection_once` and its
helpers); the outer F01 bounded-coherence loop is untouched.

### 1. Repository object format was inferred per-record from OID length

**Fix:** `_repository_object_format(cwd)` positively determines the
repository's object storage format via the read-only plumbing query `git
rev-parse --show-object-format` (supported by the installed Git version --
confirmed 2.41.0 here), returning `(format_name, hex_id_length)` for a
recognized format (`sha1`->40, `sha256`->64) or `None` for a failed/
unrecognized query. `_build_canonical_index_projection_once` calls this
*once*, first, before any of the three queries run, and threads the same
`expected_oid_length` (and the correspondingly-selected empty-blob id, from
the new `_EMPTY_BLOB_OBJECT_ID_BY_FORMAT` mapping -- replacing the old
by-length `_EMPTY_BLOB_OBJECT_ID_BY_LENGTH`) into every OID validated from
every one of the three sources (stage, status, resolve-undo). The accepted
projection also records `@object_format` so two observations under
different formats can never spuriously compare equal. An unavailable or
unrecognized format makes the whole attempt `None` -- never a guess, and
never decided source-by-source. The outer F01 2-of-3 bound is unaffected:
this is one additional deterministic step inside the existing single-shot
builder, not a new retry loop.

`REPOSITORY_OBJECT_FORMAT=sha1 (this repository); sha1 and sha256 both exercised in tests`
`OBJECT_ID_WIDTH=40 (sha1) / 64 (sha256)`
`OBJECT_FORMAT_BOUND_TO_COMPLETE_OBSERVATION=PASS`
`EMPTY_BLOB_BOUND_TO_REPOSITORY_FORMAT=PASS`

### 2. Mode grammar was lexical (`[0-7]{6}`), not real Git modes

**Fix:** `_INDEX_ENTRY_VALID_MODES = frozenset({b"100644", b"100755",
b"120000", b"160000"})` (regular file, executable, symlink,
gitlink/submodule -- the only modes a cached index entry or resolve-undo
record can ever have; `git ls-files` without `--sparse` never emits a
tree-mode entry). `_STATUS_VALID_MODES` adds `000000` (the documented
"absent" placeholder used only in porcelain-v2's HEAD/index/worktree mode
triplet). `777777` -- lexically valid six-digit octal, not a real Git mode
-- is now rejected everywhere a mode is checked (stage records, resolve-undo
records, and all three porcelain-v2 mode fields per record type).

`VALID_INDEX_MODES=100644, 100755, 120000, 160000 (plus 000000 for porcelain-v2 mode fields only)`

### 3. Tag/stage combinations were validated independently, not relationally

**Fix:** `_valid_stage_tag_combination(base_tag, assume_unchanged, stage)`
validates the (tag, assume-unchanged, stage) triple as one semantic rule:
`H`/`S` (any case) only at stage 0; `M` only at stage 1/2/3 and *never* with
the assume-unchanged bit set. Codex's exact reproduced impossible
combinations (`H` at stage 1, `M` at stage 0, lowercase `m` at stage 1) are
now rejected. Real, Git-producible combinations (ordinary/assume-unchanged/
skip-worktree/assume+skip at stage 0, unmerged at stages 1/2/3) remain
accepted -- verified both via direct unit tests against the validator and via
the full existing real-Git regression matrix.

`TAG_STAGE_RELATIONSHIP_VALIDATED=PASS`

### 4. Porcelain-v2 fields were bound but not validated

**Fix** (`_intent_to_add_paths_from_status_v2`, now delegating fixed-field
validation to a shared `_valid_status_type1_or_type2_fixed_fields` helper for
type "1"/"2", plus inline validation for type "u"):

- **XY**: per-position alphabets confirmed against real `git status
  --porcelain=v2` output across every state this Runtime's regression matrix
  exercises (ordinary/staged/worktree modify, add, delete, typechange,
  rename, copy, intent-to-add -- which empirically showed `Y="A"` -- and
  every documented unmerged pair). Type "1"/"2": X and Y each drawn from
  `{.,M,T,A,D}` (X additionally restricted to `{R,C}` for type "2", since
  Git always emits detected renames/copies as type "2", never type "1").
  Type "u": XY must be exactly one of the seven documented pairs (`DD`,
  `AU`, `UD`, `UA`, `DU`, `AA`, `UU`) -- not independently-valid characters.
  Codex's exact reproduced `XY=ZZ` is rejected.
- **Submodule field**: `N...` (not a submodule) or `S<c><m><u>` with each of
  c/m/u its own documented letter or `.`. Codex's exact reproduced
  `sub=xxxx` is rejected.
- **Modes**: every mode field (mH/mI/mW for type 1/2; m1/m2/m3/mW for type
  u) validated against `_STATUS_VALID_MODES`.
- **OIDs**: every OID field (hH/hI for type 1/2; h1/h2/h3 for type u) is now
  actually validated (previously bound to a variable and discarded
  unchecked) against the repository's own object-format width -- a
  documented zero-placeholder value is itself valid hex of that width and
  needs no special case.
  scan: `not-an-oid`, and a wrong-width-but-otherwise-valid-hex OID, are
  both rejected.
- **Type-2 rename/copy score**: `_STATUS_TYPE2_SCORE_RE = re.compile(rb"[RC](0|[1-9][0-9]*)")`,
  additionally required to match the record's own XY.X letter. Codex's exact
  reproduced `Xbad` is rejected; real `git mv`-produced `R100` remains
  accepted (confirmed by the existing rename regression test).

`STATUS_XY_VALIDATED=PASS`
`STATUS_SUBMODULE_VALIDATED=PASS`
`STATUS_TYPE1_FULLY_VALIDATED=PASS`
`STATUS_TYPE2_FULLY_VALIDATED=PASS`
`STATUS_U_FULLY_VALIDATED=PASS`
`TYPE2_SCORE_VALIDATED=PASS`

### 5. Intent-to-add cross-check now repository-format bound

Unchanged two-source cross-check structure (status classification vs.
matching stage-0 entry), but the empty-blob comparison now uses
`_EMPTY_BLOB_OBJECT_ID_BY_FORMAT[format_name]` (selected once, from the
positively-determined repository format) instead of
`_EMPTY_BLOB_OBJECT_ID_BY_LENGTH.get(len(object_id))` (selected from the
presented value's own length). A SHA-256 empty-blob id presented inside a
SHA-1 repository is now rejected (caught by the object-id width check before
ever reaching the empty-blob comparison, since the repository's bound width
is 40 and the presented id is 64).

`INTENT_TO_ADD_CROSS_CHECKED=PASS`

### 6. Resolve-undo

Same object-format-bound OID and real-mode validation applied
(`_parse_canonical_resolve_undo_record` now takes `expected_oid_length` and
checks `mode in _INDEX_ENTRY_VALID_MODES`). `777777` and a wrong-width OID
are both rejected; the existing real conflict/resolution regression test
(REUC entries from an actual merge) continues to pass unmodified.

`RESOLVE_UNDO_FULLY_VALIDATED=PASS`

### Unknown-state invariant

Every new rejection path returns `None` from the single-shot builder,
exactly like every pre-existing rejection path -- `UNKNOWN_INDEX_STATE_BLOCKS=PASS`.

## A bytes-iteration bug caught during implementation

While writing the type-1/type-2 XY validation, an initial
`frozenset(b".MTAD")` was used for the per-position character alphabets.
This iterates a `bytes` object into its integer byte codes (`{46, 77, 84,
65, 68}`), never single-byte `bytes` objects -- so `xy[0:1] in
_STATUS_TYPE1_X_CHARS` (a `bytes` slice against a set of `int`s) was always
`False`, rejecting every real status record and breaking six pre-existing
passing tests. Caught immediately by running the full `test_run_review.py`
suite before considering the change complete; fixed to explicit
`frozenset({b".", b"M", b"T", b"A", b"D"})` literals, with a comment warning
against the same mistake recurring. This is the identical class of bug
caught and fixed for `_INDEX_STAGE_BASE_TAGS` in an earlier round.

## Regression tests added

`test_run_review.py`: +27 tests (201 -> 228).

F03-R1 (2): the exact Codex reproduction (`/x` vs `/y` -> empty set) and the
misleading-textual-prefix case (no real path containment despite a shared
string prefix).

F04-R1 (25): `_repository_object_format` on a real sha1 repo, on a
command-failure, and on an unrecognized-format response; a full real
**SHA-256 repository** positive lifecycle (object format detection, 64-char
canonical entries, a genuine `add -N` intent-to-add entry validated against
the SHA-256 empty blob -- Section 7's permitted inverse case, confirmed
supported by the installed Git); direct unit tests for
`_valid_stage_tag_combination`'s required positive and impossible cases;
and, via the existing `_patch_canonical_index_queries` fault-injection
helper against a real committed workspace, every adversarial case Section 22
requires: stage/resolve-undo mode `777777`; `H`@stage1; `M`@stage0;
lowercase `m`@stage1; a SHA-256-width stage OID in a SHA-1 repo; mixed OID
widths across two sources in one observation; the real SHA-256 empty-blob id
presented in a SHA-1 repo; `XY=ZZ`; `sub=xxxx`; a malformed type-1 OID; a
wrong-width (but valid-hex) status OID; the exact `Xbad` type-2 score; a
malformed type-2 mode field; an invalid unmerged XY pair; an invalid
unmerged submodule field; an invalid unmerged mode; an invalid unmerged OID;
and a wrong-width resolve-undo OID.

All required "real Git positive matrix" states (Section 23) were already
covered by the prior round's tests and are reconfirmed passing unmodified by
this round's full-suite run: normal/assume-unchanged/skip-worktree/assume+
skip entries, real unmerged stages 1/2/3, genuine intent-to-add, resolve-undo,
split/sparse-index normalization (via `git ls-files`'s own canonicalization,
as previously noted -- no dedicated split/sparse fixture exists or is
needed), ordinary and linked-worktree index-representation refreshes, and
the Run-004-class prospective lifecycle.

## Full validation matrix (Section 24)

Full log: `TARGET04_CANONICAL_INDEX_F03_F04_FINAL_FIX_FULL.log` (this task's
log; prior rounds' logs were not rerun; Target-04A was not rerun).

| Check | Result |
|---|---|
| Focused F03-R1 cases | PASS (17) |
| Focused F04-R1 cases | PASS (25) |
| F01 regressions | PASS (39) |
| F02 regressions | PASS (10) |
| F05 regressions | PASS (4) |
| Canonical index identity semantic matrix | PASS (15) |
| Real linked-worktree + real object-format tests | PASS (3) |
| Run-004-class prospective lifecycle | PASS (3) |
| Coherent protected-metadata bootstrap / prior trusted-content guards | PASS (9) |
| Repair authority / expected-Review1 binding regressions | PASS (7) |
| Full suite `test_run_review.py` | PASS, 228 |
| Full suite `test_run_loop.py` | PASS, 92 (unchanged) |
| Full suite `test_run_repair.py` | PASS, 129 (unchanged) |
| Full suite `tests/adapters/test_claude_code_cli.py` | PASS, 436 (unchanged) |
| Full repository regression `pytest tests/ -q` | PASS, 985 (unchanged root) |
| `.tomtit/dogfood/` full tree | PASS, 449 (92 + 129 + 228) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

**Honest note on transient failures:** during the matrix's concurrent run,
two unrelated, pre-existing, real-subprocess/real-filesystem timing tests
failed once each --
`test_canonical_index_identity_real_linked_worktree_common_dir_change_rejected`
(its setup expects a `git status` invocation to physically rewrite a
disposable worktree's index; occasionally does not, under heavy concurrent
load) and `test_no_child_process_survives_a_timeout`'s adapter-suite sibling
`test_normal_exit_with_surviving_descendant_kills_descendant` (a real
child-process-timeout test). Neither touches any file this task modified.
Both were rerun in isolation (pass) and both containing full suites were
rerun cleanly with nothing else running concurrently (pass, recorded in the
log's section 20) -- this initial concurrent-run failure is disclosed here
and not erased, per the task's own instruction.

## Out of scope / unchanged

- `F01`, `F02`, `F05` behavior: unmodified (see above).
- `run_loop.py`, `run_repair.py`, `test_run_loop.py`: byte-unchanged.
- Target-04A was not rerun; Target-04B was not started.
- No real product/Claude/Codex model was invoked.
- No push, no tag.

`RUN004_RETROACTIVELY_RECLASSIFIED=NO`
