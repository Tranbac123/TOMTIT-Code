# Claude CLI Prompt Argv-Ordering Independent Review

## Verdict

APPROVE. The argv-ordering defect is resolved. The Owner may rerun the same
personal-web dogfood workload.

## Independent findings

- Root cause confirmed: the former shape placed the task prompt after the
  variadic `--allowedTools <rules...>` sequence. The prompt was therefore a
  valid additional rule token, while `-p` was followed by `--output-format`
  instead of a prompt. The observed Claude error requiring stdin or a prompt
  in print mode is consistent with that argv.
- The shipped builder now produces `claude -p <TASK_PROMPT>
  --output-format json ...`. The prompt is inserted exactly once and
  immediately after `-p`. With authorization active its index is before
  `--allowedTools`; all rules, the guard settings path, and the request's
  permission mode remain present. Optional `--model <value>` behavior is
  preserved; the option follows the authorization rules and its `--` prefix
  terminates their variadic sequence.
- The executable fake-CLI regression is substantive. It runs the old ordering
  as a subprocess and asserts exit 1 plus the missing-prompt error, then runs
  the adapter-built ordering and asserts exit 0 and exact recovery of the
  prompt and allowed-tools rules. The independent targeted run passed.
- Both updated legacy assertions are safe. Checking the value immediately
  after `-p` is stricter than checking only that the prompt is last.
  `len(sanitized_argv) == idx + 2` proves that the sole expected rule is the
  final element in that no-model test, so no additional allowed-tools rule
  follows it.
- Exact authorization remains layered and fail-closed: canonical argv
  validation and fingerprints, guard-file preflight, `-E -s`, native
  `--allowedTools`, request permission mode, `run_in_background` rejection,
  and provider-output withholding are preserved. The native bare-Bash flag
  remains `False`; layered exact authorization remains enabled. The guard
  SHA-256 is exactly
  `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`.
- Requests without authorized commands retain the prompt immediately after
  `-p` and gain neither `--settings` nor `--allowedTools`.
- No implementation, test, governance, Git history, or personal-web state was
  changed by this review. No real model was invoked.

## Verification receipt

| Command | Result |
|---|---:|
| `python3 .tomtit/dogfood/test_run_loop.py` | 52 passed, exit 0 |
| `python3 .tomtit/dogfood/test_run_repair.py` | 51 passed, exit 0 |
| `python3 .tomtit/dogfood/test_run_review.py` | 45 passed, exit 0 |
| executable fake-CLI regression test | 1 passed, exit 0 |
| `python3 -m pytest tests/adapters/test_exact_bash_guard.py -q` | 70 passed, exit 0 |
| `python3 -m pytest tests/adapters -q` | 410 passed, exit 0 |
| `python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q` | 4 passed, exit 0 |
| `python3 -m pytest -q` | 736 passed, exit 0 |
| `python3 -m compileall -q src tests scripts .tomtit/dogfood` | exit 0 |
| `ruff check src tests scripts` | exit 0 |
| `python3 -m mypy src` | exit 0; 14 source files clean |
| `git diff --check` | exit 0 |

First-run record: the three dogfood harnesses and targeted regression initially
aborted before collection with the sandbox OpenMP shared-memory error 179
(exit 134). Each completed successfully when rerun outside that sandbox
restriction. This was an execution-environment limitation, not a test failure;
no product or process-cleanup flake occurred.

Open findings: none.

VERDICT=APPROVE
ROOT_CAUSE_CONFIRMED=YES
PROMPT_IMMEDIATELY_AFTER_PRINT_FLAG=PASS
PROMPT_BEFORE_ALLOWEDTOOLS=PASS
PROMPT_PRESENT_EXACTLY_ONCE=PASS
EXECUTABLE_REGRESSION_PROOF=PASS
LEGACY_TEST_UPDATES=SAFE
EXACT_AUTHORIZATION_LAYER=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
NO_COMMAND_COMPATIBILITY=PASS
PERSONAL_WEB_DOGFOOD_READY=YES
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=RERUN_SAME_PERSONAL_WEB_DOGFOOD_WORKLOAD
