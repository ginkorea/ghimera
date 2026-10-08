"""Controlled CPU tensor contracts, not trained-model quality or weight admission."""

import asyncio
import hashlib
import importlib
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from ghimera.offline_reranking import OfflineCrossEncoder
from ghimera.onnx_reranking import (
    OnnxBackend,
    TensorSignature,
    require_standalone_graph,
    validate_tensor_contract,
)
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.reranking_config import OnnxRerankingRuntime
from ghimera.reranking_types import RerankRequest, RerankScores
from ghimera.reranking_worker import WorkerRequest, execute, score_pairs
from tests.test_offline_reranking import request_fixture, rerank_policy
from tests.test_reranking_backend import PairFixture


def runtime_policy(**updates):
    values = dict(
        schema="ghimera.onnx-reranking-runtime/1",
        onnx_version="controlled/1",
        onnxruntime_version="controlled/1",
        tokenizers_version="controlled/1",
        numpy_version="controlled/1",
        model_file="model.onnx",
        tokenizer_file="tokenizer.json",
        provider="CPUExecutionProvider",
        input_bindings=[
            dict(name="input_ids", source="ids"),
            dict(name="attention_mask", source="attention_mask"),
            dict(name="token_type_ids", source="type_ids"),
        ],
        input_dtype="int64",
        logit_output="logits",
        output_dtype="float32",
        axes="dynamic_batch_and_sequence",
        padding="longest",
        pad_id=0,
        pad_type_id=0,
        pad_token="[PAD]",
        padding_direction="right",
        execution_mode="sequential",
        graph_optimization="basic",
        enable_cpu_mem_arena=False,
        enable_mem_pattern=False,
        log_severity=3,
        max_graph_depth=32,
        max_graph_messages=1000,
    )
    return OnnxRerankingRuntime.model_validate(values | updates)


def onnx_policy(tmp_path, **updates):
    root = tmp_path / "model"
    root.mkdir(exist_ok=True)
    artifacts = []
    for name, role in (("model.onnx", "model"), ("tokenizer.json", "tokenizer")):
        data = b"controlled local contract fixture, not trained weights: " + name.encode()
        (root / name).write_bytes(data)
        artifacts.append(dict(path=name, role=role, sha256=hashlib.sha256(data).hexdigest()))
    return rerank_policy(
        tmp_path,
        schema="ghimera.offline-reranking/2",
        runtime="onnx_sequence_classification/1",
        torch_version=None,
        transformers_version=None,
        onnx=runtime_policy(),
        artifacts=artifacts,
        **updates,
    )


class Message:
    def __init__(self, name="onnx.ModelProto", fields=()):
        self.DESCRIPTOR = SimpleNamespace(full_name=name)
        self.fields = fields

    def ListFields(self):
        return self.fields


def field(name, kind=11, label=1):
    return SimpleNamespace(name=name, type=kind, is_repeated=label == 3)


def signature(name, kind="tensor(int64)", shape=("batch", "sequence")):
    return TensorSignature(name=name, element_type=kind, shape=shape)


def test_transformers_v1_bytes_and_semantics_remain_frozen():
    policy = rerank_policy(Path("/srv/fixture"), worker_python="/usr/bin/python3")
    assert policy.identity == "98c3b4483827156838e81cdaed5215bd88ca28b8cec49fa3038036e108ac1ecb"
    assert "onnx" not in policy.model_dump()
    for updates in (
        dict(onnx=runtime_policy()),
        dict(torch_version=None),
        dict(transformers_version=None),
        dict(runtime="onnx_sequence_classification/1"),
    ):
        with pytest.raises(ValidationError):
            rerank_policy(Path("/srv/fixture"), **updates)


@pytest.mark.parametrize(
    "change",
    [
        dict(provider="CUDAExecutionProvider"),
        dict(input_dtype="int32"),
        dict(output_dtype="probability"),
        dict(padding="fixed"),
        dict(model_file="weights.data"),
        dict(onnxruntime_version=" "),
        dict(input_bindings=[dict(name="ids", source="ids")]),
        dict(
            input_bindings=[
                dict(name="same", source="ids"),
                dict(name="same", source="attention_mask"),
            ]
        ),
    ],
)
def test_runtime_configuration_refuses_provider_key_dtype_and_file_expansion(change):
    with pytest.raises(ValidationError):
        runtime_policy(**change)


def test_new_policy_has_only_exact_standalone_artifacts_and_roundtrips(tmp_path):
    policy = onnx_policy(tmp_path)
    assert type(policy).model_validate_json(policy.model_dump_json()) == policy
    assert (
        "torch_version" not in policy.model_dump()
        and "transformers_version" not in policy.model_dump()
    )
    for changes in (
        dict(torch_version="extra"),
        dict(schema="ghimera.offline-reranking/1"),
        dict(onnx=None),
        dict(
            artifacts=policy.artifacts + (dict(path="weights.data", sha256="a" * 64, role="model"),)
        ),
    ):
        with pytest.raises(ValidationError):
            type(policy).model_validate(policy.model_dump() | changes)


