"""Guard: no self-modification of the safety net.

The in-repo guard configuration and the documents defining the floors
are the rules a PR is judged by. A change that edits them in the same PR
as feature work is the one move that defeats every other check — so it
fails here and needs an explicit human decision (the `guardrail-change`
PR label).

The guard *code* is out of reach by construction: it ships as a pinned
package installed in CI, not as scripts inside the repository under
review. What remains in-repo — `.guardrails.json` and any legacy guard
tree — is what this guard watches.

Legitimate guardrail work is expected and fine; it just belongs in its
own PR, reviewed on its own merits, with the label attached.
"""

from .._core import (
    PROTECTED_PATHS,
    changed_files,
    collect_acks,
    matches_any,
    report,
    show_file,
    waiver_lines,
    waiver_state,
)

FLOOR_MARKERS = ["[floor]", "human-only"]


def run(args, config):
    cfg = config.get("selfmod", {})
    protected = cfg.get("protected_paths", PROTECTED_PATHS)

    touched = [(status, path) for status, path in changed_files(args.base, args.head)
               if matches_any(path, protected)]
    if not touched:
        return report("selfmod", "PASS", ["no guardrail scripts, config, or floors touched"])

    lines = ["this PR modifies the rules it is judged by:"]
    lines += ["  %s  %s" % (status, path) for status, path in touched]

    for _, path in touched:
        if not path.endswith(".md"):
            continue
        before = show_file(args.base, path) or ""
        after = show_file(args.head, path) or ""
        for marker in FLOOR_MARKERS:
            lost = before.lower().count(marker) - after.lower().count(marker)
            if lost > 0:
                lines.append("  %s drops %d occurrence(s) of '%s'" % (path, lost, marker))

    trusted, claims = collect_acks(args.base, args.head)
    lines += waiver_lines("guardrail-change", trusted, claims)
    waivers = waiver_state("guardrail-change", trusted, claims)

    if "guardrail-change" in trusted:
        return report("selfmod", "WARN", lines + [
            "reviewer: confirm the change does not remove a floor",
            "(hard cap present, test-integrity enabled, waivers human-only).",
        ], waivers=waivers)
    return report("selfmod", "FAIL", lines + [
        "move the guardrail change into its own PR, or ask a maintainer to",
        "add the `guardrail-change` label. Floor documents may be tightened,",
        "never weakened.",
    ], waivers=waivers)
