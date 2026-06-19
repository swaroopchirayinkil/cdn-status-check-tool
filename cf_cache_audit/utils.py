"""Utility helpers shared across modules.

Includes URL normalisation, extension-based classification, content-type
mapping, rate-limiting, and retry logic for HTTP requests.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp

from cf_cache_audit.models import (
    AssetType,
    CACHEABLE_CONTENT_TYPES,
    CACHEABLE_EXTENSIONS,
)

logger = logging.getLogger("cf_cache_audit")

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalise_url(base: str, href: str) -> str | None:
    """Resolve *href* against *base* and return an absolute HTTP(S) URL.

    Returns ``None`` for mailto:, javascript:, data:, or fragment-only hrefs.
    """
    if not href or href.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return None

    resolved = urljoin(base, href)
    parsed = urlparse(resolved)

    if parsed.scheme not in ("http", "https"):
        return None

    # Strip fragment, keep query
    cleaned = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        "",  # no fragment
    ))
    return cleaned


def get_domain(url: str) -> str:
    """Extract the hostname (netloc) from a URL."""
    return urlparse(url).netloc.lower()


def is_same_origin(url: str, base_url: str, *, follow_subdomains: bool = False) -> bool:
    """Check whether *url* belongs to the same origin as *base_url*."""
    base_domain = get_domain(base_url)
    url_domain = get_domain(url)

    if url_domain == base_domain:
        return True

    if follow_subdomains:
        # e.g. cdn.example.com is a subdomain of example.com
        base_parts = base_domain.split(".")
        if len(base_parts) >= 2:
            root = ".".join(base_parts[-2:])
            return url_domain.endswith(f".{root}") or url_domain == root

    return False


# ---------------------------------------------------------------------------
# Asset classification
# ---------------------------------------------------------------------------

_EXT_TO_TYPE: dict[str, AssetType] = {
    ".css": AssetType.CSS,
    ".js": AssetType.JAVASCRIPT,
    ".mjs": AssetType.JAVASCRIPT,
    ".cjs": AssetType.JAVASCRIPT,
    ".ts": AssetType.JAVASCRIPT,
    ".png": AssetType.IMAGE,
    ".jpg": AssetType.IMAGE,
    ".jpeg": AssetType.IMAGE,
    ".webp": AssetType.IMAGE,
    ".avif": AssetType.IMAGE,
    ".gif": AssetType.IMAGE,
    ".bmp": AssetType.IMAGE,
    ".ico": AssetType.IMAGE,
    ".svg": AssetType.SVG,
    ".woff": AssetType.FONT,
    ".woff2": AssetType.FONT,
    ".ttf": AssetType.FONT,
    ".otf": AssetType.FONT,
    ".eot": AssetType.FONT,
    ".mp4": AssetType.VIDEO,
    ".webm": AssetType.VIDEO,
    ".ogg": AssetType.VIDEO,
    ".mp3": AssetType.AUDIO,
    ".wav": AssetType.AUDIO,
    ".flac": AssetType.AUDIO,
    ".html": AssetType.HTML,
    ".htm": AssetType.HTML,
    ".json": AssetType.MANIFEST,
    ".webmanifest": AssetType.MANIFEST,
}

_CT_TO_TYPE: dict[str, AssetType] = {
    "text/css": AssetType.CSS,
    "application/javascript": AssetType.JAVASCRIPT,
    "text/javascript": AssetType.JAVASCRIPT,
    "application/x-javascript": AssetType.JAVASCRIPT,
    "image/svg+xml": AssetType.SVG,
    "image/png": AssetType.IMAGE,
    "image/jpeg": AssetType.IMAGE,
    "image/webp": AssetType.IMAGE,
    "image/avif": AssetType.IMAGE,
    "image/gif": AssetType.IMAGE,
    "image/x-icon": AssetType.IMAGE,
    "image/vnd.microsoft.icon": AssetType.IMAGE,
    "image/bmp": AssetType.IMAGE,
    "font/woff": AssetType.FONT,
    "font/woff2": AssetType.FONT,
    "font/ttf": AssetType.FONT,
    "font/otf": AssetType.FONT,
    "application/font-woff": AssetType.FONT,
    "application/font-woff2": AssetType.FONT,
    "video/mp4": AssetType.VIDEO,
    "video/webm": AssetType.VIDEO,
    "video/ogg": AssetType.VIDEO,
    "audio/mpeg": AssetType.AUDIO,
    "audio/ogg": AssetType.AUDIO,
    "audio/wav": AssetType.AUDIO,
    "audio/flac": AssetType.AUDIO,
    "application/manifest+json": AssetType.MANIFEST,
    "text/html": AssetType.HTML,
}


def classify_asset(url: str, content_type: str | None = None) -> AssetType:
    """Return an :class:`AssetType` from the URL extension or content-type."""
    # Try extension first
    path = urlparse(url).path.lower()
    suffix = PurePosixPath(path).suffix
    if suffix and suffix in _EXT_TO_TYPE:
        return _EXT_TO_TYPE[suffix]

    # Fallback to content-type
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _CT_TO_TYPE:
            return _CT_TO_TYPE[ct]

    return AssetType.OTHER


def is_cacheable_asset(url: str, content_type: str | None = None) -> bool:
    """Determine whether an asset *should* be cacheable."""
    path = urlparse(url).path.lower()
    suffix = PurePosixPath(path).suffix

    if suffix and suffix in CACHEABLE_EXTENSIONS:
        return True

    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in CACHEABLE_CONTENT_TYPES:
            return True

    return False


# ---------------------------------------------------------------------------
# Rate-limited semaphore
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter built on :class:`asyncio.Semaphore`.

    Allows at most *rate* acquisitions per second while also capping total
    concurrency at *max_concurrent*.
    """

    def __init__(self, max_concurrent: int = 20, rate: float = 50.0) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._rate = rate
        self._interval = 1.0 / rate
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

