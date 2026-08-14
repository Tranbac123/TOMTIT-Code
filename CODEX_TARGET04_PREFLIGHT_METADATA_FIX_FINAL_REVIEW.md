# Codex final review — Target-04 preflight metadata correction

```text
STATUS=APPROVED
VERDICT=APPROVE
WORK_CLASS=DEFECT
CURRENT_GATE=TARGET_04

BASELINE_COMMIT=2fde70af07d58a44b32b28ae688271693c5dffed
FINAL_SOURCE_COMMIT=359e1365664e79d2dd747edadf11086658c206bd
FINAL_IMPLEMENTATION_REPORT_COMMIT=0e29e3882f9fc7f8baee75766c44794c2a53de47
REVIEWED_DIFF_RANGE=2fde70af07d58a44b32b28ae688271693c5dffed..359e1365664e79d2dd747edadf11086658c206bd

FIRST_RUN_EXACT_CAUSE=UNPROVEN
REPRODUCED_RUNTIME_CONTRACT_DEFECT=CONFIRMED
```

## Verdict

`APPROVE` for the cumulative corrected Runtime source state at
`359e1365664e79d2dd747edadf11086658c206bd`.

The review found no concrete correctness blocker within the bounded contract:
original timestamp-only false-positive closure, all F-01 collision and grammar
closures, measurement-time race preservation, bounded retry safety, semantic
repository mutation detection, consumer reconciliation, and regression
preservation.

This approval does not claim the original Target-04A failure was caused by
automatic macOS `com.apple.provenance` reattachment. The exact first-run
changed identity component was not retained, so that historical claim remains
unproven. The independently reproduced Runtime contract defect is confirmed:
cross-phase protected Git identity bound volatile `mtime_ns`/`ctime_ns` and
could therefore report a false immutability violation for metadata-only churn.

## Lineage and scope

The exact direct-parent chain was independently verified:

```text
2fde70af07d58a44b32b28ae688271693c5dffed
-> fbe07396bcc8f1294c7ad897c926320a2f56e4e8
-> 6736f3eed85b5dfcb5d15ffcf303cfdf34dd2b45
-> 5f6edb2763cfd23719dc2b3909c4f0d5eb081463
-> 33659c14c6981f9eff75b0dd62b76b4dd8e6d98a
-> c70ce7f5981481d80584ca121e13fd8f07f825af
-> 3e23e1db4bd424c5da7ed6c6edaa861bf831c3eb
-> 359e1365664e79d2dd747edadf11086658c206bd
-> 0e29e3882f9fc7f8baee75766c44794c2a53de47
```

Each source commit and following report commit has the required direct parent.
The cumulative correction changes only the four justified source/test paths:

```text
.tomtit/dogfood/run_review.py
.tomtit/dogfood/test_run_review.py
.tomtit/dogfood/test_run_loop.py
.tomtit/dogfood/test_run_repair.py
```

The cumulative lineage also contains only the four corresponding authorized
implementation/repair reports. No out-of-scope Runtime subsystem or Target-04
fixture is changed by this correction.

## A. Original defect closure

```text
CROSS_PHASE_TIMESTAMP_STABILITY=PASS
SINGLE_MEASUREMENT_TIMESTAMP_GUARD=PASS
```

`protected_git_metadata_snapshot()` returns a stable projection that removes
only the canonical `mtime_ns` and `ctime_ns` fields from cross-phase identity.
Timestamp-only changes between snapshots no longer change the protected
repository identity.

The projection occurs only after a complete coherent measurement. The
lower-level `_stat_identity()`, `_directory_descriptor()`,
`_regular_file_measurement()`, and `_measure_type_aware_tree()` continue to use
full timestamp-aware before/opened/after identity. Same-size mutation during a
measurement and pathname replacement remain fail-closed.

## B. Load-bearing repository identity

The final projection continues binding:

- descriptor type and protected-tree membership;
- exact path identity;
- mode, device, inode, size, and nlink;
- regular-file SHA-256;
- root `.git` entry identity;
- resolved Git-dir and Git-common-dir paths;
- Git index bytes and semantic stage identity;
- HEAD/ref content;
- the complete protected metadata tree structure.

Focused regressions independently confirmed detection of index stage/content
mutation, HEAD/ref mutation, ordinary root `.git` mode mutation, linked
worktree gitfile content/mode/type mutation, Git configuration drift, and fake
capability-probe mutation of protected Git state.

## C. F-01 closure

### C1. Original path collision

Direct projection results:

```text
projection(path:/tmp/repo:mtime_ns=1:ctime_ns=2/.git)
= path:/tmp/repo:mtime_ns=1:ctime_ns=2/.git

projection(path:/tmp/repo/.git)
= path:/tmp/repo/.git

DISTINCT_AFTER_PROJECTION=YES
```

A real temporary Git repository whose directory contained
`:mtime_ns=1:ctime_ns=2` preserved `@git_dir`, `@git_common_dir`, and
`@workspace_git_entry_path` exactly.

### C2. Prefix-only noncanonical grammar

