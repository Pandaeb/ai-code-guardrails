"""The red-team corpus, run as the package's test suite.

Every attack must be caught and every control must stay quiet — a
detection rate is only meaningful next to a false-positive rate. A
failing test here is a product defect, not a test-suite annoyance.
"""

import pytest

from redteam.cases import CASES
from redteam.harness import build_repo, run_guard

ATTACKS = [c for c in CASES if c["kind"] == "attack"]
CONTROLS = [c for c in CASES if c["kind"] == "control"]


def test_corpus_shape():
    """The published claim is '28/28 attacks, 0% FP on 16 controls'.

    This pins the corpus size so a case cannot be dropped silently;
    growing the corpus means updating the claim here and in README.
    """
    assert len(ATTACKS) == 28
    assert len(CONTROLS) == 16
    assert {c["expect"] for c in ATTACKS} == {"FAIL"}, \
        "attacks are caught by failing (or the corpus mislabels a case)"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case, tmp_path):
    repo, base_sha = build_repo(case, tmp_path)
    status, output, _ = run_guard(repo, base_sha, case)
    label = "attack not caught" if case["kind"] == "attack" else "false positive on a control"
    assert status == case["expect"], (
        "%s [%s]: guard %s expected %s, got %s\nrule: %s\nattack: %s\n\n%s"
        % (
            label,
            case["id"],
            case["guard"],
            case["expect"],
            status,
            case.get("rule", ""),
            case["attack"],
            output,
        )
    )
