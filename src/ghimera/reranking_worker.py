"""Offline CPU worker: verified data snapshots, no hub downloads or model code.

Optional PyTorch/Transformers objects are a dynamic boundary confined to
TransformersBackend; token counts and logits are immediately narrowed to native
validated values. All other interfaces and durable output remain native types.
"""

import hashlib
import importlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from ghimera.passive_worker import private_directory
from ghimera.reranking_config import OfflineRerankingConfig
from ghimera.reranking_types import Finite, PassageScore, RerankRequest, RerankScores


def check_artifacts(policy: OfflineRerankingConfig) -> None:
    root = policy.model_directory
    if root.resolve() != root or not root.is_dir():
        raise ValueError("model artifacts require a canonical existing local directory")
    declared = {artifact.path for artifact in policy.artifacts}
    actual: set[str] = set()
    parents = {
        str(parent) for name in declared for parent in Path(name).parents if str(parent) != "."
    }
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("offline artifacts cannot follow symlinks")
        relative = path.relative_to(root).as_posix()
        if path.is_dir() and relative not in parents:
            raise ValueError("offline artifact directory is outside the declared inventory")
        if path.is_file():
            if relative not in declared:
                raise ValueError("offline artifact file is outside the declared inventory")
            actual.add(relative)
            total += path.stat().st_size
            if total > policy.max_artifact_bytes:
                raise ValueError("offline artifact bytes exceeded their declared bound")
    if actual != declared or total > policy.max_artifact_bytes:
        raise ValueError("declare the complete bounded local artifact inventory")


@contextmanager
def artifact_snapshot(policy: OfflineRerankingConfig) -> Iterator[Path]:
    """Load only captured bytes whose digests match, not mutable source paths."""
    check_artifacts(policy)
    private_directory(policy.work_directory)
    with tempfile.TemporaryDirectory(prefix="rerank-", dir=policy.work_directory) as temporary:
        root = Path(temporary)
        used = 0
        for artifact in policy.artifacts:
            source = policy.model_directory / artifact.path
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            target = root / artifact.path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            observed = hashlib.sha256()
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("offline artifact must be a regular data file")
                with (
                    os.fdopen(descriptor, "rb", closefd=False) as incoming,
                    target.open("xb") as outgoing,
                ):
                    os.fchmod(outgoing.fileno(), 0o400)
                    while block := incoming.read(65536):
                        used += len(block)
                        if used > policy.max_artifact_bytes:
                            raise ValueError("offline artifact bytes exceeded their declared bound")
                        observed.update(block)
                        outgoing.write(block)
                if observed.hexdigest() != artifact.sha256:
                    raise ValueError("offline model/tokenizer artifact digest mismatch")
            finally:
                os.close(descriptor)
        yield root


class PairBackend(Protocol):
    def token_lengths(self, query: str, texts: tuple[str, ...]) -> tuple[int, ...]: ...

    def predict(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]: ...


class TransformersBackend:
    def __init__(self, policy: OfflineRerankingConfig, snapshot: Path) -> None:
        if (
            version("torch") != policy.torch_version
            or version("transformers") != policy.transformers_version
        ):
            raise ValueError("offline reranking requires its exact declared runtime versions")
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        torch.set_num_threads(policy.cpu_threads)
        torch.set_num_interop_threads(policy.interop_threads)
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(snapshot),
            local_files_only=True,
            trust_remote_code=False,
        )
        self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
            str(snapshot),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        )
        if self._model.config.num_labels != 1:
            raise ValueError(
                "this recipe requires one raw relevance logit, not class probabilities"
            )
        self._model.to(policy.device)
        self._model.eval()
        self._torch = torch

    def token_lengths(self, query: str, texts: tuple[str, ...]) -> tuple[int, ...]:
        encoded = self._tokenizer(
            [query] * len(texts),
            list(texts),
            padding=False,
            truncation=False,
            add_special_tokens=True,
        )
        tokens = TypeAdapter(tuple[tuple[int, ...], ...]).validate_python(encoded["input_ids"])
        return tuple(len(row) for row in tokens)

    def predict(self, query: str, texts: tuple[str, ...]) -> tuple[float, ...]:
        encoded = self._tokenizer(
            [query] * len(texts),
            list(texts),
            padding=True,
            truncation=False,
            add_special_tokens=True,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            raw = self._model(**encoded).logits.detach().cpu().tolist()
        values = TypeAdapter(tuple[tuple[Finite, ...], ...]).validate_python(raw)
        if len(values) != len(texts) or any(len(row) != 1 for row in values):
            raise ValueError("cross-encoder must return exactly one finite logit per pair")
        return tuple(row[0] for row in values)


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["prepare", "score"]
    policy: OfflineRerankingConfig
    request: RerankRequest | None

    @model_validator(mode="after")
    def coherent(self) -> "WorkerRequest":
        if (self.action == "score") != (self.request is not None) or (
            self.request is not None and self.request.policy != self.policy
        ):
            raise ValueError("worker input must bind its exact declared recipe")
        return self


def score_pairs(request: RerankRequest, backend: PairBackend) -> RerankScores:
    texts = tuple(candidate.text for candidate in request.candidates)
    lengths = backend.token_lengths(request.query, texts) if texts else ()
    if len(lengths) != len(texts) or any(
        type(length) is not int or not 0 < length <= request.policy.max_pair_tokens
        for length in lengths
    ):
        raise ValueError(
            "all complete query/document pairs must fit before any inference; no truncation"
        )
    logits: list[float] = []
    size = request.policy.batch_size
    for start in range(0, len(texts), size):
        batch = texts[start : start + size]
        returned = TypeAdapter(tuple[Finite, ...]).validate_python(
            backend.predict(request.query, batch)
        )
        if len(returned) != len(batch):
            raise ValueError("cross-encoder returned partial scores")
        logits.extend(returned)
    result = RerankScores(
        schema="ghimera.rerank-scores/1",
        request_sha256=request.sha256,
        scores=tuple(
            PassageScore(passage_id=candidate.passage_id, logit=logit, pair_tokens=tokens)
            for candidate, logit, tokens in zip(request.candidates, logits, lengths, strict=True)
        ),
    )
    result.validate_request(request)
    return result


def execute(
    envelope: WorkerRequest,
    factory: Callable[[OfflineRerankingConfig, Path], PairBackend] = TransformersBackend,
) -> bytes:
    with artifact_snapshot(envelope.policy) as snapshot:
        backend = factory(envelope.policy, snapshot)
        if envelope.request is None:
            return json.dumps({"ready": envelope.policy.identity}).encode()
        return score_pairs(envelope.request, backend).model_dump_json().encode()


def main() -> None:
    maximum = int(os.environ["GHIMERA_RERANK_REQUEST_BYTES"])
    if maximum <= 0:
        raise ValueError("worker requires a declared positive input byte bound")
    payload = sys.stdin.buffer.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("offline worker input exceeds its exact byte bound")
    envelope = WorkerRequest.model_validate_json(payload)
    if envelope.policy.max_request_bytes != maximum:
        raise ValueError("offline worker input allowance differs from its recipe")
    response = execute(envelope)
    if len(response) > envelope.policy.max_response_bytes:
        raise ValueError("offline worker response exceeds its declared bound")
    sys.stdout.buffer.write(response)


if __name__ == "__main__":
    main()
