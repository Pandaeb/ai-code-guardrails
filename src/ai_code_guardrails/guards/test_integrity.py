"""Guard: test integrity.

Flags, for files matching the configured test globs:
  1. deleted test files                      -> FAIL unless the
     `test-removal` label is present
  2. added skip/focus markers (.only, xit,
     @pytest.mark.skip, [Ignore], t.Skip...) -> FAIL unless `test-weakening`
  3. net assertion loss in modified tests    -> WARN; FAIL at/over the
     configured threshold unless `test-weakening`

Heuristics, not proof — an independent reviewer still reads the tests.
The guard exists so silent test tampering cannot merge unnoticed.
"""

import re

from .._core import (
    changed_files,
    collect_acks,
    diff_by_path,
    matches_any,
    report,
    waiver_lines,
)


def added_removed_lines(patch):
    added, removed = [], []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def count_matches(lines, patterns):
    return sum(1 for line in lines for p in patterns if re.search(p, line))


def run(args, config):
    cfg = config["test_integrity"]
    skip_res = cfg["skip_patterns"]
    assert_res = cfg["assert_patterns"]
    threshold = cfg["assert_loss_fail_threshold"]

    deleted, weakened, warnings = [], [], []
    patches = None
    for status, path in changed_files(args.base, args.head):
        if not matches_any(path, cfg["test_globs"]):
            continue
        if status == "D":
            deleted.append(path)
            continue
        if patches is None:
            patches = diff_by_path(args.base, args.head)
        added, removed = added_removed_lines(patches.get(path, ""))

        new_skips = [l.strip() for l in added if any(re.search(p, l) for p in skip_res)]
        if new_skips:
            weakened.append("%s - added skip/focus marker(s): %s" % (path, "; ".join(new_skips[:3])))

        loss = count_matches(removed, assert_res) - count_matches(added, assert_res)
        if loss >= threshold:
            weakened.append("%s - net assertion loss of %d" % (path, loss))
        elif loss > 0:
            warnings.append("%s - net assertion loss of %d (below threshold)" % (path, loss))

    trusted, claims = collect_acks(args.base, args.head)
    failures = []
    if deleted and "test-removal" not in trusted:
        failures += ["deleted test file: %s" % p for p in deleted]
        failures += waiver_lines("test-removal", trusted, claims)
    elif deleted:
        warnings += ["deleted test file (waived by label): %s" % p for p in deleted]
    if weakened and "test-weakening" not in trusted:
        failures += weakened
        failures += waiver_lines("test-weakening", trusted, claims)
    elif weakened:
        warnings += ["waived by label: %s" % w for w in weakened]

    if failures:
        return report(
            "test-integrity",
            "FAIL",
            failures
            + [
                "fix the code instead of the tests, or - if the behavior change",
                "is legitimate - call it out in the PR description and ask a",
                "maintainer for the matching waiver label.",
            ],
        )
    if warnings:
        return report("test-integrity", "WARN", warnings)
    return report("test-integrity", "PASS", ["no test deletions, skips, or assertion loss"])
