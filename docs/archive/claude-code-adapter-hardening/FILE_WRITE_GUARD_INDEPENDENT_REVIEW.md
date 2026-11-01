# TOMTIT Built-in File-Write Guard Independent Review

## 1. Verdict

**APPROVE_WITH_NON_BLOCKING_FINDINGS**

The built-in `Write`/`Edit`/`MultiEdit`/`NotebookEdit` trust boundary is correctly implemented and integrated with the Claude Code adapter. It preserves the existing layered exact Bash authorization controls. The only substantive residual issue is the inherent pre-check/use race described in sections 6 and 19; a generic filesystem sandbox or atomic write mediation is outside this gate.

M-1 changed-path consumer reconciliation and OS sandbox enforcement were not evaluated.

## 2. Source inspection

I inspected the implementation and tests directly, not only the supplied reports:

- `src/tomtit/adapters/exact_file_write_guard.py`
- `src/tomtit/adapters/claude_code_cli.py`
- `src/tomtit/adapters/__init__.py`
- `tests/adapters/test_exact_file_write_guard.py`
- `tests/adapters/test_claude_code_cli.py`

The traced data path is coherent: `ClaudeCodeCLIRequest.allowed_changed_paths` is canonicalized into a minimal temporary JSON config; the adapter emits TOMTIT-owned settings with four matcher-specific `PreToolUse` hooks; the guard reads the corresponding target field; and both lexical and resolved workspace-relative paths must independently equal an Owner-authorized source path before the guard returns success. Every rejection returns the fixed denial result and prevents the tool call.

## 3. Tool payload contract

`_TOOL_PATH_FIELDS` implements the required mapping exactly:

- `Write`, `Edit`, and `MultiEdit` use `file_path`.
- `NotebookEdit` uses `notebook_path`.

The guard requires a JSON object, a recognized string tool name, an object `tool_input`, and a non-empty string in the field assigned to that tool. Unsupported tools, missing fields, wrong fields, wrong types, malformed JSON, and malformed configuration all fail closed. Supplying (for example) `notebook_path` to `Write`, or `file_path` to `NotebookEdit`, cannot substitute for the required target.

## 4. Exact source authorization

The persisted allowlist is the exact tuple of canonical `allowed_changed_paths`, converted to an exact-membership set by the guard. There is no prefix, directory, glob, or sibling implication: authorizing `src/file.ts` does not authorize `src/file.ts.backup`, `src/other.ts`, or `src/sub/file.ts`.

`allowed_side_effect_paths` is neither written to the file-guard config nor consulted by the guard. It therefore cannot grant built-in file-tool mutation permission.

## 5. Lexical/resolved path enforcement

The lexical check uses `os.path.normpath`, `Path.relative_to`, component-based `.git` rejection, and exact allowlist membership. It does not use naive string-prefix containment. The resolved check separately applies `realpath` to the candidate and workspace, repeats containment and `.git` component rejection, and independently requires exact allowlist membership.

The source and executable subprocess tests demonstrate denial of absolute and traversal escapes, lexical and resolved `.git` paths, external symlink targets, symlinks into `.git`, symlinked parents escaping the workspace, and unauthorized lexical aliases. Both requested lexical identity and resolved target identity must be Owner-authorized.

## 6. Symlink analysis

The locked MVP alias policy is implemented. With only `src/file.ts` authorized, `alias.ts -> src/file.ts` is denied because `alias.ts` fails lexical exact membership. The alias must itself be separately authorized, while the resolved target must also remain authorized. This prevents symlink aliases from widening source authority.

Static and pre-existing symlink escapes are denied. A residual TOCTOU window remains between the hook's `realpath` validation and Claude's subsequent built-in file operation: a separate process with sufficient same-user filesystem authority could attempt to replace a target or parent in that interval. The hook does not hold an atomic file descriptor through the eventual write. This is a non-blocking locked-MVP limitation for this gate, which explicitly does not require a generic filesystem sandbox.

## 7. Guard process hardening

The adapter derives the guard module from its own absolute module location and validates it with `lstat`: missing, symlinked, directory, and other non-regular forms are rejected, and the resolved path must equal the expected module path. The hook invokes the running trusted Python interpreter by absolute path; the reviewed interpreter resolved through `/usr/local/bin/python3`, and `sys.executable` was absolute. The command uses `-E -s` and shell-quotes each fixed argument. Adapter subprocess creation uses an argv list and does not set `shell=True`.