def test_new_example_is_typed_explicit_and_inert():
    from ghimera.reranking_config import OfflineRerankingConfig

    policy = OfflineRerankingConfig.model_validate(
        tomllib.loads(Path("examples/offline-reranking-onnx.toml").read_text())
    )
    assert policy.onnx.provider == "CPUExecutionProvider"
    assert all(row.sha256 == "0" * 64 for row in policy.artifacts)


@pytest.mark.parametrize("location", ["initializer", "attribute", "function", "sparse"])
@pytest.mark.parametrize("external", ["external_data", "data_location"])
def test_external_data_is_refused_everywhere_before_runtime_load(location, external):
    tensor = Message(
        "onnx.TensorProto",
        (
            (
                field(external, 9 if external == "external_data" else 14),
                "other.data" if external == "external_data" else 1,
            ),
        ),
    )
    nested = Message("onnx.GraphProto", ((field(location, label=3), (tensor,)),))
    root = Message(fields=((field("graph"), nested),))
    with pytest.raises(ValueError, match="standalone"):
        require_standalone_graph(root, runtime_policy())


def test_graph_inspection_has_explicit_depth_and_message_bounds():
    root = Message(
        fields=(
            (field("graph"), Message(fields=((field("nodes", label=3), (Message(), Message())),))),
        )
    )
    require_standalone_graph(root, runtime_policy())
    for updates in (dict(max_graph_depth=1), dict(max_graph_messages=2)):
        with pytest.raises(ValueError, match="bounds"):
            require_standalone_graph(root, runtime_policy(**updates))


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "float_input", "static", "multi_logit", "wrong_output", "double_output"],
)
def test_tensor_metadata_refuses_incompatible_input_and_output_contract(change):
    inputs = tuple(signature(name) for name in ("input_ids", "attention_mask", "token_type_ids"))
    outputs = (signature("logits", "tensor(float)", ("batch", 1)),)
    if change == "missing":
        inputs = inputs[:-1]
    elif change == "extra":
        inputs += (signature("remote"),)
    elif change == "float_input":
        inputs = (signature("input_ids", "tensor(float)"),) + inputs[1:]
    elif change == "static":
        inputs = (signature("input_ids", shape=(1, 16)),) + inputs[1:]
    elif change == "multi_logit":
        outputs = (signature("logits", "tensor(float)", ("batch", 2)),)
    elif change == "wrong_output":
        outputs = (signature("probabilities", "tensor(float)", ("batch", 1)),)
    else:
        outputs = (signature("logits", "tensor(double)", ("batch", 1)),)
    with pytest.raises(ValueError, match="exact dynamic"):
        validate_tensor_contract(runtime_policy(), inputs, outputs)


def install_controlled_boundary(monkeypatch, policy, *, graph=None, bad=None):
    calls = []

    class Tokenizer:
        truncation = None
        padded = False

        def token_to_id(self, token):
            return 0

        def no_truncation(self):
            calls.append("no_truncation")

        def no_padding(self):
            self.padded = False

        def enable_padding(self, **kwargs):
            self.padded = True
            calls.append(("padding", kwargs))

        def encode_batch(self, pairs, **kwargs):
            calls.append(("pairs", pairs, kwargs))
            size = 100 if bad == "tokens" else 4
            return [
                SimpleNamespace(ids=[1] * size, attention_mask=[1] * size, type_ids=[0] * size)
                for _ in pairs
            ]

    class Session:
        def __init__(self, model, **kwargs):
            calls.append(("session", model, kwargs))

        def disable_fallback(self):
            calls.append("no_fallback")

        def get_providers(self):
            return ["CUDAExecutionProvider"] if bad == "provider" else ["CPUExecutionProvider"]

        def get_inputs(self):
            return [
                SimpleNamespace(name=row.name, type="tensor(int64)", shape=["batch", "sequence"])
                for row in policy.onnx.input_bindings
            ]

        def get_outputs(self):
            return [SimpleNamespace(name="logits", type="tensor(float)", shape=["batch", 1])]

        def run(self, names, feed):
            calls.append(("run", names, feed))
            n = len(next(iter(feed.values())))
            if bad == "partial":
                n -= 1
            width = 2 if bad == "width" else 1
            return [
                np.full(
                    (n, width),
                    float("nan") if bad == "nan" else -3.0,
                    dtype=np.float64 if bad == "dtype" else np.float32,
                )
            ]

    onnx = SimpleNamespace(
        load_model_from_string=lambda data: graph or Message(),
        checker=SimpleNamespace(
            check_model=lambda model, **kwargs: calls.append(("graph_checked", kwargs))
        ),
    )
    ort = SimpleNamespace(
        SessionOptions=SimpleNamespace,
        InferenceSession=Session,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
        GraphOptimizationLevel=SimpleNamespace(
            ORT_DISABLE_ALL=0, ORT_ENABLE_BASIC=1, ORT_ENABLE_EXTENDED=2, ORT_ENABLE_ALL=3
        ),
    )
    tokenizers = SimpleNamespace(Tokenizer=SimpleNamespace(from_file=lambda path: Tokenizer()))
    original = importlib.import_module
    monkeypatch.setattr("ghimera.onnx_reranking.version", lambda name: "controlled/1")
    monkeypatch.setattr(
        "ghimera.onnx_reranking.importlib.import_module",
        lambda name: (
            {"onnx": onnx, "onnxruntime": ort, "tokenizers": tokenizers, "numpy": np}.get(name)
            or original(name)
        ),
    )
    return calls


