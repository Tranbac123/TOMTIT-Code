# Development records archive

Point-in-time implementation reports, receipts and independent reviews from the TOMTIT-Code build gates (2026-08).
They are kept for provenance and are **not** current instructions. Current authority is listed in [`../README.md`](../README.md).

## How these records are referenced

- Every file here was moved unchanged (byte-identical, `git mv`) from the repository root in `chore/docs-cleanup-01`.
  The table gives each file's former root path.
- Source and test comments cite some reports by bare filename (for example
  `EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md`). Find them here by that name.
- Gate evidence in [`../gate-evidence/`](../gate-evidence/) pins these reports by **commit SHA**, and those
  commits still contain the files at their original root paths.
- Two reviews cited by tests, `EXACT_COMMAND_AUTHORIZATION_CODEX_REVIEW.md` and
  `EXACT_BASH_PRETOOLUSE_GUARD_REVIEW.md`, were never committed to this branch's history and are therefore not here.

## gate-1-governance-baseline

Gate 1 (Scenario 1): repository baseline recovery, governance persistence, and the F-002…F-004 / SOL corrective cycles.

| date | document | former root path |
|---|---|---|
| 2026-08-04 | [Claude Baseline Audit Correction Report](gate-1-governance-baseline/REPORT_CLAUDE_BASELINE_AUDIT_CORRECTION.md) | `REPORT_CLAUDE_BASELINE_AUDIT_CORRECTION.md` |
| 2026-08-04 | [Claude Corrective Patch — F-002, F-003, F-004](gate-1-governance-baseline/REPORT_CLAUDE_CORRECTIVE_F002_F003_F004.md) | `REPORT_CLAUDE_CORRECTIVE_F002_F003_F004.md` |
| 2026-08-04 | [Claude Corrective Patch — SOL-H01, SOL-M02, SOL-M03, SOL-L04](gate-1-governance-baseline/REPORT_CLAUDE_CORRECTIVE_SOL_H01_M02_M03.md) | `REPORT_CLAUDE_CORRECTIVE_SOL_H01_M02_M03.md` |
| 2026-08-04 | [Claude Corrective Patch — SOL-RH01, SOL-RM02, SOL-RM03](gate-1-governance-baseline/REPORT_CLAUDE_CORRECTIVE_SOL_RH01_RM02_RM03.md) | `REPORT_CLAUDE_CORRECTIVE_SOL_RH01_RM02_RM03.md` |
| 2026-08-04 | [Claude Final Corrective Patch — SOL-RH01 / F-002 cleanup and retryability](gate-1-governance-baseline/REPORT_CLAUDE_FINAL_SOL_RH01_F002_PATCH.md) | `REPORT_CLAUDE_FINAL_SOL_RH01_F002_PATCH.md` |
| 2026-08-04 | [Codex Sol Corrective Rereview](gate-1-governance-baseline/REPORT_CODEX_SOL_CORRECTIVE_REREVIEW.md) | `REPORT_CODEX_SOL_CORRECTIVE_REREVIEW.md` |
| 2026-08-04 | [Codex Sol Review — F-002, F-003, and F-004](gate-1-governance-baseline/REPORT_CODEX_SOL_F002_F003_F004_REVIEW.md) | `REPORT_CODEX_SOL_F002_F003_F004_REVIEW.md` |
| 2026-08-04 | [Codex Sol Final Cleanup-Retry Corrective Rereview — Gate 1](gate-1-governance-baseline/REPORT_CODEX_SOL_FINAL_CLEANUP_RETRY_REREVIEW.md) | `REPORT_CODEX_SOL_FINAL_CLEANUP_RETRY_REREVIEW.md` |
| 2026-08-04 | [Codex Sol Final Corrective Rereview — Gate 1 Scenario 1](gate-1-governance-baseline/REPORT_CODEX_SOL_FINAL_REREVIEW_SOL_RH01_RM02_RM03.md) | `REPORT_CODEX_SOL_FINAL_REREVIEW_SOL_RH01_RM02_RM03.md` |
| 2026-08-04 | [Codex Terra Recovered Baseline Rereview](gate-1-governance-baseline/REPORT_CODEX_TERRA_BASELINE_REREVIEW.md) | `REPORT_CODEX_TERRA_BASELINE_REREVIEW.md` |
| 2026-08-04 | [Codex Terra Baseline Provenance Review](gate-1-governance-baseline/REPORT_CODEX_TERRA_BASELINE_REVIEW.md) | `REPORT_CODEX_TERRA_BASELINE_REVIEW.md` |
| 2026-08-04 | [Codex Terra Mechanical Review — Governance Baseline and Gate 1 Evidence](gate-1-governance-baseline/REPORT_CODEX_TERRA_GOVERNANCE_PERSISTENCE_REVIEW.md) | `REPORT_CODEX_TERRA_GOVERNANCE_PERSISTENCE_REVIEW.md` |
| 2026-08-04 | [Repository Baseline Recovery Report](gate-1-governance-baseline/REPORT_REPOSITORY_BASELINE_RECOVERY.md) | `REPORT_REPOSITORY_BASELINE_RECOVERY.md` |

