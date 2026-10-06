"""Injected collaborators, never an implicit model, transport, or TAIPAN import."""

from typing import Protocol

from ghimera.config import GhimeraConfig
from ghimera.embedding_types import EncodingBatch
from ghimera.model_config import EmbeddingServiceConfig
from ghimera.models import (
    Document,
    Extracted,
    Goal,
    Grade,
    Harvest,
    ModelIdentity,
    Page,
    Scope,
    Verdict,
)


class Judge(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def document(self, goal: Goal, document: Extracted, *, second_look: bool) -> Verdict: ...

    async def grade(self, goal: Goal, documents: tuple[Document, ...]) -> Grade: ...


class Encoder(Protocol):
    @property
    def model(self) -> ModelIdentity: ...

    async def encode(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class EvidenceEncoder(Encoder, Protocol):
    @property
    def config(self) -> EmbeddingServiceConfig: ...

    async def encode_batch(self, texts: tuple[str, ...]) -> EncodingBatch: ...


class Extractor(Protocol):
    @property
    def revision(self) -> str: ...

    def validate_config(self, config: GhimeraConfig) -> None: ...

    async def extract(self, page: Page) -> Extracted: ...


class Spider(Protocol):
    async def run(self, goal: Goal, scope: Scope) -> Harvest: ...
