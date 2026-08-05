"""SARIF 2.1.0 output for `check --format sarif`.

One rule per guard; one result per file-level finding where the guard
knows the file (scope violations, tampered tests, dependency manifests,
the largest files of an oversized diff), else one result per guard.
FAIL maps to level "error", WARN to "warning"; PASS/SKIP produce no
results — SARIF reports findings, not attestations.

Uploaded to GitHub code scanning this turns a red check into an
annotation on the exact file that caused it.
"""

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
HOMEPAGE = "https://github.com/Pandaeb/ai-code-guardrails"

# ruleId -> shortDescription, in reporting order (matches GUARD_ORDER's
# printed names).
RULES = {
    "selfmod": "A PR must not modify the guard rules it is judged by "
               "without the human-applied guardrail-change label.",
    "diff-size": "PR diff stays reviewable: warn over the soft limit, "
                 "fail over the hard cap; claimed generated/vendored "
                 "exemptions are audited.",
    "scope": "Every changed file must be declared by the task's scope "
             "declaration, read from the base ref.",
    "test-integrity": "No deleted test files, added skip/focus markers, "
                      "or net assertion loss.",
    "deps": "Every added dependency must be named in a declaration "
            "file; optionally checked against its registry.",
}

_LEVELS = {"FAIL": "error", "WARN": "warning"}


def to_sarif(entries, version):
    """Build the SARIF document from the collector entries (_core.report)."""
    results = []
    for entry in entries:
        level = _LEVELS.get(entry["status"])
        if level is None:
            continue
        findings = entry.get("findings") or []
        for finding in findings:
            results.append({
                "ruleId": entry["guard"],
                "level": level,
                "message": {"text": finding["message"]},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding["path"]},
                    },
                }],
            })
        if not findings:
            results.append({
                "ruleId": entry["guard"],
                "level": level,
                "message": {"text": "\n".join(entry["detail"]) or entry["guard"]},
            })
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "ai-code-guardrails",
                    "version": version,
                    "informationUri": HOMEPAGE,
                    "rules": [
                        {
                            "id": rule_id,
                            "shortDescription": {"text": text},
                            "helpUri": HOMEPAGE + "#what-it-checks",
                        }
                        for rule_id, text in RULES.items()
                    ],
                },
            },
            "results": results,
        }],
    }
