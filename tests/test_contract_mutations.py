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
            "semantic_scoring",
            "budget.reserve_encoding(size)",
            "budget.check_time()",
            "test_embedding_scoring.py::test_semantic_call_budget_reserves_before_spend_and_keeps_partial_ledger",
        ),
        (
            "embedding",
            "wire.model != service.served_model",
            "False",
            "test_embedding_scoring.py::test_encoder_refusals_preserve_bounded_call_evidence[wrong_model]",
        ),
        (
            "embedding",
            "{item.index for item in wire.data} != set(range(len(inputs)))",
            "False",
            "test_embedding_scoring.py::test_encoder_refusals_preserve_bounded_call_evidence[duplicate_index]",
        ),
        (
            "politeness",
            "host.next_start = loop.time() + spacing",
            "host.next_start = 0.0",
            "test_http_fetch.py::test_global_and_host_limits_allow_parallel_work_with_delay",
        ),
        (
            "model_client",
            "wire.model != service.served_model",
            "False",
            "test_served_models.py::test_bad_model_responses_refuse_without_fallback_and_preserve_evidence[wrong_model]",
        ),
        (
            "model_client",
            'wire.choices[0].finish_reason != "stop"',
            "False",
            "test_served_models.py::test_bad_model_responses_refuse_without_fallback_and_preserve_evidence[truncated]",
        ),
        (
            "model_http",
            "address not in self._config.approved_addresses for address in addresses",
            "False for address in addresses",
            "test_served_models.py::test_model_dns_must_match_every_approved_address_before_post",
        ),
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
            "if not scope.permits(url):\n"
            "            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)\n"
            "        for route in self._routes:",
            "if False:\n"
            "            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)\n"
            "        for route in self._routes:",
            "test_fetch_route_conformance.py::test_scope_refuses_before_fetch",
        ),
        (
            "fetch",
            "budget.reserve_fetch()\n        except GhimeraRefused:",
            "budget.check_time()\n        except GhimeraRefused:",
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
            "if any((link.url, link.anchor) not in eligible for link in ranked):",
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
    file = copied / "ghimera" / f"{module}.py"
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
