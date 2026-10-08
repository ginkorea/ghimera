"""Public role transport injection; local protocol fixtures, not model quality."""

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from ghimera import Collector
from ghimera.config import GhimeraConfig
from ghimera.model_client import SelfHostedModels
from ghimera.model_config import ModelServiceConfig
from ghimera.model_http import ModelHttpResponse, PinnedModelHttp
from tests.test_collector import assembled
from tests.test_embedding_scoring import endpoint as encoder_endpoint
from tests.test_http_fetch import ResolverFixture
from tests.test_http_fetch import site as source_site
from tests.test_search_conformance import endpoint as search_endpoint
from tests.test_served_models import endpoint as model_endpoint

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


class RecordingHttp:
    """An application-owned wrapper delegates unchanged bytes to the native owner."""

    def __init__(self, config: ModelServiceConfig) -> None:
        self._config = config
        self.delegate = PinnedModelHttp(config)
        self.requests: list[bytes] = []

    @property
    def config(self) -> ModelServiceConfig:
        return self._config

    async def post(self, body: bytes) -> ModelHttpResponse:
        self.requests.append(body)
        return await self.delegate.post(body)


def test_public_collector_dispatches_all_roles_through_exact_native_delegates(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    cfg, _ = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    original = cfg.model_dump_json()
    ports = {
        role: RecordingHttp(cfg.models.service(role))
        for role in ("planner", "analyst", "reviewer", "judge")
    }
    collector = Collector(cfg, model_http=ports, source_resolver=ResolverFixture())
    assert not model_endpoint[1] and not encoder_endpoint[1] and not search_endpoint[1]
    result = asyncio.run(collector.run("find ports"))
    assert result.status == "answered"
    assert all(port.requests for port in ports.values())
    assert sum(len(port.requests) for port in ports.values()) == len(model_endpoint[1]) == 5
    assert result.harvest.receipt.judge_calls == 5
    assert collector.config.model_dump_json() == original
    assert result.harvest.receipt.effective_config.model_dump_json() == original
    assert all(headers is None for _, _, headers in model_endpoint[1])
    assert ports["reviewer"].config != ports["analyst"].config


@pytest.mark.parametrize("entrypoint", ["constructor", "from_toml", "factory"])
def test_optional_role_map_preserves_partial_and_empty_default_bindings(entrypoint):
    path = Path("examples/collector.toml")
    cfg = GhimeraConfig.from_toml(path, max_bytes=100000)
    port = RecordingHttp(cfg.models.planner)
    if entrypoint == "factory":
        roles = SelfHostedModels.from_config(cfg, model_http={"planner": port})
        assert roles.planner.model.model_id == cfg.models.planner.model_id
        assert roles.reviewer.model.model_id == cfg.models.reviewer.model_id
    elif entrypoint == "from_toml":
        collector = Collector.from_toml(path, max_config_bytes=100000, model_http={"planner": port})
        assert collector.config == cfg
    else:
        assert Collector(cfg, model_http={}).config == cfg
        assert Collector(cfg, model_http={"planner": port}).config == cfg
    assert port.requests == []


@pytest.mark.parametrize("entrypoint", ["constructor", "from_toml", "factory"])
@pytest.mark.parametrize(
    "defect", ["extra_role", "other_role", "revision", "context", "credential"]
)
def test_public_role_admission_refuses_drift_and_credential_overlap_before_dispatch(
    entrypoint, defect
):
    path = Path("examples/collector.toml")
    cfg = GhimeraConfig.from_toml(path, max_bytes=100000)
    service = cfg.models.planner
    role = "planner"
    credentials = None
    if defect == "extra_role":
        role = "semantic_extract"
    elif defect == "other_role":
        service = cfg.models.reviewer
    elif defect == "revision":
        service = service.model_copy(update={"revision": "changed-original-pin"})
    elif defect == "context":
        service = service.model_copy(
            update={
                "context": service.context.model_copy(
                    update={"max_chars": service.context.max_chars + 1}
                )
            }
        )
    else:
        credentials = {service.endpoint: SecretStr("fixture-unused-credential")}
    port = RecordingHttp(service)
    with pytest.raises(ValueError, match="model transport|injected model"):
        if entrypoint == "factory":
            SelfHostedModels.from_config(cfg, model_http={role: port}, credentials=credentials)
        elif entrypoint == "from_toml":
            Collector.from_toml(
                path,
                max_config_bytes=100000,
                model_http={role: port},
                model_credentials=credentials,
            )
        else:
            Collector(cfg, model_http={role: port}, model_credentials=credentials)
    assert port.requests == []
