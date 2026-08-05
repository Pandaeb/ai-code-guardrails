"""Guard: PR diff budget.

Counts changed lines (added + deleted) between base and head. Warns over
the soft limit, fails over the hard cap unless a human waiver (the
`oversize` PR label) is present.

Exclusions come in two kinds. **Structural** ones — lockfiles,
`node_modules` — are things nobody hand-writes, so they are dropped
silently. Everything else in `exclude` is a **claimed** exemption: the
author asserts the path is generated or vendored. Agents choose file
names, so `payments.generated.ts` and `vendor/payments.ts` are claims,
not facts — they are counted separately and fail the guard once they
exceed `exempt_audit_limit`.

Thresholds are read from the BASE ref, never from the branch under
review (see _core.load_config).
"""

from .._core import (
    collect_acks,
    matches_any,
    numstat,
    report,
    waiver_lines,
    waiver_state,
)


def run(args, config):
    cfg = config["diff_size"]
    structural = cfg.get("structural_exclude", [])
    exempt_limit = cfg.get("exempt_audit_limit", cfg["hard_limit"])

    counted, claimed_exempt = [], []
    structural_lines = 0
    for added, deleted, path in numstat(args.base, args.head):
        if added is None:  # binary
            continue
        changed = added + deleted
        if matches_any(path, structural):
            structural_lines += changed
        elif matches_any(path, cfg["exclude"]):
            claimed_exempt.append((changed, path))
        else:
            counted.append((changed, path))

    total = sum(n for n, _ in counted)
    exempt_total = sum(n for n, _ in claimed_exempt)
    soft, hard = cfg["soft_limit"], cfg["hard_limit"]
    trusted, claims = collect_acks(args.base, args.head)
    waivers = waiver_state("oversize", trusted, claims)

    detail = [
        "changed lines: %d (soft limit %d, hard cap %d)" % (total, soft, hard),
        "excluded: %d structural, %d claimed generated/vendored" % (structural_lines, exempt_total),
        "largest counted files:",
    ] + ["%5d  %s" % (n, p) for n, p in sorted(counted, reverse=True)[:10]]

    if exempt_total > exempt_limit:
        lines = detail + [
            "",
            "%d lines claim a generated/vendored exemption, over the %d-line"
            % (exempt_total, exempt_limit),
            "audit limit. Exemptions are matched on file NAME, so this much",
            "unreviewed diff needs a human to confirm the claim:",
        ] + ["%5d  %s" % (n, p) for n, p in sorted(claimed_exempt, reverse=True)[:10]]
        waiver = waiver_lines("oversize", trusted, claims)
        findings = [
            {"path": p, "message": "%d lines claim a generated/vendored exemption "
                                   "(PR claims %d in total, audit limit %d)" % (n, exempt_total, exempt_limit)}
            for n, p in sorted(claimed_exempt, reverse=True)[:10]
        ]
        if "oversize" in trusted:
            return report("diff-size", "WARN", lines + waiver, waivers=waivers, findings=findings)
        return report("diff-size", "FAIL", lines + waiver, waivers=waivers, findings=findings)

    if total > hard:
        lines = detail + [
            "over the hard cap. Split the work at a task boundary into",
            "sequential PRs, or ask a maintainer to add the `oversize` label.",
        ] + waiver_lines("oversize", trusted, claims)
        findings = [
            {"path": p, "message": "%d changed lines here (PR counts %d, hard cap %d)"
                                   % (n, total, hard)}
            for n, p in sorted(counted, reverse=True)[:10]
        ]
        if "oversize" in trusted:
            return report("diff-size", "WARN", lines, waivers=waivers, findings=findings)
        return report("diff-size", "FAIL", lines, waivers=waivers, findings=findings)

    if total > soft:
        return report("diff-size", "WARN", detail + ["over the soft limit - consider splitting."])
    return report(
        "diff-size",
        "PASS",
        ["changed lines: %d (limit %d/%d); %d excluded" % (total, soft, hard, structural_lines + exempt_total)],
    )
