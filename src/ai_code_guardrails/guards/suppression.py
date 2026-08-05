"""Guard: no silencing the analyser instead of fixing the code.

The cheapest way to make a type checker or linter go green is to tell
it to stop looking. Agents reach for it readily, and the diff looks
tiny: `# type: ignore` on the line that would not compile, `@ts-ignore`
above the call with the wrong arity, a rule flipped to `"off"` in
`.eslintrc`.

Two different acts, judged differently:

- **inline markers** (`# type: ignore`, `@ts-ignore`, `eslint-disable`,
  `# noqa`, …) are counted across the PR. One is normal engineering, so
  below `added_fail_threshold` this warns; at or above it, it fails.
- **turning a rule off in a linter's configuration** silences the whole
  repository, not one line — it fails on the first occurrence.

Both are waivable by the human-applied `suppression` label. Removing
suppressions is never penalised; only added lines are counted.
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


def added_lines(patch):
    return [line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")]


def run(args, config):
    cfg = config["suppression"]
    markers = cfg["markers"]
    threshold = cfg["added_fail_threshold"]
    excluded = cfg.get("exclude", [])

    inline, config_off, findings = [], [], []
    patches = None
    for status, path in changed_files(args.base, args.head):
        if status == "D" or matches_any(path, excluded):
            continue
        if patches is None:
            patches = diff_by_path(args.base, args.head)
        lines = added_lines(patches.get(path, ""))

        for line in lines:
            for pattern in markers:
                if re.search(pattern, line):
                    inline.append((path, line.strip()))
                    findings.append({"path": path, "message": "added suppression marker: %s"
                                                              % line.strip()[:120]})
                    break

        if matches_any(path, cfg["config_globs"]):
            for line in lines:
                for pattern in cfg["config_disable_patterns"]:
                    if re.search(pattern, line):
                        config_off.append((path, line.strip()))
                        findings.append({"path": path, "message": "linter/type-checker rule "
                                                                  "disabled repository-wide: %s"
                                                                  % line.strip()[:120]})
                        break

    if not inline and not config_off:
        return report("suppression", "PASS", ["no analyser suppressions added"])

    detail = []
    if config_off:
        detail.append("analysis switched off in configuration (repository-wide):")
        detail += ["  %s: %s" % (p, l) for p, l in config_off[:10]]
    if inline:
        detail.append("%d suppression marker(s) added (threshold %d):" % (len(inline), threshold))
        detail += ["  %s: %s" % (p, l) for p, l in inline[:10]]

    trusted, claims = collect_acks(args.base, args.head)
    waivers = waiver_state("suppression", trusted, claims)
    fails = bool(config_off) or len(inline) >= threshold

    if not fails:
        return report("suppression", "WARN", detail + [
            "below the failure threshold - fine if each one is justified,",
            "but a reviewer should see them.",
        ], waivers=waivers, findings=findings)

    detail += waiver_lines("suppression", trusted, claims)
    if "suppression" in trusted:
        return report("suppression", "WARN", detail, waivers=waivers, findings=findings)
    return report("suppression", "FAIL", detail + [
        "fix what the analyser is reporting instead of silencing it. If the",
        "suppression is genuinely correct (a wrong stub, a known upstream",
        "bug), say so in the PR and ask a maintainer for the `suppression`",
        "label.",
    ], waivers=waivers, findings=findings)
