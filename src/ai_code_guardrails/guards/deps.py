"""Guard: dependency policy.

Diffs the project's dependency manifests between base and head. Every
ADDED dependency must be named in one of the configured declaration
files — otherwise FAIL unless a human waiver (the `new-dependency` PR
label) is present.

Optional `--registry-check` verifies each added name exists on its
ecosystem's canonical registry (anti-slopsquatting; needs network, so it's
off by default — unreachable registries WARN, they never fail the build).

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


def run(args, config):
    cfg = config["deps"]

    added = {}  # name -> (ecosystem, manifest path)
    for status, path in changed_files(args.base, args.head):
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

    if not added:
        return report("deps", "PASS", ["no new dependencies"])

    root = repo_root()
    declarations = ""
    for tmpl in cfg["declaration_files"]:
        rel = tmpl.format(feature=args.feature or "*")
        if "*" in rel:
            continue
        text = show_file(args.head, rel)
        if text is None:
            candidate = root / rel
            text = candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
        declarations += "\n" + text.lower()

    undeclared, notes = [], []
    for name, (ecosystem, path) in sorted(added.items()):
        if is_declared(name, declarations):
            notes.append("%s (%s) - declared" % (name, path))
        else:
            undeclared.append("%s (added in %s, not named in %s)"
                              % (name, path, " / ".join(cfg["declaration_files"])))
        if args.registry_check:
            exists = registry_exists(ecosystem, name)
            if exists is False:
                undeclared.append("%s - NOT FOUND on the %s registry (hallucinated name?)"
                                  % (name, ecosystem))
            elif exists is None:
                notes.append("%s - registry not checkable for %s" % (name, ecosystem))

    if not undeclared:
        return report("deps", "PASS", ["%d new dependencies, all declared" % len(added)] + notes)

    trusted, claims = collect_acks(args.base, args.head)
    waiver = waiver_lines("new-dependency", trusted, claims)
    waivers = waiver_state("new-dependency", trusted, claims)
    if "new-dependency" in trusted:
        return report("deps", "WARN", undeclared + waiver, waivers=waivers)
    return report(
        "deps",
        "FAIL",
        undeclared
        + notes
        + waiver
        + [
            "declare the dependency in one of the declaration files",
            "(config key deps.declaration_files) as a backticked or quoted",
            "name, verify the exact name on the registry, or ask a",
            "maintainer to add the `new-dependency` label.",
        ],
        waivers=waivers,
    )
