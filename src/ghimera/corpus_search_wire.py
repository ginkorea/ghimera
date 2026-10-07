"""Original corpus observations own leads; lead snippets are not answer citations."""

import hashlib
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ghimera.corpus_bindings import validate_query
from ghimera.corpus_search_config import CorpusSearchConfig
from ghimera.corpus_types import BoundCorpusDocument, CorpusQuery, CorpusRecord
from ghimera.models import Document

CorpusLeadOmission = Literal[
    "result_limit",
    "minimum_cosine",
    "same_source",
    "non_http_source",
    "original_bytes",
    "response_bytes",
]


def corpus_source_url(url: str) -> bool:
    """Lead syntax only; the research scope and actual fetch still own admission."""
    try:
        parsed = urlsplit(url)
        _ = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not any(ord(char) < 33 or ord(char) == 127 for char in url)
        )
    except ValueError:
        return False


class CorpusSearchWire(CorpusRecord):
    schema_version: Literal["ghimera.corpus-leads/1"] = Field(alias="schema")
    binding_revision: str
    query_text: Annotated[str, Field(min_length=1)]
    query: CorpusQuery
    sources: tuple[Document, ...]
    selected_passages: tuple[Annotated[int, Field(strict=True, gt=0)], ...]
    omissions: tuple[CorpusLeadOmission, ...]

    @model_validator(mode="after")
    def native_sources(self) -> "CorpusSearchWire":
        sources = tuple(BoundCorpusDocument(source) for source in self.sources)
        by_id = {source.identity: source for source in sources}
        hits = {hit.passage_id: hit for hit in self.query.hits}
        if (
            self.query.query_sha256 != hashlib.sha256(self.query_text.encode()).hexdigest()
            or len(by_id) != len(sources)
            or len(set(self.selected_passages)) != len(self.selected_passages)
            or len(set(self.omissions)) != len(self.omissions)
            or not set(self.selected_passages) <= hits.keys()
            or {hits[identity].passage.document_id for identity in self.selected_passages}
            != by_id.keys()
        ):
            raise ValueError("corpus leads require distinct, selected, original source bindings")
        for identity in self.selected_passages:
            hit = hits[identity]
            hit.passage.validate_binding(by_id[hit.passage.document_id])
        return self

    def validate_policy(self, policy: CorpusSearchConfig, query_text: str) -> None:
        query = self.query
        validate_query(
            query,
            query_text,
            corpus_id=policy.corpus_id,
            config_sha256=policy.corpus_config_sha256,
            query_encoder=policy.query_encoder,
        )
        if (
            self.binding_revision != policy.identity[1]
            or self.query_text != query_text
            or len(query_text) > policy.max_query_chars
            or not query_text.strip()
            or len(query.hits) > policy.max_passage_hits
            or len(self.sources) > policy.max_results
            or len(self.selected_passages) > policy.max_results
            or len(self.model_dump_json().encode()) > policy.max_response_bytes
            or any(
                len(source.model_dump_json().encode()) > policy.max_original_bytes
                for source in self.sources
            )
            or any(
                hit.cosine < policy.minimum_cosine
                or (policy.languages and hit.passage.language not in policy.languages)
                for hit in query.hits
                if hit.passage_id in self.selected_passages
            )
        ):
            raise ValueError("corpus leads must bind their exact configured corpus and query model")