The guard has only exit codes 0 (defer/allow) and 2 (deny), catches `BaseException` at its outer boundary, emits a fixed denial message, and does not expose provider-controlled paths, secrets, or tracebacks.

## 8. Guard artifact safety

The file-guard config contains only:

- the canonical workspace;
- the exact canonical `allowed_changed_paths` list.

It contains no prompt, provider output, authorized command, side-effect path, or secret. The temporary directory is mode 0700 and generated files are mode 0600. Guard-module preflight and artifact construction happen before worker creation; tested missing, symlinked, non-regular, and creation-failure cases return the stable `FILE_WRITE_GUARD_UNAVAILABLE` category without starting the worker. If the config is missing or corrupt when the hook executes, the guard denies.

Artifact cleanup is in the provider execution `finally` path and is tested after both success and failure. Cleanup is best-effort (`ignore_errors=True`); section 19 records the limited observability implication.

## 9. Always-on file guard semantics

Every Claude worker invocation receives `--setting-sources ""`, `--settings <TOMTIT settings>`, and exactly one matcher for each of `Write`, `Edit`, `MultiEdit`, and `NotebookEdit`, regardless of command authorization. This intentional architecture is safe because the generated file config is minimal, exact, and Owner-controlled.

With no authorized commands, settings and all four file hooks remain present, while the Bash hook, Bash sandbox, `--allowedTools`, and Bash fingerprint artifact are absent.

## 10. Conditional Bash composition

When `authorized_commands` is non-empty, the same four file hooks are combined with exactly one Bash matcher, the existing Bash sandbox, native `--allowedTools` rules, and exact argv fingerprints. `exact_bash_guard_enabled` remains true only when the Bash fingerprint artifact exists; the mere presence of always-on settings does not set it.

## 11. Hook matcher audit

Generated settings contain exactly one matcher for each file tool and exactly one Bash matcher only for command-authorized requests. The updated test helper selects by matcher, requires a match, and requires `len(matches) == 1`; it therefore fails for both missing and duplicate matchers and does not depend on array order.

No stale `PreToolUse[0]` security assertion remains in the reviewed adapter tests. Matcher-based selection strengthens the former positional assumption by binding assertions to the intended tool.

## 12. Settings-source lockdown

`--setting-sources ""` is emitted on every worker argv immediately as a separate option/value pair alongside the generated settings path. The task prompt remains immediately after `-p`, before later variadic options, so it cannot absorb or displace the lockdown arguments. Project, user, and local Claude settings are excluded and cannot replace or widen TOMTIT's generated file hooks through Claude's settings-source mechanism.

OS sandbox enforcement was not re-proved in this gate.

## 13. Authorization independence

All required directions remain independent:

- Source paths do not generate a Bash matcher, Bash sandbox, `--allowedTools`, or fingerprints.
- Side-effect paths are excluded from the file allowlist and file-guard artifact.
- Authorized commands do not add or alter file source paths.
- The Bash sandbox constrains process scope but does not authorize a command; exact guard fingerprints and native tool rules remain separately required.

## 14. Bash security regressions

No Bash authorization regression was found. The exact Bash guard SHA-256 is `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`. The native bare-Bash exactness flag remains false and layered exact authorization remains enabled. Exact argv fingerprints, `-E -s`, `run_in_background` denial, prompt placement immediately after `-p` and before `--allowedTools`, provider-output withholding, and the existing authorized-run sandbox values are preserved.

The focused Bash guard suite and the full adapter suite both passed.

## 15. Fail-closed startup behavior

File-guard construction is mandatory before `_execute` creates the worker. A missing, symlinked, or non-regular guard module, or an artifact-creation error, takes the early failure path; tests assert that the worker factory is not called. The stable file-guard failure category is used, and `_early_result` preserves already-approved side-effect evidence fields while reporting zero provider output.

Post-construction tampering is covered by fail-closed hook parsing/validation, subject to the same-user race assumption in section 19.

## 16. Test-harness audit

The fake CLI exercises actual guard subprocesses and all four real Claude payload shapes. It covers exact allow, prefix/sibling denial, workspace and `.git` escapes, symlink targets and parents, lexical alias policy, malformed inputs/configs, fixed output, and the `BaseException` boundary.

The adapter tests exercise always-on settings, conditional Bash composition, independent authorization sources, worker-not-started preflight failures, provider-output withholding, and artifact cleanup. Replacing positional hook access with unique matcher selection is stricter. The reviewed `*args` monkeypatch stubs either forward the production arguments or intentionally raise at the patched boundary; they accommodate the current three-argument artifact-builder signature without bypassing production argument checks.

