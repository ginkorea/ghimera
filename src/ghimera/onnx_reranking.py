"""Optional offline ONNX boundary; dynamic library values narrow to native types.

The official ONNX protobuf reader/checker owns graph parsing. Tensor metadata,
tokenizer outputs and CPU logits are checked here before leaving this module.
No external tensor data, custom-op library, hub, model code or provider fallback
is admitted by this recipe.
"""

import importlib
from collections.abc import Iterable, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ghimera.reranking_config import OfflineRerankingConfig, OnnxRerankingRuntime
from ghimera.reranking_types import Finite


class ProtoField(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def type(self) -> int: ...

    @property
    def is_repeated(self) -> bool: ...


class ProtoDescriptor(Protocol):
    @property
    def full_name(self) -> str: ...


@runtime_checkable
class ProtoMessage(Protocol):
    @property
    def DESCRIPTOR(self) -> ProtoDescriptor: ...

    def ListFields(self) -> Sequence[tuple[ProtoField, object]]: ...


def require_standalone_graph(model: object, policy: OnnxRerankingRuntime) -> None:
    """Inspect every protobuf message, including attribute/function/sparse tensors."""
    pending = [(model, 0)]
    count = 0
    while pending:
        message, depth = pending.pop()
        count += 1
        if (
            not isinstance(message, ProtoMessage)
            or depth > policy.max_graph_depth
            or count > policy.max_graph_messages
        ):
            raise ValueError("ONNX graph messages exceed their explicit inspection bounds")
        for field, value in message.ListFields():
            if message.DESCRIPTOR.full_name == "onnx.TensorProto" and (
                field.name == "external_data" or (field.name == "data_location" and value != 0)
            ):
                raise ValueError("ONNX model must be standalone; external tensor data is refused")
            # Stable official protobuf descriptor constant: MESSAGE=11.
            if field.type == 11:
                if field.is_repeated:
                    if not isinstance(value, Iterable):
                        raise ValueError("ONNX repeated graph message has an invalid shape")
                    for child in value:
                        if len(pending) + count >= policy.max_graph_messages:
                            raise ValueError(
                                "ONNX graph messages exceed their explicit inspection bounds"
                            )
                        pending.append((child, depth + 1))
                else:
                    if len(pending) + count >= policy.max_graph_messages:
                        raise ValueError(
                            "ONNX graph messages exceed their explicit inspection bounds"
                        )
                    pending.append((value, depth + 1))


Dimension = Annotated[int, Field(strict=True, gt=0)] | str | None
Token = Annotated[int, Field(strict=True, ge=0)]


class TensorSignature(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    element_type: str
    shape: tuple[Dimension, ...]


def dynamic(dimension: Dimension) -> bool:
    return dimension is None or (isinstance(dimension, str) and bool(dimension.strip()))


def validate_tensor_contract(
    policy: OnnxRerankingRuntime,
    inputs: tuple[TensorSignature, ...],
    outputs: tuple[TensorSignature, ...],
) -> None:
    if (
        len(inputs) != len(policy.input_bindings)
        or {row.name for row in inputs} != {binding.name for binding in policy.input_bindings}
        or any(
            row.element_type != "tensor(int64)"
            or len(row.shape) != 2
            or not all(dynamic(dimension) for dimension in row.shape)
            for row in inputs
        )
        or len(outputs) != 1
        or outputs[0].name != policy.logit_output
        or outputs[0].element_type != "tensor(float)"
        or len(outputs[0].shape) != 2
        or not dynamic(outputs[0].shape[0])
        or outputs[0].shape[1] != 1
    ):
        raise ValueError("ONNX requires exact dynamic int64 pair tensors and one float32 logit")


class OnnxBackend:
    """CPU-only, byte-loaded session with an explicit tokenizer pair recipe."""

    def __init__(self, policy: OfflineRerankingConfig, snapshot: Path) -> None:
        if policy.onnx is None or policy.runtime != "onnx_sequence_classification/1":
            raise ValueError("ONNX backend requires its explicit versioned runtime")
        runtime = policy.onnx
        for package, expected in (
            ("onnx", runtime.onnx_version),
            ("onnxruntime", runtime.onnxruntime_version),
            ("tokenizers", runtime.tokenizers_version),
            ("numpy", runtime.numpy_version),
        ):
            if version(package) != expected:
                raise ValueError("ONNX reranking requires its exact declared runtime versions")
        onnx = importlib.import_module("onnx")
        ort = importlib.import_module("onnxruntime")
        tokenizers = importlib.import_module("tokenizers")
        numpy = importlib.import_module("numpy")
        model_bytes = (snapshot / runtime.model_file).read_bytes()
        if len(model_bytes) > policy.max_artifact_bytes:
            raise ValueError("ONNX model bytes exceed the declared artifact bound")
        model = onnx.load_model_from_string(model_bytes)
        require_standalone_graph(model, runtime)
        onnx.checker.check_model(model, full_check=True)
        options = ort.SessionOptions()
        options.intra_op_num_threads = policy.cpu_threads
        options.inter_op_num_threads = policy.interop_threads
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        levels = {
            "disabled": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
            "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
            "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
            "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
        }
        options.graph_optimization_level = levels[runtime.graph_optimization]
        options.enable_cpu_mem_arena = runtime.enable_cpu_mem_arena
        options.enable_mem_pattern = runtime.enable_mem_pattern
        options.log_severity_level = runtime.log_severity
        session = ort.InferenceSession(
            model_bytes, sess_options=options, providers=[runtime.provider]
        )
        session.disable_fallback()
        if TypeAdapter(tuple[str, ...]).validate_python(session.get_providers()) != (
            runtime.provider,
        ):
            raise ValueError("ONNX session cannot widen its CPU-only provider policy")
        validate_tensor_contract(
            runtime,
            tuple(
                TensorSignature(name=row.name, element_type=row.type, shape=tuple(row.shape))
                for row in session.get_inputs()
            ),
            tuple(
                TensorSignature(name=row.name, element_type=row.type, shape=tuple(row.shape))
                for row in session.get_outputs()
            ),
        )
        tokenizer = tokenizers.Tokenizer.from_file(str(snapshot / runtime.tokenizer_file))
        if tokenizer.token_to_id(runtime.pad_token) != runtime.pad_id:
            raise ValueError("ONNX tokenizer must bind its exact declared padding token/id")
        tokenizer.no_truncation()
        self._policy, self._runtime = policy, runtime
        self._session, self._tokenizer, self._numpy = session, tokenizer, numpy

    def _encoded(
        self, query: str, texts: tuple[str, ...], *, padded: bool
    ) -> tuple[dict[str, tuple[int, ...]], ...]:
        self._tokenizer.no_truncation()
        if self._tokenizer.truncation is not None:
            raise ValueError("ONNX tokenizer cannot implicitly truncate complete pairs")
        self._tokenizer.no_padding()
        if padded:
            self._tokenizer.enable_padding(
                direction=self._runtime.padding_direction,
                pad_id=self._runtime.pad_id,
                pad_type_id=self._runtime.pad_type_id,
                pad_token=self._runtime.pad_token,
                length=None,
                pad_to_multiple_of=None,
            )
        rows = self._tokenizer.encode_batch(
            [(query, text) for text in texts], add_special_tokens=True
        )
        output = tuple(
            {
                source: TypeAdapter(tuple[Token, ...]).validate_python(getattr(row, source))
                for source in ("ids", "attention_mask", "type_ids")
            }
            for row in rows
        )
        if len(output) != len(texts) or any(
            not 0 < len(row["ids"]) <= self._policy.max_pair_tokens
            or any(len(values) != len(row["ids"]) for values in row.values())
            or not set(row["attention_mask"]) <= {0, 1}
            for row in output
        ):
            raise ValueError("ONNX requires every complete bounded pair without token truncation")
        return output

    def token_lengths(self, query: str, texts: tuple[str, ...]) -> tuple[int, ...]:
        return tuple(len(row["ids"]) for row in self._encoded(query, texts, padded=False))

    def predict(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
        if not texts or len(texts) > self._policy.batch_size:
            raise ValueError("ONNX inference requires its explicit nonempty batch bound")
        rows = self._encoded(query, texts, padded=True)
        if len({len(row["ids"]) for row in rows}) != 1:
            raise ValueError("ONNX longest padding must preserve rectangular complete pairs")
        feed = {
            binding.name: self._numpy.asarray(
                [row[binding.source] for row in rows], dtype=self._numpy.int64
            )
            for binding in self._runtime.input_bindings
        }
        returned = self._session.run([self._runtime.logit_output], feed)
        if len(returned) != 1:
            raise ValueError("ONNX returned a different logit output contract")
        raw = returned[0]
        if not isinstance(raw, self._numpy.ndarray) or raw.dtype.name != self._runtime.output_dtype:
            raise ValueError("ONNX requires its actual declared float32 output tensor")
        values = TypeAdapter(tuple[tuple[Finite, ...], ...]).validate_python(raw.tolist())
        if len(values) != len(texts) or any(len(row) != 1 for row in values):
            raise ValueError("ONNX must return one finite raw logit per complete pair")
        return tuple(row[0] for row in values)
