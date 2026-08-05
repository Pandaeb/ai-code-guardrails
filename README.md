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

## Install

Not on PyPI yet — install straight from GitHub:

```bash
pipx install git+https://github.com/Pandaeb/ai-code-guardrails
```

or, without pipx:

```bash
pip install git+https://github.com/Pandaeb/ai-code-guardrails
```

or, for hacking on it:

```bash
git clone https://github.com/Pandaeb/ai-code-guardrails
cd ai-code-guardrails
pip install -e ".[dev]"
```

Python 3.9+, no runtime dependencies, works anywhere `git` does.

## Run it on your repository

From inside any git repository with an open change:

```bash
ai-code-guardrails check --base origin/main
```

That compares `origin/main...HEAD` and prints one verdict per guard
plus a summary; exit code 1 means at least one guard failed. On a
repository with no configuration at all you get sensible behaviour:
diff budget (400/800), test integrity, and dependency policy are live,
the scope guard skips (it needs a declared task scope), and
self-modification watches `.guardrails.json`.

## Wire it into CI

```yaml
permissions:
  contents: read

on:
  pull_request:
    # labeled/unlabeled matter: adding a waiver label must re-run the guards
    types: [opened, synchronize, reopened, labeled, unlabeled]

jobs:
  guards:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0 # the guards diff against the base branch
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - run: pip install git+https://github.com/Pandaeb/ai-code-guardrails@main
      - run: ai-code-guardrails check --base "origin/${GITHUB_BASE_REF}" --head HEAD
        env:
          GUARDRAILS_ACKS: ${{ join(github.event.pull_request.labels.*.name, ',') }}
          GUARDRAILS_PR_BODY: ${{ github.event.pull_request.body }}
```

Then create the waiver labels (`oversize`, `scope`, `test-removal`,
`test-weakening`, `new-dependency`, `guardrail-change`) and make the
check required in branch protection. The package installs from outside
the repository under review — pin `@main` to a tag once releases exist —
so a PR cannot rewrite the checks that judge it.

## Machine-readable output

`--format json` prints a stable, versioned report (`schema_version: 1`):
package version, one entry per guard (status, detail, structured waiver
records — including ignored self-waiver attempts), summary counts, and
the exit code.

`--format sarif` prints SARIF 2.1.0 with one rule per guard and, where
the guard knows the offending file, a location per finding. Uploaded to
GitHub code scanning it turns a red check into an annotation on the
exact file — the undeclared dependency's manifest, the deleted test,
the file that blew the budget:

```yaml
jobs:
  guards:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      # Job-level exception to the contents:read default: the SARIF
      # upload writes to the repository's code-scanning API.
      security-events: write
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - run: pip install git+https://github.com/Pandaeb/ai-code-guardrails@main
      - run: ai-code-guardrails check --base "origin/${GITHUB_BASE_REF}" --head HEAD --format sarif > guardrails.sarif
        env:
          GUARDRAILS_ACKS: ${{ join(github.event.pull_request.labels.*.name, ',') }}
          GUARDRAILS_PR_BODY: ${{ github.event.pull_request.body }}
      - uses: github/codeql-action/upload-sarif@v3
        # The check step exits 1 when a guard fails - upload the
        # annotations exactly then, and keep the job red.
        if: always()
        with:
          sarif_file: guardrails.sarif
```

## Configure

Optional `.guardrails.json` at the repository root — read from the
**base** ref, so a PR editing it is judged by the old rules (and trips
the self-modification guard):

```json
{
  "diff_size": { "soft_limit": 400, "hard_limit": 800 },
  "selfmod": {
    "protected_paths": [".guardrails.json", ".github/workflows/**"]
  },
  "deps": { "declaration_files": ["docs/dependencies.md"] },
  "test_integrity": { "assert_loss_fail_threshold": 3 }
}
```

Every key overrides the built-in defaults per section; this repository's
own [.guardrails.json](.guardrails.json) is a working example.

## License

[FSL-1.1-MIT](LICENSE.md) (Functional Source License): the source is
open, you can use it freely — including commercially, in your company's
CI — but not to build a competing product. Each version automatically
becomes plain **MIT two years** after its release. Deliberately
source-available, not OSI open source.

Vulnerabilities — including guard bypasses — are security reports: see
[SECURITY.md](SECURITY.md). Contributions: see
[CONTRIBUTING.md](CONTRIBUTING.md).
