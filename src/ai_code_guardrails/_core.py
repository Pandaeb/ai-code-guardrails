"""Shared helpers for the ai-code-guardrails checks.

Stdlib-only, Python 3.9+. Every guard reports [PASS|WARN|FAIL|SKIP] and
contributes 0 (pass, possibly with warnings) or 1 (fail) to the process
exit code. The CLI in `cli.py` runs all guards in one process.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_CONFIG = {
    "diff_size": {
        "soft_limit": 400,
        "hard_limit": 800,
        "exclude": [
            ".specforge/**",
            "**/package-lock.json",
            "**/yarn.lock",
            "**/pnpm-lock.yaml",
            "**/Cargo.lock",
            "**/poetry.lock",
            "**/uv.lock",
            "**/Gemfile.lock",
            "**/composer.lock",
            "**/packages.lock.json",
            "**/pubspec.lock",
            "**/go.sum",
            "**/*.generated.*",
            "**/*.g.dart",
            "**/*.g.cs",
            "**/*.Designer.cs",
            "**/*_pb2.py",
            "**/*.pb.go",
            "**/__snapshots__/**",
            "**/*.snap",
            "**/vendor/**",
            "**/node_modules/**",
        ],
        # Exclusions nobody hand-writes: never audited, never counted.
        # `.specforge/` is the spec/steering tree of the framework this
        # package was extracted from — kept for first-client compatibility.
        "structural_exclude": [
            ".specforge/**",
            "**/package-lock.json",
            "**/yarn.lock",
            "**/pnpm-lock.yaml",
            "**/Cargo.lock",
            "**/poetry.lock",
            "**/uv.lock",
            "**/Gemfile.lock",
            "**/composer.lock",
            "**/packages.lock.json",
            "**/pubspec.lock",
            "**/go.sum",
            "**/node_modules/**",
        ],
        # Everything else in `exclude` is a CLAIMED exemption — a path
        # the author asserts is generated or vendored. Agents choose file
        # names, so the claim is audited: claimed-exempt lines above this
        # many fail the guard (waivable by a human, like any oversize).
        "exempt_audit_limit": 800,
    },
    "scope": {
        # Spec-less scope declaration: a repo-level allow-list, read from
        # the BASE ref like every other declaration. Used when no
        # SpecForge tasks.md exists for the feature.
        "declaration_file": ".guardrails/scope.yml",
        # Paths every PR may touch regardless of the task's declared
        # files. `.guardrails/**` must be editable for the scope file to
        # be bootstrappable at all — editing it never widens the CURRENT
        # PR's permissions, because the declaration is read from base.
        "always_allow": [
            ".specforge/**",
            ".guardrails/**",
            ".env.example",
        ],
    },
    "test_integrity": {
        "test_globs": [
            "**/test/**",
            "**/tests/**",
            "**/__tests__/**",
            "**/*.test.*",
            "**/*.spec.*",
            "**/*_test.go",
            "**/*_test.py",
            "**/test_*.py",
            "**/*Tests.cs",
            "**/*Test.java",
            "**/*_test.dart",
        ],
        "skip_patterns": [
            r"\.skip\s*\(",
            r"\.only\s*\(",
            r"\.todo\s*\(",
            r"\bxit\s*\(",
            r"\bxdescribe\s*\(",
            r"\bfit\s*\(",
            r"\bfdescribe\s*\(",
            r"@pytest\.mark\.skip",
            r"@unittest\.skip",
            r"@Disabled",
            r"@Ignore\b",
            r"\[Ignore",
            r"\[Skip",
            r"t\.Skip\s*\(",
            r"#\[ignore\]",
            r"markTestSkipped",
        ],
        "assert_patterns": [
            r"\bassert\b",
            r"\bexpect\s*\(",
            r"\bAssert\.",
            r"\.Should\s*\(",
            r"\bshould\.",
            r"\bverify\s*\(",
            r"\brequire\.",
            r"XCTAssert",
            r"t\.(Error|Errorf|Fatal|Fatalf)\s*\(",
        ],
        # Net assertion loss at or above this count fails (below it warns).
        "assert_loss_fail_threshold": 3,
    },
    "suppression": {
        # Silencing the analyser instead of fixing the code. Inline
        # markers are counted per PR: below the threshold they warn
        # (one justified ignore is normal engineering), at or above it
        # they fail. Turning a rule off in a linter's CONFIG is a
        # different act — it silences the whole repository, so it fails
        # on the first occurrence.
        "added_fail_threshold": 3,
        "markers": [
            r"#\s*type:\s*ignore",
            r"#\s*mypy:\s*ignore-errors",
            r"#\s*noqa\b",
            r"#\s*pylint:\s*disable",
            r"@ts-ignore",
            r"@ts-nocheck",
            r"eslint-disable",
            r"@SuppressWarnings",
            r"//\s*nolint",
            r"#\s*pragma\s+warning\s+disable",
            r"#\[allow\(",
        ],
        "config_globs": [
            "**/.eslintrc*",
            "**/eslint.config.*",
            "**/tsconfig*.json",
            "**/.flake8",
            "**/setup.cfg",
            "**/pyproject.toml",
            "**/.golangci.y*ml",
            "**/.rubocop.yml",
        ],
        # Added lines in those files that switch analysis OFF.
        "config_disable_patterns": [
            r'"[\w./@-]+"\s*:\s*(?:"off"|0\b)',
            r"^\s*[\w./@-]+:\s*(?:off|false)\s*$",
            r'"(?:strict|noImplicitAny|strictNullChecks|noUnusedLocals)"\s*:\s*false',
            r"^\s*strict\s*=\s*false",
            r"^\s*(?:extend-)?ignore\s*=",
            r"^\s*ignore_errors\s*=\s*[Tt]rue",
            r"^\s*disable\s*[:=]",
        ],
        # Paths whose contents are not code an analyser ever reads.
        # Prose ABOUT suppressions ("never add `# type: ignore`") is not
        # a suppression, and a guard that fires on a style guide gets
        # uninstalled. Extend this for data files that carry markers as
        # payloads — e.g. a red-team corpus.
        "exclude": [
            "**/*.md",
            "**/*.rst",
            "**/*.txt",
            "**/*.adoc",
        ],
    },
    "deps": {
        # Where new dependencies must be declared (searched at HEAD).
        # {feature} is substituted when a feature name is known. The
        # `.specforge/` entries are the legacy SpecForge layout; the
        # last two are the spec-less defaults. When NONE of these exist
        # the guard warns with instructions instead of failing — but a
        # repo that configured this key explicitly has promised a
        # policy, and an unusable one fails (see guards/deps.py).
        "declaration_files": [
            ".specforge/specs/{feature}/architecture.md",
            ".specforge/steering/tech.md",
            "DEPENDENCIES.md",
            "docs/dependencies.md",
        ],
    },
    "review": {
        # Tier-1 provider separation — consumed by workflows and review
        # tooling around the guards, not by the guards themselves.
        # Levels: off | preferred | required-for-automerge | required.
        "provider_separation": "required-for-automerge",
        "reviewer_provider": "",
        "reviewer_command": "",
    },
}

ACK_LINE_RE = re.compile(
    r"guard-ack:\s*(?P<token>[a-z][a-z0-9-]*)\s*(?:[—–-]+\s*(?P<reason>.+))?",
    re.IGNORECASE,
)

WAIVER_TOKENS = {
    "oversize",
    "scope",
    "test-removal",
    "test-weakening",
    "new-dependency",
    "guardrail-change",
    "suppression",
}

# Files that define the rules. A PR may not quietly rewrite them; see
# guards/selfmod.py. The guard code itself ships as a pinned package —
# outside the repository under review — so only the in-repo config and
# floor documents need protecting.
PROTECTED_PATHS = [
    ".guardrails.json",
    ".specforge/guardrails/**",
    ".specforge/AI-GUARDRAILS.md",
]

# Config is read from the base ref at the first of these paths that
# exists. `.guardrails.json` is this package's home; the `.specforge/`
# path keeps existing adopters of the pre-package scripts working.
CONFIG_REL_PATHS = [
    ".guardrails.json",
    ".specforge/guardrails/guardrails.json",
]


def repo_root():
    out = run_git(["rev-parse", "--show-toplevel"])
    return Path(out.strip())


def run_git(args):
    proc = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def load_config(path=None, base=None):
    """Merge the project's config file over the defaults, per section.

    When `base` is given, the config is read from that ref rather than
    from the working tree. A PR must not be judged by thresholds it
    edited itself, so the CLI passes its --base here; the working tree
    is used only when the file doesn't exist at base yet (new project)
    or when --config points somewhere explicitly.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    text = None
    if path:
        candidate = Path(path)
        text = candidate.read_text(encoding="utf-8") if candidate.is_file() else None
    else:
        if base:
            for rel in CONFIG_REL_PATHS:
                text = show_file(base, rel)
                if text is not None:
                    break
        if text is None:
            root = repo_root()
            for rel in CONFIG_REL_PATHS:
                candidate = root / rel
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8")
                    break
    user_keys = []
    if text:
        user = json.loads(text)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
                user_keys += ["%s.%s" % (section, key) for key in values]
            else:
                cfg[section] = values
                user_keys.append(section)
    # Which keys the project set explicitly (vs. built-in defaults) —
    # "you configured a policy" and "you never thought about it" deserve
    # different failure modes (see guards/deps.py).
    cfg["_user_keys"] = user_keys
    return cfg


