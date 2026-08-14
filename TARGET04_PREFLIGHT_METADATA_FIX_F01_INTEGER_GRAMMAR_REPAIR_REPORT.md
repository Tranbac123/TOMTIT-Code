# Target-04 preflight metadata fix — F-01 integer-grammar precision report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

BLOCKING_FINDING=F01_CANONICAL_INTEGER_LEXICAL_GRAMMAR

PREVIOUS_REPAIR_SOURCE_COMMIT=c70ce7f5981481d80584ca121e13fd8f07f825af
PREVIOUS_REPAIR_REPORT_COMMIT=3e23e1db4bd424c5da7ed6c6edaa861bf831c3eb
```

This is a narrow precision correction to exactly one remaining gap. It makes
no broader Runtime claim: the retry contract, the 3-attempt bound,
measurement-time race detection, path/symlink preservation, and every field
grammar other than the integer fields identified below are unchanged from
the previous (canonical-grammar) commit.

## Root cause

The prior repair used `-?\d+` for `dev`/`ino`/`size`/`nlink`/`mtime_ns`/
`ctime_ns`. That pattern is *broader* than what the producer functions
actually emit: `_directory_descriptor()` and `_regular_file_measurement()`
render every one of those fields with Python's plain `{value}` formatting of
an `os.stat_result` integer field -- i.e. exactly `str(int)` -- which is
lexically canonical: `"0"`, or a non-zero digit sequence with no leading
zeros, optionally negated. `str(int)` never emits `"00"`, `"01"`, `"-0"`, or
`"-01"`; those are alternate spellings of the same numeric value, not
strings the producer can actually write. `-?\d+` accepted all of them
anyway, so a descriptor such as
`dir:dev=001:ino=2:mode=0755:size=64:nlink=1:mtime_ns=100:ctime_ns=200` --
which is not byte-for-byte anything `_directory_descriptor()` emits --
still fullmatched the canonical pattern and had its timestamp fields
stripped, violating the locked requirement that only an *exact* canonical
Runtime-emitted descriptor may be projected.

## Repair

`_CANONICAL_INT = r"(?:0|-?[1-9][0-9]*)"` replaces every `-?\d+` occurrence
for `dev`, `ino`, `size`, `nlink` (all still captured, for reconstruction)
and `mtime_ns`/`ctime_ns` (matched but not captured, as before -- they are
exactly what gets discarded). This is precisely Python's `str(int)` lexical
grammar: `"0"` is the sole spelling of zero; any other value is a leading
digit `1`-`9` followed by zero or more further digits, with an optional
single leading `-` for a negative value. `00`/`01`/`0001`/`-0`/`-00`/`-01`
all fail to match either alternative and are therefore never accepted as
canonical.

`mode` and `sha256` were inspected and left unchanged, per instruction, since
inspection showed no mismatch: `mode` is `stat.S_IMODE(...)` formatted with
an explicit `:04o` format spec, whose value is masked to at most `0o7777`
(4 octal digits) and is *always* zero-padded to exactly 4 digits by that
format spec -- `"0755"` genuinely is the canonical form, unlike a plain
`{int}` field, which is never zero-padded. `sha256` is a
`hashlib.sha256().hexdigest()`, a fixed 64-lowercase-hex-character string
with no leading-zero ambiguity to correct.

No other function changed. `_stable_cross_phase_projection()`, the
3-attempt bounded-retry logic in `protected_git_metadata_snapshot()`, and
every single-measurement race-detection primitive are byte-for-byte
unchanged. `run_loop.py` and `run_repair.py` needed no changes.

## Regression coverage

All in `.tomtit/dogfood/test_run_review.py`.

Required negative cases (each preserved byte-for-byte):

1. `test_strip_volatile_timestamp_fields_preserves_dir_with_leading_zero_dev`
   — `dev=001`.
2. `test_strip_volatile_timestamp_fields_preserves_dir_with_negative_zero_ino`
   — `ino=-0`.
3. `test_strip_volatile_timestamp_fields_preserves_file_with_leading_zero_size`
   — `size=0003`.
4. `test_strip_volatile_timestamp_fields_preserves_file_with_leading_zero_negative_nlink`
   — `nlink=-01`.
5. `test_strip_volatile_timestamp_fields_preserves_leading_zero_mtime_ns`
   — `mtime_ns=001`.
6. `test_strip_volatile_timestamp_fields_preserves_negative_zero_ctime_ns`
   — `ctime_ns=-0`.

One additional positive case,
`test_strip_volatile_timestamp_fields_strips_canonical_negative_integer_fields`,
confirms genuinely canonical negative integers (`dev=-1`, `ino=-12`,
`mtime_ns=-100`, `ctime_ns=-200` -- no leading zero, single leading `-`)
are still correctly recognized and stripped, so the tightened grammar
narrows exactly to the intended lexical form without becoming overly strict
in the other direction.

The two producer-generated positive tests from the prior repair
(`test_strip_volatile_timestamp_fields_strips_real_directory_descriptor`,
`test_strip_volatile_timestamp_fields_strips_real_file_descriptor`) needed
no changes and still pass: real `os.stat_result` integer fields never carry
a leading zero in the first place, so they were never affected by the
overbroad `-?\d+` grammar and remain unaffected by narrowing it.

## Preserved regressions

Rerun and still passing, unchanged in behavior: all prior F-01 tests
(noncanonical opaque `dir:`/`file:`, truncated grammar, reordered fields,
extra fields, path preservation, symlink preservation, the original
real-path collision), cross-phase timestamp stability, same-size content
mutation, index stage mutation, HEAD/ref mutation, root
`.git`/linked-worktree gitfile mutation, bounded-retry
transient/persistent/content-changing behavior (same call-count
assertions), semantic capability-probe mutation blocking Loop preflight,
and Review-to-Repair timestamp-only reconciliation.

## Validation

Full command output in
`TARGET04_PREFLIGHT_METADATA_FIX_F01_INTEGER_GRAMMAR_REPAIR_FULL.log`.

- New canonical-integer focused regressions plus all prior F-01/Target-04
  metadata-fix focused regressions
  (`test_run_review.py -k "linked_worktree or capability_probe or
  protected_git_metadata or immutab or strip_volatile or timestamp_like"`,
  `test_run_loop.py -k capability_probe_semantic`,
  `test_run_repair.py -k timestamp_only_drift`): **46 + 1 + 1 = 48 passed**,
  exit code 0 across all three. (The one unrelated timing-sensitive flake
  noted in the previous repair round,
  `test_capability_probe_delayed_descendant_is_quiesced`, did not recur in
  this run.)
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **762
  passed**, exit code 0 (755 previous baseline + 7 new integer-grammar
  tests).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  `tests/` tree only): **985 passed**, exit code 0, unchanged (no new test
  lives under `tests/`).
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=db91f58c8fdc505aa4d04d6fb2b5b962dfce3513d8376f73ff1028cc3bfbfa47
VALIDATION_LOG_SIZE=3312
```

## Scope audit

Diff (`git diff --stat`) touches exactly two files:

```text
.tomtit/dogfood/run_review.py      | 37 ++++++++++++++++----------
.tomtit/dogfood/test_run_review.py | 53 ++++++++++++++++++++++++++++++++++++++
```

`run_loop.py` and `run_repair.py` source were not modified. The retry
contract, the 3-attempt bound, and single-measurement race detection are
unchanged. `mode`/`sha256` grammar was inspected and left unchanged, since
no mismatch exists. Evidence reporting, Decision Evaluation V0, Product CLI
V0, Target-03, Target-04, and personal-web have no diff and were not opened
for writing.

No real product provider/model was invoked. Target-04 was not modified;
Target-04A was not rerun; Target-04B was not started. Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REREVIEW

REPAIR_SOURCE_COMMIT=359e1365664e79d2dd747edadf11086658c206bd
REPAIR_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

DEV_CANONICAL_INTEGER=PASS
INO_CANONICAL_INTEGER=PASS
SIZE_CANONICAL_INTEGER=PASS
NLINK_CANONICAL_INTEGER=PASS
MTIME_CANONICAL_INTEGER=PASS
CTIME_CANONICAL_INTEGER=PASS

NONCANONICAL_NUMERIC_FORMS_PRESERVED=PASS
PRODUCER_GENERATED_DIR=PASS
PRODUCER_GENERATED_FILE=PASS
PREVIOUS_FOCUSED_REGRESSIONS=PASS

AFFECTED_TESTS=762/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=CODEX_REREVIEWS_FINAL_EXACT_CANONICAL_GRAMMAR
```

This report claims no broader Runtime improvement. It fixes exactly
F01_CANONICAL_INTEGER_LEXICAL_GRAMMAR: integer fields in the canonical
`dir:`/`file:` grammar now match exactly Python's default `str(int)`
lexical form, rejecting leading-zero and negative-zero alternate spellings
that are not byte-for-byte what the producer functions emit.
