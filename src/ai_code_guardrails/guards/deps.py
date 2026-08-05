"""Guard: dependency policy.

Diffs the project's dependency manifests between base and head. Every
ADDED dependency must be named in one of the configured declaration
files — otherwise FAIL unless a human waiver (the `new-dependency` PR
label) is present. Git submodules count as dependencies: they pull in
third-party code from a URL this diff never shows.

Three routes bypass the manifest entirely and are checked separately,
failing regardless of declaration policy:

- a **lockfile** that authorises a direct dependency the manifest does
  not name (`npm install --package-lock-only`). Only formats that
  record which dependencies are direct are compared, so an ordinary
  lockfile refresh stays quiet.
- an **install-time script** (`preinstall`, `postinstall`, `prepare`, …)
  added or changed: arbitrary code execution on every install.

A repository that never configured the policy and has none of the
default declaration files gets a WARN with setup instructions, not a
red build — there is no policy to enforce yet. A repository that set
`deps.declaration_files` explicitly has promised a policy, so a
configuration under which nothing could ever be declared (empty list,
missing files) FAILs.

Optional `--registry-check` verifies each added name exists on its
ecosystem's canonical registry (anti-slopsquatting; needs network, so it's
off by default — unreachable registries WARN, they never fail the build).
A name missing from its registry fails regardless of declaration policy:
a hallucinated dependency is not a paperwork problem.

Manifest parsing is best-effort per format; unknown formats are ignored.
"""

import json
import re
import urllib.request

from .._core import (
    changed_files,
    collect_acks,
    report,
    repo_root,
    show_file,
    waiver_lines,
    waiver_state,
)


def parse_package_json(text):
    data = json.loads(text)
    names = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        names |= set((data.get(key) or {}).keys())
    return names


def parse_composer_json(text):
    data = json.loads(text)
    names = set()
    for key in ("require", "require-dev"):
        names |= {n for n in (data.get(key) or {}) if "/" in n}  # skip "php", ext-*
    return names


def parse_requirements_txt(text):
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = re.match(r"[A-Za-z0-9._-]+", line)
        if m:
            names.add(m.group(0))
    return names


def parse_pyproject_toml(text):
    """PEP 621 + poetry. Uses tomllib where available (Python 3.11+).

    The line-scanning fallback below cannot tell `name = "demo"` in
    [project] from a real dependency, so it reports project metadata as
    dependencies. Parse properly when we can.
    """
    try:
        import tomllib
    except ImportError:
        return _parse_pyproject_lines(text)

    try:
        data = tomllib.loads(text)
    except Exception:
        return _parse_pyproject_lines(text)

    names = set()

    def add_specs(specs):
        for spec in specs or []:
            m = re.match(r"[A-Za-z0-9._-]+", str(spec))
            if m:
                names.add(m.group(0))

    project = data.get("project") or {}
    add_specs(project.get("dependencies"))
    for group in (project.get("optional-dependencies") or {}).values():
        add_specs(group)

    poetry = ((data.get("tool") or {}).get("poetry")) or {}
    for key in ("dependencies", "dev-dependencies"):
        names |= {n for n in (poetry.get(key) or {}) if n.lower() != "python"}
    for group in (poetry.get("group") or {}).values():
        names |= {n for n in ((group or {}).get("dependencies") or {}) if n.lower() != "python"}

    dependency_groups = data.get("dependency-groups") or {}
    for group in dependency_groups.values():
        add_specs([g for g in (group or []) if isinstance(g, str)])

    return names


def _parse_pyproject_lines(text):
    names = set()
    in_deps = False
    for line in text.splitlines():
        if re.match(r"\s*(dependencies\s*=|\[tool\.poetry\.(dev-)?dependencies\])", line):
            in_deps = True
        elif re.match(r"\s*\[", line):
            in_deps = False
        if not in_deps:
            continue
        for m in re.finditer(r'"([A-Za-z0-9._-]+)[^"]*"', line):
            names.add(m.group(1))
        m = re.match(r"\s*([A-Za-z0-9._-]+)\s*=", line)
        if m and m.group(1) not in {"python", "dependencies", "version"}:
            names.add(m.group(1))
    return names


