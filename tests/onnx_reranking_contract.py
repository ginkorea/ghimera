"""Finite actual CPU/child contract with an untrained tiny graph, not model quality.

Run explicitly in an owned environment containing the configured optional ONNX,
ONNX Runtime, tokenizers and NumPy libraries. No pytest/repository test imports.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import onnx
from onnx import TensorProto, helper
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from ghimera.offline_reranking import OfflineCrossEncoder
from ghimera.onnx_reranking import require_standalone_graph
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.reranking_config import OfflineRerankingConfig
from ghimera.reranking_types import RerankCandidate, RerankRequest


def create_fixture(root: Path) -> OfflineRerankingConfig:
    model_directory = root / "model"
    model_directory.mkdir(mode=0o700)
    nodes = (
        helper.make_node("Cast", ["input_ids"], ["ids_float"], to=TensorProto.FLOAT),
        helper.make_node("Cast", ["attention_mask"], ["mask_float"], to=TensorProto.FLOAT),
        helper.make_node("Mul", ["ids_float", "mask_float"], ["masked_ids"]),
        helper.make_node("ReduceSum", ["masked_ids"], ["total"], axes=[1], keepdims=1),
        helper.make_node("Neg", ["total"], ["logits"]),
    )
    graph = helper.make_graph(
        nodes,
        "untrained_cpu_contract",
        [
            helper.make_tensor_value_info(name, TensorProto.INT64, ["batch", "sequence"])
            for name in ("input_ids", "attention_mask")
        ],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 1])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 12)])
    model.ir_version = 10  # Deliberate older-format fixture, not an operator model choice.
    (model_directory / "model.onnx").write_bytes(model.SerializeToString())
    tokenizer = Tokenizer(
        models.WordLevel(
            {
                "[PAD]": 0,
                "[UNK]": 1,
                "[CLS]": 2,
                "[SEP]": 3,
                "ports": 4,
                "ship": 5,
                "airport": 6,
                "water": 7,
            },
            unk_token="[UNK]",
        )
    )
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    tokenizer.save(str(model_directory / "tokenizer.json"))
    return OfflineRerankingConfig.model_validate(
        dict(
            schema="ghimera.offline-reranking/2",
            runtime="onnx_sequence_classification/1",
            model_id="untrained-cpu-contract",
            revision="fixture/1",
            device="cpu",
            score_semantics="single_relevance_logit",
            input_recipe="query_document_pair/1",
            model_directory=model_directory,
            work_directory=root / "worker",
            worker_python=sys.executable,
            artifacts=[
                dict(
                    path=name,
                    role=role,
                    sha256=hashlib.sha256((model_directory / name).read_bytes()).hexdigest(),
                )
                for name, role in (("model.onnx", "model"), ("tokenizer.json", "tokenizer"))
            ],
            max_artifact_bytes=1048576,
            cpu_threads=1,
            interop_threads=1,
            batch_size=2,
            max_workers=1,
            max_pairs=2,
            max_pair_tokens=16,
            max_input_chars=1000,
            max_request_bytes=65536,
            max_response_bytes=65536,
            max_diagnostic_bytes=8192,
            timeout_seconds=15.0,
            cleanup_timeout_seconds=5.0,
            max_passages_per_document=1,
            max_source_documents=2,
            onnx=dict(
                schema="ghimera.onnx-reranking-runtime/1",
                onnx_version=version("onnx"),
                onnxruntime_version=version("onnxruntime"),
                tokenizers_version=version("tokenizers"),
                numpy_version=version("numpy"),
                model_file="model.onnx",
                tokenizer_file="tokenizer.json",
                provider="CPUExecutionProvider",
                input_dtype="int64",
                output_dtype="float32",
                logit_output="logits",
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
                max_graph_depth=64,
                max_graph_messages=1000,
                input_bindings=[
                    dict(name="input_ids", source="ids"),
                    dict(name="attention_mask", source="attention_mask"),
                ],
            ),
        )
    )


async def verify(root: Path) -> dict[str, object]:
    policy = create_fixture(root)
    candidates = tuple(
        RerankCandidate(
            passage_id=index,
            document_id=str(index) * 64,
            passage_sha256="f" * 64,
            text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            cosine=1.0,
            language="en",
        )
        for index, text in ((1, "ship"), (2, "airport water"))
    )
    request = RerankRequest(
        schema="ghimera.rerank-request/1",
        policy=policy,
        corpus_id="0" * 32,
        config_sha256="a" * 64,
        generation=1,
        query="ports",
        candidates=candidates,
    )
    adapter = OfflineCrossEncoder(policy)
    await adapter.prepare()
    result = await adapter.score(request)
    result.validate_request(request)
    assert tuple(row.logit for row in result.scores) == (-17.0, -25.0)
    assert tuple(row.pair_tokens for row in result.scores) == (5, 6)
    assert type(result).model_validate_json(result.model_dump_json()) == result

    # A real protobuf nested tensor is refused before the session can read data.
    model = onnx.load_model_from_string((policy.model_directory / "model.onnx").read_bytes())
    external = model.graph.initializer.add()
    external.name, external.data_type, external.data_location = (
        "external",
        TensorProto.FLOAT,
        TensorProto.EXTERNAL,
    )
    external.dims.append(1)
    pair = external.external_data.add()
    pair.key, pair.value = "location", "not-admitted.data"
    try:
        require_standalone_graph(model, policy.onnx)
    except ValueError as failure:
        assert "external tensor data" in str(failure)
    else:
        raise AssertionError("external data must refuse before ONNX Runtime loading")

    # The actual worker must not reuse the first successful artifact admission.
    (policy.model_directory / "model.onnx").write_bytes(b"changed after admitted preparation")
    try:
        await adapter.prepare()
    except GhimeraRefused as failure:
        assert failure.code == RefusalCode.MODEL_UNAVAILABLE
    else:
        raise AssertionError("changed pinned bytes must refuse")
    return dict(
        schema="ghimera.controlled-onnx-contract/1",
        fixture_only=True,
        cases_passed=3,
        python=sys.version.split()[0],
        interpreter=sys.executable,
        runtime_versions={
            name: version(name) for name in ("onnx", "onnxruntime", "tokenizers", "numpy")
        },
        policy_sha256=policy.identity,
        scores=result.model_dump(),
        quality_claim=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_directory
    if not root.is_absolute() or root.exists():
        raise ValueError("controlled contract requires a fresh explicit absolute owned directory")
    root.mkdir(mode=0o700)
    print(json.dumps(asyncio.run(verify(root)), sort_keys=True))


if __name__ == "__main__":
    main()
