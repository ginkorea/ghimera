"""Actual artifact capture/worker recipe with controlled backend; no real weights."""

import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ghimera.offline_reranking import OfflineCrossEncoder
from ghimera.reranking_worker import (
    TransformersBackend,
    WorkerRequest,
    artifact_snapshot,
    check_artifacts,
    execute,
    score_pairs,
)
from tests.test_offline_reranking import request_fixture, rerank_policy


def artifact_policy(tmp_path, **updates):
    root = tmp_path / "model"
    root.mkdir()
    artifacts = []
    for name, role in (
        ("config.json", "model"),
        ("model.safetensors", "model"),
        ("tokenizer.json", "tokenizer"),
    ):
        data = b"controlled artifact, not trained weights: " + name.encode()
        (root / name).write_bytes(data)
        artifacts.append(dict(path=name, role=role, sha256=hashlib.sha256(data).hexdigest()))
    return rerank_policy(tmp_path, artifacts=artifacts, **updates)


class PairFixture:
    def __init__(self, lengths=None, logits=None):
        self.lengths = lengths
        self.logits = logits
        self.calls = []

    def token_lengths(self, query, texts):
        return self.lengths if self.lengths is not None else tuple(4 for _ in texts)

    def predict(self, query, texts):
        self.calls.append((query, texts))
        return self.logits if self.logits is not None else tuple(-2.0 for _ in texts)


def test_snapshot_is_digest_verified_and_runtime_loads_only_captured_bytes(tmp_path):
    policy = artifact_policy(tmp_path)
    with artifact_snapshot(policy) as snapshot:
        assert snapshot.parent == policy.work_directory and snapshot != policy.model_directory
        (policy.model_directory / "config.json").write_bytes(b"changed outside snapshot")
        assert (snapshot / "config.json").read_bytes().startswith(b"controlled artifact")
    assert not snapshot.exists()
    called = []
    with pytest.raises(ValueError, match="digest mismatch"):
        execute(
            WorkerRequest(action="prepare", policy=policy, request=None),
            lambda p, path: called.append(path),
        )
    assert not called


@pytest.mark.parametrize("defect", ["extra", "symlink", "bytes", "missing"])
def test_artifact_inventory_refuses_before_loading(tmp_path, defect):
    policy = artifact_policy(tmp_path)
    if defect == "extra":
        (policy.model_directory / "remote-code.py").write_text("not executed")
    elif defect == "symlink":
        (policy.model_directory / "linked").symlink_to(policy.model_directory / "config.json")
    elif defect == "missing":
        (policy.model_directory / "tokenizer.json").unlink()
    else:
        policy = policy.model_copy(update={"max_artifact_bytes": 1})
    with pytest.raises(ValueError):
        check_artifacts(policy)


@pytest.mark.parametrize(
    "changes",
    [
        dict(device="cuda"),
        dict(score_semantics="probability"),
        dict(input_recipe="remote"),
        dict(batch_size=100),
        dict(artifacts=[dict(path="../model.safetensors", sha256="0" * 64, role="model")]),
    ],
)
def test_configuration_cannot_expand_device_semantics_or_artifacts(tmp_path, changes):
    with pytest.raises(ValidationError):
        rerank_policy(tmp_path, **changes)


@pytest.mark.parametrize("lengths", [(100,) * 6, (4,), (True,) * 6, (0,) * 6])
def test_all_pair_tokens_are_admitted_before_any_predict(tmp_path, lengths):
    backend = PairFixture(lengths=lengths)
    with pytest.raises(ValueError, match="no truncation"):
        score_pairs(request_fixture(tmp_path), backend)
    assert not backend.calls


@pytest.mark.parametrize(
    "logits", [(float("nan"), float("nan")), (float("inf"), float("inf")), (-1.0,), (True, True)]
)
def test_partial_or_nonfinite_logits_never_become_scores(tmp_path, logits):
    with pytest.raises(ValueError):
        score_pairs(request_fixture(tmp_path), PairFixture(logits=logits))


def test_native_worker_batches_complete_pairs_and_preserves_raw_negative_logits(tmp_path):
    request = request_fixture(tmp_path)
    backend = PairFixture()
    scored = score_pairs(request, backend)
    assert len(backend.calls) == 3 and all(len(texts) == 2 for _, texts in backend.calls)
    assert all(row.logit == -2.0 and row.pair_tokens == 4 for row in scored.scores)
    scored.validate_request(request)


