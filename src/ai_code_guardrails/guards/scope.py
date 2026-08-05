"""Guard: scope discipline.

Every changed file must be declared by the change's scope declaration.
Two providers, tried in order:

  1. SpecForge layout: `.specforge/specs/<feature>/tasks.md` — the union
     of all "Files to create/modify" lists (entries may be globs), plus
     an optional "Scope exceptions" section.
  2. Spec-less: `.guardrails/scope.yml` (config key
     `scope.declaration_file`) — a repo-level `allow:` list of globs,
     for teams that will never write a spec.

Either declaration is read from the **base ref**, not from the branch
under review. A PR that widens its own declaration is a scope change
and needs approval; letting the branch supply its own permission slip
would make the rule self-referential. For the same reason the PR
description is NOT a provider: the agent authors it.

Undeclared files fail unless a human waiver (the `scope` PR label) is
present. Skips (exit 0) when no declaration exists at either ref.
"""

import re

from .._core import (
    changed_files,
    collect_acks,
    matches_any,
    report,
    repo_root,
    show_file,
    waiver_lines,
    waiver_state,
)

FILES_HEADING_RE = re.compile(r"^#{2,4}\s*(files to (create|modify)|scope exceptions)", re.I)
HEADING_RE = re.compile(r"^#{1,4}\s")
LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+(.*)$")


def declared_paths(tasks_md_text):
    """Collect list-item paths under 'Files to create/modify' headings."""
    paths = []
    in_section = False
    for line in tasks_md_text.splitlines():
        if FILES_HEADING_RE.match(line):
            in_section = True
            continue
        if HEADING_RE.match(line):
            in_section = False
            continue
        if not in_section:
            continue
        m = LIST_ITEM_RE.match(line)
        if not m:
            continue
        entry = m.group(1).strip().strip("`").split(" — ")[0].split(" - ")[0].strip().strip("`")
        # Skip template placeholders.
        if not entry or "[" in entry or entry.endswith("...") or entry in {"src/...", "tests/..."}:
            continue
        paths.append(entry)
    return paths


def scope_yml_paths(text):
    """Globs from a `.guardrails/scope.yml` `allow:` list.

    Deliberately a minimal YAML subset, not a YAML parser (stdlib-only
    is a floor of this package): one top-level `allow:` key followed by
    `- glob` items; `#` comments and quotes are tolerated. Anything
    fancier is ignored rather than guessed at.
    """
    paths = []
    in_allow = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^allow\s*:\s*$", line):
            in_allow = True
            continue
        if re.match(r"^\S", line):  # any other top-level key ends the list
            in_allow = False
            continue
        if in_allow:
            m = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if m:
                entry = m.group(1).strip().strip("'\"")
                if entry:
                    paths.append(entry)
    return paths


def _texts(args, rel):
    """(base, head) contents of rel; head falls back to the working tree."""
    base_text = show_file(args.base, rel)
    head_text = show_file(args.head, rel)
    if head_text is None:
        candidate = repo_root() / rel
        head_text = candidate.read_text(encoding="utf-8") if candidate.is_file() else None
    return base_text, head_text


def run(args, config):
    cfg = config["scope"]
    scope_file = cfg.get("declaration_file", ".guardrails/scope.yml")

    provider = None  # (rel path, base text, head text, extractor)
    if args.feature:
        rel = ".specforge/specs/%s/tasks.md" % args.feature
        base_text, head_text = _texts(args, rel)
        if base_text is not None or head_text is not None:
            provider = (rel, base_text, head_text, declared_paths)
    if provider is None:
        base_text, head_text = _texts(args, scope_file)
        if base_text is not None or head_text is not None:
            provider = (scope_file, base_text, head_text, scope_yml_paths)

    if provider is None:
        wanted = ([".specforge/specs/%s/tasks.md" % args.feature] if args.feature else [])
        wanted.append(scope_file)
        return report("scope", "SKIP", [
            "no scope declaration (%s) - the scope guard applies only when one exists"
            % " or ".join(wanted),
        ])

    rel, base_text, head_text, extract = provider
    notes = []
    if base_text is None:
        # Bootstrap: the declaration arrives with this PR, so head is the
        # only one that exists. Report it, since it is unreviewed.
        source_text = head_text
        notes.append("%s is new in this PR - its declaration has not been reviewed yet" % rel)
    else:
        source_text = base_text

    allowed = extract(source_text) + cfg["always_allow"]
    changed = [p for _, p in changed_files(args.base, args.head)]
    violations = [p for p in changed if not matches_any(p, allowed)]

    if base_text is not None and head_text is not None:
        grew = set(extract(head_text)) - set(extract(base_text))
        if grew:
            notes.append(
                "%s grew by %d declared path(s) in this PR: %s"
                % (rel, len(grew), ", ".join(sorted(grew)))
            )
            notes.append("a wider scope is a spec change - it needs approval, not a self-edit")

    if not violations:
        return report(
            "scope",
            "PASS",
            ["%d changed files, all declared in %s" % (len(changed), rel)] + notes,
        )

    trusted, claims = collect_acks(args.base, args.head)
    waivers = waiver_state("scope", trusted, claims)
    findings = [
        {"path": v, "message": "not declared in the scope declaration %s "
                               "(read from %s)" % (rel, args.base)}
        for v in violations
    ]
    lines = (
        ["files not declared in %s (as of %s):" % (rel, args.base)]
        + ["  " + v for v in violations]
        + notes
        + waiver_lines("scope", trusted, claims)
    )
    if "scope" in trusted:
        return report("scope", "WARN", lines, waivers=waivers, findings=findings)
    return report(
        "scope",
        "FAIL",
        lines
        + [
            "either update the scope declaration and get that change approved,",
            "revert the drive-by edits into their own task/PR, or ask a",
            "maintainer to add the `scope` label.",
        ],
        waivers=waivers,
        findings=findings,
    )