def parse_csproj(text):
    return set(re.findall(r'Package(?:Reference|Version)\s+Include="([^"]+)"', text))


def parse_pubspec_yaml(text):
    names = set()
    in_deps = False
    for line in text.splitlines():
        if re.match(r"^(dev_)?dependencies(_overrides)?:\s*$", line):
            in_deps = True
            continue
        if re.match(r"^\S", line):
            in_deps = False
        if in_deps:
            m = re.match(r"^  ([A-Za-z0-9_]+)\s*:", line)
            if m and m.group(1) not in {"sdk", "flutter"}:
                names.add(m.group(1))
    return names


def parse_go_mod(text):
    names = set()
    for m in re.finditer(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+v[\w.+-]+", text, re.M):
        names.add(m.group(1))
    return names


def parse_cargo_toml(text):
    names = set()
    in_deps = False
    for line in text.splitlines():
        if re.match(r"\s*\[(dev-|build-)?dependencies", line):
            in_deps = True
            continue
        if re.match(r"\s*\[", line):
            in_deps = False
        if in_deps:
            m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", line)
            if m:
                names.add(m.group(1))
    return names


def parse_gemfile(text):
    return set(re.findall(r"""^\s*gem\s+['"]([^'"]+)['"]""", text, re.M))


def parse_gitmodules(text):
    """{submodule name: url} from a .gitmodules file.

    A submodule is a dependency: it pulls third-party code into the
    build from a URL nobody reviewed in this diff.
    """
    mods, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^\[submodule\s+"([^"]+)"\]', line)
        if m:
            current = m.group(1)
            mods.setdefault(current, "")
            continue
        m = re.match(r"^url\s*=\s*(.+)$", line)
        if m and current:
            mods[current] = m.group(1).strip()
    return mods


def lockfile_direct_deps(path, text):
    """Direct (top-level) dependency names a lockfile declares, or None.

    Only formats that actually record which dependencies are *direct*
    are parsed: npm's package-lock v2/v3 (`packages[""]`) and pnpm
    (`importers`). yarn.lock, poetry.lock, Cargo.lock and friends
    flatten the graph, so a name there may be transitive and comparing
    it against the manifest would fire on every legitimate refresh.
    None means "this format carries no direct-dependency claim".
    """
    basename = path.rsplit("/", 1)[-1]
    if basename == "package-lock.json":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        root = (data.get("packages") or {}).get("")
        if root is None:  # lockfileVersion 1 has no direct/transitive split
            return None
        names = set()
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            names |= set((root.get(key) or {}).keys())
        return names
    if basename == "pnpm-lock.yaml":
        names, in_root_deps = set(), False
        for raw in text.splitlines():
            if re.match(r"^\s{4}(dependencies|devDependencies):\s*$", raw):
                in_root_deps = True
                continue
            if re.match(r"^\s{0,4}\S", raw) and not raw.startswith("      "):
                in_root_deps = False
            if in_root_deps:
                m = re.match(r"^\s{6}'?([@\w][\w./@-]*)'?\s*:", raw)
                if m:
                    names.add(m.group(1))
        return names or None
    return None


# lockfile -> the manifest that is supposed to authorise its contents.
LOCKFILE_MANIFESTS = {
    "package-lock.json": "package.json",
    "pnpm-lock.yaml": "package.json",
}

# npm lifecycle scripts that run on `npm install` — adding one is adding
# arbitrary code execution to every developer's and CI's install step.
INSTALL_SCRIPT_KEYS = ("preinstall", "install", "postinstall", "prepare", "prepublish")

PARSERS = {
    "package.json": ("npm", parse_package_json),
    "composer.json": ("packagist", parse_composer_json),
    "pyproject.toml": ("pypi", parse_pyproject_toml),
    "pubspec.yaml": ("pub", parse_pubspec_yaml),
    "go.mod": ("go", parse_go_mod),
    "Cargo.toml": ("crates", parse_cargo_toml),
    "Gemfile": ("rubygems", parse_gemfile),
}

REGISTRY_URLS = {
    "npm": "https://registry.npmjs.org/{name}",
    "pypi": "https://pypi.org/pypi/{name}/json",
    "nuget": "https://api.nuget.org/v3-flatcontainer/{lower}/index.json",
    "crates": "https://crates.io/api/v1/crates/{name}",
    "pub": "https://pub.dev/api/packages/{name}",
    "rubygems": "https://rubygems.org/api/v1/gems/{name}.json",
    "packagist": "https://repo.packagist.org/p2/{name}.json",
}


