"""Explicit binary-PDF admission; original source declarations remain evidence."""

import hashlib
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.refusals import GhimeraRefused, RefusalCode

DOCX_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DocumentMime = Literal[
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
]
DownloadMime = Literal["application/octet-stream", "binary/octet-stream"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DocumentMediaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.document-media/1"] = Field(alias="schema")
    pdf_download_types: tuple[DownloadMime, ...]

    @model_validator(mode="after")
    def unique_types(self) -> "DocumentMediaConfig":
        if len(set(self.pdf_download_types)) != len(self.pdf_download_types):
            raise ValueError("PDF download media types cannot repeat")
        return self

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json(by_alias=True).encode()).hexdigest()


class DocumentMediaEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.document-media-evidence/1"] = Field(alias="schema")
    declared_content_type: Annotated[str, Field(min_length=1)]
    resolved_content_type: DocumentMime
    method: Literal["declared", "pdf_header_at_start"]
    source_sha256: Digest
    config_digest: Digest

    @model_validator(mode="after")
    def declaration_binding(self) -> "DocumentMediaEvidence":
        mime = self.declared_content_type.split(";", 1)[0].strip().lower()
        if self.method == "declared":
            if mime != self.resolved_content_type:
                raise ValueError("declared media resolution must retain the source declaration")
        elif mime not in {"application/octet-stream", "binary/octet-stream"} or (
            self.resolved_content_type != "application/pdf"
        ):
            raise ValueError("PDF-header admission belongs only to explicit binary downloads")
        return self

    def validate_source(self, body: bytes) -> None:
        if hashlib.sha256(body).hexdigest() != self.source_sha256 or (
            self.method == "pdf_header_at_start" and not body.startswith(b"%PDF-")
        ):
            raise ValueError("media resolution must bind the retained source bytes and PDF header")

    def validate_policy(self, policy: DocumentMediaConfig, body: bytes) -> None:
        try:
            _, expected = resolve_document_media(self.declared_content_type, body, policy)
        except GhimeraRefused:
            raise ValueError("media resolution is outside the effective policy") from None
        if expected != self:
            raise ValueError("media resolution must bind the effective policy and source bytes")


def resolve_document_media(
    content_type: str, body: bytes, policy: DocumentMediaConfig | None
) -> tuple[DocumentMime, DocumentMediaEvidence | None]:
    """Resolve only configured types; never infer from URL or change source metadata.

    The PDF header is admission, not proof of a valid or safe PDF. The pinned
    parser still validates the entire document under its own resource bounds.
    Generic ZIP/DOCX sniffing is deliberately not part of this policy.
    """
    mime = content_type.split(";", 1)[0].strip().lower()
    resolved: DocumentMime
    method: Literal["declared", "pdf_header_at_start"] = "declared"
    if mime == "application/pdf":
        resolved = "application/pdf"
    elif mime == DOCX_TYPE:
        resolved = DOCX_TYPE
    elif policy is not None and mime in policy.pdf_download_types and body.startswith(b"%PDF-"):
        resolved, method = "application/pdf", "pdf_header_at_start"
    else:
        raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
    evidence = (
        DocumentMediaEvidence(
            schema="ghimera.document-media-evidence/1",
            declared_content_type=content_type,
            resolved_content_type=resolved,
            method=method,
            source_sha256=hashlib.sha256(body).hexdigest(),
            config_digest=policy.content_digest(),
        )
        if policy is not None
        else None
    )
    return resolved, evidence
