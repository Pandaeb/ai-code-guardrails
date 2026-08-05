# ai-code-guardrails

Deterministic guardrails for AI-written code. **Catches 21 of 21 known
failure modes of AI-generated changes, at 0% false positives on the
control corpus** — and ships the corpus so you can verify that claim
yourself.

## The problem

Code written by AI agents fails in patterned, adversarial-looking ways
that human review is bad at catching: the failing test quietly deleted
or skipped, the diff that balloons past reviewability, the "drive-by"
edit outside the task, the hallucinated or squatted dependency, and —
once you add automated checks — the agent editing the checks
themselves, or granting itself the waiver.

Linters and CVE scanners don't look for any of that. These guards do,
mechanically, on every pull request.

## What it checks

Every guard is a pure function of the git diff (`base...head`), runnable
in any CI and locally. No runtime dependencies — stdlib Python only.

| Guard | Catches |
|---|---|
| **diff budget** | PRs past reviewable size (default: warn at 400 changed lines, fail at 800), including evasion via fake "generated"/"vendored" file names — claimed exemptions are counted and capped |
| **scope discipline** | files the declared task never authorised — declarations are read from the base ref, so a PR can't widen its own permission slip |
| **test integrity** | deleted test files, added skip/focus markers, net assertion loss |
| **dependency policy** | dependencies absent from the project's declaration file (with machine-recognisable matching — a package named `is` can't hide in prose), optional registry-existence check against slopsquatting |
| **self-modification** | any PR touching the guards' own code or config without a human-applied label |

**Waivers are structurally human-only.** A guard is overridden by adding
a PR label — which requires triage permission on the repository. Waiver
tokens written into commit messages or the PR body (both of which an
agent authors) never change a verdict; they are reported as attempted
self-waivers.

## Verify the claim

The red-team corpus is executable: 21 attacks that must be caught and
10 legitimate controls that must stay quiet, each a throwaway git
repository whose diff *is* the attack. It runs as this package's test
suite:

```bash
git clone https://github.com/Pandaeb/ai-code-guardrails
cd ai-code-guardrails
pip install -e ".[dev]"
pytest
```

Controls are not optional decoration: a detection rate is only
meaningful next to a false-positive rate. A guard that flags everything
scores 100% on attacks and gets uninstalled within a week.

## License

[FSL-1.1-MIT](LICENSE.md) (Functional Source License): the source is
open, you can use it freely — including commercially, in your company's
CI — but not to build a competing product. Each version automatically
becomes plain **MIT two years** after its release. Deliberately
source-available, not OSI open source.

Vulnerabilities — including guard bypasses — are security reports: see
[SECURITY.md](SECURITY.md). Contributions: see
[CONTRIBUTING.md](CONTRIBUTING.md).
