# Codex Target-04 trusted pre-instability content proof fix review

```text
STATUS=APPROVED
VERDICT=APPROVE
WORK_CLASS=DEFECT_REVIEW
CURRENT_GATE=TARGET_04

BASELINE_COMMIT=f792d5f4b81b2d697d0b9e5917caf5022405fdf7
SOURCE_COMMIT=2578901ff6c385fe4b75449e38badfcd487d1516
IMPLEMENTATION_REPORT_COMMIT=4fa6119b4763b5515191d51aa510336ace8d4920

ORIGINAL_F01_CASE=PASS
RUN002_FAILURE_LOCATION=INITIAL_SNAPSHOT
```

## Verdict

`APPROVE`. The previous
`F01_SAME_MEASUREMENT_CONTENT_MUTATION_PROOF_GAP` is closed. A regular-file
reading made unstable during its own measurement is retry-eligible only when
its device, inode, mode, size, link count, and SHA-256 match evidence parsed
from the stable projection of an earlier completed protected-metadata
snapshot. A same-size A-to-B mutation therefore compares SHA(B) with trusted
SHA(A) and fails closed instead of allowing the current call to authorize
itself.

No concrete current correctness or lifecycle blocker was reproduced. The
review did not invoke a product model, modify or rerun Target-04, start
Target-04B, create or move a tag, or push.

## Commit lineage and reviewed scope

The direct-parent chain is:

```text
f792d5f4b81b2d697d0b9e5917caf5022405fdf7
-> d0ca9b4a6e77ecdaa215d6148cbbd16cacb91bbe
-> 7c729f26cb323005e061ee150410fae35ebddc18
-> 2578901ff6c385fe4b75449e38badfcd487d1516
-> 4fa6119b4763b5515191d51aa510336ace8d4920
```

Thus the reviewed source is correctly descended from the intended baseline,
and the implementation report is its direct child. The reviewed source commit
changes only:

```text
.tomtit/dogfood/run_loop.py
.tomtit/dogfood/run_review.py
.tomtit/dogfood/test_run_review.py
```

The following commit adds only
`TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_REPORT.md`. The cumulative
range also contains the preceding intra-measurement retry source/report pair,
as the lineage requires.

## Trusted-evidence design

`TrustedRegularFileEvidence` is frozen and contains exactly the six
non-timestamp fields required to bind one regular file: device, inode,
permission mode, size, link count, and SHA-256. The current call never adds an
unstable observation to this evidence. `protected_git_metadata_snapshot()`
parses all trusted entries once before its retry loop from its explicit
`trusted_baseline` argument; each fresh attempt receives the same derived
root evidence and per-role dictionaries.

In production dataflow, every nonempty baseline argument comes directly from
an earlier successful return of `protected_git_metadata_snapshot()` or from
index 5 of an earlier completed `_snapshot_workspace_for_review()` tuple.
An exception has no snapshot return, partially built `identity` dictionaries
remain local to the abandoned attempt, and callers represent a caught failure
as `None` or `{}`. Neither representation contains a stable file descriptor,
so it cannot authorize a timestamp-only retry. There is no module-global or
mutable same-call evidence cache.

```text
TRUSTED_PRIOR_ORIGIN=PASS
CURRENT_MEASUREMENT_CANNOT_SELF_AUTHORIZE=PASS
PARTIAL_RESULT_CANNOT_BECOME_TRUSTED=PASS
```

## Initial and later behavior

With no trusted prior, an intra-measurement mismatch confined to timestamps
still raises `EvidenceIntegrityFailure`; it does not establish a baseline from
the bytes it just read. With a completed coherent prior, an unchanged file
whose timestamps alone become unstable can raise the internal retry signal,
consume one attempt from the shared bound, and complete on a later coherent
whole-snapshot attempt.

Disposable-repository regressions independently confirmed both cases and the
original counterexample:

```text
INITIAL_UNTRUSTED_TIMESTAMP_INSTABILITY=PASS
BENIGN_TIMESTAMP_CHANGE_WITH_TRUSTED_PRIOR=PASS
INITIAL_CASE=PASS
LATER_TRUSTED_CASE=PASS
REAL_PREFLIGHT_RECOVERY_PATH=PASS
```

## Run-loop preflight lifecycle

The current Target-04 loop preflight performs these protected snapshot calls.
Calls through `_snapshot_workspace_for_review()` are shown explicitly because
that helper owns the underlying call.

