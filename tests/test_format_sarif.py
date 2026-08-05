"""Structure tests for `check --format sarif` (SARIF 2.1.0).

Every guard is a rule; FAIL results are level "error", WARN "warning";
PASS/SKIP produce no results. Where a guard knows the offending file,
the result carries a physicalLocation, which is what turns a red check
into a PR annotation on GitHub.
"""

import json

from ai_code_guardrails import __version__
from ai_code_guardrails.sarif import RULES
from test_format_json import (
    QUIET_CASE,
    SELF_WAIVER_CASE,
    WAIVED_CASE,
    run_cli,
)


def run_sarif(case, tmp_path):
    code, out = run_cli(case, tmp_path, ["--format", "sarif"])
    return code, json.loads(out)  # the WHOLE stdout must be one JSON document


def test_document_structure(tmp_path):
    _, doc = run_sarif(QUIET_CASE, tmp_path)
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-schema-2.1.0.json")
    assert len(doc["runs"]) == 1
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "ai-code-guardrails"
    assert driver["version"] == __version__
    assert [r["id"] for r in driver["rules"]] == list(RULES)
    for rule in driver["rules"]:
        assert rule["shortDescription"]["text"]


def test_quiet_run_has_no_results(tmp_path):
    code, doc = run_sarif(QUIET_CASE, tmp_path)
    assert code == 0
    assert doc["runs"][0]["results"] == []


def test_failure_is_an_error_with_a_file_location(tmp_path):
    code, doc = run_sarif(SELF_WAIVER_CASE, tmp_path)
    assert code == 1
    results = doc["runs"][0]["results"]
    tampered = [r for r in results if r["ruleId"] == "test-integrity"]
    assert tampered, "the deleted test must surface as a result"
    r = tampered[0]
    assert r["level"] == "error"
    assert r["message"]["text"] == "deleted test file"
    uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "tests/test_app.py"


def test_waived_failure_downgrades_to_warning(tmp_path):
    code, doc = run_sarif(WAIVED_CASE, tmp_path)
    assert code == 0
    tampered = [r for r in doc["runs"][0]["results"] if r["ruleId"] == "test-integrity"]
    assert tampered and all(r["level"] == "warning" for r in tampered)


def test_every_result_references_a_declared_rule(tmp_path):
    _, doc = run_sarif(SELF_WAIVER_CASE, tmp_path)
    rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
    for result in doc["runs"][0]["results"]:
        assert result["ruleId"] in rule_ids
        assert result["message"]["text"]
