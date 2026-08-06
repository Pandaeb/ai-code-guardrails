"""Guard registry.

GUARD_ORDER is the execution order of `ai-code-guardrails check`;
RUNNERS maps each name to a `run(args, config) -> int` callable that
prints one [PASS|WARN|FAIL|SKIP] report and returns 1 only on FAIL.
"""

from . import deps, diff_size, scope, selfmod, suppression, test_integrity

GUARD_ORDER = ["selfmod", "diff_size", "scope", "test_integrity", "suppression", "deps"]

RUNNERS = {
    "selfmod": selfmod.run,
    "diff_size": diff_size.run,
    "scope": scope.run,
    "test_integrity": test_integrity.run,
    "suppression": suppression.run,
    "deps": deps.run,
}
