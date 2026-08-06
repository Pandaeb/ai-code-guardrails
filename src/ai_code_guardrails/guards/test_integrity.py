"""Guard: test integrity.

Flags, for files matching the configured test globs:
  1. deleted test files                      -> FAIL unless the
     `test-removal` label is present
  2. added skip/focus markers (.only, xit,
     @pytest.mark.skip, [Ignore], t.Skip...) -> FAIL unless `test-weakening`
  3. net assertion loss in modified tests    -> WARN; FAIL at/over the
     configured threshold unless `test-weakening`
  4. a MODIFIED snapshot in a PR that changes nothing else — the
     recorded output was bent to match broken behaviour (`jest -u` on a
     red suite)                              -> FAIL unless `test-weakening`
  5. tautological assertions: a literal tautology (`assert True`,
     `expect(1).toBe(1)`), or asserting the very value a mock in the
     same change was told to return          -> FAIL unless `test-weakening`
  6. a test that mocks the unit it is named after (`test_charge.py`
     patching `...charge`) — the subject is replaced by the mock, so
     the test exercises nothing              -> FAIL unless `test-weakening`

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
    waiver_state,
)

LITERAL_TAUTOLOGY_RES = [
    r"\bassert\s+True\b(?!\s*==)",
    r"\bassert\s+1\s*==\s*1\b",
    r"\bassertTrue\(\s*True\s*\)",
    r"\bexpect\(\s*true\s*\)\.toBe\(\s*true\s*\)",
    r"\bexpect\(\s*(\d+)\s*\)\.toBe\(\s*\1\s*\)",
]

STUB_VALUE_RES = [
    r"\.return_value\s*=\s*(?P<lit>[^\s#]+)",
    r"\bmockReturnValue\(\s*(?P<lit>[^)]+)\)",
    r"\bmockResolvedValue\(\s*(?P<lit>[^)]+)\)",
]

MOCK_TARGET_RES = [
    r"""(?:@|mocker\.|mock\.|unittest\.mock\.)?patch(?:\.object)?\(\s*['"](?P<target>[\w./-]+)['"]""",
    r"""\b(?:jest|vi)\.mock\(\s*['"](?P<target>[\w./@-]+)['"]""",
]

TEST_NAME_AFFIXES = ("test_", "_test", ".test", ".spec", "spec_")


def test_subject(path):
    """The unit a test file is named after: tests/test_charge.py -> charge."""
    stem = path.rsplit("/", 1)[-1]
    stem = stem.split(".", 1)[0] if "." in stem else stem
    for affix in TEST_NAME_AFFIXES:
        stem = stem.replace(affix, "")
    return stem.strip("_").lower()


def stubbed_literals(lines):
    """Literal values that mocks in these added lines are told to return."""
    literals = []
    for line in lines:
        for pattern in STUB_VALUE_RES:
            m = re.search(pattern, line)
            if m:
                lit = m.group("lit").strip().rstrip(";,")
                # Only self-evident literals: a name could legitimately
                # be recomputed by the unit under test.
                if lit and len(lit) < 40 and (lit[0] in "\"'0123456789-" or lit in ("True", "False", "true", "false")):
                    literals.append(lit)
    return literals


def tautologies(added):
    """Human-readable descriptions of tautological assertions in `added`."""
    found = []
    for line in added:
        for pattern in LITERAL_TAUTOLOGY_RES:
            if re.search(pattern, line):
                found.append("literal tautology: %s" % line.strip()[:100])
                break
    for lit in set(stubbed_literals(added)):
        esc = re.escape(lit)
        for line in added:
            if re.search(r"(?:==\s*|\.toBe\(\s*|\.toEqual\(\s*)%s" % esc, line) and not re.search(
                r"\.return_value|mockReturnValue|mockResolvedValue", line
            ):
                found.append("asserts the very value the mock returns (%s): %s"
                             % (lit, line.strip()[:100]))
                break
    return found


def mocked_subjects(path, added):
    """Mock targets in `added` whose last component IS this file's subject."""
    subject = test_subject(path)
    if not subject:
        return []
    hits = []
    for line in added:
        for pattern in MOCK_TARGET_RES:
            m = re.search(pattern, line)
            if m:
                last = re.split(r"[./]", m.group("target").rstrip("/"))[-1].lower()
                if last == subject:
                    hits.append("mocks the unit it is named after (`%s`): %s"
                                % (m.group("target"), line.strip()[:100]))
    return hits


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

    changed = changed_files(args.base, args.head)
    snapshot_globs = cfg.get("snapshot_globs", [])

    deleted, weakened, warnings, findings = [], [], [], []

    # A modified snapshot is only suspicious when it is the ONLY thing
    # changing: no source edit, no test edit — the recorded expectation
    # was regenerated to match whatever the code now produces. New
    # snapshots (status A) arrive with new tests and are fine.
    modified_snapshots = [p for s, p in changed if s == "M" and matches_any(p, snapshot_globs)]
    non_snapshot_changes = [p for s, p in changed if not matches_any(p, snapshot_globs)]
    if modified_snapshots and not non_snapshot_changes:
        for path in modified_snapshots:
            weakened.append("%s - snapshot updated with no code or test change" % path)
            findings.append({"path": path, "message": "snapshot updated with no code or "
                                                      "test change - the expectation was bent "
                                                      "to the output"})

    patches = None
    for status, path in changed:
        if not matches_any(path, cfg["test_globs"]):
            continue
        if status == "D":
            deleted.append(path)
            findings.append({"path": path, "message": "deleted test file"})
            continue
        if patches is None:
            patches = diff_by_path(args.base, args.head)
        added, removed = added_removed_lines(patches.get(path, ""))

        for desc in tautologies(added) + mocked_subjects(path, added):
            weakened.append("%s - %s" % (path, desc))
            findings.append({"path": path, "message": desc})

        new_skips = [l.strip() for l in added if any(re.search(p, l) for p in skip_res)]
        if new_skips:
            weakened.append("%s - added skip/focus marker(s): %s" % (path, "; ".join(new_skips[:3])))
            findings.append({"path": path, "message": "added skip/focus marker(s): %s"
                                                      % "; ".join(new_skips[:3])})

        loss = count_matches(removed, assert_res) - count_matches(added, assert_res)
        if loss >= threshold:
            weakened.append("%s - net assertion loss of %d" % (path, loss))
            findings.append({"path": path, "message": "net assertion loss of %d" % loss})
        elif loss > 0:
            warnings.append("%s - net assertion loss of %d (below threshold)" % (path, loss))
            findings.append({"path": path, "message": "net assertion loss of %d (below threshold)" % loss})

    trusted, claims = collect_acks(args.base, args.head)
    failures, waivers = [], []
    if deleted:
        waivers += waiver_state("test-removal", trusted, claims)
    if weakened:
        waivers += waiver_state("test-weakening", trusted, claims)
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
            waivers=waivers,
            findings=findings,
        )
    if warnings:
        return report("test-integrity", "WARN", warnings, waivers=waivers, findings=findings)
    return report("test-integrity", "PASS", ["no test deletions, skips, or assertion loss"])