def test_child_environment_has_no_credentials_and_refuses_input_bytes_before_start(
    tmp_path, monkeypatch
):
    captured = {}

    class WorkerFixture:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, payload):
            raise AssertionError("oversized request must not start a child")

    monkeypatch.setattr("ghimera.offline_reranking.PassiveWorker", WorkerFixture)
    policy = artifact_policy(tmp_path, max_request_bytes=1)
    adapter = OfflineCrossEncoder(policy)
    import asyncio

    with pytest.raises(ValueError, match="byte bound"):
        asyncio.run(adapter.prepare())
    environment = captured["environment"]
    assert environment["HF_HUB_OFFLINE"] == environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["CUDA_VISIBLE_DEVICES"] == ""
    assert (
        not {"TAIPAN_TOKEN", "TAIPAN_COGNITO_ACCESS_TOKEN", "HTTP_PROXY", "HTTPS_PROXY"}
        & environment.keys()
    )


def test_each_prepare_and_score_revalidates_native_worker_recipe(tmp_path, monkeypatch):
    import asyncio

    from ghimera.reranking_types import RerankScores

    calls = []

    class WorkerFixture:
        def __init__(self, **kwargs):
            pass

        async def run(self, payload):
            envelope = WorkerRequest.model_validate_json(payload)
            calls.append(envelope)
            if envelope.request is None:
                return json.dumps({"ready": envelope.policy.identity}).encode()
            return score_pairs(envelope.request, PairFixture()).model_dump_json().encode()

    monkeypatch.setattr("ghimera.offline_reranking.PassiveWorker", WorkerFixture)
    policy = artifact_policy(tmp_path)
    adapter = OfflineCrossEncoder(policy)
    request = request_fixture(tmp_path).model_copy(update={"policy": policy})

    async def operation():
        await adapter.prepare()
        await adapter.prepare()
        result = await adapter.score(request)
        assert isinstance(result, RerankScores)
        result.validate_request(request)

    asyncio.run(operation())
    assert tuple(row.action for row in calls) == ("prepare", "prepare", "score")


def test_actual_offline_child_refuses_unavailable_exact_runtime_without_remote_fallback(tmp_path):
    import asyncio

    from ghimera.refusals import GhimeraRefused, RefusalCode

    policy = artifact_policy(tmp_path, torch_version="unavailable-controlled-runtime/1")
    adapter = OfflineCrossEncoder(policy)
    with pytest.raises(GhimeraRefused) as observed:
        asyncio.run(adapter.prepare())
    assert observed.value.code == RefusalCode.MODEL_UNAVAILABLE


def test_transformers_boundary_requires_local_files_safetensors_no_remote_code_or_truncation(
    tmp_path, monkeypatch
):
    policy = artifact_policy(tmp_path)
    calls = []

    class Tokenizer:
        def __call__(self, queries, texts, **kwargs):
            calls.append(("tokenize", kwargs))
            return {"input_ids": [[1, 2] for _ in texts]}

    class Tensor:
        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return [[-3.0], [-4.0]]

    class Model:
        config = SimpleNamespace(num_labels=1)

        def to(self, device):
            calls.append(("device", device))

        def eval(self):
            pass

        def __call__(self, **kwargs):
            return SimpleNamespace(logits=Tensor())

    def tokenizer(path, **kwargs):
        calls.append(("tokenizer", kwargs))
        return Tokenizer()

    def model(path, **kwargs):
        calls.append(("model", kwargs))
        return Model()

    from contextlib import nullcontext

    torch = SimpleNamespace(
        set_num_threads=lambda n: None,
        set_num_interop_threads=lambda n: None,
        inference_mode=nullcontext,
    )
    transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer),
        AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=model),
    )
    monkeypatch.setattr("ghimera.reranking_worker.version", lambda name: "operator-pinned")
    original = importlib.import_module
    monkeypatch.setattr(
        "ghimera.reranking_worker.importlib.import_module",
        lambda name: (
            torch if name == "torch" else transformers if name == "transformers" else original(name)
        ),
    )
    backend = TransformersBackend(policy, policy.model_directory)
    assert backend.token_lengths("ports", ("A", "B")) == (2, 2)
    assert backend.predict("ports", ("A", "B")) == (-3.0, -4.0)
    for kind, options in calls:
        if kind in {"tokenizer", "model"}:
            assert options["local_files_only"] is True and options["trust_remote_code"] is False
        elif kind == "tokenize":
            assert options["truncation"] is False
    assert next(options for kind, options in calls if kind == "model")["use_safetensors"] is True