## gate-2-scenario-2

Gate 2 (Scenario 2): minimal revise-then-complete flow, trusted TaskContract binding, archive trust and evidence persistence.

| date | document | former root path |
|---|---|---|
| 2026-08-04 | [Claude Gate 2 — Minimal Scenario 2 Implementation](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_MINIMAL_SCENARIO_2.md) | `REPORT_CLAUDE_GATE_2_MINIMAL_SCENARIO_2.md` |
| 2026-08-04 | [Independent Correctness Review — Gate 2 Minimal Scenario 2](gate-2-scenario-2/REPORT_CODEX_SOL_GATE_2_MINIMAL_SCENARIO_2_REVIEW.md) | `REPORT_CODEX_SOL_GATE_2_MINIMAL_SCENARIO_2_REVIEW.md` |
| 2026-08-05 | [Claude Gate 2 — Canonical Archive Membership Patch](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_CANONICAL_ARCHIVE_MEMBERSHIP.md) | `REPORT_CLAUDE_GATE_2_CANONICAL_ARCHIVE_MEMBERSHIP.md` |
| 2026-08-05 | [Claude Gate 2 Corrective Patch — Revision Authority and Provenance](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_CORRECTIVE_AUTHORITY_PROVENANCE.md) | `REPORT_CLAUDE_GATE_2_CORRECTIVE_AUTHORITY_PROVENANCE.md` |
| 2026-08-05 | [Claude Gate 2 — Evidence Correction Report](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_EVIDENCE_CORRECTION.md) | `REPORT_CLAUDE_GATE_2_EVIDENCE_CORRECTION.md` |
| 2026-08-05 | [Claude Gate 2 — Evidence Persistence Report](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_EVIDENCE_PERSISTENCE.md) | `REPORT_CLAUDE_GATE_2_EVIDENCE_PERSISTENCE.md` |
| 2026-08-05 | [Claude Gate 2 Final Corrective Patch — Archive Trust and Budget Authority](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_FINAL_CORRECTIVE_ARCHIVE_TRUST.md) | `REPORT_CLAUDE_GATE_2_FINAL_CORRECTIVE_ARCHIVE_TRUST.md` |
| 2026-08-05 | [Claude Gate 2 — Trusted TaskContract Binding Patch](gate-2-scenario-2/REPORT_CLAUDE_GATE_2_TRUSTED_CONTRACT_BINDING.md) | `REPORT_CLAUDE_GATE_2_TRUSTED_CONTRACT_BINDING.md` |
| 2026-08-05 | [Final Independent Gate 2 Trusted TaskContract Binding Review](gate-2-scenario-2/REPORT_CODEX_SOL_FINAL_CONTRACT_BINDING_REVIEW.md) | `REPORT_CODEX_SOL_FINAL_CONTRACT_BINDING_REVIEW.md` |
| 2026-08-05 | [Final Independent Correctness Review — Gate 2 Trust Boundary](gate-2-scenario-2/REPORT_CODEX_SOL_FINAL_GATE_2_TRUST_BOUNDARY_REVIEW.md) | `REPORT_CODEX_SOL_FINAL_GATE_2_TRUST_BOUNDARY_REVIEW.md` |
| 2026-08-05 | [Final Corrective Independent Correctness Rereview — Gate 2 Minimal Scenario 2](gate-2-scenario-2/REPORT_CODEX_SOL_GATE_2_CORRECTIVE_REREVIEW.md) | `REPORT_CODEX_SOL_GATE_2_CORRECTIVE_REREVIEW.md` |
| 2026-08-05 | [Owner-Authorized Final Independent Correctness Rereview — Gate 2 Minimal Scenario 2](gate-2-scenario-2/REPORT_CODEX_SOL_OWNER_AUTHORIZED_FINAL_GATE_2_REREVIEW.md) | `REPORT_CODEX_SOL_OWNER_AUTHORIZED_FINAL_GATE_2_REREVIEW.md` |
| 2026-08-05 | [Final Bounded Mechanical Rereview — Gate 2 Evidence Correction](gate-2-scenario-2/REPORT_CODEX_TERRA_GATE_2_EVIDENCE_CORRECTION_REREVIEW.md) | `REPORT_CODEX_TERRA_GATE_2_EVIDENCE_CORRECTION_REREVIEW.md` |
| 2026-08-05 | [Independent Mechanical Review — Gate 2 Evidence Persistence](gate-2-scenario-2/REPORT_CODEX_TERRA_GATE_2_EVIDENCE_REVIEW.md) | `REPORT_CODEX_TERRA_GATE_2_EVIDENCE_REVIEW.md` |

