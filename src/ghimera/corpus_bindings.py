"""Shared corpus-reader admission and original query/model binding invariants."""

import hashlib

from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_types import CorpusQuery
from ghimera.model_config import EmbeddingServiceConfig


def validate_reader(
    corpus: EvidenceCorpus,
    *,
    corpus_id: str,
    config_sha256: str,
    query_encoder: EmbeddingServiceConfig,
    max_query_chars: int,
    max_passage_hits: int,
    minimum_cosine: float,
) -> None:
    corpus.check_ready()
    if (
        corpus.identity != corpus_id
        or corpus.config.identity != config_sha256
        or corpus.config.query_encoder != query_encoder
        or max_query_chars > corpus.config.max_query_chars
        or max_passage_hits > corpus.config.max_top_k
        or minimum_cosine < corpus.config.minimum_cosine
    ):
        raise ValueError("corpus reader requires its exact admitted corpus, model and bounds")


def validate_query(
    query: CorpusQuery,
    text: str,
    *,
    corpus_id: str,
    config_sha256: str,
    query_encoder: EmbeddingServiceConfig,
) -> None:
    encoded = query_encoder.text_prefix + text
    if (
        query.corpus_id != corpus_id
        or query.config_sha256 != config_sha256
        or query.query_sha256 != hashlib.sha256(text.encode()).hexdigest()
        or query.encoding_call.service != query_encoder
        or query.encoding_call.input_sha256 != (hashlib.sha256(encoded.encode()).hexdigest(),)
        or query.encoding_call.input_chars != len(encoded)
    ):
        raise ValueError("corpus evidence requires its exact query and encoding observation")