def manifest_parser(path):
    basename = path.rsplit("/", 1)[-1]
    if basename in PARSERS:
        return PARSERS[basename]
    if basename.endswith(".csproj") or basename == "Directory.Packages.props":
        return ("nuget", parse_csproj)
    if re.match(r"requirements[^/]*\.txt$", basename):
        return ("pypi", parse_requirements_txt)
    return None


def is_declared(name, declarations):
    """True when `name` is named as a dependency, not merely present as text.

    Substring matching used to accept any occurrence, so real packages
    with short English names (`is`, `os`, `api`) counted as declared
    because prose in the declaration file happened to contain the word.
    A declaration must be machine-recognisable: backticked, quoted, a
    list/table entry, or written with a version.
    """
    n = re.escape(name.lower())
    patterns = [
        r"`%s`" % n,
        r'"%s"' % n,
        r"'%s'" % n,
        r"^\s*[-*+]\s+%s(?![\w.-])" % n,
        r"^\s*\|\s*%s\s*\|" % n,
        r"(?<![\w.-])%s\s*[@=~^]" % n,
    ]
    return any(re.search(p, declarations, re.M) for p in patterns)


def registry_exists(ecosystem, name):
    """True/False, or None when the registry can't be checked."""
    url_tmpl = REGISTRY_URLS.get(ecosystem)
    if not url_tmpl:
        return None
    url = url_tmpl.format(name=name, lower=name.lower())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-code-guardrails"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def supply_chain_findings(args, changed):
    """Dependency additions that never touch a manifest's dependency list.

    Three routes, all of which used to walk past this guard:
    a lockfile that authorises a direct dependency the manifest doesn't
    name, a new git submodule, and an install-time lifecycle script.
    Returns (lines, findings) — both empty when the PR is clean.
    """
    lines, findings = [], []
    changed_paths = {path for status, path in changed if status != "D"}

    for path in sorted(changed_paths):
        basename = path.rsplit("/", 1)[-1]

        manifest_name = LOCKFILE_MANIFESTS.get(basename)
        if manifest_name:
            head_direct = lockfile_direct_deps(path, show_file(args.head, path) or "")
            base_direct = lockfile_direct_deps(path, show_file(args.base, path) or "") or set()
            if head_direct is not None:
                manifest_path = path.rsplit("/", 1)[0] + "/" + manifest_name if "/" in path else manifest_name
                try:
                    manifest_names = parse_package_json(show_file(args.head, manifest_path) or "{}")
                except ValueError:
                    manifest_names = set()
                for name in sorted((head_direct - base_direct) - manifest_names):
                    lines.append(
                        "%s adds direct dependency `%s`, which %s does not declare"
                        % (path, name, manifest_path)
                    )
                    findings.append({"path": path, "message": "lockfile adds direct dependency "
                                                              "`%s` absent from %s" % (name, manifest_path)})

        if basename == "package.json":
            try:
                head_scripts = (json.loads(show_file(args.head, path) or "{}").get("scripts") or {})
                base_scripts = (json.loads(show_file(args.base, path) or "{}").get("scripts") or {})
            except ValueError:
                head_scripts, base_scripts = {}, {}
            for key in INSTALL_SCRIPT_KEYS:
                if key in head_scripts and head_scripts[key] != base_scripts.get(key):
                    lines.append(
                        "%s adds/changes the `%s` script - it runs on every install: %s"
                        % (path, key, head_scripts[key])
                    )
                    findings.append({"path": path, "message": "install-time script `%s` added or "
                                                              "changed: %s" % (key, head_scripts[key])})

    return lines, findings


def submodule_additions(args, changed):
    """{name: url} for submodules this PR introduces (not SHA bumps)."""
    if not any(path == ".gitmodules" and status != "D" for status, path in changed):
        return {}
    head = parse_gitmodules(show_file(args.head, ".gitmodules") or "")
    base = parse_gitmodules(show_file(args.base, ".gitmodules") or "")
    return {name: url for name, url in head.items() if name not in base}