## gate-3-claude-code-cli

Gate 3: Claude Code CLI capability spike, process-boundary correction and real CLI evidence.

| date | document | former root path |
|---|---|---|
| 2026-08-05 | [Claude Gate 3 — Claude Code CLI Capability Spike](gate-3-claude-code-cli/REPORT_CLAUDE_GATE_3_CLAUDE_CODE_CLI_SPIKE.md) | `REPORT_CLAUDE_GATE_3_CLAUDE_CODE_CLI_SPIKE.md` |
| 2026-08-05 | [Claude Gate 3 — Evidence Persistence](gate-3-claude-code-cli/REPORT_CLAUDE_GATE_3_EVIDENCE_PERSISTENCE.md) | `REPORT_CLAUDE_GATE_3_EVIDENCE_PERSISTENCE.md` |
| 2026-08-05 | [Claude Gate 3 — Process Boundary Correction](gate-3-claude-code-cli/REPORT_CLAUDE_GATE_3_PROCESS_BOUNDARY_CORRECTION.md) | `REPORT_CLAUDE_GATE_3_PROCESS_BOUNDARY_CORRECTION.md` |
| 2026-08-05 | [Claude Gate 3 — Real CLI Evidence Refresh](gate-3-claude-code-cli/REPORT_CLAUDE_GATE_3_REAL_CLI_EVIDENCE_REFRESH.md) | `REPORT_CLAUDE_GATE_3_REAL_CLI_EVIDENCE_REFRESH.md` |
| 2026-08-05 | [Final Bounded Gate 3 Corrective Rereview](gate-3-claude-code-cli/REPORT_CODEX_SOL_GATE_3_CORRECTIVE_REREVIEW.md) | `REPORT_CODEX_SOL_GATE_3_CORRECTIVE_REREVIEW.md` |
| 2026-08-05 | [Final Independent Gate 3 Claude Code CLI Capability Review](gate-3-claude-code-cli/REPORT_CODEX_SOL_GATE_3_FINAL_REVIEW.md) | `REPORT_CODEX_SOL_GATE_3_FINAL_REVIEW.md` |
| 2026-08-05 | [Final Mechanical Review — Persisted Gate 3 Evidence](gate-3-claude-code-cli/REPORT_CODEX_TERRA_GATE_3_EVIDENCE_REVIEW.md) | `REPORT_CODEX_TERRA_GATE_3_EVIDENCE_REVIEW.md` |

## claude-code-adapter-hardening

