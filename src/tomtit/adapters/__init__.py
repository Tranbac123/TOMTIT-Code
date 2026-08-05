"""Bounded external-worker adapters. Isolated from Runtime/domain internals."""

from tomtit.adapters.claude_code_cli import (
    ADAPTER_NAME,
    ADAPTER_VERSION,
    DEFAULT_ENVIRONMENT_ALLOWLIST,
    ClaudeCodeCLIAdapter,
    ClaudeCodeCLIRequest,
    ClaudeCodeCLIResult,
    ClaudeCodeCLIStatus,
    build_child_environment,
    build_evidence_manifest,
    discover_claude_executable,
    render_evidence_manifest_json,
    render_unified_diff,
    snapshot_workspace_contents,
    snapshot_workspace_hashes,
    workspace_hash,
    write_evidence,
)

__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "DEFAULT_ENVIRONMENT_ALLOWLIST",
    "ClaudeCodeCLIAdapter",
    "ClaudeCodeCLIRequest",
    "ClaudeCodeCLIResult",
    "ClaudeCodeCLIStatus",
    "build_child_environment",
    "build_evidence_manifest",
    "discover_claude_executable",
    "render_evidence_manifest_json",
    "render_unified_diff",
    "snapshot_workspace_contents",
    "snapshot_workspace_hashes",
    "workspace_hash",
    "write_evidence",
]
