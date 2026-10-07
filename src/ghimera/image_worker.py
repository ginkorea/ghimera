"""Offline bounded raster decode in the existing owned parser subprocess."""

import hashlib
import io
import sys
import warnings
from typing import Literal

from pydantic import Field

from ghimera.models import Record
from ghimera.visual_config import VisualConfig


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
    if not request.raw or len(request.raw) > config.max_image_bytes:
        raise ValueError("image byte budget exceeded")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(request.raw)) as image:
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
            if (
                media not in config.image_types
                or width < config.min_width
                or height < config.min_height
                or width * height > config.max_pixels
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
    sys.stdout.write(decode(request).model_dump_json())


if __name__ == "__main__":
    main()
