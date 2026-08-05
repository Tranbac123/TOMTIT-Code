# Gate Evidence — Gate 3 Claude Code CLI Capability Spike

## Gate decision

```yaml
gate_id: GATE_3_CLAUDE_CODE_CLI_CAPABILITY_SPIKE
gate_status: APPROVED
capability_status: PROVEN
review_verdict: APPROVE_WITH_NON_BLOCKING_FINDINGS
review_budget_status: COMPLETE
mechanical_review_status: PENDING
recorded_at: 2026-08-05T17:09:38+07:00
recorded_at_basis: FINAL_APPROVAL_REVIEW_COMMIT
next_gate: NONE_OWNER_DOGFOOD_HOLD
no_next_gate_authorized: true
no_further_gate_3_correctness_review: true
post_persistence_action: STOP_FOR_OWNER_MANUAL_DOGFOOD
```

`recorded_at` is the committer timestamp of the final approval review commit
(`b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e`), obtained with:

```bash
git show -s --format=%cI b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e
```

This avoids self-referencing the Gate-evidence commit this document is
persisted in, whose hash cannot be known while writing it.

## Product/capability statement

```text
TOMTIT can invoke one real Claude Code CLI worker in an isolated
workspace, prevent worker descendants from mutating after result
finalization, capture a bounded immutable result and workspace
evidence, detect unexpected path changes, normalize supported
execution failures, and preserve Runtime authority.

ASSESSMENT=PROVEN
```

This proves a bounded capability, not production reliability or statistical
stability. It is proven for one real, successful, end-to-end invocation
against an isolated temporary Git repository, plus deterministic offline
coverage of every required failure and cleanup mode. It does not prove
multi-task, cross-version, or cross-account real-world stability.

## Approval bundle

```yaml
approval_bundle_id: TOMTIT_GOVERNANCE_BASELINE_V1
bundle_status: APPROVED

documents:
  - path: docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md
    version: V1
    sha256: a3128e2cac4956b642161e4b6583764200a89d831a291c8926e93549aac61a13

  - path: docs/EVALUATION_CONTRACT_V1.md
    version: V1
    sha256: 0e9c5184f44504add626a81092c1795399d3fcdd3c64d11181c59db541060b4a

  - path: docs/VERIFIER_POLICY_V1.md
    version: V1
    sha256: 2da9fb1b5f70e2ca75e55cf3ebabf7958a19018b8215d366c3b83b231eaec915
```

This is the same governance bundle recorded at Gate 1 and Gate 2. All three
hashes were independently recomputed with `sha256sum` before this evidence
was persisted and matched the recorded values exactly.

## Reviewed artifact (final approved corrective cycle)

```yaml
target_source_commit: 70a23eae1b9cd043d16484e529d1f5eb6faa045e
implementation_report_commit: 187e85b1cd634e5fdf803be57b8789152f1fe663
review_report_commit: b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e
review_verdict: APPROVE_WITH_NON_BLOCKING_FINDINGS
```

## Review budget

```yaml
review_budget_status: COMPLETE
owner_authorization_ref: OWNER_STANDING_AUTHORIZATION_FOR_NARROW_REPRODUCED_SAME_SCOPE_DEFECTS_2026-08-05
```

The initial Gate 3 implementation and real-evidence refresh were reviewed
once each. The independent Sol review of the initial implementation
(`8f3ab4d6fd090f4656e6d37169fdd3d38348a37c`) returned `REQUEST_CHANGES` with
two reproduced, same-scope defects (G3-SOL-01, G3-SOL-02). The bounded
corrective patch that resolved both was performed under the Owner's standing
authorization for narrow, reproduced, same-scope defects. The final corrective
rereview (`b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e`) closed both findings with
no remaining MEDIUM-or-higher finding and required no further Owner
authorization to conclude.

## Full Gate 3 audit chain

| Stage | Source commit | Report commit | Review/rereview commit |
|---|---|---|---|
| Gate 2 closing mechanical review | — | — | `66b6afb6d82934b09d5b8214d2d3e4e6b629111c` |
| Initial Gate 3 implementation | `6b370c3236f4120e5a917676231e3420055aa950` | `fefaf4e9063f87517d951e0fdeaf76d0ccc2c641` | — |
| Real evidence refresh (successful invocation) | `ecfa981bdedf25f31e46bf870aee6fa5be3fc31c` | `9846ff1771ec4607ec342411d25def662fcf95e4` | — |
| Initial independent Sol review | — | — | `8f3ab4d6fd090f4656e6d37169fdd3d38348a37c` (`REQUEST_CHANGES`) |
| Process-boundary corrective cycle | `70a23eae1b9cd043d16484e529d1f5eb6faa045e` | `187e85b1cd634e5fdf803be57b8789152f1fe663` | `b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e` (`APPROVE_WITH_NON_BLOCKING_FINDINGS`) |

