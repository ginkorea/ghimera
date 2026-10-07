"""Pinned Patchright artifact stream boundary, with no unbounded save_as copy.

Python's public Download API exposes save_as, not incremental reads. This one
adapter deliberately binds Patchright 1.63.0's existing saveAsStream protocol;
dynamic vendor replies are narrowed before use. The browser's own downloading
and disk quota remain operator-owned and are not represented as metered traffic.
"""

import asyncio
import base64
import binascii
import io
import zipfile
from typing import TYPE_CHECKING, Literal, Protocol

from ghimera.document_media import DOCX_TYPE, DocumentMime
from ghimera.refusals import GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from patchright.async_api import Download


class DownloadSpend(Protocol):
    bytes_read: int
    max_bytes: int


def observe_document_media(
    body: bytes, expected: DocumentMime
) -> Literal["pdf_header_at_start", "docx_archive_members"]:
    """Format admission, not a claim that the downstream parser will succeed."""
    if expected == "application/pdf" and body.startswith(b"%PDF-"):
        return "pdf_header_at_start"
    if expected == DOCX_TYPE and body.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                # No decompression here. The document worker owns ZIP expansion limits.
                members = set(archive.namelist())
                if {"[Content_Types].xml", "word/document.xml"} <= members:
                    return "docx_archive_members"
        except (zipfile.BadZipFile, ValueError):
            pass
    raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)


def admit_download_media(
    body: bytes, formats: tuple[DocumentMime, ...]
) -> tuple[DocumentMime, Literal["pdf_header_at_start", "docx_archive_members"]]:
    """Only actual bytes can select one of the operator-admitted formats."""
    for mime in formats:
        try:
            observation = observe_document_media(body, mime)
        except GhimeraRefused:
            continue
        return mime, observation
    raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)


async def read_download(
    download: "Download",
    *,
    max_file_bytes: int,
    chunk_bytes: int,
    spend: DownloadSpend,
    cleanup_timeout_seconds: float,
) -> bytes:
    from patchright._impl._connection import Channel, from_channel
    from patchright._impl._download import Download as DriverDownload
    from patchright._impl._stream import Stream

    impl: object = download._impl_obj
    if not isinstance(impl, DriverDownload):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    reply: object = await impl._artifact._channel.send("saveAsStream", None)
    if not isinstance(reply, Channel):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    stream: object = from_channel(reply)
    if not isinstance(stream, Stream):
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    # Reserve one actual stream byte for detecting an over-limit file. It is
    # charged on refusal, not dropped from the ledger or hidden as an EOF probe.
    limit = min(max_file_bytes, spend.max_bytes - spend.bytes_read - 1)
    body = bytearray()
    try:
        if limit <= 0:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        while True:
            size = min(chunk_bytes, limit - len(body) + 1)
            encoded: object = await stream._channel.send("read", None, {"size": size})
            if not isinstance(encoded, str):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            # Bound the encoded reply before allocating its decoded counterpart.
            if len(encoded) > 4 * ((size + 2) // 3):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
            spend.bytes_read += len(chunk)
            if len(chunk) > size:
                raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
            if not chunk:
                break
            if len(body) + len(chunk) > limit:
                raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
            body.extend(chunk)
        if not body:
            raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
        return bytes(body)
    finally:
        async with asyncio.timeout(cleanup_timeout_seconds):
            await stream._channel.send("close", None)