def changed_files(base, head):
    """[(status, path)] for base...head. Renames report the NEW path."""
    out = run_git(["diff", "--name-status", "-M", "%s...%s" % (base, head)])
    entries = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]  # R100 -> R
        path = parts[2] if status == "R" and len(parts) > 2 else parts[1]
        entries.append((status, path))
    return entries


def numstat(base, head):
    """[(added, deleted, path)]; binary files yield (None, None, path)."""
    out = run_git(["diff", "--numstat", "%s...%s" % (base, head)])
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if "=>" in path:  # rename syntax a/{old => new}/b
            path = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", path).replace("//", "/")
        if added == "-" or deleted == "-":
            rows.append((None, None, path))
        else:
            rows.append((int(added), int(deleted), path))
    return rows


def diff_by_path(base, head):
    """{path: patch} for the whole diff, in ONE git invocation.

    Calling `git diff -- <path>` per file costs a process per file, which
    dominated the test-integrity guard's runtime on large PRs (~2.6s at
    500 changed files versus ~0.2s here). Renames and deletions key on
    the new path, matching changed_files().
    """
    out = run_git(["diff", "%s...%s" % (base, head)])
    result = {}
    buf, a_path, b_path = [], None, None
    for line in out.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if buf and (b_path or a_path):
                result[b_path or a_path] = "".join(buf)
            buf, a_path, b_path = [], None, None
        elif line.startswith("--- a/"):
            a_path = line[6:].rstrip("\r\n")
        elif line.startswith("+++ b/"):
            b_path = line[6:].rstrip("\r\n")
        buf.append(line)
    if buf and (b_path or a_path):
        result[b_path or a_path] = "".join(buf)
    return result


