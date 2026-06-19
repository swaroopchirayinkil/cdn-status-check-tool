"""Cache-status analyser for cf-cache-audit.

Validates every discovered asset by performing HEAD (then GET) requests,
collecting HTTP headers, classifying cache behaviour, and producing
:class:`AssetInfo` records with audit verdicts.

Also implements the *warm-cache* workflow (repeat requests to observe
MISS → HIT transitions).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import aiohttp

from cf_cache_audit.cloudflare import (
    detect_cdn_provider,
    detect_cloudflare,
    parse_cf_cache_status,
)
from cf_cache_audit.models import (
    AssetInfo,
    AssetType,
    AuditResult,
    AuditSummary,
    CfCacheStatus,
    CloudflareInfo,
    FrameworkHint,
    ScanConfig,
    WarmCacheAttempt,
)
from cf_cache_audit.utils import (
    RateLimiter,
    classify_asset,
    fetch_with_retry,
    is_cacheable_asset,
)

logger = logging.getLogger("cf_cache_audit")


# ---------------------------------------------------------------------------
# Single-asset validation
# ---------------------------------------------------------------------------

async def _validate_single_asset(
    session: aiohttp.ClientSession,
    url: str,
    asset_type: AssetType,
    config: ScanConfig,
    rate_limiter: RateLimiter,
) -> AssetInfo:
    """Perform a HEAD/GET request against *url* and build an :class:`AssetInfo`."""
    info = AssetInfo(url=url, asset_type=asset_type)

    resp = await fetch_with_retry(
        session,
        url,
        method="HEAD",
        max_retries=2,
        timeout=config.timeout,
        rate_limiter=rate_limiter,
    )

    if resp is None:
        info.error = "Request failed after retries"
        info.audit_result = AuditResult.ERROR
        info.audit_message = "Could not reach asset"
        return info

    headers = resp.headers

    info.http_status = resp.status
    info.content_type = headers.get("content-type")
    info.cache_control = headers.get("cache-control")
    info.etag = headers.get("etag")
    info.last_modified = headers.get("last-modified")
    info.age = headers.get("age")
    info.cf_ray = headers.get("cf-ray")

    # Content-Length: prefer header, fall back to response
    cl = headers.get("content-length")
    if cl and cl.isdigit():
        info.content_length = int(cl)

    # Refine asset type from content-type if we only had OTHER
    if info.asset_type == AssetType.OTHER and info.content_type:
        info.asset_type = classify_asset(url, info.content_type)

    # Cloudflare cache status
    info.cf_cache_status = parse_cf_cache_status(headers).value  # type: ignore[assignment]

    # CDN provider
    info.cdn_provider = detect_cdn_provider(headers).value  # type: ignore[assignment]

    # Cacheability
    info.is_cacheable = is_cacheable_asset(url, info.content_type)

    # ---- Audit verdict --------------------------------------------------
    cf_status = info.cf_cache_status

    if info.http_status and info.http_status >= 400:
        info.audit_result = AuditResult.ERROR
        info.audit_message = f"HTTP {info.http_status}"
    elif info.is_cacheable and cf_status == CfCacheStatus.HIT.value:
        info.audit_result = AuditResult.PASS
        info.audit_message = "Served from cache"
    elif info.is_cacheable and cf_status in (
        CfCacheStatus.MISS.value,
        CfCacheStatus.EXPIRED.value,
        CfCacheStatus.DYNAMIC.value,
        CfCacheStatus.BYPASS.value,
    ):
        info.audit_result = AuditResult.WARNING
        info.audit_message = (
            f"Expected cacheable asset — current status: {cf_status}"
        )
    elif info.is_cacheable and cf_status in (
        CfCacheStatus.REVALIDATED.value,
        CfCacheStatus.STALE.value,
    ):
        info.audit_result = AuditResult.PASS
        info.audit_message = f"Cacheable, status: {cf_status}"
    else:
        info.audit_result = AuditResult.INFO
        info.audit_message = "Non-cacheable or status unavailable"

    # Close the response body to free the connection
    resp.close()

    return info


# ---------------------------------------------------------------------------
# Warm-cache testing
# ---------------------------------------------------------------------------

async def _warm_cache_probe(
    session: aiohttp.ClientSession,
    asset: AssetInfo,
    config: ScanConfig,
    rate_limiter: RateLimiter,
) -> None:
    """Issue multiple requests to *asset* and record MISS→HIT transitions."""
    attempts: list[WarmCacheAttempt] = []

    for i in range(1, config.warm_cache_attempts + 1):
        t0 = time.monotonic()
        resp = await fetch_with_retry(
            session,
            asset.url,
            method="HEAD",
            max_retries=1,
            timeout=config.timeout,
            rate_limiter=rate_limiter,
        )
        elapsed = (time.monotonic() - t0) * 1000  # ms

        if resp is None:
            attempts.append(WarmCacheAttempt(
                attempt=i,
                cf_cache_status=CfCacheStatus.UNKNOWN,
                response_time_ms=elapsed,
            ))
            continue

        status = parse_cf_cache_status(resp.headers)
        attempts.append(WarmCacheAttempt(
            attempt=i,
            cf_cache_status=status,
            http_status=resp.status,
            response_time_ms=round(elapsed, 1),
        ))
        resp.close()

        # Small delay between warm-cache probes to let the edge propagate
        if i < config.warm_cache_attempts:
            await asyncio.sleep(0.3)

    asset.warm_cache_results = attempts


# ---------------------------------------------------------------------------
# Analyser orchestrator
# ---------------------------------------------------------------------------

class Analyzer:
    """Coordinate parallel validation of all discovered assets.

    Parameters
    ----------
    config:
        Scan configuration from CLI.
    session:
        Shared ``aiohttp`` session.
    rate_limiter:
        Shared rate limiter.
    progress_callback:
        ``(completed, total) -> None`` for live progress updates.
    """

    def __init__(
        self,
        config: ScanConfig,
        session: aiohttp.ClientSession,
        rate_limiter: RateLimiter,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        self.config = config
        self.session = session
        self.rate_limiter = rate_limiter
        self.progress_callback = progress_callback

        self.cf_info = CloudflareInfo()
        self.assets: list[AssetInfo] = []

    # ---- internal --------------------------------------------------------

    async def _validate_batch(
        self,
        items: list[tuple[str, AssetType]],
    ) -> list[AssetInfo]:
        """Validate a batch of assets concurrently."""
        sem = asyncio.Semaphore(self.config.workers)
        results: list[AssetInfo] = []
        completed = 0
        total = len(items)

        async def _worker(url: str, atype: AssetType) -> AssetInfo:
            nonlocal completed
            async with sem:
                result = await _validate_single_asset(
                    self.session, url, atype, self.config, self.rate_limiter
                )
                completed += 1
                if self.progress_callback:
                    self.progress_callback(completed, total)
                return result

        tasks = [
            asyncio.create_task(_worker(url, atype))
            for url, atype in items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)  # type: ignore[assignment]
        return results

    # ---- public ----------------------------------------------------------

    async def detect_cloudflare_on_target(self) -> CloudflareInfo:
        """Issue a HEAD against the target URL to detect Cloudflare."""
        resp = await fetch_with_retry(
            self.session,
            self.config.target_url,
            method="HEAD",
            timeout=self.config.timeout,
            rate_limiter=self.rate_limiter,
        )
        if resp is None:
            logger.warning("Could not reach target for Cloudflare detection")
            return CloudflareInfo()

        self.cf_info = detect_cloudflare(resp.headers)
        resp.close()
        return self.cf_info

    async def analyse(
        self,
        discovered: dict[str, AssetType],
    ) -> list[AssetInfo]:
        """Validate all assets and return the results."""
        items = list(discovered.items())
        logger.info("Validating %d assets …", len(items))

        self.assets = await self._validate_batch(items)

        # Warm-cache pass
        if self.config.warm_cache:
            cacheable = [a for a in self.assets if a.is_cacheable]
            logger.info("Warm-cache testing %d cacheable assets …", len(cacheable))

            sem = asyncio.Semaphore(self.config.workers)
            warm_completed = 0

            async def _warm_worker(asset: AssetInfo) -> None:
                nonlocal warm_completed
                async with sem:
                    await _warm_cache_probe(
                        self.session, asset, self.config, self.rate_limiter
                    )
                    warm_completed += 1
                    if self.progress_callback:
                        self.progress_callback(warm_completed, len(cacheable))

            warm_tasks = [
                asyncio.create_task(_warm_worker(a)) for a in cacheable
            ]
            await asyncio.gather(*warm_tasks, return_exceptions=True)

        return self.assets

    def build_summary(
        self,
        assets: list[AssetInfo],
        framework: FrameworkHint = FrameworkHint.UNKNOWN,
    ) -> AuditSummary:
        """Compute aggregate statistics from validated assets."""
        summary = AuditSummary(framework_hint=framework)
        summary.total_assets = len(assets)

        cdn_counts: dict[str, int] = {}

        for asset in assets:
            if asset.is_cacheable:
                summary.cacheable_assets += 1

            status = asset.cf_cache_status
            if status == CfCacheStatus.HIT.value:
                summary.hit += 1
            elif status == CfCacheStatus.MISS.value:
                summary.miss += 1
            elif status == CfCacheStatus.EXPIRED.value:
                summary.expired += 1
            elif status == CfCacheStatus.REVALIDATED.value:
                summary.revalidated += 1
            elif status == CfCacheStatus.STALE.value:
                summary.stale += 1
            elif status == CfCacheStatus.BYPASS.value:
                summary.bypass += 1
            elif status == CfCacheStatus.DYNAMIC.value:
                summary.dynamic += 1
            elif status == CfCacheStatus.UNKNOWN.value:
                summary.unknown += 1
            else:
                summary.none_status += 1

            if asset.audit_result == AuditResult.ERROR.value:
                summary.errors += 1
            elif asset.audit_result == AuditResult.WARNING.value:
                summary.warnings += 1

            cdn = asset.cdn_provider or "none"
            cdn_counts[cdn] = cdn_counts.get(cdn, 0) + 1

        # Hit ratio: HIT / cacheable
        if summary.cacheable_assets > 0:
            summary.hit_ratio = round(
                summary.hit / summary.cacheable_assets * 100, 1
            )

        summary.cdn_breakdown = cdn_counts
        return summary
