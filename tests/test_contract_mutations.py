"""Deliberately break shared properties in isolated copies; require executable reds."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module,old,new,witness",
    (
        (
            "search",
            'if "discover" in cls.__dict__:',
            "if False:",
            "test_search_conformance.py::test_search_cannot_override_template_or_spend_without_reservation",
        ),
        (
            "search",
            "budget.reserve_fetch()",
            "budget.check_time()",
            "test_search_conformance.py::test_search_cannot_override_template_or_spend_without_reservation",
        ),
        (
            "search",
            "if len(response.raw) > allowance or len(response.hits) > request.limit:",
            "if False:",
            "test_search_conformance.py::test_search_response_limit_is_a_contract_not_a_suggestion",
        ),
        (
            "fetch",
            'if "execute" in cls.__dict__:',
            "if False:",
            "test_fetch_route_conformance.py::test_a_route_cannot_override_the_final_template",
        ),
        (
            "fetch",
            "if not scope.permits(url):",
            "if False:",
            "test_fetch_route_conformance.py::test_scope_refuses_before_fetch",
        ),
        (
            "fetch",
            "budget.reserve_fetch()",
            "budget.check_time()",
            "test_fetch_route_conformance.py::test_fetch_conformance",
        ),
        (
            "fetch",
            "if code != RefusalCode.FETCH_FAILED:",
            "if False:",
            "test_fetch_route_conformance.py::test_terminal_refusal_never_escalates",
        ),
        (
            "scoring",
            'if "score" in cls.__dict__:',
            "if False:",
            "test_scorer_conformance.py::test_a_scorer_cannot_invent_candidates_or_override_template",
        ),
        (
            "scoring",
            "if any(link.url not in eligible for link in ranked):",
            "if False:",
            "test_scorer_conformance.py::test_a_scorer_cannot_invent_candidates_or_override_template",
        ),
        (
            "scoring",
            "tuple(sorted(ranked, key=lambda link: link.score, reverse=True))",
            "ranked",
            "test_scorer_conformance.py::test_scorer_conformance",
        ),
    ),
)
def test_contract_mutation(tmp_path, module, old, new, witness):
    project = Path(__file__).resolve().parents[1]
    copied = tmp_path / "src"
    shutil.copytree(project / "src", copied, ignore=shutil.ignore_patterns("__pycache__"))
    file = copied / "chimera" / f"{module}.py"
    original = file.read_text()
    assert original.count(old) == 1, "mutation anchor changed: revise the witness, do not skip it"
    file.write_text(original.replace(old, new))
    env = dict(os.environ, PYTHONPATH=str(copied), PYTHONDONTWRITEBYTECODE="1")
    env.pop("TAIPAN_TOKEN", None)
    env.pop("TAIPAN_COGNITO_ACCESS_TOKEN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(project / "tests" / witness),
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAILED" in result.stdout
