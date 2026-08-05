"""Guard registry.

GUARD_ORDER is the execution order of `ai-code-guardrails check`;
RUNNERS maps each name to a `run(args, config) -> int` callable that
prints one [PASS|WARN|FAIL|SKIP] report and returns 1 only on FAIL.
"""

from . import diff_size, selfmod

GUARD_ORDER = ["selfmod", "diff_size"]

RUNNERS = {
    "selfmod": selfmod.run,
    "diff_size": diff_size.run,
}
