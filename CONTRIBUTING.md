# Contributing

Thanks for considering a contribution. This project holds itself to the
rules it enforces, so the bar is explicit and mechanical.

## Licensing of contributions (CLA)

The project is licensed under [FSL-1.1-MIT](LICENSE.md): source-available,
with each version becoming MIT two years after its release. Making that
promise — and keeping the option to offer the software under other terms —
requires the maintainer to hold sufficient rights to the whole work.

**By submitting a contribution you agree that:**

1. you have the right to license your contribution, and
2. you grant the maintainer a perpetual, worldwide, irrevocable licence to
   use, modify, distribute, and **relicense** your contribution as part of
   this project, including under future license versions or different
   terms.

There is no signing ceremony today; opening a PR constitutes agreement.
A CLA bot will be added once external PRs start arriving, and this
paragraph will move into it.

## The rules every PR is held to

The red-team corpus is the product's regression suite. Before opening a
PR, run the full suite locally and keep it green:

```bash
pip install -e ".[dev]"
pytest
```

- **Detection may never regress:** all corpus attacks stay caught, all
  controls stay quiet (currently 28/28 attacks, 0% false positives).
- **A new guard ships with its controls in the same PR** — at least one
  legitimate change the guard must stay quiet on. A guard that scores
  100% by failing everything is worthless.
- **A fixed bypass becomes a corpus case** — write the case first, watch
  it fail, then fix. A case that has never been red proves nothing.
- **Diff budget:** target ≤ 400 changed lines per PR, hard cap 800.
  Oversized work is split into sequential PRs.
- **Waivers are human-only.** Guard waivers come from PR labels (which
  require triage permission), never from anything an author writes.

## Workflow security rules (non-negotiable)

These apply to every GitHub Actions workflow in this repository. PRs
that violate them will not be merged, whatever else they do:

- **Never `pull_request_target`.** Workflows triggered by PRs run on
  `pull_request` with the default read-only token.
- **Untrusted input is never interpolated into `run:`.** PR titles,
  bodies, branch names, commit messages, and labels reach scripts only
  via `env:` variables, quoted at use.
- **`permissions:` defaults to `contents: read`** at workflow level;
  jobs that need more request the specific scope, with a comment saying
  why.
- **GitHub-hosted runners only.** No self-hosted runners, ever.
- **No long-lived credentials in secrets.** Publishing to PyPI uses
  trusted publishing (OIDC); there is no API token to steal.

## Practicalities

- One logical change per PR; PRs from branches, `main` is protected
  (PR required, no force pushes, required checks).
- Vulnerabilities — including guard bypasses — go through
  [SECURITY.md](SECURITY.md), not public issues.
