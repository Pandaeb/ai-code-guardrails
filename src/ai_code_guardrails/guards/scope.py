"""Guard: scope discipline.

Every changed file must be declared by the feature's tasks.md — the union
of all "Files to create/modify" lists (entries may be globs), an optional
"Scope exceptions" section, and the config's always-allowed paths.

The declaration is read from the **base ref**, not from the branch under
review. A PR that widens its own "Files to create/modify" list is a spec
change and needs approval; letting the branch supply its own permission
slip would make the rule self-referential.

Undeclared files fail unless a human waiver (the `scope` PR label) is
present. Skips (exit 0) when the feature has no tasks.md at either ref.
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


def run(args, config):
    cfg = config["scope"]

    rel = ".specforge/specs/%s/tasks.md" % args.feature
    base_text = show_file(args.base, rel)
    head_text = show_file(args.head, rel)
    if head_text is None:
        candidate = repo_root() / rel
        head_text = candidate.read_text(encoding="utf-8") if candidate.is_file() else None

    if base_text is None and head_text is None:
        return report("scope", "SKIP", ["no %s - scope guard applies only to spec'd features" % rel])

    notes = []
    if base_text is None:
        # New feature: its tasks.md arrives with this PR, so head is the
        # only declaration that exists. Report it, since it is unreviewed.
        source_text = head_text
        notes.append("tasks.md is new in this PR - its declaration has not been reviewed yet")
    else:
        source_text = base_text

    allowed = declared_paths(source_text) + cfg["always_allow"]
    changed = [p for _, p in changed_files(args.base, args.head)]
    violations = [p for p in changed if not matches_any(p, allowed)]

    if base_text is not None and head_text is not None:
        grew = set(declared_paths(head_text)) - set(declared_paths(base_text))
        if grew:
            notes.append(
                "tasks.md grew by %d declared path(s) in this PR: %s"
                % (len(grew), ", ".join(sorted(grew)))
            )
            notes.append("a wider scope is a spec change - it needs approval, not a self-edit")

    if not violations:
        return report(
            "scope",
            "PASS",
            ["%d changed files, all declared in tasks.md" % len(changed)] + notes,
        )

    trusted, claims = collect_acks(args.base, args.head)
    lines = (
        ["files not declared in tasks.md 'Files to create/modify' (as of %s):" % args.base]
        + ["  " + v for v in violations]
        + notes
        + waiver_lines("scope", trusted, claims)
    )
    if "scope" in trusted:
        return report("scope", "WARN", lines)
    return report(
        "scope",
        "FAIL",
        lines
        + [
            "either update tasks.md and get the spec change approved, revert the",
            "drive-by edits into their own task/PR, or ask a maintainer to add",
            "the `scope` label.",
        ],
    )
