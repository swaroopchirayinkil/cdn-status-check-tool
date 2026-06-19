"""Website crawler for cf-cache-audit.

Responsibilities:
  1. Fetch the target page HTML (and optionally crawl to *depth*).
  2. Parse HTML to discover asset URLs (images, scripts, stylesheets, …).
  3. Recursively inspect CSS for ``url(…)`` references.
  4. Best-effort JS inspection for dynamic imports / chunks.
  5. Fetch and parse ``robots.txt`` and ``sitemap.xml``.
  6. Detect front-end frameworks (Next.js, React, Angular, Vue, WP, Drupal).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from cf_cache_audit.models import (
    AssetType,
    FrameworkHint,
    ScanConfig,
)
from cf_cache_audit.utils import (
    RateLimiter,
    classify_asset,
    extract_css_urls,
    extract_js_urls,
    fetch_with_retry,
    is_same_origin,
    normalise_url,
)

logger = logging.getLogger("cf_cache_audit")


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

def detect_framework(html: str, url: str) -> FrameworkHint:
    """Infer the front-end framework from HTML content and URL patterns."""
    lower = html.lower()

    if "/_next/" in html or "__next" in lower:
        return FrameworkHint.NEXTJS
    if "ng-version" in lower or "ng-app" in lower or "angular" in lower:
        return FrameworkHint.ANGULAR
    if "data-reactroot" in lower or "react" in lower and "__react" in lower:
        return FrameworkHint.REACT
    if "__vue" in lower or "data-v-" in lower:
        return FrameworkHint.VUE
    if "wp-content" in lower or "wp-includes" in lower:
        return FrameworkHint.WORDPRESS
    if "drupal" in lower or "sites/default/files" in lower:
        return FrameworkHint.DRUPAL

    return FrameworkHint.STATIC


# ---------------------------------------------------------------------------
# HTML asset extraction
# ---------------------------------------------------------------------------

_TAG_ATTR_MAP: list[tuple[str, str, AssetType | None]] = [
    ("img", "src", AssetType.IMAGE),
    ("img", "srcset", AssetType.IMAGE),
    ("script", "src", AssetType.JAVASCRIPT),
    ("link", "href", None),  # classified later by rel / extension
    ("video", "src", AssetType.VIDEO),
    ("video", "poster", AssetType.IMAGE),
    ("audio", "src", AssetType.AUDIO),
    ("source", "src", None),
    ("source", "srcset", None),
    ("iframe", "src", AssetType.IFRAME),
]


def _parse_srcset(srcset: str) -> list[str]:
    """Return a list of URLs from an HTML ``srcset`` attribute value."""
    urls: list[str] = []
    for part in srcset.split(","):
        part = part.strip()
        if part:
            url_part = part.split()[0]
            if url_part:
                urls.append(url_part)
    return urls


def extract_assets_from_html(html: str, base_url: str) -> dict[str, AssetType]:
    """Parse *html* and return ``{absolute_url: AssetType}``."""
    soup = BeautifulSoup(html, "lxml")
    assets: dict[str, AssetType] = {}

    for tag_name, attr, default_type in _TAG_ATTR_MAP:
        for tag in soup.find_all(tag_name):
            raw = tag.get(attr)
            if not raw:
                continue

            # Handle srcset (comma-separated list)
            if attr == "srcset":
                for src in _parse_srcset(raw):
                    url = normalise_url(base_url, src)
                    if url:
                        assets[url] = default_type or classify_asset(url)
                continue

            url = normalise_url(base_url, raw)
            if not url:
                continue

            # Refine type for <link> by rel attribute
            if tag_name == "link":
                rel = " ".join(tag.get("rel", [])).lower()
                if "stylesheet" in rel:
                    atype = AssetType.CSS
                elif "icon" in rel or "apple-touch-icon" in rel:
                    atype = AssetType.IMAGE
                elif "manifest" in rel:
                    atype = AssetType.MANIFEST
                elif "preload" in rel:
                    as_attr = (tag.get("as") or "").lower()
                    atype = {
                        "style": AssetType.CSS,
                        "script": AssetType.JAVASCRIPT,
                        "font": AssetType.FONT,
                        "image": AssetType.IMAGE,
                    }.get(as_attr, classify_asset(url))
                else:
                    atype = classify_asset(url)
            elif default_type is None:
                atype = classify_asset(url)
            else:
                atype = default_type

            assets[url] = atype

    # --- Inline style url() references ---
    for tag in soup.find_all(style=True):
        style_text = tag.get("style", "")
        for css_url in extract_css_urls(style_text, base_url):
            assets[css_url] = classify_asset(css_url)

    # --- <style> blocks ---
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            for css_url in extract_css_urls(style_tag.string, base_url):
                assets[css_url] = classify_asset(css_url)

    return assets


def extract_page_links(html: str, base_url: str) -> list[str]:
    """Return internal page links for recursive crawling."""
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for a_tag in soup.find_all("a", href=True):
        url = normalise_url(base_url, a_tag["href"])
        if url:
            links.append(url)
    return links


# ---------------------------------------------------------------------------
# Sitemap & robots.txt
# ---------------------------------------------------------------------------

async def fetch_robots_txt(
    session: aiohttp.ClientSession,
    base_url: str,
    timeout: int,
) -> str | None:
    """Download ``/robots.txt`` and return its text, or ``None``."""
    url = urljoin(base_url, "/robots.txt")
    try:
        resp = await session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        )
        if resp.status == 200:
            text = await resp.text(errors="replace")
            logger.info("Fetched robots.txt (%d bytes)", len(text))
            return text
    except Exception as exc:
        logger.debug("Could not fetch robots.txt: %s", exc)
    return None


async def fetch_sitemap_urls(
    session: aiohttp.ClientSession,
    base_url: str,
    timeout: int,
) -> list[str]:
    """Download ``/sitemap.xml`` and extract ``<loc>`` URLs."""
    url = urljoin(base_url, "/sitemap.xml")
    urls: list[str] = []
    try:
        resp = await session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        )
        if resp.status == 200:
            text = await resp.text(errors="replace")
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.IGNORECASE)
            urls.extend(locs)
            logger.info("Sitemap: found %d URLs", len(urls))
    except Exception as exc:
        logger.debug("Could not fetch sitemap.xml: %s", exc)
    return urls


# ---------------------------------------------------------------------------
# Main crawler
# ---------------------------------------------------------------------------

class Crawler:
    """Async website crawler that discovers all static assets.

    Parameters
    ----------
    config:
        Scan configuration from CLI arguments.
    session:
        Shared ``aiohttp`` session.
    rate_limiter:
        Shared rate limiter.
    progress_callback:
        Optional callable ``(message: str) -> None`` for live progress.
    """

    def __init__(
        self,
        config: ScanConfig,
        session: aiohttp.ClientSession,
        rate_limiter: RateLimiter,
        progress_callback: Any | None = None,
    ) -> None:
        self.config = config
        self.session = session
        self.rate_limiter = rate_limiter
        self.progress_callback = progress_callback

        self.visited_pages: set[str] = set()
        self.discovered_assets: dict[str, AssetType] = {}
        self.framework: FrameworkHint = FrameworkHint.UNKNOWN
        self.robots_txt: str | None = None
        self.sitemap_urls: list[str] = []

    # ---- internal helpers ------------------------------------------------

    def _report(self, msg: str) -> None:
        logger.info(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    async def _fetch_page(self, url: str) -> str | None:
        """Download a page and return its HTML text."""
        try:
            async with self.rate_limiter:
                resp = await self.session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    allow_redirects=True,
                    headers={"User-Agent": self.config.user_agent},
                )
                if resp.status != 200:
                    logger.debug("Non-200 for page %s: %d", url, resp.status)
                    return None
                ct = resp.headers.get("content-type", "")
                if "text/html" not in ct:
                    return None
                return await resp.text(errors="replace")
        except Exception as exc:
            logger.debug("Failed to fetch page %s: %s", url, exc)
            return None

    async def _fetch_text(self, url: str) -> str | None:
        """Download a text resource (CSS / JS)."""
        try:
            async with self.rate_limiter:
                resp = await self.session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    allow_redirects=True,
                    headers={"User-Agent": self.config.user_agent},
                )
                if resp.status == 200:
                    return await resp.text(errors="replace")
        except Exception as exc:
            logger.debug("Failed to fetch resource %s: %s", url, exc)
        return None

    async def _deep_inspect_css(self, css_url: str) -> None:
        """Download CSS and extract nested ``url(…)`` references."""
        text = await self._fetch_text(css_url)
        if not text:
            return
        for found_url in extract_css_urls(text, css_url):
            if found_url not in self.discovered_assets:
                self.discovered_assets[found_url] = classify_asset(found_url)

    async def _deep_inspect_js(self, js_url: str) -> None:
        """Download JS and extract chunk / dynamic-import URLs."""
        text = await self._fetch_text(js_url)
        if not text:
            return
        for found_url in extract_js_urls(text, js_url):
            if found_url not in self.discovered_assets:
                self.discovered_assets[found_url] = classify_asset(found_url)

    # ---- page crawl ------------------------------------------------------

    async def _crawl_page(self, url: str, depth: int) -> None:
        """Recursively crawl a single page to the given *depth*."""
        if url in self.visited_pages:
            return
        if depth < 0:
            return

        self.visited_pages.add(url)
        self._report(f"Crawling page ({depth} levels left): {url}")

        html = await self._fetch_page(url)
        if not html:
            return

        # Framework detection on the first page
        if len(self.visited_pages) == 1:
            self.framework = detect_framework(html, url)
            self._report(f"Framework detected: {self.framework.value}")

        # Extract assets
        page_assets = extract_assets_from_html(html, url)
        new_count = 0
        for asset_url, asset_type in page_assets.items():
            if asset_url not in self.discovered_assets:
                self.discovered_assets[asset_url] = asset_type
                new_count += 1
        self._report(
            f"  Found {len(page_assets)} assets on page, "
            f"{new_count} new (total: {len(self.discovered_assets)})"
        )

        # Deep-inspect CSS and JS
        css_urls = [u for u, t in page_assets.items() if t == AssetType.CSS]
        js_urls = [u for u, t in page_assets.items() if t == AssetType.JAVASCRIPT]

        tasks: list[asyncio.Task[None]] = []
        for css_url in css_urls:
            tasks.append(asyncio.create_task(self._deep_inspect_css(css_url)))
        for js_url in js_urls:
            tasks.append(asyncio.create_task(self._deep_inspect_js(js_url)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._report(
                f"  After deep CSS/JS inspection: "
                f"{len(self.discovered_assets)} total assets"
            )

        # Recursive page crawl
        if depth > 0:
            page_links = extract_page_links(html, url)
            child_tasks: list[asyncio.Task[None]] = []
            for link in page_links:
                if link in self.visited_pages:
                    continue
                if not is_same_origin(
                    link,
                    self.config.target_url,
                    follow_subdomains=self.config.follow_subdomains,
                ):
                    continue
                child_tasks.append(
                    asyncio.create_task(self._crawl_page(link, depth - 1))
                )
            if child_tasks:
                await asyncio.gather(*child_tasks, return_exceptions=True)

    # ---- public entry point ----------------------------------------------

    async def crawl(self) -> dict[str, AssetType]:
        """Run the full crawl and return discovered assets.

        Returns
        -------
        dict[str, AssetType]
            Mapping of absolute URL → asset type.
        """
        base = self.config.target_url

        # Robots.txt & sitemap (run concurrently)
        robots_task = asyncio.create_task(
            fetch_robots_txt(self.session, base, self.config.timeout)
        )
        sitemap_task = asyncio.create_task(
            fetch_sitemap_urls(self.session, base, self.config.timeout)
        )

        # Main crawl
        await self._crawl_page(base, self.config.depth)

        self.robots_txt = await robots_task
        self.sitemap_urls = await sitemap_task

        self._report(
            f"Crawl complete — {len(self.discovered_assets)} assets, "
            f"{len(self.visited_pages)} pages visited"
        )
        return self.discovered_assets
