"""Cloudflare detection and header analysis.

Examines HTTP response headers to determine:
  • Whether the origin is proxied through Cloudflare.
  • Presence of Cloudflare APO (Automatic Platform Optimization).
  • Hints about Cloudflare Cache Rules / Page Rules.
  • Detection of third-party CDN providers for off-origin assets.
"""

from __future__ import annotations

import logging
from typing import Mapping

from cf_cache_audit.models import (
    CdnProvider,
    CfCacheStatus,
    CloudflareInfo,
)

logger = logging.getLogger("cf_cache_audit")

# ---------------------------------------------------------------------------
# Header-based CDN fingerprints
# ---------------------------------------------------------------------------

_CDN_SERVER_HINTS: dict[str, CdnProvider] = {
    "cloudflare": CdnProvider.CLOUDFLARE,
    "akamaighost": CdnProvider.AKAMAI,
    "akamai": CdnProvider.AKAMAI,
    "cloudfront": CdnProvider.CLOUDFRONT,
    "amazons3": CdnProvider.CLOUDFRONT,
    "fastly": CdnProvider.FASTLY,
    "gws": CdnProvider.GOOGLE,
    "gse": CdnProvider.GOOGLE,
    "bunnycdn": CdnProvider.BUNNY,
    "stackpath": CdnProvider.STACKPATH,
    "keycdn": CdnProvider.KEYCDN,
    "sucuri": CdnProvider.SUCURI,
}

_CDN_HEADER_HINTS: list[tuple[str, str, CdnProvider]] = [
    ("x-amz-cf-id", "", CdnProvider.CLOUDFRONT),
    ("x-amz-cf-pop", "", CdnProvider.CLOUDFRONT),
    ("x-cache", "cloudfront", CdnProvider.CLOUDFRONT),
    ("x-akamai-transformed", "", CdnProvider.AKAMAI),
    ("x-fastly-request-id", "", CdnProvider.FASTLY),
    ("x-served-by", "cache-", CdnProvider.FASTLY),
    ("x-cdn", "bunny", CdnProvider.BUNNY),
    ("server", "bunnycdn", CdnProvider.BUNNY),
    ("x-sucuri-id", "", CdnProvider.SUCURI),
    ("x-ms-ref", "", CdnProvider.AZURE),
    ("x-azure-ref", "", CdnProvider.AZURE),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_cloudflare(headers: Mapping[str, str]) -> CloudflareInfo:
    """Analyse response *headers* for Cloudflare signals.

    Returns a populated :class:`CloudflareInfo` instance.
    """
    info = CloudflareInfo()

    server = headers.get("server", "").lower()
    if "cloudflare" in server:
        info.detected = True
        info.server_header = headers.get("server")

    if "cf-ray" in headers:
        info.detected = True
        info.cf_ray_present = True

    if "cf-cache-status" in headers:
        info.detected = True

    # --- APO detection ---------------------------------------------------
    cf_apo = headers.get("cf-apo-via", "")
    cf_edge = headers.get("cf-edge-cache", "")
    if cf_apo or cf_edge:
        info.apo_detected = True
        info.additional_notes.append(
            f"Cloudflare APO detected (cf-apo-via={cf_apo!r}, "
            f"cf-edge-cache={cf_edge!r})"
        )

    # --- Cache-rule hints ------------------------------------------------
    cache_status = headers.get("cf-cache-status", "").upper()
    cache_control = headers.get("cache-control", "")

    if cache_status == "BYPASS" and "no-cache" not in cache_control:
        info.cache_rules_hints.append(
            "BYPASS without no-cache in Cache-Control — possible Cache Rule "
            "or Page Rule overriding default behaviour"
        )

    if cache_status == "DYNAMIC" and any(
        ext in headers.get("content-type", "")
        for ext in ("text/css", "application/javascript", "image/", "font/")
    ):
        info.cache_rules_hints.append(
            "DYNAMIC for a typically-cacheable content-type — check "
            "Cloudflare Cache Rules / Page Rules"
        )

    # --- CDN-level cache information -------------------------------------
    cf_cache_tag = headers.get("cache-tag", "")
    if cf_cache_tag:
        info.additional_notes.append(
            f"Cache-Tag header present: {cf_cache_tag!r}"
        )

    return info


def parse_cf_cache_status(headers: Mapping[str, str]) -> CfCacheStatus:
    """Extract ``cf-cache-status`` from *headers*.

    Returns :attr:`CfCacheStatus.NONE` when the header is absent.
    """
    raw = headers.get("cf-cache-status", "").upper().strip()
    try:
        return CfCacheStatus(raw)
    except ValueError:
        if raw:
            logger.debug("Unrecognised cf-cache-status: %r", raw)
            return CfCacheStatus.UNKNOWN
        return CfCacheStatus.NONE


def detect_cdn_provider(headers: Mapping[str, str]) -> CdnProvider:
    """Identify which CDN (if any) served the response."""
    server = headers.get("server", "").lower()

    # Check server header keywords
    for keyword, provider in _CDN_SERVER_HINTS.items():
        if keyword in server:
            return provider

    # Check specialised headers
    for header_name, value_hint, provider in _CDN_HEADER_HINTS:
        hval = headers.get(header_name, "")
        if hval and (not value_hint or value_hint in hval.lower()):
            return provider

    return CdnProvider.NONE
