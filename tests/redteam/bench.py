"""Guard performance benchmark.

The corpus answers "do the guards catch it". This answers "what do they
cost". The guards run on every PR and block the merge button, so their
latency is felt on every single change; a guard that takes a minute on a
large repository will get skipped, and a skipped guard is not a guard.

Builds synthetic repositories of increasing diff size and times each
guard against them, in-process — the same way `check` runs them.

Usage:
    python tests/redteam/bench.py
    python tests/redteam/bench.py --scales 10,100,1000 --repeat 3
"""

import argparse
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import build_repo, run_guard  # noqa: E402

GUARDS = ["selfmod", "diff_size", "scope", "test_integrity", "deps"]
TAKES_FEATURE = {"scope", "deps"}

TASKS_MD = """# Tasks: bench

## Task 1

### Files to create/modify
- `src/**`
- `tests/**`
"""


def make_case(n_files, lines_per_file):
    body = "".join("statement_%d = %d\n" % (i, i) for i in range(lines_per_file))
    test_body = "".join("    assert compute(%d) == %d\n" % (i, i) for i in range(lines_per_file))

    base = {
        ".specforge/specs/bench/tasks.md": TASKS_MD,
        ".specforge/steering/tech.md": "# Tech\n\n- `pytest`\n",
        "src/seed.py": "seed = 1\n",
    }
    head = {}
    for i in range(n_files):
        head["src/module_%04d.py" % i] = body
    for i in range(max(1, n_files // 4)):
        head["tests/test_module_%04d.py" % i] = "def test_x():\n" + test_body

    return {
        "id": "bench-%d" % n_files,
        "category": "bench",
        "kind": "bench",
        "base": base,
        "head": head,
        "env": {},
        "attack": "",
        "expect": None,
        "commit_message": "feat: bench %d files" % n_files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="10,100,500", help="comma-separated changed-file counts")
    parser.add_argument("--lines", type=int, default=20, help="lines per changed file")
    parser.add_argument("--repeat", type=int, default=1, help="timed runs per guard")
    args = parser.parse_args()

    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    print("lines per file: %d, repeats: %d\n" % (args.lines, args.repeat))
    header = "%-8s %-10s " % ("files", "diff") + "".join("%-13s" % g for g in GUARDS) + "total"
    print(header)
    print("-" * len(header))

    for n in scales:
        case = make_case(n, args.lines)
        workdir = tempfile.mkdtemp(prefix="guardrails-bench-")
        try:
            repo, base_sha = build_repo(case, workdir)
            row, total = [], 0.0
            for guard in GUARDS:
                case["guard"] = guard
                case["feature"] = "bench" if guard in TAKES_FEATURE else None
                samples = []
                for _ in range(args.repeat):
                    status, output, elapsed = run_guard(repo, base_sha, case)
                    if status == "ERROR":
                        raise SystemExit("guard %s errored during bench:\n%s" % (guard, output))
                    samples.append(elapsed)
                median = statistics.median(samples)
                total += median
                row.append("%-13s" % ("%.3fs" % median))
            changed = len(case["head"])
            diff_lines = changed * args.lines
            print("%-8d %-10s " % (changed, "%dk" % (diff_lines // 1000) if diff_lines >= 1000
                                   else str(diff_lines)) + "".join(row) + "%.3fs" % total)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    print("\nNote: guards run in-process here, exactly as `check` runs them —")
    print("a full run pays one interpreter start, not one per guard. Each")
    print("sample still includes the guard's own `git` subprocess calls.")


if __name__ == "__main__":
    raise SystemExit(main())