Claude Code adapter hardening: exact-command authorization, exact-argv Bash guard, provider-output evidence safety, file-write guard, workspace scope, CLI argv order.

| date | document | former root path |
|---|---|---|
| 2026-08-09 | [Claude CLI Prompt Argv-Ordering Fix Report](claude-code-adapter-hardening/CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md) | `CLAUDE_CLI_PROMPT_ARGV_ORDER_FIX_REPORT.md` |
| 2026-08-09 | [Claude CLI Prompt Argv-Ordering Independent Review](claude-code-adapter-hardening/CLAUDE_CLI_PROMPT_ARGV_ORDER_REVIEW.md) | `CLAUDE_CLI_PROMPT_ARGV_ORDER_REVIEW.md` |
| 2026-08-09 | [Exact-Argv Bash Authorization — Enablement Report](claude-code-adapter-hardening/EXACT_BASH_AUTHORIZATION_ENABLEMENT_REPORT.md) | `EXACT_BASH_AUTHORIZATION_ENABLEMENT_REPORT.md` |
| 2026-08-09 | [Exact-Argv Bash Authorization: Final Enablement Review](claude-code-adapter-hardening/EXACT_BASH_AUTHORIZATION_ENABLEMENT_REVIEW.md) | `EXACT_BASH_AUTHORIZATION_ENABLEMENT_REVIEW.md` |
| 2026-08-09 | [Exact Bash Guard: Process-Boundary Hardening Report](claude-code-adapter-hardening/EXACT_BASH_GUARD_PROCESS_HARDENING_REPORT.md) | `EXACT_BASH_GUARD_PROCESS_HARDENING_REPORT.md` |
| 2026-08-09 | [Exact Bash Guard: Process-Boundary Hardening — Narrow Security Review](claude-code-adapter-hardening/EXACT_BASH_GUARD_PROCESS_HARDENING_REVIEW.md) | `EXACT_BASH_GUARD_PROCESS_HARDENING_REVIEW.md` |
| 2026-08-09 | [Exact-Argv PreToolUse Bash Guard: H-1 / H-2 Corrective Fix Report](claude-code-adapter-hardening/EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_FIX_REPORT.md) | `EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_FIX_REPORT.md` |
| 2026-08-09 | [Exact-Argv PreToolUse Bash Guard: H-1 / H-2 Independent Security Re-Review](claude-code-adapter-hardening/EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_REREVIEW.md) | `EXACT_BASH_PRETOOLUSE_GUARD_H1_H2_REREVIEW.md` |
| 2026-08-09 | [Exact-Argv PreToolUse Bash Guard: Implementation Report](claude-code-adapter-hardening/EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md) | `EXACT_BASH_PRETOOLUSE_GUARD_IMPLEMENTATION_REPORT.md` |
| 2026-08-09 | [Exact-Bash Provider-Output Evidence-Safety Fix Report (M-1 / M-2)](claude-code-adapter-hardening/EXACT_BASH_PROVIDER_OUTPUT_EVIDENCE_SAFETY_FIX_REPORT.md) | `EXACT_BASH_PROVIDER_OUTPUT_EVIDENCE_SAFETY_FIX_REPORT.md` |
| 2026-08-09 | [Exact-Bash Provider-Output Evidence Safety (M-1 / M-2) — Independent Security Review](claude-code-adapter-hardening/EXACT_BASH_PROVIDER_OUTPUT_EVIDENCE_SAFETY_REVIEW.md) | `EXACT_BASH_PROVIDER_OUTPUT_EVIDENCE_SAFETY_REVIEW.md` |
| 2026-08-09 | [Fix 3 Exact Command Authorization Re-review](claude-code-adapter-hardening/EXACT_COMMAND_AUTHORIZATION_CODEX_FIX3_REREVIEW.md) | `EXACT_COMMAND_AUTHORIZATION_CODEX_FIX3_REREVIEW.md` |
| 2026-08-09 | [Exact Command Authorization — Corrective Implementation Report](claude-code-adapter-hardening/EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md) | `EXACT_COMMAND_AUTHORIZATION_CORRECTIVE_IMPLEMENTATION_REPORT.md` |
| 2026-08-09 | [Exact Command Authorization — Implementation Report](claude-code-adapter-hardening/EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md) | `EXACT_COMMAND_AUTHORIZATION_IMPLEMENTATION_REPORT.md` |
| 2026-08-09 | [Exact Command Authorization — Validator Fix2 Report](claude-code-adapter-hardening/EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX2_REPORT.md) | `EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX2_REPORT.md` |
| 2026-08-09 | [Exact Command Authorization — Validator Fix3 Report](claude-code-adapter-hardening/EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX3_REPORT.md) | `EXACT_COMMAND_AUTHORIZATION_VALIDATOR_FIX3_REPORT.md` |
| 2026-08-09 | [TOMTIT Built-in File-Write Guard Independent Review](claude-code-adapter-hardening/FILE_WRITE_GUARD_INDEPENDENT_REVIEW.md) | `FILE_WRITE_GUARD_INDEPENDENT_REVIEW.md` |
| 2026-08-09 | [File-Write Guard Integration Report](claude-code-adapter-hardening/FILE_WRITE_GUARD_INTEGRATION_REPORT.md) | `FILE_WRITE_GUARD_INTEGRATION_REPORT.md` |
| 2026-08-09 | [Historical Provider-Evidence Sanitization Report](claude-code-adapter-hardening/HISTORICAL_PROVIDER_EVIDENCE_SANITIZATION_REPORT.md) | `HISTORICAL_PROVIDER_EVIDENCE_SANITIZATION_REPORT.md` |
| 2026-08-09 | [TOMTIT M-1 Changed-Path Independent Rereview](claude-code-adapter-hardening/M1_CHANGED_PATH_INDEPENDENT_REREVIEW.md) | `M1_CHANGED_PATH_INDEPENDENT_REREVIEW.md` |
| 2026-08-09 | [M-1 Identity Corrective Patch — Narrow Independent Rereview](claude-code-adapter-hardening/M1_IDENTITY_NARROW_REREVIEW.md) | `M1_IDENTITY_NARROW_REREVIEW.md` |
| 2026-08-09 | [Workspace Scope Corrective Fix Report](claude-code-adapter-hardening/WORKSPACE_SCOPE_CORRECTIVE_FIX_REPORT.md) | `WORKSPACE_SCOPE_CORRECTIVE_FIX_REPORT.md` |
| 2026-08-09 | [Workspace Side-Effect Scope Accounting Fix Report](claude-code-adapter-hardening/WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md) | `WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_FIX_REPORT.md` |
| 2026-08-09 | [Workspace Side-Effect Scope Accounting Independent Review](claude-code-adapter-hardening/WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_REVIEW.md) | `WORKSPACE_SIDE_EFFECT_SCOPE_ACCOUNTING_REVIEW.md` |

