# Target-04 preflight metadata fix — F-01 canonical-grammar repair report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

BLOCKING_FINDING=F01_CANONICAL_GRAMMAR_RECOGNITION
ORIGINAL_PATH_COLLISION_FIXED=YES

PREVIOUS_REPAIR_SOURCE_COMMIT=5f6edb2763cfd23719dc2b3909c4f0d5eb081463
PREVIOUS_REPAIR_REPORT_COMMIT=33659c14c6981f9eff75b0dd62b76b4dd8e6d98a
```

This is a narrow repair of exactly one remaining Codex-blocking issue. It
makes no broader Runtime claim: the retry contract, the 3-attempt bound,
lower-level timestamp-aware measurement integrity, and every other metadata
field are unchanged from the previous (partially-fixed) commit. The
original F-01 real-path collision (`.../repo:mtime_ns=1:ctime_ns=2/.git` vs
`.../repo/.git`) remains fixed by the prior repair and is not reopened here.

## Root cause

```text
ROOT_CAUSE=prefix-only descriptor recognition accepted noncanonical dir:/file: strings
```

The prior repair gated stripping on `descriptor.startswith("dir:")` /
`"file:"` (via `_DIR_OR_FILE_DESCRIPTOR_PREFIX_RE = re.compile(r"^(?:dir|file):")`)
and then applied an unconditional substring substitution
(`_VOLATILE_TIMESTAMP_FIELDS_RE = re.compile(r":mtime_ns=\d+:ctime_ns=\d+")`)
to anything with that prefix. This correctly excluded `path:`/`symlink:`
descriptors, but it did not require the *rest* of the string to actually be
one of the two canonical shapes `_directory_descriptor()` /
`_regular_file_measurement()` emit. Any caller-controlled opaque string that
merely *begins* with `dir:` or `file:` — for example
`dir:opaque=caller:mtime_ns=1:ctime_ns=2:payload=retained` — matched the
prefix test and had its embedded `:mtime_ns=1:ctime_ns=2` substring deleted
even though it is not a real Runtime-emitted descriptor at all.

## Repair

```text
REPAIR=exact canonical descriptor grammar recognition before timestamp projection
```

`_strip_volatile_timestamp_fields()` now requires a `re.fullmatch()` against
one of two new patterns that encode the complete canonical grammar, derived
directly from `_directory_descriptor()` and `_regular_file_measurement()`:

```python
_CANONICAL_DIR_DESCRIPTOR_RE = re.compile(
    r"dir:dev=(?P<dev>-?\d+):ino=(?P<ino>-?\d+):mode=(?P<mode>[0-7]{4}):"
    r"size=(?P<size>-?\d+):nlink=(?P<nlink>-?\d+):"
    r"mtime_ns=-?\d+:ctime_ns=-?\d+"
)
_CANONICAL_FILE_DESCRIPTOR_RE = re.compile(
    r"file:dev=(?P<dev>-?\d+):ino=(?P<ino>-?\d+):mode=(?P<mode>[0-7]{4}):"
    r"size=(?P<size>-?\d+):nlink=(?P<nlink>-?\d+):"
    r"mtime_ns=-?\d+:ctime_ns=-?\d+:sha256=(?P<sha256>[0-9a-f]{64})"
)
```

Field shapes were read directly off the two producer functions, not
invented from the finding text:

- `dev`/`ino`/`size`/`nlink` are Python's default `{int}` formatting of
  `os.stat_result` integer fields — plain decimal digits (`-?\d+` only to
  match exactly what that formatting *could* produce for a negative value;
  never observed on a real filesystem, but the grammar must match the true
  range of the producer, not a narrower guess).
- `mode` is `stat.S_IMODE(...)` formatted `:04o`; `S_IMODE`'s mask
  (`0o7777`) means the result is always exactly four octal digits
  (`[0-7]{4}`).
- `sha256` is `hashlib.sha256().hexdigest()`: always exactly 64 lowercase
  hex digits (`[0-9a-f]{64}`).
- `mtime_ns`/`ctime_ns` are matched but *not* captured, since they are
  exactly what gets discarded.

`fullmatch()` (not `match()`/prefix `re.compile(r"^...")`) requires the
*entire* input string to conform, closing the exact gap Codex identified:
truncated descriptors, reordered fields, extra fields, and any
caller-controlled string that only starts with `dir:`/`file:` all fail to
fullmatch and are returned unchanged. On a match, the stripped output is
reconstructed *only* from the captured non-timestamp groups — never by
deleting a substring from the original string — so the result is provably
injective with respect to every load-bearing field: two canonical
descriptors that fullmatch and differ in any non-timestamp field always
produce different captured groups, and therefore different output.

No other function changed. `_stable_cross_phase_projection()`, the
3-attempt bounded-retry logic in `protected_git_metadata_snapshot()`, and
every single-measurement race-detection primitive (`_stat_identity`,
`_directory_descriptor`, `_regular_file_measurement`,
`_measure_type_aware_tree`) are byte-for-byte unchanged. `run_loop.py` and
`run_repair.py` needed no changes — they only ever consume
`protected_git_metadata_snapshot()`'s return value.

One existing test's fixture needed correction, not because its intent
changed but because it was itself using non-canonical data that the
tightened (more correct) grammar now properly rejects:
`test_strip_volatile_timestamp_fields_file_descriptor_retains_sha256`
previously used the 8-character placeholder `sha256=deadbeef`, which is not
a real 64-hex-character `hashlib.sha256().hexdigest()` and therefore does
not fullmatch the canonical grammar. It now uses `"deadbeef" * 8` (64
characters), which correctly exercises canonical-file-descriptor stripping.

## Regression coverage

All in `.tomtit/dogfood/test_run_review.py`.

Negative cases:

1. `test_strip_volatile_timestamp_fields_preserves_noncanonical_dir_with_timestamp_like_text`
   — `dir:opaque=caller:mtime_ns=1:ctime_ns=2:payload=retained` preserved
   byte-for-byte (Codex's exact reproduced example).
2. `test_strip_volatile_timestamp_fields_preserves_noncanonical_file_with_timestamp_like_text`
   — the `file:` counterpart, same treatment.
3. `test_strip_volatile_timestamp_fields_preserves_truncated_canonical_dir_descriptor`
   — missing the trailing `ctime_ns` field, preserved unchanged.
4. `test_strip_volatile_timestamp_fields_preserves_truncated_canonical_file_descriptor`
   — missing the trailing `sha256` field, preserved unchanged.
5. `test_strip_volatile_timestamp_fields_preserves_reordered_canonical_fields`
   — `mode` and `ino` swapped, preserved unchanged.
6. `test_strip_volatile_timestamp_fields_preserves_extra_unknown_field`
   — an otherwise-canonical `dir:` descriptor with a trailing `extra=1`
   field, preserved unchanged.

Positive cases, generated through the real producer functions rather than
hand-written approximations:

7. `test_strip_volatile_timestamp_fields_strips_real_directory_descriptor`
   — calls `_directory_descriptor()` on a real `tmp_path` directory's
   `lstat()`; asserts `mtime_ns=`/`ctime_ns=` are absent from the result and
   every other field matches the live metadata exactly.
8. `test_strip_volatile_timestamp_fields_strips_real_file_descriptor`
   — calls `_regular_file_measurement()` on a real `tmp_path` file; asserts
   `mtime_ns=`/`ctime_ns=` are absent and every other field, including a
   `hashlib.sha256()` computed independently over the same content,
   survives exactly.

## Preserved regressions

Rerun and still passing, unchanged in behavior:

- All prior F-01 tests (path/symlink/unrecognized-grammar preservation,
  distinct-path non-collision, ordinary `dir:`/`file:` stripping) —
  14 tests.
- Cross-phase timestamp-only stability, same-size content mutation, index
  stage mutation, HEAD/ref mutation, root `.git`/linked-worktree gitfile
  mutation (six tests), bounded-retry transient/persistent/content-changing
  behavior (three tests, same call-count assertions as before) — all
  unchanged.
- `test_capability_probe_semantic_repository_mutation_still_blocks_preflight`
  in `test_run_loop.py`.
- `test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift`
  in `test_run_repair.py`.

One pre-existing, unrelated flake was observed and is not a regression:
`test_capability_probe_delayed_descendant_is_quiesced` failed once with
`SUBPROCESS_QUIESCENCE_UNVERIFIED` (a timing-sensitive background-process
quiescence probe, the same class of flake Codex's prior re-review noted in
an unrelated node) during the focused-selection run, then passed cleanly
3/3 in isolation and cleanly in every subsequent full run, including the
final logged validation below. It touches process-group lifecycle handling,
not this commit's descriptor-grammar change.

## Validation

Full command output in `TARGET04_PREFLIGHT_METADATA_FIX_F01_GRAMMAR_REPAIR_FULL.log`.

- Canonical-grammar focused tests plus all previous F-01/Target-04
  metadata-fix focused regressions
  (`test_run_review.py -k "linked_worktree or capability_probe or
  protected_git_metadata or immutab or strip_volatile or timestamp_like"`,
  `test_run_loop.py -k capability_probe_semantic`,
  `test_run_repair.py -k timestamp_only_drift`): **39 + 1 + 1 = 41 passed**,
  exit code 0 across all three.
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **755
  passed**, exit code 0 (747 previous baseline + 8 new canonical-grammar
  tests).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  `tests/` tree only): **985 passed**, exit code 0, unchanged (no new test
  lives under `tests/`).
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=11228918b935f6ee70ca7ba3d1b632c030c00635382f3af2b7de91e8a98f4a33
VALIDATION_LOG_SIZE=3533
```

## Scope audit

Diff (`git diff --stat`) touches exactly two files:

```text
.tomtit/dogfood/run_review.py      | 74 ++++++++++++++++++++--------
.tomtit/dogfood/test_run_review.py | 99 +++++++++++++++++++++++++++++++++++++-
```

`run_loop.py` and `run_repair.py` source were not modified — no concrete
need was demonstrated by this exact blocker (both only ever consume
`protected_git_metadata_snapshot()`'s return value). The retry contract and
3-attempt bound are unchanged. No other metadata field was relaxed. The
separate evidence-reporting limitation was not touched. Decision Evaluation
V0, Product CLI V0, Target-03, Target-04, WorktreeManager, routing, memory,
FindingTriage, recovery, self-improvement, and personal-web have no diff
and were not opened for writing.

No real product provider/model was invoked. Target-04 was not modified;
Target-04A was not rerun; Target-04B was not started. Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REREVIEW

REPAIR_SOURCE_COMMIT=c70ce7f5981481d80584ca121e13fd8f07f825af
REPAIR_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

CANONICAL_DIR_RECOGNITION=PASS
CANONICAL_FILE_RECOGNITION=PASS
NONCANONICAL_DIR_PRESERVED=PASS
NONCANONICAL_FILE_PRESERVED=PASS
PATH_PRESERVED=PASS
SYMLINK_PRESERVED=PASS
UNKNOWN_GRAMMAR_PRESERVED=PASS

PREVIOUS_FOCUSED_REGRESSIONS=PASS
AFFECTED_TESTS=755/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=CODEX_REREVIEWS_CANONICAL_GRAMMAR_FIX_ONLY
```

This report claims no broader Runtime improvement. It fixes exactly
F01_CANONICAL_GRAMMAR_RECOGNITION: descriptor recognition now requires an
exact `fullmatch` against the complete canonical grammar `dir:`/`file:`
descriptors actually take, not merely a type prefix.