def test_backend_rejects_external_tensor_data_before_graph_check_or_session_creation(
    tmp_path, monkeypatch
):
    policy = onnx_policy(tmp_path)
    tensor = Message("onnx.TensorProto", ((field("data_location", 14), 1),))
    graph = Message(fields=((field("graph"), tensor),))
    calls = install_controlled_boundary(monkeypatch, policy, graph=graph)
    with pytest.raises(ValueError, match="external tensor data"):
        OnnxBackend(policy, policy.model_directory)
    assert not any(
        isinstance(row, tuple) and row[0] in {"session", "graph_checked"} for row in calls
    )


def test_local_cpu_backend_loads_captured_bytes_and_exact_pair_tensors(tmp_path, monkeypatch):
    policy = onnx_policy(tmp_path)
    calls = install_controlled_boundary(monkeypatch, policy)
    envelope = WorkerRequest(
        action="score",
        policy=policy,
        request=RerankRequest.model_validate(
            request_fixture(tmp_path).model_dump() | {"policy": policy}
        ),
    )
    scores = RerankScores.model_validate_json(execute(envelope))
    scores.validate_request(envelope.request)
    assert all(row.logit == -3.0 for row in scores.scores)
    session = next(row for row in calls if isinstance(row, tuple) and row[0] == "session")
    assert isinstance(session[1], bytes) and session[2]["providers"] == ["CPUExecutionProvider"]
    assert "no_fallback" in calls
    for row in calls:
        if isinstance(row, tuple) and row[0] == "run":
            assert set(row[2]) == {binding.name for binding in policy.onnx.input_bindings}
            assert all(
                value.dtype == np.int64 and value.shape == (2, 4) for value in row[2].values()
            )
        elif isinstance(row, tuple) and row[0] == "padding":
            assert row[1]["length"] is None and row[1]["pad_to_multiple_of"] is None


@pytest.mark.parametrize("bad", ["provider", "tokens", "partial", "width", "nan", "dtype"])
def test_backend_never_falls_back_or_accepts_shortened_nonfinite_foreign_tensors(
    tmp_path, monkeypatch, bad
):
    policy = onnx_policy(tmp_path)
    calls = install_controlled_boundary(monkeypatch, policy, bad=bad)
    request = RerankRequest.model_validate(
        request_fixture(tmp_path).model_dump() | {"policy": policy}
    )
    with pytest.raises(ValueError):
        score_pairs(request, OnnxBackend(policy, policy.model_directory))
    if bad in {"provider", "tokens"}:
        assert not any(isinstance(row, tuple) and row[0] == "run" for row in calls)


def test_injected_backend_reuses_verified_snapshot_and_native_complete_scores(tmp_path):
    policy = onnx_policy(tmp_path)
    request = RerankRequest.model_validate(
        request_fixture(tmp_path).model_dump() | {"policy": policy}
    )
    captured = []

    def factory(config, snapshot):
        assert config == policy and snapshot != policy.model_directory
        captured.append(snapshot)
        return PairFixture()

    result = RerankScores.model_validate_json(
        execute(WorkerRequest(action="score", policy=policy, request=request), factory)
    )
    result.validate_request(request)
    assert captured and not captured[0].exists()


def test_actual_onnx_child_refuses_missing_runtime_without_transformers_fallback(tmp_path):
    policy = onnx_policy(tmp_path)
    adapter = OfflineCrossEncoder(policy)
    with pytest.raises(GhimeraRefused) as failure:
        asyncio.run(adapter.prepare())
    assert failure.value.code == RefusalCode.MODEL_UNAVAILABLE
