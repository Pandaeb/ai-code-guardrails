"""Run every guard in one process and summarize (CI / local entry point).

Usage:
    ai-code-guardrails check --base origin/main [--head HEAD]
        [--feature <name>] [--config <path>] [--registry-check]
        [--skip guard1,guard2]

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
import os

from . import __version__
from ._core import load_config, run_git
from .guards import GUARD_ORDER, RUNNERS


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

    for guard in GUARD_ORDER:
        if guard in skip:
            results[guard] = "SKIP"
            continue
        if guard == "scope" and not feature:
            print("[SKIP] scope — no feature name detectable")
            results[guard] = "SKIP"
            continue
        code = RUNNERS[guard](args, config)
        results[guard] = "FAIL" if code else "OK"

    print("\n=== guardrails summary ===")
    for guard, outcome in results.items():
        print("%-16s %s" % (guard, outcome))
    if any(v == "FAIL" for v in results.values()):
        print("\nOne or more guards failed. Waivers (guard-ack) are human-only —")
        print("a PR label added by a maintainer, never anything the author writes.")
        return 1
    return 0


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

    args = parser.parse_args(argv)
    return run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