The final corrective rereview (`b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e`) is
the authoritative Gate 3 verdict. The initial Sol review's `REQUEST_CHANGES`
verdict remains part of the permanent audit trail and is not hidden or
rewritten; it does not override the final approval.

The chronological ancestry of every commit above was independently verified
with `git merge-base --is-ancestor` before this evidence was persisted; each
link held.

**The original blocked real-invocation attempt (CLI `1.0.85`, two attempts,
both `404 not_found_error`, recorded in
`REPORT_CLAUDE_GATE_3_CLAUDE_CODE_CLI_SPIKE.md`) was never successful.** The
capability-proving successful real invocation is the one persisted at
`ecfa981bdedf25f31e46bf870aee6fa5be3fc31c` (CLI `2.1.197`), independently
authenticated by the Sol final review as not a relabeled prior failure: fresh
run identity, distinct timestamps, differing workspace hashes, and exit 0.

## Finding status

```text
G3-SOL-01=RESOLVED
G3-SOL-02=RESOLVED

CLAUDE_CODE_CLI_ADAPTER=RESOLVED

PROCESS_TIMEOUT_HANDLING=RESOLVED
PROCESS_CLEANUP=RESOLVED
PROCESS_GROUP_QUIESCENCE=RESOLVED
READER_THREAD_COMPLETION=RESOLVED
FINAL_SNAPSHOT_ORDERING=RESOLVED
SCOPE_CHANGE_DETECTION=RESOLVED

EXPLICIT_EXECUTABLE_VALIDATION=RESOLVED
PROCESS_START_NORMALIZATION=RESOLVED
TOTAL_RESULT_NORMALIZATION=RESOLVED

REAL_CLI_INVOCATION=PROVEN
REAL_CLI_RESULT_FILE=PROVEN
REAL_CLI_SCOPE_CONTROL=PROVEN
REAL_CLI_EVIDENCE=PROVEN

SECRET_REDACTION=RESOLVED
RUNTIME_AUTHORITY_PRESERVED=RESOLVED

HISTORICAL_FAILURE_PRESERVED=PASS
GATE_1_PRESERVATION=PRESERVED
GATE_2_PRESERVATION=PRESERVED

CLI_INSTALLATION_VERSION_METADATA_MISMATCH=OBSERVED_NONBLOCKING
REAL_WORLD_ADAPTER_STABILITY=NOT_PROVEN
```

## Real evidence paths

```text
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_RESULT.json
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_DIFF.patch
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_RESULT.json
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_DIFF.patch
```

The first pair is the canonical, currently successful invocation (CLI
`2.1.197`, `SUCCEEDED`, exit 0, `result.txt` replaced with the exact required
string, no unexpected path). The `ATTEMPT1` pair preserves the original
blocked CLI `1.0.85` attempt for audit continuity; it is not, and has never
been claimed to be, a successful invocation. All four files are unchanged
since `ecfa981bdedf25f31e46bf870aee6fa5be3fc31c`/`6b370c3236f4120e5a917676231e3420055aa950`
and were re-verified as byte-for-byte unchanged before this evidence was
persisted.

Manual Owner rerun command:

```bash
python3 scripts/run_claude_code_cli_spike.py
```

## Nonblocking limitations

These are recorded as LOW, deferred, not proven, or explicitly out of scope —
not unresolved Gate 3 correctness findings:

* Theoretical PID/PGID reuse race between leader reap and group-quiescence probe.
* POSIX-only process-group cleanup verification.
* Bounded worst-case cleanup latency (up to two grace periods before `run()` returns).
* `PROCESS_CLEANUP_FAILED` is checked ahead of snapshot-derived scope status.
* Only one successful real invocation sample; not statistical stability.
* Real-world adapter stability across CLI versions, prompts, and accounts is not proven.
* Absolute local filesystem paths appear in evidence (`executable_path`, evidence paths).
* `claude update` install-report version and the invoking binary's self-reported version were observed to differ (`2.1.222` vs `2.1.197`).
* No persistent Runtime integration.
* No production sandbox.
* No crash recovery.
* No cost-control system.
* No routing or multi-provider support.

None of these reopens or blocks Gate 3.

## Audit lineage

```text
187e85b1cd634e5fdf803be57b8789152f1fe663
→ b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e
```

The final corrective Sol rereview commit (`b93cd49c7f1c8aaaf496b7a3e5e2be5a1ac1181e`)
is the authoritative end of the reviewed implementation lineage. The
Gate-evidence commit and the persistence-report commit that follow are
forward-only Git history that persists and records this decision; they are
not part of the reviewed implementation diff and are not self-referenced from
within this file.

## Recorded by

```yaml
gate_evidence_writer: CLAUDE_CODE_SONNET
verifier_mode: MECHANICAL
mechanical_review_status: PENDING
```
