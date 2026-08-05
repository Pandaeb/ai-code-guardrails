"""Run every guard in one process and summarize (CI / local entry point).

Usage:
    ai-code-guardrails check --base origin/main [--head HEAD]
        [--feature <name>] [--config <path>] [--registry-check]
        [--skip guard1,guard2] [--format human|json]

The feature name (for the scope and deps guards) is taken from --feature,
else the GUARDRAILS_FEATURE / GITHUB_HEAD_REF / CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
env vars, else the current branch — mapping `feature/<name>` -> `<name>`
and `fix/<name>` -> `fix-<name>`.

Waiver env vars, wired by the CI workflow:
    GUARDRAILS_ACKS     comma-separated PR labels — the ONLY trusted
                        waiver surface (adding a label needs triage
                        permission, which keeps waivers human-only)
    GUARDRAILS_PR_BODY  the PR description — scanned for REPORTING only;
                        a guard-ack token found here is an attempted
                        self-waiver and never changes a verdict

Exit code: 1 if any guard fails, else 0. Lint / type check / tests /
build and a secrets scan are separate pipeline stages — this runner
covers only the guards.
"""

import argparse
import contextlib
import io
import json
import os

from . import __version__
from ._core import load_config, report, run_git, start_collecting, stop_collecting
from .guards import GUARD_ORDER, RUNNERS

# The machine-readable schema is a public contract, versioned separately
# from the package: bump only on breaking changes to the JSON shape.
SCHEMA_VERSION = 1


def detect_feature():
    branch = (
        os.environ.get("GUARDRAILS_FEATURE")
        or os.environ.get("GITHUB_HEAD_REF")
        or os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME")
        or run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    )
    if branch.startswith("feature/"):
        return branch.split("/", 1)[1]
    if branch.startswith("fix/"):
        return "fix-" + branch.split("/", 1)[1]
    return branch if not branch.endswith("HEAD") else None


def run_check(args):
    feature = args.feature or detect_feature()
    args.feature = feature
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    config = load_config(args.config, base=args.base)
    results = {}

    # Machine formats reuse the guards untouched: report() collects the
    # structured entries while its printed output goes to a discarded
    # buffer, so stdout stays pure JSON. The default human format is
    # byte-for-byte what it always was — CI log readers and the corpus
    # harness parse it.
    machine = args.format != "human"
    if machine:
        start_collecting()
    sink = contextlib.redirect_stdout(io.StringIO()) if machine else contextlib.nullcontext()

    with sink:
        for guard in GUARD_ORDER:
            if guard in skip:
                results[guard] = "SKIP"
                if machine:
                    # Guard names in machine output match the printed report
                    # (diff-size, test-integrity), not the registry keys.
                    report(guard.replace("_", "-"), "SKIP", ["skipped via --skip"])
                continue
            if guard == "scope" and not feature:
                if machine:
                    report(guard, "SKIP", ["no feature name detectable"])
                else:
                    print("[SKIP] scope — no feature name detectable")
                results[guard] = "SKIP"
                continue
            code = RUNNERS[guard](args, config)
            results[guard] = "FAIL" if code else "OK"

    exit_code = 1 if any(v == "FAIL" for v in results.values()) else 0

    if machine:
        entries = stop_collecting()
        doc = {
            "schema_version": SCHEMA_VERSION,
            "package": {"name": "ai-code-guardrails", "version": __version__},
            "check": {"base": args.base, "head": args.head, "feature": feature},
            "guards": entries,
            "summary": {
                status.lower(): sum(1 for e in entries if e["status"] == status)
                for status in ("PASS", "WARN", "FAIL", "SKIP")
            },
            "exit_code": exit_code,
        }
        print(json.dumps(doc, indent=2))
        return exit_code

    print("\n=== guardrails summary ===")
    for guard, outcome in results.items():
        print("%-16s %s" % (guard, outcome))
    if exit_code:
        print("\nOne or more guards failed. Waivers (guard-ack) are human-only —")
        print("a PR label added by a maintainer, never anything the author writes.")
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ai-code-guardrails",
        description="Deterministic guardrails for AI-written code.",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + __version__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="run every guard against a git diff (base...head)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument("--base", required=True, help="base ref (integration branch)")
    check.add_argument("--head", default="HEAD", help="head ref (default HEAD)")
    check.add_argument("--feature", default=None,
                       help="feature name for the scope and deps guards")
    check.add_argument("--config", default=None,
                       help="path to a config file (default: .guardrails.json at --base)")
    check.add_argument("--registry-check", action="store_true",
                       help="verify added dependencies exist on their registry (needs network)")
    check.add_argument("--skip", default="", help="comma-separated guard names to skip")
    check.add_argument("--format", default="human", choices=["human", "json"],
                       help="output format (default: the human-readable report)")

    args = parser.parse_args(argv)
    return run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
