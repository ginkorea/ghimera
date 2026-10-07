"""Bounded same-origin fetch in the caller's page; no session material exported.

The second GET is explicit policy, not an assertion that bytes observed during
navigation were intercepted. Browser network bytes remain unknown. Only this
operation's AbortController is cancelled; the borrowed browser stays open.
"""

import asyncio
import base64
import binascii
import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ghimera.browser_download_stream import DownloadSpend, admit_download_media
from ghimera.document_media import DocumentMime
from ghimera.refusals import GhimeraRefused, RefusalCode

if TYPE_CHECKING:
    from patchright.async_api import Page


class InlineReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["received"]
    url: str
    document_url: str
    response_status: int = Field(strict=True)
    response_content_type: str = Field(max_length=512)
    body_base64: str
    complete: bool = Field(strict=True)


class InlineFailureReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["refused"]
    reason: Literal["fetch_failed"]


class InlineRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    body: bytes
    reply: InlineReply
    content_type: DocumentMime
    media_observation: Literal["pdf_header_at_start", "docx_archive_members"]


async def read_inline(
    page: "Page",
    url: str,
    *,
    formats: tuple[DocumentMime, ...],
    max_file_bytes: int,
    spend: DownloadSpend,
    timeout_seconds: float,
    cleanup_timeout_seconds: float,
) -> InlineRead:
    limit = min(max_file_bytes, spend.max_bytes - spend.bytes_read - 1)
    if limit <= 0 or timeout_seconds <= 0:
        raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
    key = "ghimera_inline_" + uuid.uuid4().hex
    try:
        observed: object = await page.evaluate(
            """async ({url, cap, formats, key, timeout_ms}) => {
                if (document.URL !== url || new URL(url).origin !== location.origin)
                    throw new Error('inline source changed');
                const controller = new AbortController();
                Object.defineProperty(globalThis, key, {value:controller, configurable:true});
                const timer = setTimeout(()=>controller.abort(), timeout_ms);
                let reader;
                try {
                    let response;
                    try {
                        response = await fetch(url, {
                            credentials:'same-origin', redirect:'error', signal:controller.signal
                        });
                    } catch {
                        return {outcome:'refused', reason:'fetch_failed'};
                    }
                    const header = response.headers.get('content-type') || '';
                    const reply = {outcome:'received', url:response.url, document_url:document.URL,
                        response_status:response.status, response_content_type:header,
                        body_base64:'', complete:false};
                    if (response.url !== url || document.URL !== url || response.status !== 200
                        || !formats.includes(header.split(';',1)[0].trim().toLowerCase()))
                        return reply;
                    if (!response.body) return reply;
                    reader = response.body.getReader();
                    const output = new Uint8Array(cap + 1);
                    let length = 0, complete = false;
                    while (true) {
                        let item;
                        try { item = await reader.read(); } catch { break; }
                        if (item.done) { complete = true; break; }
                        const kept = item.value.subarray(0, cap + 1 - length);
                        output.set(kept, length);
                        length += kept.length;
                        if (length > cap) break;
                    }
                    let binary = '';
                    for (let i=0; i<length; i+=4096) {
                        const part = output.subarray(i, Math.min(length,i+4096));
                        binary += String.fromCharCode(...part);
                    }
                    reply.body_base64 = btoa(binary);
                    reply.complete = complete;
                    reply.document_url = document.URL;
                    return reply;
                } finally {
                    clearTimeout(timer);
                    controller.abort();
                    delete globalThis[key];
                    if (reader) {
                        // Aborting an owned stream can make cancel reject.
                        // That cleanup must not replace the observed body/refusal.
                        try { await reader.cancel(); } catch {} finally { reader.releaseLock(); }
                    }
                }
            }""",
            dict(
                url=url,
                cap=limit,
                formats=list(formats),
                key=key,
                timeout_ms=timeout_seconds * 1000,
            ),
            isolated_context=True,
        )
        boundary: TypeAdapter[InlineReply | InlineFailureReply] = TypeAdapter(
            Annotated[InlineReply | InlineFailureReply, Field(discriminator="outcome")]
        )
        reply = boundary.validate_python(observed)
        if isinstance(reply, InlineFailureReply):
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        if len(reply.body_base64) > 4 * ((limit + 3) // 3):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        try:
            body = base64.b64decode(reply.body_base64, validate=True)
        except (ValueError, binascii.Error):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT) from None
        spend.bytes_read += len(body)
        if reply.document_url != url or reply.url != url or page.url != url:
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if reply.response_status != 200:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        if reply.response_content_type.split(";", 1)[0].strip().lower() not in formats:
            raise GhimeraRefused(RefusalCode.CONTENT_TYPE_UNWANTED)
        if len(body) > limit:
            raise GhimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        if not reply.complete:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        content_type, media_observation = admit_download_media(body, formats)
        return InlineRead(
            body=body, reply=reply, content_type=content_type, media_observation=media_observation
        )
    finally:
        # A cancelled driver call need not cancel JavaScript. Explicitly abort
        # this one operation, without closing pages or navigating another target.
        task = asyncio.create_task(
            page.evaluate(
                """key => {
                    const own=globalThis[key]; if(own) own.abort(); delete globalThis[key];
                }""",
                key,
                isolated_context=True,
            )
        )
        try:
            async with asyncio.timeout(cleanup_timeout_seconds):
                await asyncio.shield(task)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
