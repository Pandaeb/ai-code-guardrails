"""Red-team corpus — harness.

Builds a throwaway git repository per case, replays a known AI failure
mode (or a legitimate change, for the controls), runs one guard against
it **in-process**, and returns the guard's verdict for comparison with
the expected one.

The verdict is parsed from the guard's printed report — the report
format is the public contract, so the corpus consumes the guards exactly
the way a CI log reader would.
"""

import contextlib
import io
import os
import subprocess
import time
from argparse import Namespace
from pathlib import Path

from ai_code_guardrails._core import load_config
from ai_code_guardrails.guards import RUNNERS

STATUSES = ("PASS", "WARN", "FAIL", "SKIP")

# What install-time trust looks like for a legacy-layout adopter: an
# honest config committed on the base ref. Cases that attack the config
# then MODIFY this file rather than introducing it.
HONEST_CONFIG = """{
  "diff_size": {"soft_limit": 400, "hard_limit": 800},
  "review": {"provider_separation": "required-for-automerge"}
}
"""
CONFIG_REL = ".specforge/guardrails/guardrails.json"


def git(repo, args, check=True):
    proc = subprocess.run(
        ["git"] + args,
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (" ".join(args), repo, proc.stderr))
    return proc.stdout


def write_tree(root, files):
    """Apply a {path: content} map. A None value deletes the file."""
    for rel, content in files.items():
        target = root / rel
        if content is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Path.write_text() grew `newline` only in 3.10; open() has it everywhere.
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)


def build_repo(case, workdir):
    repo = Path(workdir) / "repo"
    repo.mkdir()
    git(repo, ["init", "-q", "-b", "main"])
    git(repo, ["config", "user.email", "redteam@corpus.local"])
    git(repo, ["config", "user.name", "Red Team"])
    git(repo, ["config", "commit.gpgsign", "false"])

    write_tree(repo, case["base"])
    if CONFIG_REL not in case["base"]:
        write_tree(repo, {CONFIG_REL: HONEST_CONFIG})
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "chore: baseline"])
    base_sha = git(repo, ["rev-parse", "HEAD"]).strip()

    write_tree(repo, case["head"])
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "--allow-empty", "-m", case.get("commit_message", "feat: change")])
    return repo, base_sha


def parse_status(output):
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            token = line[1:line.index("]")]
            if token in STATUSES:
                return token
    return "ERROR"


@contextlib.contextmanager
def case_environment(repo, case):
    """cwd inside the throwaway repo; only the case's GUARDRAILS_* vars set."""
    saved_cwd = os.getcwd()
    saved_env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("GUARDRAILS_")}
    os.environ.pop("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", None)
    os.chdir(str(repo))
    os.environ.update(case.get("env", {}))
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        for k in list(os.environ):
            if k.startswith("GUARDRAILS_"):
                del os.environ[k]
        os.environ.update(saved_env)


def run_guard(repo, base_sha, case):
    """Run one guard in-process; return (status, output, seconds)."""
    args = Namespace(
        base=base_sha,
        head="HEAD",
        feature=case.get("feature"),
        config=None,
        registry_check=False,
        skip="",
    )
    buf = io.StringIO()
    started = time.perf_counter()
    with case_environment(repo, case):
        config = load_config(base=base_sha)
        with contextlib.redirect_stdout(buf):
            RUNNERS[case["guard"]](args, config)
    elapsed = time.perf_counter() - started
    output = buf.getvalue()
    return parse_status(output), output.strip(), elapsed
