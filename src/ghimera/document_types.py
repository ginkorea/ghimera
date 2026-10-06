"""Typed conversion provenance and retained vendor structure with a read boundary."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.document_media import DOCX_TYPE, DocumentMediaEvidence

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]


class DoclingHeader(BaseModel):
    """Envelope reader only; Docling owns full layout validation in the worker.

    The exact vendor JSON is retained, not interpreted as arbitrary native data.
    Consumers needing its full tree must use the pinned Docling reader.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)
    schema_name: Literal["DoclingDocument"]
    version: Annotated[str, Field(pattern=r"^1\.[0-9]+\.[0-9]+$")]
    name: str


class DocumentLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.document-layout/1"] = Field(alias="schema")
    docling_json: str
    sha256: Digest

    @model_validator(mode="after")
    def structure_binding(self) -> "DocumentLayout":
        DoclingHeader.model_validate_json(self.docling_json)
        if hashlib.sha256(self.docling_json.encode()).hexdigest() != self.sha256:
            raise ValueError("layout digest must bind the exact retained vendor document")
        return self


class DocumentParseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.document-parse/1", "chimera.document-parse/2"] = Field(
        alias="schema"
    )
    media: DocumentMediaEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_sha256: Digest
    source_url: str
    text_sha256: Digest
    layout_sha256: Digest
    config_digest: Digest
    parser_revision: str
    pipeline: Literal["docx", "native", "standard"]
    title_source: Literal["heading", "first_text"]
    page_count: Count
    table_count: Count
    artifact_manifest_digest: Digest | None
    language_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_margin: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    language_sample_chars: Count
    omitted_links: Count

    @model_validator(mode="after")
    def media_binding(self) -> "DocumentParseEvidence":
        if (self.schema_version == "chimera.document-parse/2") != (self.media is not None):
            raise ValueError("document-parse/2 requires media evidence; /1 retains its old shape")
        if self.media is not None and (
            self.media.source_sha256 != self.source_sha256
            or (self.media.resolved_content_type == DOCX_TYPE) != (self.pipeline == "docx")
        ):
            raise ValueError("document media must bind the parser's exact source and format")
        return self
