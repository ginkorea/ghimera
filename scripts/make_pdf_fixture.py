"""Controlled raster-only PDF; not a representative publisher-quality corpus."""

import argparse
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make_scan(font: Path, output: Path) -> None:
    image = Image.new("RGB", (1500, 1900), "white")
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(font), 32)
    title = ImageFont.truetype(str(font), 52)
    draw.text((70, 70), "Port infrastructure report", font=title, fill="black")
    left = (
        "Maritime infrastructure matters.",
        "Ports connect trade routes.",
        "Taiwan develops shipping capacity.",
        "Analysts examine source documents.",
        "This section comes first.",
    )
    right = (
        "The second section examines",
        "terminal investment and logistics.",
        "Japan maintains shipping routes.",
        "Reports include original tables.",
        "This section comes second.",
    )
    for x, lines in ((70, left), (790, right)):
        for index, line in enumerate(lines):
            draw.text((x, 200 + index * 60), line, font=face, fill="black")
    draw.text((70, 600), "Reported shipping capacity", font=title, fill="black")
    for y in (720, 860, 1000, 1140):
        draw.line((100, y, 1300, y), fill="black", width=4)
    for x in (100, 720, 1300):
        draw.line((x, 720, x, 1140), fill="black", width=4)
    for y, values in zip(
        (760, 900, 1040),
        (("Location", "Capacity"), ("Taiwan", "42"), ("Japan", "19")),
        strict=True,
    ):
        for x, value in zip((150, 780), values, strict=True):
            draw.text((x, y), value, font=face, fill="black")
    # Fixed fixture dates prevent time-of-run PDF metadata from changing bytes.
    with output.open("xb") as stream:
        image.save(
            stream,
            format="PDF",
            resolution=144,
            creationDate=time.gmtime(0),
            modDate=time.gmtime(0),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    make_scan(args.font, args.output)
