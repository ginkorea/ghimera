"""Offline bounded raster decode in the existing owned parser subprocess."""

import hashlib
import io
import sys
import warnings
from typing import Literal

from pydantic import Field

from ghimera.models import Record
from ghimera.passive_worker import BOUND_EXIT
from ghimera.visual_config import VisualConfig


class ImageBoundExceeded(ValueError):
    """An image over an input bound; it cannot be cut, so the image is refused."""


class DecodeRequest(Record):
    config: VisualConfig
    raw: bytes


class DecodedImage(Record):
    image_sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    png: bytes


def decode(request: DecodeRequest) -> DecodedImage:
    from PIL import Image

    config = request.config
    if not request.raw:
        raise ValueError("image is empty")
    if len(request.raw) > config.max_image_bytes:
        raise ImageBoundExceeded(
            f"input bound visuals.max_image_bytes exceeded: {len(request.raw)} over limit "
            f"{config.max_image_bytes}"
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            opened = Image.open(io.BytesIO(request.raw))
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ImageBoundExceeded(f"input bound decoder pixel limit exceeded: {exc}") from None
        with opened as image:
            media: Literal["image/png", "image/jpeg", "image/webp"]
            if image.format == "PNG":
                media = "image/png"
            elif image.format == "JPEG":
                media = "image/jpeg"
            elif image.format == "WEBP":
                media = "image/webp"
            else:
                raise ValueError("unsupported raster format")
            width, height = image.size
            if width * height > config.max_pixels:
                raise ImageBoundExceeded(
                    f"input bound visuals.max_pixels exceeded: {width * height} over limit "
                    f"{config.max_pixels}"
                )
            if (
                media not in config.image_types
                or width < config.min_width
                or height < config.min_height
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("image dimensions/animation/format refused")
            image.load()
            out = io.BytesIO()
            image.convert("RGB").save(out, format="PNG")
            return DecodedImage(
                image_sha256=hashlib.sha256(request.raw).hexdigest(),
                width=width,
                height=height,
                media_type=media,
                png=out.getvalue(),
            )


def main() -> None:
    from ghimera.html_worker import no_network

    # This worker cannot initiate I/O; the parent alone owns the OCR process.
    sys.addaudithook(no_network)
    request = DecodeRequest.model_validate_json(sys.stdin.buffer.read())
    try:
        decoded = decode(request)
    except ImageBoundExceeded as exc:
        # The parent names the bound; only this one sentence crosses stderr.
        sys.stderr.write(str(exc)[:300])
        sys.exit(BOUND_EXIT)
    sys.stdout.write(decoded.model_dump_json())


if __name__ == "__main__":
    main()
