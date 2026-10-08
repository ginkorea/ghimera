"""Owned network-disabled PDF raster/crop worker; no model or OCR side channel."""

import io
import math
import sys

from ghimera.html_worker import no_network
from ghimera.page_render_worker import render
from ghimera.page_renderer import PageRenderRequest
from ghimera.pdf_figures import FigureCrop, FigureCropRequest, FigureCropResult, figure_anchor
from ghimera.visual_types import ImageCandidate


def crop_figures(request: FigureCropRequest) -> FigureCropResult:
    from PIL import Image

    if not 0 < len(request.selections) <= request.config.max_figures:
        raise ValueError("figure selection count exceeds the admitted crop budget")
    rendered = render(PageRenderRequest(config=request.config.renderer, pdf=request.pdf))
    result: list[FigureCrop] = []
    for selection in request.selections:
        if selection.page_index >= len(rendered.pages):
            raise ValueError("figure refers to a missing PDF page")
        page = rendered.pages[selection.page_index]
        region = selection.region
        bounds = (
            math.floor(region.left * page.width),
            math.floor(region.top * page.height),
            math.ceil(region.right * page.width),
            math.ceil(region.bottom * page.height),
        )
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if width * height > request.config.max_crop_pixels:
            raise ValueError("figure crop pixel bound exceeded before allocation")
        with Image.open(io.BytesIO(page.png)) as image:
            cropped = image.crop(bounds)
            try:
                output = io.BytesIO()
                cropped.save(output, "PNG")
                raw = output.getvalue()
            finally:
                cropped.close()
        if len(raw) > request.config.max_crop_bytes:
            raise ValueError("figure crop byte bound exceeded")
        result.append(
            FigureCrop(
                candidate=ImageCandidate(
                    url=request.source_url + "#ghimera-figure=" + str(selection.picture_index),
                    parent_url=request.source_url,
                    parent_sha256=rendered.source_sha256,
                    element_index=selection.picture_index,
                    caption=selection.caption,
                    attributes="native_pdf_figure",
                    declared_width=width,
                    declared_height=height,
                    pdf_crop=figure_anchor(request, selection, page.image_sha256),
                ),
                png=raw,
            )
        )
    return FigureCropResult(crops=tuple(result))


def main() -> None:
    sys.addaudithook(no_network)
    request = FigureCropRequest.model_validate_json(sys.stdin.buffer.read())
    response = crop_figures(request).model_dump_json().encode()
    if len(response) > request.config.renderer.max_output_bytes:
        raise ValueError("figure crop worker output budget exceeded")
    sys.stdout.buffer.write(response)


if __name__ == "__main__":
    main()
