# Security policy

## Reporting a vulnerability

**Please do not open a public issue or PR for security problems.**

Use GitHub's private vulnerability reporting: go to the repository's
**Security** tab → **Report a vulnerability**. Reports go directly and
privately to the maintainer. You should get a first response within a
few days; please allow a reasonable disclosure window before publishing
anything.

## What counts as a vulnerability here

This project is a defence layer, so its threat model is wider than
"remote code execution in the CLI":

- **A guard bypass is a vulnerability in this product.** If you find a
  diff pattern, environment trick, or configuration that makes a guard
  report PASS on something it is designed to catch (an evaded diff
  budget, a silently weakened test, a forged waiver, an undeclared
  dependency), report it privately — exactly as you would an exploit.
  It will be fixed and added to the red-team corpus as a permanent
  regression case.
- Classic issues (code execution via crafted repository contents,
  path traversal, secrets handling) are of course in scope too.

False positives — a guard firing on legitimate code — are **not**
security reports; please open a regular issue for those.

## Trust boundary: the token you give your agent

"Waivers are human-only" is enforced through GitHub's permission model:
a waiver is a PR label, and applying a label requires **triage**
permission, which the author of an agent-written PR does not have. That
floor holds against the *author* of the change — it says nothing about
what your agent can do with the credentials you hand it.

If you run an AI agent with a token that carries triage or maintainer
permission (a bot account with write access, a maintainer's PAT, a
GitHub App with `issues: write`), that agent can apply waiver labels to
its own PRs, and the guards will honour them — the guards see a label,
not the judgement behind it. This is not a bypass of the tool; it is a
configuration that steps outside its trust model.

**Recommendation:** agents that author code get tokens with no
permission to edit labels. Keep triage for humans and for automation
you trust as much as a human reviewer.

## Supported versions

Pre-1.0, only the latest released version is supported with fixes.