## product-cli-v0

Productized CLI V0 implementation receipt and reviews.

| date | document | former root path |
|---|---|---|
| 2026-08-09 | [TOMTIT Productized CLI V0 — Implementation Receipt](product-cli-v0/CLI_V0_IMPLEMENTATION_RECEIPT.md) | `CLI_V0_IMPLEMENTATION_RECEIPT.md` |
| 2026-08-09 | [Productized CLI V0 — Independent Software Correctness Review](product-cli-v0/CODEX_CLI_V0_INDEPENDENT_REVIEW.md) | `CODEX_CLI_V0_INDEPENDENT_REVIEW.md` |
| 2026-08-09 | [Productized CLI V0 Corrective Patch — Narrow Independent Rereview](product-cli-v0/CODEX_CLI_V0_NARROW_REREVIEW.md) | `CODEX_CLI_V0_NARROW_REREVIEW.md` |

## decision-evaluation-v0

Decision Evaluation V0 implementation receipt and reviews (the design itself is in docs/architecture/).

| date | document | former root path |
|---|---|---|
| 2026-08-09 | [Decision Evaluation V0 — Independent Semantic Correctness Review](decision-evaluation-v0/CODEX_DECISION_EVAL_V0_INDEPENDENT_REVIEW.md) | `CODEX_DECISION_EVAL_V0_INDEPENDENT_REVIEW.md` |
| 2026-08-09 | [Decision Evaluation V0 — Narrow Independent Semantic Rereview](decision-evaluation-v0/CODEX_DECISION_EVAL_V0_NARROW_REREVIEW.md) | `CODEX_DECISION_EVAL_V0_NARROW_REREVIEW.md` |
| 2026-08-09 | [Decision Evaluation V0 Implementation Receipt](decision-evaluation-v0/DECISION_EVALUATION_V0_IMPLEMENTATION_RECEIPT.md) | `DECISION_EVALUATION_V0_IMPLEMENTATION_RECEIPT.md` |

