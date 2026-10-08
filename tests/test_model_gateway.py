"""Gateway admission and native curl options, without DNS or network contact."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest
from curl_cffi import CurlInfo, CurlOpt
from pydantic import SecretStr, ValidationError

from ghimera.model_config import EmbeddingServiceConfig, ModelServiceConfig, PrivateModelService
from ghimera.model_gateway_config import SelfHostedGatewayConfig
from ghimera.model_http import ModelWireFailure, PinnedModelHttp
from ghimera.private_json import PinnedJsonHttp
from ghimera.private_service_config import PrivateJsonConfig
from tests.test_embedding_scoring import service as private_embedding
from tests.test_served_models import service as private_model

ORIGIN = "https://inference.example.invalid"
PINS = ("8.8.8.8", "2001:4860:4860::8888")


def configured(kind="model", **updates):
    original = private_model(8000) if kind == "model" else private_embedding(8000)
    raw = dict(
        original.model_dump(),
        schema="chimera.model-service/3" if kind == "model" else "chimera.embedding-service/2",
        endpoint=ORIGIN + ("/v1/chat/completions" if kind == "model" else "/v1/embeddings"),
        approved_addresses=PINS,
        allow_plaintext=False,
        allow_plaintext_credentials=False,
        authorization="bearer",
        gateway=dict(schema="ghimera.self-hosted-gateway/1", origin=ORIGIN),
    )
    raw.update(updates)
    return type(original).model_validate(raw)


@pytest.mark.parametrize("kind", ["model", "embedding"])
def test_gateway_is_explicit_versioned_and_kept_in_exact_native_recipe(kind):
    service = configured(kind)
    assert type(service).model_validate_json(service.model_dump_json()) == service
    assert service.gateway.origin == ORIGIN and service.approved_addresses == PINS
    bound = PinnedModelHttp(service, credential=SecretStr("fixture-credential"))
    assert bound.config == service
    legacy = private_model(8000) if kind == "model" else private_embedding(8000)
    assert "gateway" not in legacy.model_dump()
    assert "gateway" not in legacy.model_dump_json()
    assert (
        hashlib.sha256(legacy.model_dump_json().encode()).hexdigest()
        != hashlib.sha256(service.model_dump_json().encode()).hexdigest()
    )


@pytest.mark.parametrize("kind", ["model", "embedding"])
@pytest.mark.parametrize(
    "change",
    [
        {"gateway": None},
        {"schema": "unknown"},
        {"allow_plaintext": True},
        {"allow_plaintext_credentials": True},
        {"approved_addresses": ("127.0.0.1",)},
        {"approved_addresses": ("10.0.0.1",)},
        {"approved_addresses": ("169.254.169.254",)},
        {"approved_addresses": ("100.100.100.200",)},
        {"approved_addresses": ("168.63.129.16",)},
        {"approved_addresses": ("192.0.2.1",)},
        {"approved_addresses": ("224.0.0.1",)},
        {"approved_addresses": ("0.0.0.0",)},
        {"approved_addresses": ("::",)},
        {"approved_addresses": ("::1",)},
        {"approved_addresses": ("fc00::1",)},
        {"approved_addresses": ("fe80::1",)},
        {"approved_addresses": ("2001:db8::1",)},
        {"approved_addresses": ("::ffff:8.8.8.8",)},
        {"approved_addresses": ("2002:808:808::1",)},
        {"approved_addresses": ("64:ff9b::a9fe:a9fe",)},
        {"approved_addresses": ("8.8.8.8", "8.8.8.8")},
        {"approved_addresses": ()},
        {"endpoint": "http://inference.example.invalid/v1/chat/completions"},
        {"endpoint": "https://other.example.invalid/v1/chat/completions"},
        {"endpoint": "https://inference.example.invalid:444/v1/chat/completions"},
        {"endpoint": "https://owner:secret@inference.example.invalid/v1/chat/completions"},
        {"endpoint": ORIGIN + "/v1/chat/completions?"},
        {"endpoint": ORIGIN + "/v1/chat/completions#"},
    ],
)
def test_gateway_ambiguous_or_unsafe_recipe_refused_before_transport(kind, change):
    with pytest.raises(ValidationError):
        configured(kind, **change)


@pytest.mark.parametrize(
    "origin",
    [
        "http://inference.example.invalid",
        ORIGIN + "/",
        ORIGIN + "/v1",
        ORIGIN + "?key=secret",
        ORIGIN + "#fragment",
        "https://inference.example.invalid:443",
        "https://INFERENCE.example.invalid",
        "https://inference.example.invalid.",
        "https://inference.example.invalid:0",
        "https://inference.example.invalid:invalid",
        "https://inference.example.invalid\\evil",
        "https://inference.example.invalid\n",
        "https://owner@inference.example.invalid",
        "https://inference..example.invalid",
    ],
)
def test_gateway_origin_requires_one_canonical_https_authority(origin):
    with pytest.raises(ValidationError):
        SelfHostedGatewayConfig(schema="ghimera.self-hosted-gateway/1", origin=origin)


@pytest.mark.parametrize("kind", ["model", "embedding"])
def test_older_schemas_and_generic_json_cannot_gain_public_control_policy(kind):
    service = configured(kind)
    legacy_schema = "chimera.model-service/1" if kind == "model" else "chimera.embedding-service/1"
    with pytest.raises(ValidationError):
        type(service).model_validate(dict(service.model_dump(), schema=legacy_schema))
    with pytest.raises(ValidationError):
        type(service).model_validate(dict(service.model_dump(), schema=legacy_schema, gateway=None))
    with pytest.raises(ValueError, match="private service addresses"):
        PinnedJsonHttp(service, credential=SecretStr("fixture-credential"))
    with pytest.raises(ValidationError):
        PrivateJsonConfig.model_validate(
            dict(
                endpoint=service.endpoint,
                approved_addresses=service.approved_addresses,
                allow_plaintext=False,
                allow_plaintext_credentials=False,
                authorization="bearer",
                timeout_seconds=1.0,
                max_request_bytes=100,
                max_response_bytes=100,
                max_header_bytes=100,
            )
        )


def test_gateway_cannot_bypass_versioned_model_boundary_with_mutated_instances():
    service = configured()
    altered = service.model_copy(update={"schema_version": "chimera.model-service/1"})
    with pytest.raises(ValidationError):
        PinnedModelHttp(altered, credential=SecretStr("fixture-credential"))
    base = PrivateModelService.model_validate(
        {name: getattr(service, name) for name in PrivateModelService.model_fields}
    )
    with pytest.raises(ValueError, match="versioned model or embedding"):
        PinnedModelHttp(base, credential=SecretStr("fixture-credential"))


def test_gateway_model_schema_can_preserve_explicit_generation_controls():
    selected = configured(
        generation=dict(schema="ghimera.local-generation/1", enable_thinking=False)
    )
    assert selected.generation.wire_fields() == {"chat_template_kwargs": {"enable_thinking": False}}
    with pytest.raises(ValidationError):
        ModelServiceConfig.model_validate(
            dict(selected.model_dump(), schema="chimera.model-service/2")
        )


@pytest.fixture
def native_wire(monkeypatch):
    handles = []
    response = {"status": 200, "body": b'{"observed":true}'}

    class CurlProbe:
        def __init__(self):
            self.options = {}
            self.closed = False

        def setopt(self, option, value):
            self.options[option] = value

        def getinfo(self, option):
            assert option == CurlInfo.RESPONSE_CODE
            return response["status"]

        def close(self):
            self.closed = True

    class MultiProbe:
        async def add_handle(self, handle):
            handles.append(handle)
            callback = handle.options[CurlOpt.HEADERFUNCTION]
            callback(b"HTTP/1.1 200 OK\r\n")
            callback(b"Content-Type: application/json\r\n")
            callback(b"Location: https://other.example.invalid/redirect\r\n")
            callback(b"\r\n")
            handle.options[CurlOpt.WRITEFUNCTION](response["body"])

        async def close(self):
            pass

    monkeypatch.setattr("ghimera.private_json.Curl", CurlProbe)
    monkeypatch.setattr("ghimera.private_json.AsyncCurl", MultiProbe)
    return handles, response


class ResolvedPins:
    def __init__(self, addresses=PINS):
        self.addresses = addresses
        self.calls = []

    async def resolve(self, host, port):
        self.calls.append((host, port))
        return self.addresses


@pytest.mark.parametrize("kind", ["model", "embedding"])
def test_native_wire_uses_real_global_pin_hostname_tls_and_exact_bearer_origin(native_wire, kind):
    handles, _ = native_wire
    resolver = ResolvedPins()
    service = configured(kind)
    client = PinnedModelHttp(service, credential=SecretStr("fixture-credential"), resolver=resolver)
    assert asyncio.run(client.post(b'{"native":true}')).status == 200
    assert resolver.calls == [("inference.example.invalid", 443)] and len(handles) == 1
    wire = handles[0].options
    assert wire[CurlOpt.URL] == service.endpoint
    assert wire[CurlOpt.RESOLVE] == ["inference.example.invalid:443:8.8.8.8"]
    assert wire[CurlOpt.SSL_VERIFYPEER] == 1 and wire[CurlOpt.SSL_VERIFYHOST] == 2
    assert wire[CurlOpt.FOLLOWLOCATION] == 0 and wire[CurlOpt.PROTOCOLS_STR] == "https"
    assert wire[CurlOpt.PROXY] == "" and wire[CurlOpt.NETRC] == 0
    assert b"Authorization: Bearer fixture-credential" in wire[CurlOpt.HTTPHEADER]
    assert wire[CurlOpt.POSTFIELDS] == b'{"native":true}' and handles[0].closed


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("8.8.4.4",),
        ("8.8.8.8", "127.0.0.1"),
        ("8.8.8.8", "169.254.169.254"),
    ],
)
def test_every_real_dns_answer_must_be_pinned_before_native_contact(native_wire, addresses):
    handles, _ = native_wire
    client = PinnedModelHttp(
        configured(), credential=SecretStr("fixture-credential"), resolver=ResolvedPins(addresses)
    )
    with pytest.raises(ModelWireFailure):
        asyncio.run(client.post(b"{}"))
    assert not handles


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_gateway_redirect_is_refused_without_credential_or_destination_expansion(
    native_wire, status
):
    handles, response = native_wire
    response["status"] = status
    client = PinnedModelHttp(
        configured(), credential=SecretStr("fixture-credential"), resolver=ResolvedPins()
    )
    with pytest.raises(ModelWireFailure) as refused:
        asyncio.run(client.post(b"{}"))
    assert refused.value.response.status == status and len(handles) == 1
    assert handles[0].options[CurlOpt.URL].startswith(ORIGIN + "/")


def test_gateway_retains_existing_payload_and_response_bounds(native_wire):
    handles, response = native_wire
    service = configured(max_request_bytes=2, max_response_bytes=2)
    resolver = ResolvedPins()
    client = PinnedModelHttp(service, credential=SecretStr("fixture-credential"), resolver=resolver)
    with pytest.raises(ModelWireFailure):
        asyncio.run(client.post(b"oversized"))
    assert not handles and not resolver.calls
    response["body"] = b"oversized"
    with pytest.raises(ModelWireFailure) as refused:
        asyncio.run(client.post(b"{}"))
    assert len(handles) == 1 and len(refused.value.response.body) <= 2


def test_gateway_credential_must_match_its_explicit_authentication_mode():
    for credential in (None, SecretStr(""), SecretStr("bad\r\nheader")):
        with pytest.raises(ValueError):
            PinnedModelHttp(configured(), credential=credential)
    with pytest.raises(ValueError):
        PinnedModelHttp(
            configured(authorization="none"), credential=SecretStr("fixture-credential")
        )


def test_gateway_example_is_versioned_nonactive_and_contains_no_credential():
    data = tomllib.loads(Path("examples/self-hosted-gateway.toml").read_text())
    model = ModelServiceConfig.model_validate(data["model"])
    encoder = EmbeddingServiceConfig.model_validate(data["embedding"])
    assert model.gateway == encoder.gateway
    assert model.endpoint.startswith(ORIGIN) and encoder.endpoint.startswith(ORIGIN)


@pytest.mark.parametrize(
    "constructor,expected",
    [
        (private_model, "8766e37642499dfd39e19cd785cbbd3ba0553ea880e7cf4a9b750ebd83340963"),
        (private_embedding, "0c114744b62fc8cb3ead5c9f68cef8ce74c300c3473cbe1b339e8550a02f684f"),
    ],
)
def test_legacy_serialized_recipes_retain_frozen_base_bytes(constructor, expected):
    # Independently compared to model_config.py at the frozen base 3bbef7e.
    assert hashlib.sha256(constructor(8000).model_dump_json().encode()).hexdigest() == expected


def test_gateway_native_resolution_preserves_ipv6_pin_and_explicit_origin_port(native_wire):
    handles, _ = native_wire
    origin = ORIGIN + ":8443"
    service = configured(
        endpoint=origin + "/v1/chat/completions",
        gateway=dict(schema="ghimera.self-hosted-gateway/1", origin=origin),
    )
    resolver = ResolvedPins((PINS[1],))
    client = PinnedModelHttp(service, credential=SecretStr("fixture-credential"), resolver=resolver)
    asyncio.run(client.post(b"{}"))
    assert resolver.calls == [("inference.example.invalid", 8443)]
    assert handles[0].options[CurlOpt.RESOLVE] == [
        "inference.example.invalid:8443:[2001:4860:4860::8888]"
    ]
    assert handles[0].options[CurlOpt.URL] == service.endpoint


def test_gateway_literal_origin_must_match_its_canonical_global_pin():
    origin = "https://8.8.8.8"
    updates = dict(
        endpoint=origin + "/v1/chat/completions",
        gateway=dict(schema="ghimera.self-hosted-gateway/1", origin=origin),
    )
    service = configured(**updates)
    assert service.gateway.origin == origin
    with pytest.raises(ValidationError):
        configured(**updates, approved_addresses=("8.8.4.4",))


def test_gateway_retains_existing_header_bound(native_wire):
    handles, _ = native_wire
    client = PinnedModelHttp(
        configured(max_header_bytes=1),
        credential=SecretStr("fixture-credential"),
        resolver=ResolvedPins(),
    )
    with pytest.raises(ModelWireFailure):
        asyncio.run(client.post(b"{}"))
    assert len(handles) == 1
