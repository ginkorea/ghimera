"""Shared HTTP response semantics, not deployment policy or a second HTTP client."""

from urllib.parse import urldefrag, urljoin

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def redirect_target(url: str, headers: tuple[tuple[str, str], ...]) -> str | None:
    values = [value for key, value in headers if key.lower() == "location"]
    if len(values) != 1 or not values[0].strip():
        return None
    return urldefrag(urljoin(url, values[0]))[0]


# Retain only response semantics used by passive rendering and conditional GET.
# Cookies/authentication headers are never retained or replayed. Repeated CSP
# headers must remain repeated: independent policies apply simultaneously.
RETAINED_HEADERS = frozenset(
    {
        "content-type",
        "location",
        "etag",
        "last-modified",
        "retry-after",
        "content-security-policy",
        "content-security-policy-report-only",
        "access-control-allow-origin",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-expose-headers",
        "access-control-max-age",
        "cross-origin-resource-policy",
        "cross-origin-opener-policy",
        "cross-origin-embedder-policy",
        "x-content-type-options",
        "referrer-policy",
        "vary",
    }
)