## runtime-final-corrective

Runtime final bounded corrective cycle (pinned by commit in docs/gate-evidence/FREEZE_PROVENANCE_CLOSURE.md).

| date | document | former root path |
|---|---|---|
| 2026-08-13 | [Runtime Final Narrow Software Correctness Review](runtime-final-corrective/CODEX_RUNTIME_FINAL_NARROW_REVIEW.md) | `CODEX_RUNTIME_FINAL_NARROW_REVIEW.md` |
| 2026-08-13 | [Runtime final bounded corrective report](runtime-final-corrective/RUNTIME_FINAL_BOUNDED_CORRECTIVE_REPORT.md) | `RUNTIME_FINAL_BOUNDED_CORRECTIVE_REPORT.md` |

## target-04

Target-04 dogfood runtime fixes: preflight metadata, canonical index, repair authority, trusted projection, Run005 identity.

| date | document | former root path |
|---|---|---|
| 2026-08-13 | [Target-04 preflight metadata fix report](target-04/TARGET04_PREFLIGHT_METADATA_FIX_REPORT.md) | `TARGET04_PREFLIGHT_METADATA_FIX_REPORT.md` |
| 2026-08-14 | [Codex final review — Target-04 preflight metadata correction](target-04/CODEX_TARGET04_PREFLIGHT_METADATA_FIX_FINAL_REVIEW.md) | `CODEX_TARGET04_PREFLIGHT_METADATA_FIX_FINAL_REVIEW.md` |
| 2026-08-14 | [Target-04 preflight metadata fix — F-01 canonical-grammar repair report](target-04/TARGET04_PREFLIGHT_METADATA_FIX_F01_GRAMMAR_REPAIR_REPORT.md) | `TARGET04_PREFLIGHT_METADATA_FIX_F01_GRAMMAR_REPAIR_REPORT.md` |
| 2026-08-14 | [Target-04 preflight metadata fix — F-01 integer-grammar precision report](target-04/TARGET04_PREFLIGHT_METADATA_FIX_F01_INTEGER_GRAMMAR_REPAIR_REPORT.md) | `TARGET04_PREFLIGHT_METADATA_FIX_F01_INTEGER_GRAMMAR_REPAIR_REPORT.md` |
| 2026-08-14 | [Target-04 preflight metadata fix — F-01 repair report](target-04/TARGET04_PREFLIGHT_METADATA_FIX_F01_REPAIR_REPORT.md) | `TARGET04_PREFLIGHT_METADATA_FIX_F01_REPAIR_REPORT.md` |
| 2026-08-15 | [Codex Target-04 trusted pre-instability content proof fix review](target-04/CODEX_TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_REVIEW.md) | `CODEX_TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_REVIEW.md` |
| 2026-08-15 | [TARGET-04 Coherent Initial Baseline Fix -- Implementation Report](target-04/TARGET04_COHERENT_INITIAL_BASELINE_FIX_REPORT.md) | `TARGET04_COHERENT_INITIAL_BASELINE_FIX_REPORT.md` |
| 2026-08-15 | [Target-04 intra-measurement timestamp retry fix report](target-04/TARGET04_INTRA_MEASUREMENT_TIMESTAMP_RETRY_FIX_REPORT.md) | `TARGET04_INTRA_MEASUREMENT_TIMESTAMP_RETRY_FIX_REPORT.md` |
| 2026-08-15 | [Target-04 trusted pre-instability content proof fix report](target-04/TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_REPORT.md) | `TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_REPORT.md` |
| 2026-08-16 | [Codex Target-04 repair authority-binding fix review](target-04/CODEX_TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REVIEW.md) | `CODEX_TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REVIEW.md` |
| 2026-08-16 | [TARGET-04 Canonical Index Authority Correction Report](target-04/TARGET04_CANONICAL_INDEX_AUTHORITY_CORRECTION_REPORT.md) | `TARGET04_CANONICAL_INDEX_AUTHORITY_CORRECTION_REPORT.md` |
| 2026-08-16 | [TARGET-04 Canonical Index Identity Fix -- Implementation Report](target-04/TARGET04_CANONICAL_INDEX_IDENTITY_FIX_REPORT.md) | `TARGET04_CANONICAL_INDEX_IDENTITY_FIX_REPORT.md` |
| 2026-08-16 | [TARGET-04 Repair Authority Binding Fix -- Implementation Report](target-04/TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REPORT.md) | `TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REPORT.md` |
| 2026-08-16 | [TARGET-04 Repair Trusted Projection Fix -- Implementation Report](target-04/TARGET04_REPAIR_TRUSTED_PROJECTION_FIX_REPORT.md) | `TARGET04_REPAIR_TRUSTED_PROJECTION_FIX_REPORT.md` |
| 2026-08-17 | [Target-04 F03/F04 Semantic-Completeness Review](target-04/CODEX_TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_REVIEW.md) | `CODEX_TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_REVIEW.md` |
| 2026-08-17 | [TARGET-04 Canonical Index F03/F04 Final Fix Report](target-04/TARGET04_CANONICAL_INDEX_F03_F04_FINAL_FIX_REPORT.md) | `TARGET04_CANONICAL_INDEX_F03_F04_FINAL_FIX_REPORT.md` |
| 2026-08-17 | [TARGET-04 Canonical Index F03/F04 Semantic Completeness Report](target-04/TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_REPORT.md) | `TARGET04_CANONICAL_INDEX_F03_F04_SEMANTIC_COMPLETENESS_REPORT.md` |
| 2026-08-18 | [Target-04 Run005 Prior-Evidence Stable-Identity Fix — Independent Review](target-04/CODEX_TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REVIEW.md) | `CODEX_TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REVIEW.md` |
| 2026-08-18 | [TARGET-04 Run005 Prior-Evidence Stable-Identity Fix Report](target-04/TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REPORT.md) | `TARGET04_RUN005_PRIOR_EVIDENCE_STABLE_IDENTITY_FIX_REPORT.md` |