Opaque `dir:` and `file:` values, truncated descriptors, reordered fields,
and descriptors containing extra fields all remained byte-for-byte unchanged.
Recognition uses complete `fullmatch()` patterns rather than prefix matching
or arbitrary substring replacement.

### C3. Integer lexical canonicality

The recognizer uses the exact lexical grammar of Python `str(int)`:

```text
(?:0|-?[1-9][0-9]*)
```

Direct checks confirmed that `00`, `01`, `0001`, `-0`, `-00`, and `-01` are
not accepted as canonical. The required `dev=001`, `ino=-0`, `size=0003`,
`nlink=-01`, `mtime_ns=001`, and `ctime_ns=-0` descriptors remained unchanged.
Canonical negative integers without leading zeros remained accepted, matching
the producer's plain integer formatting without adding an unsupported
positivity restriction.

## D. Exact canonical descriptor grammar

```text
DIR_CANONICAL_GRAMMAR=PASS
FILE_CANONICAL_GRAMMAR=PASS
```

The directory recognizer exactly matches `_directory_descriptor()`:

```text
dir:dev=<int>:ino=<int>:mode=<four octal digits>:size=<int>:
nlink=<int>:mtime_ns=<int>:ctime_ns=<int>
```

The file recognizer exactly matches `_regular_file_measurement()`:

```text
file:dev=<int>:ino=<int>:mode=<four octal digits>:size=<int>:
nlink=<int>:mtime_ns=<int>:ctime_ns=<int>:sha256=<64 lowercase hex>
```

Field order and the complete string are mandatory. Mode matches
`stat.S_IMODE(...):04o`; SHA matches `hashlib.sha256().hexdigest()`. Successful
projection reconstructs only from captured validated non-timestamp fields.
Real producer-generated directory and file descriptors were both accepted and
retained every load-bearing field.

Path, symlink, unknown, malformed, truncated, reordered, extended, and
noncanonical-integer descriptors are returned byte-for-byte unchanged.

## E. Bounded retry

```text
BOUNDED_RETRY=PASS
RETRY_LIMIT=3
```

The retry loop remains three total attempts with no sleep and no stale-state
reuse. Each attempt rebuilds the composite snapshot. Timestamp-only root `.git`
entry instability may retry; persistent instability exhausts the bound and
fails closed. Git-dir/Git-common-dir path changes and non-timestamp root-entry
changes fail immediately. A real mutation after an initial timestamp-only
retry also fails immediately.

## F. Consumers

`run_loop.py` and `run_repair.py` consume the stable protected snapshot/hash
without reconstructing descriptor grammar and therefore inherit the corrected
semantics without further source changes.

Independent regressions confirmed:

- a fake capability probe modifying protected Git state still blocks deep
  preflight before Attempt1;
- Review-to-Repair timestamp-only reconciliation succeeds;
- Review-to-Repair Git configuration and linked-worktree gitfile drift fail
  with `WORKSPACE_STATE_MISMATCH`.

## Independent validation

```text
FOCUSED_TESTS=50/PASS
AFFECTED_TESTS=762/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
GIT_DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED_AFTER_VALIDATION=YES
```

The first 46-test focused selection encountered the documented timing-sensitive
`test_capability_probe_delayed_descendant_is_quiesced` lifecycle flake: 45
tests passed and that node reported an indeterminate process-group state. The
same node passed immediately when rerun independently. It did not recur in the
complete 762-test affected suites, which passed, and the separate 985-test full
repository regression also passed. The occurrence is outside the cumulative
corrective source diff and is recorded here rather than hidden.

The focused total also includes the semantic Loop capability test, timestamp
Review-to-Repair reconciliation, and two semantic Review-to-Repair drift tests.

## Completion receipt

```text
STATUS=APPROVED
VERDICT=APPROVE

FIRST_RUN_EXACT_CAUSE=UNPROVEN
REPRODUCED_RUNTIME_CONTRACT_DEFECT=CONFIRMED

ORIGINAL_PATH_COLLISION=PASS
NONCANONICAL_DIR_PRESERVED=PASS
NONCANONICAL_FILE_PRESERVED=PASS
INTEGER_LEXICAL_CANONICALITY=PASS
DIR_CANONICAL_GRAMMAR=PASS
FILE_CANONICAL_GRAMMAR=PASS
PATH_IDENTITY_PRESERVED=PASS
SYMLINK_IDENTITY_PRESERVED=PASS
UNKNOWN_GRAMMAR_PRESERVED=PASS
INDEX_MUTATION_DETECTED=PASS
HEAD_REF_MUTATION_DETECTED=PASS
GIT_IDENTITY_MUTATION_DETECTED=PASS
BOUNDED_RETRY=PASS
SEMANTIC_CAPABILITY_MUTATION_BLOCKED=PASS
REVIEW_REPAIR_RECONCILIATION=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
TAG_CREATED=NO
PUSHED=NO

NEXT_ACTION=OWNER_BINDS_APPROVED_RUNTIME_REVISION_FOR_TARGET04A_RERUN
```
