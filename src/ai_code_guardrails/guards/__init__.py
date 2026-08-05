"""Guard registry.

GUARD_ORDER is the execution order of `ai-code-guardrails check`;
RUNNERS maps each name to a `run(args, config) -> int` callable that
prints one [PASS|WARN|FAIL|SKIP] report and returns 1 only on FAIL.
"""

from . import deps, diff_size, scope, selfmod, test_integrity

GUARD_ORDER = ["selfmod", "diff_size", "scope", "test_integrity", "deps"]

RUNNERS = {
    "selfmod": selfmod.run,
    "diff_size": diff_size.run,
    "scope": scope.run,
    "test_integrity": test_integrity.run,
    "deps": deps.run,
}