## dogfood-evaluation-telemetry-v0

Dogfood evaluation telemetry V0 implementation, blocker and summary-semantics fixes.

| date | document | former root path |
|---|---|---|
| 2026-08-18 | [Dogfood Evaluation Telemetry V0 -- Blocker Fix Report](dogfood-evaluation-telemetry-v0/DOGFOOD_EVALUATION_TELEMETRY_V0_BLOCKER_FIX_REPORT.md) | `DOGFOOD_EVALUATION_TELEMETRY_V0_BLOCKER_FIX_REPORT.md` |
| 2026-08-18 | [Dogfood Evaluation Telemetry V0 -- Implementation Receipt](dogfood-evaluation-telemetry-v0/DOGFOOD_EVALUATION_TELEMETRY_V0_IMPLEMENTATION_RECEIPT.md) | `DOGFOOD_EVALUATION_TELEMETRY_V0_IMPLEMENTATION_RECEIPT.md` |
| 2026-08-20 | [DOGFOOD_EVALUATION_TELEMETRY_V0 — Final Summary-Semantics Fix Review](dogfood-evaluation-telemetry-v0/CODEX_DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_REVIEW.md) | `CODEX_DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_REVIEW.md` |
| 2026-08-20 | [Dogfood Evaluation Telemetry V0 -- Summary Semantics Fix Report](dogfood-evaluation-telemetry-v0/DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_REPORT.md) | `DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_REPORT.md` |
