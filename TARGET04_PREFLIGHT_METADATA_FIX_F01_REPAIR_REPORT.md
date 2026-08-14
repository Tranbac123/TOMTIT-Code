# Target-04 preflight metadata fix — F-01 repair report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

BLOCKING_FINDING=F-01_PATH_DESCRIPTOR_PROJECTION_COLLISION

PREVIOUS_SOURCE_COMMIT=fbe07396bcc8f1294c7ad897c926320a2f56e4e8
PREVIOUS_IMPLEMENTATION_REPORT_COMMIT=6736f3eed85b5dfcb5d15ffcf303cfdf34dd2b45
```

This is a narrow repair of exactly one Codex-blocking finding. It makes no
broader Runtime claim: the retry contract, the 3-attempt bound,
measurement-time race detection, and every other metadata field are
unchanged from the previous (blocked) implementation.

## Root cause

```text
ROOT_CAUSE=untyped timestamp regex rewrote load-bearing path descriptors
```

`_strip_volatile_timestamp_fields()` applied
`_VOLATILE_TIMESTAMP_FIELDS_RE` (`r":mtime_ns=\d+:ctime_ns=\d+"`)
unconditionally to every descriptor string in the identity dict, regardless
of descriptor type. `path:` descriptors embed a real, caller-controlled
filesystem path. When that path happened to contain the literal substring
`:mtime_ns=<digits>:ctime_ns=<digits>` (Codex's reproduction used a
directory named `repo:mtime_ns=1:ctime_ns=2`), the regex matched inside the
path text itself and silently deleted it — not a timestamp field at all.
Codex demonstrated this collapses two distinct real paths
(`.../repo:mtime_ns=1:ctime_ns=2/.git` and `.../repo/.git`) onto the same
projected value, which is exactly the invariant violation the fix was
required to avoid: "stable projection excludes exactly timestamp metadata
fields, and no load-bearing path/type/content identity is altered."

## Repair

```text
REPAIR=descriptor-type-aware projection
```

`_strip_volatile_timestamp_fields()` now gates on descriptor type before
touching anything:

```python
_DIR_OR_FILE_DESCRIPTOR_PREFIX_RE = re.compile(r"^(?:dir|file):")

def _strip_volatile_timestamp_fields(descriptor: str) -> str:
    if not _DIR_OR_FILE_DESCRIPTOR_PREFIX_RE.match(descriptor):
        return descriptor
    return _VOLATILE_TIMESTAMP_FIELDS_RE.sub("", descriptor, count=1)
