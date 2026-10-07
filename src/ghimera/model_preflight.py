"""Serialize complete semantic review requests without making model calls.

This reuses the configured model's actual request path, including its original
source window, proposal, generated grammar and byte/character limits. The
inspection transport never obtains credentials or returns a model answer.
"""

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from ghimera.config import GhimeraConfig
from ghimera.model_client import SelfHostedModel
from ghimera.model_config import ModelServiceConfig
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Document
from ghimera.semantic_batching import review_selections
from ghimera.semantic_types import SemanticConfig, SemanticProposal
from ghimera.semantic_verification import review_service


@dataclass(frozen=True)
class CompletionRequestFootprint:
    """Local preparation metadata, not model-call or acceptance evidence."""

    request_sha256: str
    request_bytes: int
    input_chars: int


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    content: str


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    messages: tuple[_Message, ...]


class _RequestPrepared(Exception):
    def __init__(self, footprint: CompletionRequestFootprint) -> None:
        self.footprint = footprint
        super().__init__("request prepared without network or model response")


class _InspectionTransport:
    def __init__(self, service: ModelServiceConfig) -> None:
        self._config = service

    @property
    def config(self) -> ModelServiceConfig:
        return self._config

    async def post(self, body: bytes) -> ModelHttpResponse:
        envelope = _Envelope.model_validate_json(body)
        # Stop at the transport boundary. No unavailable/success response is
        # manufactured, and no post-response model validation can run.
        raise _RequestPrepared(
            CompletionRequestFootprint(
                request_sha256=hashlib.sha256(body).hexdigest(),
                request_bytes=len(body),
                input_chars=sum(len(message.content) for message in envelope.messages),
            )
        )


async def preflight_semantic_review(
    config: GhimeraConfig,
    intent: str,
    document: Document,
    start: int,
    end: int,
    policy: SemanticConfig,
    proposal: SemanticProposal,
) -> tuple[CompletionRequestFootprint, ...]:
    """Check every complete review request before a caller admits model work.

    Existing configuration, source, proposal and service checks remain in force.
    An oversized later partition refuses the complete preflight, even if earlier
    requests fit. No budget is spent, model call recorded, credential discovered,
    network contacted or graph projected. Sizes are characters/bytes, not a
    tokenizer measurement or proof of model context capacity or factual quality.
    """
    config = GhimeraConfig.model_validate(config.model_dump())
    service = review_service(config)
    model = SelfHostedModel(config, service, http=_InspectionTransport(service))
    verification = policy.verification
    selections = (
        review_selections(verification, proposal)
        if verification is not None
        and verification.schema_version == "ghimera.semantic-verification/4"
        else (None,)
    )
    footprints = []
    for selection in selections:
        try:
            if selection is None:
                await model.semantic_review(intent, document, start, end, policy, proposal)
            else:
                await model.semantic_review_part(
                    intent, document, start, end, policy, proposal, selection
                )
        except _RequestPrepared as prepared:
            footprints.append(prepared.footprint)
        else:
            raise AssertionError("inspection transport cannot return a model answer")
    return tuple(footprints)
