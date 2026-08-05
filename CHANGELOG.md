# Changelog

All notable changes to this package are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A guard bypass is a vulnerability, not a changelog entry — see
[SECURITY.md](SECURITY.md).

## [Unreleased]

### Added

- Dependency guard covers the routes that bypass a manifest's dependency
  list: a lockfile authorising a direct dependency the manifest does not
  name, a new git submodule, and an added or changed install-time script
  (`preinstall`/`install`/`postinstall`/`prepare`/`prepublish`).
- New **suppression** guard: silencing the type checker or linter
  instead of fixing the code. Inline markers (`# type: ignore`,
  `@ts-ignore`, `eslint-disable`, `# noqa`, …) warn below
  `added_fail_threshold` and fail at or above it; switching a rule off
  in a linter's configuration fails on the first occurrence. Waivable
  by the new `suppression` label. Corpus grows to 32 attacks /
  19 controls.

### Changed

- **New waiver label required:** repositories using the guards should
  create a `suppression` label alongside the existing five.

## [0.1.0] — 2026-08-05

First public release, extracted from the SpecForge framework's
verification layer and generalized to run on any git repository.

### Added

- Five guards in one process — `ai-code-guardrails check --base <ref>`:
  diff budget (soft/hard limits, audited "generated/vendored" exemption
  claims), scope discipline, test integrity (deletions, skip/focus
  markers, net assertion loss), dependency policy (machine-recognisable
  declarations, optional `--registry-check` against slopsquatting), and
  guard self-modification.
- Waivers that are structurally human-only: PR labels (triage-gated)
  are the single trusted surface; `guard-ack` tokens in commit messages
  or the PR body are reported as attempted self-waivers and never
  change a verdict.
- Configuration via `.guardrails.json`, always read from the **base**
  ref (legacy `.specforge/guardrails/guardrails.json` layout still
  honoured), so a PR is judged by rules it did not write.
- Spec-less scope declarations: `.guardrails/scope.yml` allow-list as
  an alternative to a SpecForge `tasks.md`, with the same base-ref
  trust rules; the PR description is never a scope provider.
- Graduated dependency policy: unconfigured repositories get a WARN
  with setup instructions; an explicitly configured but unusable policy
  fails; registry misses fail in every mode.
- Machine-readable output: `--format json` (versioned schema v1) and
  `--format sarif` (SARIF 2.1.0 with file-level locations, ready for
  GitHub code scanning).
- Reusable composite GitHub Action at the repository root
  (`uses: Pandaeb/ai-code-guardrails@v0.1.0`) that installs the package
  from the action's own pinned ref and wires the waiver surfaces.
- The executable red-team corpus as the test suite: 25 attacks that
  must be caught, 13 controls that must stay quiet, shipped so the
  detection claim is verifiable by anyone.
- Zero runtime dependencies (stdlib only), Python 3.9+.

[Unreleased]: https://github.com/Pandaeb/ai-code-guardrails/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Pandaeb/ai-code-guardrails/releases/tag/v0.1.0
