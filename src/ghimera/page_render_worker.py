"""Offline PDF pixels only; no inference, remote source or model downloads."""

import hashlib
import io
import math
import sys
from importlib.metadata import version

from ghimera.html_worker import no_network
from ghimera.page_renderer import PageRenderRequest
from ghimera.page_transcription_types import RenderedPdf, RenderedPdfPage


def render(request: PageRenderRequest) -> RenderedPdf:
    import pypdfium2

    config = request.config
    if (
        version("pypdfium2") != config.renderer_package_version
        or version("Pillow") != config.image_package_version
        or not request.pdf.startswith(b"%PDF-")
        or len(request.pdf) > config.max_input_bytes
    ):
        raise ValueError("offline renderer identity or input refused")
    source = hashlib.sha256(request.pdf).hexdigest()
    policy = config.content_digest()
    pages: list[RenderedPdfPage] = []
    total = 0
    document = pypdfium2.PdfDocument(request.pdf)
    try:
        count = len(document)
        if not 0 < count <= config.max_pages:
            raise ValueError("PDF page bound exceeded")
        for index in range(count):
            page = document[index]
            try:
                width, height = page.get_size()
                pixels = math.ceil(width * config.scale) * math.ceil(height * config.scale)
                if pixels <= 0 or pixels > config.max_pixels_per_page:
                    raise ValueError("rendered page pixel bound exceeded before allocation")
                bitmap = page.render(scale=config.scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                        raw = output.getvalue()
                        actual_width, actual_height = image.size
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                total += len(raw)
                if len(raw) > config.max_image_bytes or total > config.max_total_image_bytes:
                    raise ValueError("rendered image byte bound exceeded")
                pages.append(
                    RenderedPdfPage(
                        schema="ghimera.rendered-pdf-page/1",
                        source_sha256=source,
                        policy_sha256=policy,
                        page_index=index,
                        page_count=count,
                        width=actual_width,
                        height=actual_height,
                        scale=config.scale,
                        image_sha256=hashlib.sha256(raw).hexdigest(),
                        png=raw,
                    )
                )
            finally:
                page.close()
    finally:
        document.close()
    return RenderedPdf(source_sha256=source, policy_sha256=policy, pages=tuple(pages))


def main() -> None:
    sys.addaudithook(no_network)
    request = PageRenderRequest.model_validate_json(sys.stdin.buffer.read())
    response = render(request).model_dump_json().encode()
    if len(response) > request.config.max_output_bytes:
        raise ValueError("render response bound exceeded")
    sys.stdout.buffer.write(response)


if __name__ == "__main__":
    main()