def run(args, config):
    cfg = config["deps"]

    changed = changed_files(args.base, args.head)
    added = {}  # name -> (ecosystem, manifest path)
    for status, path in changed:
        found = manifest_parser(path)
        if not found or status == "D":
            continue
        ecosystem, parse = found
        try:
            base_names = parse(show_file(args.base, path) or "") if status != "A" else set()
            head_names = parse(show_file(args.head, path) or "")
        except Exception as e:
            print("    note: could not parse %s (%s) - skipping" % (path, e))
            continue
        for name in head_names - base_names:
            added[name] = (ecosystem, path)

    for name, url in submodule_additions(args, changed).items():
        added[name] = ("submodule", ".gitmodules%s" % (" -> " + url if url else ""))

    supply_lines, supply_findings = supply_chain_findings(args, changed)

    if not added and not supply_lines:
        return report("deps", "PASS", ["no new dependencies"])

    root = repo_root()
    declarations = ""
    found_files = []
    for tmpl in cfg["declaration_files"]:
        rel = tmpl.format(feature=args.feature or "*")
        if "*" in rel:
            continue
        text = show_file(args.head, rel)
        if text is None:
            candidate = root / rel
            text = candidate.read_text(encoding="utf-8") if candidate.is_file() else None
        if text is None:
            continue
        found_files.append(rel)
        declarations += "\n" + text.lower()

    explicit = "deps.declaration_files" in config.get("_user_keys", ())
    enforce = bool(found_files) or explicit

    undeclared, registry_failures, notes, findings = [], [], [], []
    for name, (ecosystem, path) in sorted(added.items()):
        if is_declared(name, declarations):
            notes.append("%s (%s) - declared" % (name, path))
        else:
            undeclared.append("%s (added in %s, not named in %s)"
                              % (name, path, " / ".join(cfg["declaration_files"]) or "<nothing>"))
            findings.append({"path": path, "message": "dependency `%s` added but not named "
                                                      "in a declaration file" % name})
        if args.registry_check:
            exists = registry_exists(ecosystem, name)
            if exists is False:
                registry_failures.append("%s - NOT FOUND on the %s registry (hallucinated name?)"
                                         % (name, ecosystem))
                findings.append({"path": path, "message": "dependency `%s` NOT FOUND on the "
                                                          "%s registry (hallucinated name?)"
                                                          % (name, ecosystem)})
            elif exists is None:
                notes.append("%s - registry not checkable for %s" % (name, ecosystem))

    findings += supply_findings

    if not undeclared and not registry_failures and not supply_lines:
        return report("deps", "PASS", ["%d new dependencies, all declared" % len(added)] + notes)

    # Hallucinated names and manifest-bypassing additions fail in any
    # mode: neither is a missing-paperwork problem that configuring a
    # declaration file would fix.
    failures = list(registry_failures) + list(supply_lines)
    if enforce:
        failures += undeclared
        if explicit and not found_files:
            failures.append(
                "deps.declaration_files is configured, but none of the listed "
                "files exist - nothing could ever be declared under this policy."
            )

    if not failures:
        # Unconfigured repository: guidance instead of a red build.
        return report("deps", "WARN", undeclared + [
            "no dependency declaration file found, so the policy cannot be",
            "enforced yet. Name new dependencies (backticked or quoted) in",
            "DEPENDENCIES.md or docs/dependencies.md, or point the config",
            "key deps.declaration_files somewhere else - undeclared",
            "additions then fail instead of warning.",
        ], findings=findings)

    trusted, claims = collect_acks(args.base, args.head)
    waiver = waiver_lines("new-dependency", trusted, claims)
    waivers = waiver_state("new-dependency", trusted, claims)
    if "new-dependency" in trusted:
        return report("deps", "WARN", failures + waiver, waivers=waivers, findings=findings)
    return report(
        "deps",
        "FAIL",
        failures
        + notes
        + waiver
        + [
            "declare the dependency in one of the declaration files",
            "(config key deps.declaration_files) as a backticked or quoted",
            "name, verify the exact name on the registry, or ask a",
            "maintainer to add the `new-dependency` label.",
        ],
        waivers=waivers,
        findings=findings,
    )