async def fetch_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    *,
    method: str = "HEAD",
    max_retries: int = 2,
    timeout: int = 15,
    rate_limiter: RateLimiter | None = None,
) -> aiohttp.ClientResponse | None:
    """Perform an HTTP request with retry and optional rate limiting.

    Tries HEAD first; if the server returns 405 Method Not Allowed, falls
    back to GET automatically.  Returns ``None`` on persistent failure.
    """
    attempts = 0
    last_exc: Exception | None = None
    current_method = method

    while attempts <= max_retries:
        try:
            if rate_limiter:
                await rate_limiter.acquire()
            try:
                resp = await session.request(
                    current_method,
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                    ssl=True,
                )
                # Some servers reject HEAD — retry with GET
                if resp.status == 405 and current_method == "HEAD":
                    current_method = "GET"
                    attempts += 1
                    continue
                return resp
            finally:
                if rate_limiter:
                    rate_limiter.release()
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            OSError,
            ConnectionError,
        ) as exc:
            last_exc = exc
            attempts += 1
            if attempts <= max_retries:
                wait = 0.5 * (2 ** (attempts - 1))  # exponential back-off
                logger.debug("Retry %d for %s after %s", attempts, url, exc)
                await asyncio.sleep(wait)

    logger.warning("All retries exhausted for %s: %s", url, last_exc)
    return None


# ---------------------------------------------------------------------------
# CSS & JS URL extraction helpers
# ---------------------------------------------------------------------------

# Matches  url("...")  /  url('...')  /  url(...)
_CSS_URL_RE = re.compile(
    r"""url\(\s*['"]?\s*([^'"\)\s]+)\s*['"]?\s*\)""",
    re.IGNORECASE,
)

# Matches dynamic import("...") and import('...')
_JS_IMPORT_RE = re.compile(
    r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)

# Matches fetch("...") or fetch('...')
_JS_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)

# Matches string literals that look like chunk / asset paths
_JS_CHUNK_RE = re.compile(
    r"""['"]([^'"]*?(?:\.chunk|\.bundle|\.module|\.lazy)[^'"]*?\.\w{2,4})['"]""",
)

# Matches Next.js /_next/static paths
_NEXTJS_ASSET_RE = re.compile(
    r"""['"](\/?_next\/static\/[^'"]+)['"]""",
)


def extract_css_urls(css_text: str, base_url: str) -> list[str]:
    """Pull all ``url(...)`` references from CSS source."""
    urls: list[str] = []
    for match in _CSS_URL_RE.finditer(css_text):
        raw = match.group(1)
        resolved = normalise_url(base_url, raw)
        if resolved:
            urls.append(resolved)
    return urls


def extract_js_urls(js_text: str, base_url: str) -> list[str]:
    """Best-effort extraction of asset URLs from JavaScript source."""
    urls: list[str] = []
    for pattern in (_JS_IMPORT_RE, _JS_FETCH_RE, _JS_CHUNK_RE, _NEXTJS_ASSET_RE):
        for match in pattern.finditer(js_text):
            raw = match.group(1)
            resolved = normalise_url(base_url, raw)
            if resolved:
                urls.append(resolved)
    return urls


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def truncate(text: str, length: int = 80) -> str:
    """Truncate *text* to *length* characters, adding an ellipsis."""
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"
