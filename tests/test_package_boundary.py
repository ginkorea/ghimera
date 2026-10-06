"""A crawler never installs or constructs an inference service or external model fallback."""

import ast
from pathlib import Path

import pytest

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import ModelIdentity
from ghimera.refusals import GhimeraRefused, RefusalCode


def test_production_import_graph_has_no_inference_or_platform_stack():
    banned = {"torch", "transformers", "vllm", "ollama", "openai", "redis", "celery", "taipan"}
    for path in (Path(__file__).resolve().parents[1] / "src" / "ghimera").glob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert not imports & banned, path
        assert "type: ignore" not in source
        assert "typing.Any" not in source


def test_refusal_table_is_complete_and_immutable():
    from ghimera.refusals import REFUSALS

    assert set(REFUSALS) == set(RefusalCode)
    assert all(sentence.strip() for sentence in REFUSALS.values())
    with pytest.raises(TypeError):
        REFUSALS[RefusalCode.PAYWALL] = "changed"


def test_external_model_refuses_before_work():
    class ExternalJudge(FakeJudge):
        @property
        def model(self):
            return ModelIdentity(model_id="external-test", revision="1", location="external")

    with pytest.raises(GhimeraRefused, match="model_unavailable"):
        GoalLoop(
            config=GhimeraConfig.from_toml(Path("examples/chimera.toml")),
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=ExternalJudge(),
        )