```

- `dir:` and `file:` descriptors: their `mtime_ns=<int>:ctime_ns=<int>`
  field pair is entirely constructed by `_directory_descriptor()` /
  `_regular_file_measurement()` from integer `os.stat_result` fields, in a
  fixed position, with no caller-controlled text anywhere in the
  descriptor. Stripping is safe and precise for these two types only.
- `path:` and `symlink:` descriptors: returned byte-for-byte unchanged.
  These embed a real filesystem path or symlink target, which is
  caller-controlled and must never be regex-rewritten.
- Any other/unrecognized descriptor grammar: also returned unchanged
  (preserve, the safe default), since the grammar cannot be positively
  confirmed non-path-like.

`count=1` bounds the substitution to a single occurrence even though the
fixed descriptor grammar only ever contains the pair once (defense in
depth, not a behavior change).

No other function changed. `_stable_cross_phase_projection()`,
`protected_git_metadata_snapshot()`'s bounded-retry logic, the 3-attempt
limit, and every single-measurement race-detection primitive
(`_stat_identity`, `_directory_descriptor`, `_regular_file_measurement`,
`_measure_type_aware_tree`) are byte-for-byte unchanged from the previous
(blocked) commit. `run_loop.py` and `run_repair.py` needed no changes, as
before — they only ever consume `protected_git_metadata_snapshot()`'s
return value.

## F-01 regression coverage

All in `.tomtit/dogfood/test_run_review.py`:

- **A** — `test_strip_volatile_timestamp_fields_preserves_path_descriptor_byte_for_byte`:
  `path:/tmp/repo:mtime_ns=1:ctime_ns=2/.git` projects to itself, byte-for-byte.
- **B** — `test_strip_volatile_timestamp_fields_does_not_collide_distinct_paths`:
  the same colliding path and `path:/tmp/repo/.git` remain distinct after
  projection (the exact non-injectivity Codex demonstrated).
- **C** — `test_protected_git_metadata_git_dir_path_with_timestamp_like_text_is_preserved`:
  a real, committed Git repository whose own directory name is
  `repo:mtime_ns=1:ctime_ns=2`; `@git_dir` in the returned snapshot matches
  the live resolved git-dir path exactly and still contains the
  timestamp-like substring.
- **D** — `test_protected_git_metadata_git_common_dir_path_with_timestamp_like_text_is_preserved`:
  a linked worktree off that same primary repository; `@git_common_dir` on
  the linked worktree's snapshot matches the live resolved common-dir path
  exactly (confirmed distinct from git-dir, proving this is a genuine
  linked-worktree case) and still contains the timestamp-like substring.
- **E** — `test_protected_git_metadata_workspace_root_git_path_with_timestamp_like_text_is_preserved`:
  the workspace-root `.git` entry path key on the same primary repository
  matches the live resolved path exactly.
- **F** — `test_strip_volatile_timestamp_fields_preserves_symlink_descriptor_byte_for_byte`:
  a `symlink:` descriptor containing timestamp-like text projects to
  itself, byte-for-byte.
- **G** — `test_strip_volatile_timestamp_fields_dir_descriptor_removes_only_timestamp_fields`:
  an ordinary `dir:` descriptor still has exactly `mtime_ns`/`ctime_ns`
  removed, nothing else.
- **H** — `test_strip_volatile_timestamp_fields_file_descriptor_retains_sha256`:
  an ordinary `file:` descriptor still has exactly `mtime_ns`/`ctime_ns`
  removed while every other field, including `sha256`, survives.

One additional test,
`test_strip_volatile_timestamp_fields_preserves_unrecognized_descriptor_grammar`,
covers required-correction item 5 (unknown descriptor grammar is preserved,
not silently rewritten) directly, since it is not otherwise implied by A-H.

## Preserved regressions from the previous fix

Rerun and still passing, unchanged in behavior:

- Cross-phase timestamp-only stability
  (`test_protected_git_metadata_ctime_only_drift_is_cross_phase_stable`).
- Same-size content mutation during measurement still rejected
  (`test_protected_git_metadata_same_size_content_mutation_is_detected`).
- Index content/stage mutation still detected
  (`test_protected_git_metadata_index_stage_mutation_is_detected`).
- HEAD/ref mutation still detected
  (`test_protected_git_metadata_head_ref_mutation_is_detected`).
- Root `.git` / linked-worktree gitfile identity mutation still detected
  (five pre-existing tests plus
  `test_protected_git_metadata_root_directory_mode_change_is_detected`).
- Bounded retry behavior unchanged: transient timestamp-only recovery,
  persistent-instability fail-closed, and content-changing-race-during-retry
  fail-closed (three tests, all still passing with the same call-count
  assertions).
- Semantic capability-probe mutation still blocks deep preflight
  (`test_capability_probe_semantic_repository_mutation_still_blocks_preflight`
  in `test_run_loop.py`).
- Review-to-Repair timestamp-only reconciliation still passes
  (`test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift`
  in `test_run_repair.py`).

## Validation

Full command output in `TARGET04_PREFLIGHT_METADATA_FIX_F01_REPAIR_FULL.log`.

- F-01 focused tests plus previous required focused selection
  (`test_run_review.py -k "linked_worktree or capability_probe or
  protected_git_metadata or immutab or strip_volatile or timestamp_like"`,
  `test_run_loop.py -k capability_probe_semantic`,
  `test_run_repair.py -k timestamp_only_drift`): **31 + 1 + 1 = 33 passed**,
  exit code 0 across all three.
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **747
  passed**, exit code 0 (738 previous baseline + 9 new F-01 tests).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  `tests/` tree only): **985 passed**, exit code 0, unchanged (no new test
  lives under `tests/`).
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=5006d6147e75f84b90fb9212edfc749eb0d5adfb9bf269d581c8f6727c90f87c
VALIDATION_LOG_SIZE=3285
```

## Scope audit

Diff (`git diff --stat`) touches exactly two files:

```text
.tomtit/dogfood/run_review.py      |  22 ++++++--
.tomtit/dogfood/test_run_review.py | 105 +++++++++++++++++++++++++++++++++++++
```

`run_loop.py` and `run_repair.py` source were not modified — no concrete
need was demonstrated by F-01 (both only ever consume
`protected_git_metadata_snapshot()`'s return value). The retry contract and
3-attempt bound are unchanged. No other metadata field was relaxed. The
separate evidence-reporting limitation Codex explicitly excluded from this
finding was not touched. Decision Evaluation V0, Product CLI V0, Target-03,
Target-04, and personal-web have no diff and were not opened for writing.

No real product provider/model was invoked. Target-04 was not modified;
Target-04A was not rerun; Target-04B was not started. Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REREVIEW

REPAIR_SOURCE_COMMIT=5f6edb2763cfd23719dc2b3909c4f0d5eb081463
REPAIR_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

PATH_DESCRIPTORS_BYTE_PRESERVED=YES
SYMLINK_DESCRIPTORS_BYTE_PRESERVED=YES
DIR_TIMESTAMP_FIELDS_EXCLUDED=YES
FILE_TIMESTAMP_FIELDS_EXCLUDED=YES
PATH_COLLISION_REGRESSION=PASS

PREVIOUS_FOCUSED_REGRESSIONS=PASS
AFFECTED_TESTS=747/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=CODEX_REREVIEWS_ONLY_F01_AND_REGRESSION_PRESERVATION
```

This report claims no broader Runtime improvement. It fixes exactly F-01:
the stable cross-phase projection now excludes exactly the two intended
timestamp metadata fields from `dir:`/`file:` descriptors, and leaves every
`path:`/`symlink:`/unrecognized descriptor byte-for-byte unchanged.