| CALL_ID | PURPOSE | ORDER | HAS_TRUSTED_BASELINE | TRUSTED_BASELINE_SOURCE | IS_INITIAL_ESTABLISHMENT | IS_BEFORE_CAPABILITY_CHECK | IS_AFTER_CAPABILITY_CHECK |
|---|---|---:|---|---|---|---|---|
| L1 | Establish loop baseline for final/fallback evidence | 1 | No | none | Yes | Yes | No |
| L2 | Capture capability-probe BEFORE tuple | 2 | No | none | Yes | Yes | No |
| L3 | Capture capability-probe AFTER tuple | 3 | Yes | completed L2 tuple index 5 | No | No | Yes |
| L4 | Failure-path fallback final metadata, if preflight blocks | 4 | Conditional | completed L1 snapshot when available; otherwise `None` | No | No | Yes |

L3 correctly receives L2, not L1, an unrelated phase, or its own result. An
L2 failure produces `{}`; passing that empty mapping to L3 provides no parsed
per-file evidence and therefore remains fail-closed.

The child review runner has the analogous complete chain:

| CALL_ID | PURPOSE | ORDER | HAS_TRUSTED_BASELINE | TRUSTED_BASELINE_SOURCE | IS_INITIAL_ESTABLISHMENT | IS_BEFORE_CAPABILITY_CHECK | IS_AFTER_CAPABILITY_CHECK |
|---|---|---:|---|---|---|---|---|
| R1 | Review-entry identity | 1 | No | none | Yes | Yes | No |
| R2 | Capability-probe identity | 2 | Yes | completed R1 | No | No | Yes |
| R3 | Post-deterministic-check identity | 3 | Yes | completed R2 | No | No | Yes |
| R4 | Review-exit identity | 4 | Yes | completed R3 | No | No | Yes |

```text
PREFLIGHT_TRUSTED_BASELINE_THREADING=PASS
```

### Preserved run-002 evidence

The preserved run reports `protected_git_metadata_baseline_hash: null` while
recording a nonempty final protected-metadata hash. Under the preserved
run-loop code, that null is emitted only when the initial L1 establishment
snapshot raised and was stored as `None`; therefore the proven failure
location is `INITIAL_SNAPSHOT`. The block itself was raised later by the
capability before/after tuple comparison. Because the evidence did not retain
L2 and L3 separately, it does not prove which capability-pair component also
differed, and this review does not infer one.

```text
RUN002_FAILURE_LOCATION=INITIAL_SNAPSHOT
```

## Stable descriptor grammar

`_STABLE_FILE_DESCRIPTOR_RE.fullmatch()` exactly recognizes:

```text
file:dev=<canonical-int>:ino=<canonical-int>:mode=<four-octal-digits>:
size=<canonical-int>:nlink=<canonical-int>:sha256=<64-lowercase-hex>
```

The canonical integer grammar is exactly Python's `str(int)` shape: zero or a
nonzero digit sequence with optional minus sign and no alternate leading-zero
spelling. `_parse_trusted_file_descriptor()` converts mode from octal and the
other numeric fields from decimal. Timestamp fields, unknown fields,
reordering, truncation, extra suffixes, uppercase/short hashes, malformed
integers, path descriptors, directory descriptors, and symlink descriptors do
not full-match and yield no trusted evidence.

```text
STABLE_DESCRIPTOR_GRAMMAR=PASS
```

## Path and role binding

The root linked-worktree `.git` file is bound only by the dedicated
`@workspace_git_entry` key. Tree members are partitioned by the exact
`git_dir/` and `git_common_dir/` prefixes before the prefix is stripped, then
looked up by their complete relative path. Evidence cannot cross from the root
entry into a tree, between roles, or between relative paths. In an ordinary
repository the two roles legitimately resolve to the same tree; in a linked
worktree they remain separate, while device/inode and content fields provide
an additional object binding.

The resolved `@git_dir`, `@git_common_dir`, and workspace `.git` path values
remain load-bearing in the completed snapshot and are never timestamp-
projected as file evidence.

```text
TRUSTED_PATH_BINDING=PASS
ROLE_PATH_BASELINE_BINDING=PASS
```

## Retry boundary

`_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS` remains 3. There is one `for`
loop and no nested per-file retry. A retry re-enters before root-entry
measurement, recreates the identity dictionary, resolves both roots, and walks
both complete trees again. No completed subtree or abandoned identity mapping
is carried forward. Trusted root/per-role evidence is derived before the loop
and never updated from an attempt or exception.

```text
WHOLE_SNAPSHOT_RETRY=PASS
GLOBAL_RETRY_LIMIT=3
NO_NESTED_RETRY=PASS
NO_PARTIAL_TREE_REUSE=PASS
TRUSTED_BASELINE_IMMUTABLE_DURING_CALL=PASS
```

