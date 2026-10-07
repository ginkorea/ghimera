"""Bounded WHATWG-style srcset tokenization and explicit collection selection."""

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

from ghimera.responsive_config import ResponsiveImageConfig
from ghimera.visual_types import ResponsiveSelection

ASCII_SPACE = "\t\n\f\r "
FLOAT = re.compile(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
SourceAttribute = Literal["src", "data-src", "srcset", "data-srcset"]


@dataclass(frozen=True)
class Variant:
    url: str
    width: int | None
    density: float | None


@dataclass(frozen=True)
class SourceSet:
    attributes: dict[str, str]
    picture_index: int | None


def safe_image_url(base: str, observed: str) -> str | None:
    try:
        url = urljoin(base, observed)
        parts = urlsplit(url)
        if (
            not observed
            or parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or any(ord(c) < 33 or ord(c) == 127 for c in observed + url)
        ):
            return None
        _ = parts.port
        return url
    except ValueError:
        return None


def _variant(url: str, descriptors: tuple[str, ...]) -> Variant | None:
    width: int | None = None
    density: float | None = None
    height: int | None = None
    for token in descriptors:
        numeric, suffix = token[:-1], token[-1:]
        if suffix in {"w", "h"} and re.fullmatch(r"[0-9]+", numeric):
            # Avoid pathological integer conversion without inventing a width.
            if len(numeric) > 18 or int(numeric) == 0 or density is not None:
                return None
            if suffix == "w":
                if width is not None:
                    return None
                width = int(numeric)
            else:
                if height is not None:
                    return None
                height = int(numeric)
        elif suffix == "x" and FLOAT.fullmatch(numeric):
            if any(value is not None for value in (width, density, height)):
                return None
            density = float(numeric)
            if not math.isfinite(density) or density < 0:
                return None
        else:
            return None
    if height is not None and width is None:
        return None
    return Variant(url, width, density if density is not None or width is not None else 1.0)


def parse_srcset(value: str, policy: ResponsiveImageConfig) -> tuple[Variant, ...]:
    """Preserve commas inside URL tokens; bound attempts, including malformed ones.

    Descriptor tokenization follows the HTML algorithm's whitespace/parenthesis
    states. A malformed candidate is ignored, not treated as a separate URL.
    Exceeding an operator bound refuses the whole set, never a misleading prefix.
    """
    if len(value) > policy.max_attribute_chars:
        raise ValueError("attribute_chars")
    position, attempts = 0, 0
    candidates: list[Variant] = []
    while position < len(value):
        while position < len(value) and value[position] in ASCII_SPACE + ",":
            position += 1
        if position == len(value):
            break
        attempts += 1
        if attempts > policy.max_variants_per_attribute:
            raise ValueError("variant_count")
        start = position
        while position < len(value) and value[position] not in ASCII_SPACE:
            position += 1
        url = value[start:position]
        descriptors: list[str] = []
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            current, state = "", "descriptor"
            while position < len(value):
                character = value[position]
                position += 1
                if state == "parens":
                    current += character
                    if character == ")":
                        state = "descriptor"
                elif character == ",":
                    break
                elif character in ASCII_SPACE:
                    if current:
                        descriptors.append(current)
                        current = ""
                    state = "after"
                else:
                    current += character
                    state = "parens" if character == "(" else "descriptor"
            if current:
                descriptors.append(current)
        parsed = _variant(url, tuple(descriptors))
        if parsed is not None:
            candidates.append(parsed)
    return tuple(candidates)


def selected_sources(
    base: str,
    markup_sha256: str,
    markup_encoding: str,
    image: dict[str, str],
    picture: tuple[SourceSet, ...],
    policy: ResponsiveImageConfig,
    image_types: tuple[str, ...],
    *,
    picture_overflow: bool,
) -> tuple[tuple[str, ResponsiveSelection], ...]:
    """One bounded variant per explicitly eligible source group, with original metadata.

    `all_declared` captures art-directed alternatives as alternatives; their media
    conditions are retained, never asserted to match a current browser viewport.
    """
    output: list[tuple[str, ResponsiveSelection]] = []
    omitted: list[str] = ["picture_source_count"] if picture_overflow else []
    groups = (() if picture_overflow else picture) + (SourceSet(image, None),)
    for group in groups:
        attrs, index = group.attributes, group.picture_index
        media, sizes, content_type = (attrs.get(k, "") for k in ("media", "sizes", "type"))
        if any(len(text) > policy.max_attribute_chars for text in (media, sizes, content_type)):
            omitted.append("metadata_chars")
            continue
        if index is not None and (
            (content_type and content_type not in image_types)
            or (media and policy.picture_media == "unconditional_only")
        ):
            omitted.append("picture_type_or_media")
            continue
        choice: tuple[SourceAttribute, Variant, str] | None = None
        seen = 0
        for srcset_attribute in policy.srcset_attributes:
            value = attrs.get(srcset_attribute, "")
            if not value:
                continue
            try:
                variants = parse_srcset(value, policy)
            except ValueError as exc:
                omitted.append(str(exc))
                continue
            seen = len(variants)
            # Mixed dimensions cannot be honestly ranked without a rendered size.
            if any(item.width is not None for item in variants) and any(
                item.width is None for item in variants
            ):
                omitted.append("mixed_descriptor_space")
                continue
            bounded = [
                (url, item)
                for item in variants
                if (url := safe_image_url(base, item.url)) is not None
                and (
                    (item.width is not None and item.width <= policy.max_declared_width)
                    or (
                        item.density is not None and 0 < item.density <= policy.max_declared_density
                    )
                )
            ]
            if not bounded:
                omitted.append("no_bounded_variant")
                continue
            url, variant = max(bounded, key=lambda pair: pair[1].width or pair[1].density or 0)
            choice = srcset_attribute, variant, url
            break
        if choice is None and index is None:
            for url_attribute in policy.url_attributes:
                value = attrs.get(url_attribute, "")
                if len(value) > policy.max_attribute_chars:
                    omitted.append("attribute_chars")
                    continue
                if url := safe_image_url(base, value):
                    choice = url_attribute, Variant(value, None, None), url
                    seen = 1
                    break
        if choice is None:
            continue
        attribute, variant, url = choice
        output.append(
            (
                url,
                ResponsiveSelection(
                    schema="ghimera.responsive-selection/1",
                    policy_sha256=policy.identity,
                    markup_sha256=markup_sha256,
                    markup_encoding=markup_encoding,
                    attribute=attribute,
                    attribute_sha256=hashlib.sha256(attrs[attribute].encode()).hexdigest(),
                    source_token=variant.url,
                    width=variant.width,
                    density=variant.density,
                    picture_source_index=index,
                    media=media,
                    sizes=sizes,
                    declared_type=content_type,
                    variants_seen=seen,
                    omissions=tuple(dict.fromkeys(omitted)),
                ),
            )
        )
    return tuple(output)
