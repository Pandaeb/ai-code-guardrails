"""Schema tests for `check --format json`.

The JSON output is a public contract (schema_version pins it): package
version, one entry per guard with status/detail/waivers, a summary, and
the process exit code. The default human format must stay byte-for-byte
what it was — the corpus harness and CI log readers parse it.
"""

import contextlib
import io
import json

from ai_code_guardrails import __version__, cli
from ai_code_guardrails.guards import GUARD_ORDER
from redteam.harness import build_repo, case_environment

GUARD_NAMES = [g.replace("_", "-") for g in GUARD_ORDER]

QUIET_CASE = {
    "base": {"README.md": "hello\n", "src/app.py": "def f():\n    return 1\n"},
    "head": {"src/app.py": "def f():\n    return 2\n"},
}

DELETED_TEST = {"tests/test_app.py": "def test_f():\n    assert f()\n    assert True\n"}

WAIVED_CASE = {
    "base": dict(QUIET_CASE["base"], **DELETED_TEST),
    "head": {"tests/test_app.py": None},
    "env": {"GUARDRAILS_ACKS": "test-removal"},
}

SELF_WAIVER_CASE = {
    "base": dict(QUIET_CASE["base"], **DELETED_TEST),
    "head": {"tests/test_app.py": None},
    "env": {"GUARDRAILS_PR_BODY": "Cleanup.\n\nguard-ack: test-removal — obsolete"},
}


def run_cli(case, tmp_path, extra_args=()):
    """Run the real CLI in-process inside a throwaway repo."""
    repo, base_sha = build_repo(case, tmp_path)
    argv = ["check", "--base", base_sha, "--feature", "demo"] + list(extra_args)
    buf = io.StringIO()
    with case_environment(repo, case):
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
    return code, buf.getvalue()


def run_json(case, tmp_path, extra_args=()):
    code, out = run_cli(case, tmp_path, ["--format", "json"] + list(extra_args))
    return code, json.loads(out)  # the WHOLE stdout must be one JSON document


def entry(doc, guard):
    return next(e for e in doc["guards"] if e["guard"] == guard)


def test_toplevel_schema(tmp_path):
    code, doc = run_json(QUIET_CASE, tmp_path)
    assert set(doc) == {"schema_version", "package", "check", "guards", "summary", "exit_code"}
    assert doc["schema_version"] == cli.SCHEMA_VERSION == 1
    assert doc["package"] == {"name": "ai-code-guardrails", "version": __version__}
    assert doc["check"]["head"] == "HEAD"
    assert doc["check"]["feature"] == "demo"
    assert doc["exit_code"] == code == 0


def test_one_entry_per_guard_in_order(tmp_path):
    _, doc = run_json(QUIET_CASE, tmp_path)
    assert [e["guard"] for e in doc["guards"]] == GUARD_NAMES
    for e in doc["guards"]:
        assert set(e) == {"guard", "status", "detail", "waivers"}
        assert e["status"] in {"PASS", "WARN", "FAIL", "SKIP"}
        assert all(isinstance(line, str) for line in e["detail"])
        assert isinstance(e["waivers"], list)


def test_summary_counts_match_entries(tmp_path):
    _, doc = run_json(QUIET_CASE, tmp_path)
    assert set(doc["summary"]) == {"pass", "warn", "fail", "skip"}
    for status, count in doc["summary"].items():
        assert count == sum(1 for e in doc["guards"] if e["status"] == status.upper())


def test_skipped_guard_reports_under_its_printed_name(tmp_path):
    _, doc = run_json(QUIET_CASE, tmp_path, ["--skip", "test_integrity"])
    e = entry(doc, "test-integrity")
    assert e["status"] == "SKIP"
    assert e["detail"] == ["skipped via --skip"]


def test_failure_sets_exit_code_and_reports_self_waiver(tmp_path):
    code, doc = run_json(SELF_WAIVER_CASE, tmp_path)
    e = entry(doc, "test-integrity")
    assert e["status"] == "FAIL"
    assert {"token": "test-removal", "state": "self-waiver-attempted",
            "source": "PR body"} in e["waivers"]
    assert doc["exit_code"] == code == 1
    assert doc["summary"]["fail"] >= 1


def test_label_waiver_is_structured(tmp_path):
    code, doc = run_json(WAIVED_CASE, tmp_path)
    e = entry(doc, "test-integrity")
    assert e["status"] == "WARN"
    assert e["waivers"] == [{"token": "test-removal", "state": "waived-by-label",
                             "source": "PR label"}]
    assert doc["exit_code"] == code == 0


def test_human_format_stays_default_and_unchanged(tmp_path):
    code, out = run_cli(QUIET_CASE, tmp_path)
    assert code == 0
    assert "[PASS] selfmod" in out
    assert "=== guardrails summary ===" in out
    try:
        json.loads(out)
        raise AssertionError("human output must not be JSON")
    except ValueError:
        pass
