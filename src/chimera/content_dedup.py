"""Deterministic Unicode SimHash index. Never treats a claimed URL as identity."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from chimera.dedup_config import DedupConfig
from chimera.dedup_types import ContentDrift, ContentFingerprint, DedupEvidence
from chimera.refusals import ChimeraRefused, RefusalCode

if TYPE_CHECKING:
    from chimera.models import DocumentSource


def canonical_url(url: str, policy: DedupConfig) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(ord(char) < 33 for char in url)
    ):
        raise ValueError("canonical identity requires an ordinary unauthenticated HTTP URL")
    host = parts.hostname.lower()
    port = parts.port
    netloc = (
        host
        if port is None or port == (443 if parts.scheme == "https" else 80)
        else f"{host}:{port}"
    )
    pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in policy.tracking_parameters
        and not any(key.lower().startswith(prefix) for prefix in policy.tracking_prefixes)
    ]
    return urlunsplit((parts.scheme, netloc, parts.path or "/", urlencode(pairs), ""))


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def simhash(text: str, shingle_chars: int) -> int:
    votes = [0] * 64
    features = Counter(
        text[start : start + shingle_chars]
        for start in range(max(1, len(text) - shingle_chars + 1))
    )
    for feature, weight in features.items():
        value = int.from_bytes(hashlib.sha256(feature.encode()).digest()[:8], "big")
        for bit in range(64):
            votes[bit] += weight if value & (1 << bit) else -weight
    return sum(1 << bit for bit, vote in enumerate(votes) if vote > 0)


def fingerprint(document: DocumentSource, policy: DedupConfig) -> ContentFingerprint:
    if len(document.extracted.text) > policy.max_text_chars:
        raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
    text = normalized(document.extracted.text)
    # A source's rel=canonical is untrusted metadata, not authority to alias hosts.
    url = document.url
    hint = document.extracted.canonical_url
    if hint is not None:
        try:
            canonical_url(hint, policy)
        except ValueError:
            pass
        else:
            if urlsplit(hint).hostname == urlsplit(url).hostname:
                url = hint
    return ContentFingerprint(
        schema="chimera.content-fingerprint/1",
        revision="unicode-char-simhash64@1",
        source_sha256=document.sha256,
        normalized_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        simhash=f"{simhash(text, policy.shingle_chars):016x}",
        normalized_chars=len(text),
        language=document.extracted.language,
        canonical_url=canonical_url(url, policy),
        config_digest=policy.content_digest(),
    )


class ContentIndex:
    """Run-owned index; fixed-size disjoint bands preserve Hamming candidate recall."""

    def __init__(self, policy: DedupConfig) -> None:
        self.policy = policy
        self._documents: dict[str, DocumentSource] = {}
        self._fingerprints: dict[str, ContentFingerprint] = {}
        self._texts: dict[tuple[str, str], str] = {}
        self._bands: dict[tuple[str, int, int], set[str]] = {}
        self._urls: dict[str, ContentFingerprint] = {}
        self._positions: dict[str, int] = {}

    def _keys(self, item: ContentFingerprint) -> tuple[tuple[str, int, int], ...]:
        count = self.policy.max_hamming_distance + 1
        value = int(item.simhash, 16)
        return tuple(
            (
                item.language,
                band,
                (value >> (band * 64 // count))
                & ((1 << (((band + 1) * 64 // count) - band * 64 // count)) - 1),
            )
            for band in range(count)
        )

    def _evidence(
        self, current: ContentFingerprint, previous: ContentFingerprint, reason: str
    ) -> DedupEvidence:
        return DedupEvidence.model_validate(
            dict(
                schema="chimera.dedup-evidence/1",
                reason=reason,
                representative_sha256=previous.source_sha256,
                current=current,
                hamming_distance=(int(current.simhash, 16) ^ int(previous.simhash, 16)).bit_count(),
                config_digest=self.policy.content_digest(),
            )
        )

    def match(self, document: DocumentSource) -> DedupEvidence | None:
        current = fingerprint(document, self.policy)
        if current.source_sha256 in self._fingerprints:
            return self._evidence(
                current, self._fingerprints[current.source_sha256], "content_sha256"
            )
        exact = self._texts.get((current.language, current.normalized_text_sha256))
        if exact is not None:
            return self._evidence(current, self._fingerprints[exact], "normalized_text")
        if current.normalized_chars < self.policy.min_near_chars or current.language == "und":
            return None
        candidates: set[str] = set()
        for band_key in self._keys(current):
            candidates.update(self._bands.get(band_key, ()))
        ranked = sorted(
            candidates,
            key=lambda key: (
                (int(current.simhash, 16) ^ int(self._fingerprints[key].simhash, 16)).bit_count(),
                self._positions[key],
            ),
        )
        for key in ranked:
            previous = self._fingerprints[key]
            distance = (int(current.simhash, 16) ^ int(previous.simhash, 16)).bit_count()
            if distance > self.policy.max_hamming_distance:
                break
            if previous.normalized_chars < self.policy.min_near_chars:
                continue
            if (
                min(current.normalized_chars, previous.normalized_chars)
                / max(current.normalized_chars, previous.normalized_chars)
                < self.policy.min_length_ratio
            ):
                continue
            # A changed number is substantive evidence, even beside near-identical prose.
            if re.findall(r"\d+(?:[.,]\d+)*", normalized(document.extracted.text)) != re.findall(
                r"\d+(?:[.,]\d+)*", normalized(self._documents[key].extracted.text)
            ):
                continue
            return self._evidence(current, previous, "simhash")
        return None

    def drift(self, document: DocumentSource) -> ContentDrift | None:
        current = fingerprint(document, self.policy)
        previous = self._urls.get(current.canonical_url)
        if previous is None or previous.source_sha256 == current.source_sha256:
            return None
        return ContentDrift(
            schema="chimera.content-drift/1",
            canonical_url=current.canonical_url,
            previous_sha256=previous.source_sha256,
            current_sha256=current.source_sha256,
            reason="changed_native_text"
            if previous.normalized_text_sha256 != current.normalized_text_sha256
            else "changed_source_bytes",
            config_digest=self.policy.content_digest(),
        )

    def add(self, document: DocumentSource) -> None:
        if document.sha256 in self._documents:
            return
        if len(self._documents) >= self.policy.max_index_documents:
            raise ChimeraRefused(RefusalCode.BUDGET_EXHAUSTED)
        item = fingerprint(document, self.policy)
        self._positions[item.source_sha256] = len(self._documents)
        self._documents[item.source_sha256] = document
        self._fingerprints[item.source_sha256] = item
        self._texts.setdefault((item.language, item.normalized_text_sha256), item.source_sha256)
        for key in self._keys(item):
            self._bands.setdefault(key, set()).add(item.source_sha256)
        self._urls[item.canonical_url] = item

    def observe(self, document: DocumentSource) -> None:
        """Track source revisions without adding another cluster representative."""
        item = fingerprint(document, self.policy)
        self._urls[item.canonical_url] = item