## 17. Verification receipt

All prescribed commands passed on their first execution; there were no retries or flakes:

| Check | Result |
|---|---|
| Exact file-write guard tests | PASS, exit 0 |
| Exact Bash guard tests | PASS, exit 0 |
| Claude adapter integration tests | PASS, exit 0 |
| All adapter tests | PASS, exit 0 |
| Dogfood loop harness | PASS, exit 0 |
| Dogfood repair harness | PASS, exit 0 |
| Dogfood review harness | PASS, exit 0 |
| Claude CLI spike scenario tests | PASS, exit 0 |
| Full pytest suite | PASS, exit 0 |
| `compileall` | PASS, exit 0 |
| Ruff | PASS, exit 0 |
| mypy | PASS, exit 0; 15 source files |
| `git diff --check` | PASS, exit 0 |
| Bash guard SHA-256 | PASS, exact expected hash |

Full stdout/stderr, command lines, exit codes, source-audit anchors, interpreter check, and repository-state receipt are in `FILE_WRITE_GUARD_INDEPENDENT_REVIEW_FULL.log`.

An additional review-document structure check initially exited 1 because my validator expected 29 receipt keys while the required receipt actually contains 30. That failed check is retained in the log; its corrected run passed. No product or test command was rerun.

No real Claude invocation, Codex child invocation, TOMTIT self-run, personal-web access, or Git write operation occurred.

## 18. Findings by severity

- Critical/high/medium: none.
- Low — residual pre-check/use race: lexical and resolved checks occur before the built-in tool performs its write. A separate same-user process able to mutate workspace entries could race that interval. Atomic write mediation or a generic filesystem sandbox would close a broader class of races but is outside this gate.
- Informational — cleanup observability: temporary-artifact deletion is attempted in `finally` and verified in success/failure tests, but `ignore_errors=True` means an exceptional cleanup failure is not surfaced. The directory is private and the artifact contains only the minimal Owner-controlled scope data.

## 19. Remaining assumptions

- The Coordinator runs in the intended isolated workspace without a hostile same-user process racing filesystem entries between hook validation and tool use.
- The running Python interpreter and installed adapter package are trusted before worker launch.
- Filesystem permissions continue to enforce the requested 0700 temporary directory and 0600 artifact modes.
- Best-effort artifact cleanup succeeds under normal local filesystem operation.
- M-1 changed-path reconciliation and OS-level sandbox enforcement remain separate gates.

## 20. Next gate

The built-in file-write guard gate may close. The next authorized review should address M-1 changed-path consumer reconciliation before any personal-web dogfood rerun; this review does not authorize that rerun.

VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
FILE_TOOL_PAYLOAD_CONTRACT=PASS
EXACT_SOURCE_ALLOWLIST=PASS
LEXICAL_PATH_ENFORCEMENT=PASS
RESOLVED_PATH_ENFORCEMENT=PASS
GIT_METADATA_WRITE=DENIED
OUTSIDE_WORKSPACE_WRITE=DENIED
SYMLINK_ESCAPE=DENIED
LEXICAL_ALIAS_WIDENING=DENIED
FILE_GUARD_FAIL_CLOSED=PASS
FILE_GUARD_PRESENT_EVERY_RUN=PASS
SETTINGS_PRESENT_EVERY_RUN=PASS
SETTING_SOURCES_LOCKDOWN=PASS
BASH_GUARD_ONLY_WHEN_AUTHORIZED_COMMANDS=PASS
BASH_SANDBOX_ONLY_WHEN_AUTHORIZED_COMMANDS=PASS
SIDE_EFFECT_PATH_GRANTS_FILE_WRITE=NO
SOURCE_PATH_GRANTS_BASH=NO
AUTHORIZED_COMMANDS_BROADEN_SOURCE=NO
HOOK_SELECTION_BY_MATCHER=PASS
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
BASH_GUARD_SHA256=b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15
FULL_LOG=FILE_WRITE_GUARD_INDEPENDENT_REVIEW_FULL.log
M1_CHANGED_PATH_CONSUMERS=OUT_OF_SCOPE
OS_SANDBOX_ENFORCEMENT=NOT_EVALUATED_IN_THIS_GATE
PERSONAL_WEB_DOGFOOD_READY=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
PERSONAL_WEB_TOUCHED=NO
NEXT_ACTION=REVIEW_M1_CHANGED_PATH_CONSUMER_RECONCILIATION