## Changed call sites

The review checked every changed call site in `run_loop.py` and
`run_review.py`:

- deep-preflight capability AFTER trusts capability BEFORE index 5;
- review 1 and review 2 post snapshots trust their corresponding pre snapshot
  index 5;
- each post-review recheck trusts the completed post-review snapshot it is
  comparing against;
- review-runner capability, post-check, and review-exit phases trust the
  immediately preceding completed phase;
- normal and fallback final metadata trust the loop's initial baseline;
- failed calls represented by an empty mapping contain no parseable trusted
  descriptor and cannot authorize a retry.

No wrong tuple index, cross-attempt review baseline, omitted later baseline,
same-call baseline, or stale phase handoff was found.

```text
CALLSITE_THREADING=PASS
```

## Preserved correctness guarantees

The tolerant branch remains reachable only for regular files and only when
every observed stat difference is confined to mtime/ctime. Device, inode,
type, mode, size, link count, byte count, pathname identity, and SHA-256 remain
load-bearing. Same-size content changes, inode replacement, mode/type changes,
symlink replacement, HEAD/ref drift, Git index drift, linked-worktree gitfile
drift, and capability-probe semantic mutations remain detected by the focused
and affected suites.

Directory re-lstat comparison is unchanged and exact; directory timestamp
instability remains fail-closed. Default callers, including canonical
evidence reading and Review-to-Repair's initial current snapshot, supply no
trusted baseline and retain their conservative behavior. Stable cross-phase
projection and exact canonical descriptor recognition are unchanged.

```text
SEMANTIC_CHANGE_DETECTION=PASS
CROSS_PHASE_PROJECTION=PASS
DEFAULT_FAIL_CLOSED_CALLERS=PASS
DIRECTORY_GUARD_PRESERVED=PASS
SYMLINK_GUARD_PRESERVED=PASS
```

## Independent validation

```text
FOCUSED_TESTS=64/PASS
AFFECTED_TESTS=775/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES
```

The 64-test focused selection covered trusted prior evidence, initial
no-prior behavior, benign later timestamp instability, the exact A-to-B F01
counterexample, prior Target-04 metadata projection and retry regressions,
capability semantic mutation, and Review-to-Repair timestamp reconciliation.
The affected suites were `test_run_review.py`, `test_run_repair.py`,
`test_run_loop.py`, and `tests/adapters/test_claude_code_cli.py`. The exact
full repository regression passed cleanly. No timing-sensitive lifecycle test
failed in this review run.

Compileall wrote caches only beneath a disposable `/private/tmp` prefix.
Ruff, working-tree `git diff --check`, reviewed-range `git diff --check`, and
post-validation SHA-256 checks of the three source/test paths all passed.

## Findings and completion receipt

No blocking or non-blocking correctness finding was identified within the
reviewed change.

```text
STATUS=APPROVED
VERDICT=APPROVE

ORIGINAL_F01_CASE=PASS
TRUSTED_PRIOR_ORIGIN=PASS
CURRENT_MEASUREMENT_CANNOT_SELF_AUTHORIZE=PASS
PARTIAL_RESULT_CANNOT_BECOME_TRUSTED=PASS

INITIAL_UNTRUSTED_TIMESTAMP_INSTABILITY=PASS
BENIGN_TIMESTAMP_CHANGE_WITH_TRUSTED_PRIOR=PASS
PREFLIGHT_TRUSTED_BASELINE_THREADING=PASS
REAL_PREFLIGHT_RECOVERY_PATH=PASS
RUN002_FAILURE_LOCATION=INITIAL_SNAPSHOT

STABLE_DESCRIPTOR_GRAMMAR=PASS
TRUSTED_PATH_BINDING=PASS
ROLE_PATH_BASELINE_BINDING=PASS

WHOLE_SNAPSHOT_RETRY=PASS
GLOBAL_RETRY_LIMIT=3
NO_NESTED_RETRY=PASS
NO_PARTIAL_TREE_REUSE=PASS
TRUSTED_BASELINE_IMMUTABLE_DURING_CALL=PASS
CALLSITE_THREADING=PASS

SEMANTIC_CHANGE_DETECTION=PASS
CROSS_PHASE_PROJECTION=PASS
DEFAULT_FAIL_CLOSED_CALLERS=PASS
DIRECTORY_GUARD_PRESERVED=PASS
SYMLINK_GUARD_PRESERVED=PASS

FOCUSED_TESTS=64/PASS
AFFECTED_TESTS=775/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=OWNER_BINDS_FIX_FOR_TARGET04A_RERUN
```