def show_file(ref, path):
    """File contents at ref, or None if it doesn't exist there."""
    proc = subprocess.run(
        ["git", "show", "%s:%s" % (ref, path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout if proc.returncode == 0 else None


_GLOB_CACHE = {}


def glob_to_regex(pattern):
    """Translate a glob with **, *, ? into a full-path regex."""
    cached = _GLOB_CACHE.get(pattern)
    if cached is not None:
        return cached
    original = pattern
    pattern = pattern.replace("\\", "/").strip()
    if pattern.endswith("/"):
        pattern += "**"
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    compiled = re.compile("^(?:%s)$" % "".join(out))
    _GLOB_CACHE[original] = compiled
    return compiled


def matches_any(path, patterns):
    """Full-path glob match.

    A pattern without a slash matches only at the repository root. It
    used to match at any depth, which let a task declaring `config.json`
    authorise edits to `src/anything/config.json`; use `**/name` when a
    pattern really is meant to match anywhere.
    """
    path = path.replace("\\", "/")
    return any(glob_to_regex(p).match(path) for p in patterns)


def collect_acks(base, head):
    """Return (trusted, claimed) waiver tokens.

    trusted — GUARDRAILS_ACKS only. Wire it to PR labels in CI: adding a
    label needs triage permission on the repository, which is the only
    thing that makes waivers structurally human-only. Nothing an agent
    can write reaches this dict.

    claimed — tokens found in agent-writable surfaces: the PR body (the
    agent runs `gh pr create --body`) and commit messages. These NEVER
    waive anything. They are returned so the guard can report the
    attempt, which independent review should treat as a CRITICAL finding.
    """
    trusted = {}
    for raw in os.environ.get("GUARDRAILS_ACKS", "").split(","):
        token = raw.strip().lower()
        if token.startswith("guard-ack:"):
            token = token[len("guard-ack:"):].strip()
        if token in WAIVER_TOKENS:
            trusted[token] = "PR label"

    claimed = {}
    sources = [("PR body", os.environ.get("GUARDRAILS_PR_BODY", ""))]
    sources.append(("commit message", run_git(["log", "--format=%B", "%s..%s" % (base, head)])))
    for origin, text in sources:
        for m in ACK_LINE_RE.finditer(text):
            token = m.group("token").lower()
            if token in WAIVER_TOKENS and token not in trusted:
                claimed[token] = origin
    return trusted, claimed


def waiver_lines(token, trusted, claimed):
    """Report lines describing the waiver state for one token."""
    if token in trusted:
        return ["waived by a human: `%s` (%s)" % (token, trusted[token])]
    if token in claimed:
        return [
            "IGNORED a `guard-ack: %s` found in the %s — waivers are human-only."
            % (token, claimed[token]),
            "An agent-authored waiver is a CRITICAL review finding. A real",
            "waiver is a PR label added by a maintainer.",
        ]
    return []


def waiver_state(token, trusted, claimed):
    """Structured waiver records for machine formats; [] when moot.

    States: "waived-by-label" — a human applied the waiver label;
    "self-waiver-attempted" — the token appeared on an agent-writable
    surface and was IGNORED (the verdict is unchanged; machine consumers
    should surface it as a finding, like the human report does).
    """
    if token in trusted:
        return [{"token": token, "state": "waived-by-label", "source": trusted[token]}]
    if token in claimed:
        return [{"token": token, "state": "self-waiver-attempted", "source": claimed[token]}]
    return []


# When set (see cli.py), report() also appends structured entries here.
# The printed human report stays the contract for log readers and the
# corpus harness; machine formats are built from this collector.
_COLLECTOR = None


def start_collecting():
    global _COLLECTOR
    _COLLECTOR = []


def stop_collecting():
    global _COLLECTOR
    collected, _COLLECTOR = _COLLECTOR, None
    return collected or []


def report(name, status, lines=None, waivers=None, findings=None):
    """status: PASS | WARN | FAIL | SKIP. Returns the exit code.

    `findings` are optional file-level records ({"path", "message"}) for
    machine formats that can point at files (SARIF); the human report
    and the JSON schema carry the same facts inside `lines`.
    """
    if _COLLECTOR is not None:
        _COLLECTOR.append({
            "guard": name,
            "status": status,
            "detail": list(lines or []),
            "waivers": list(waivers or []),
            "findings": list(findings or []),
        })
    print("[%s] %s" % (status, name))
    for line in lines or []:
        print("    " + line)
    return 1 if status == "FAIL" else 0
