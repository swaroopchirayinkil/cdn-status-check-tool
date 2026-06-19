"""Unit tests for cf_cache_audit.analyzer (summary computation)."""

from __future__ import annotations

import pytest

from cf_cache_audit.analyzer import Analyzer
from cf_cache_audit.models import (
    AssetInfo,
    AssetType,
    AuditResult,
    CfCacheStatus,
    FrameworkHint,
    ScanConfig,
)


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    """Test the summary builder with pre-constructed AssetInfo lists."""

    def _make_asset(
        self,
        cf_status: str = CfCacheStatus.HIT.value,
        cacheable: bool = True,
        result: str = AuditResult.PASS.value,
        cdn: str = "cloudflare",
    ) -> AssetInfo:
        return AssetInfo(
            url="https://example.com/asset",
            asset_type=AssetType.CSS,
            http_status=200,
            cf_cache_status=cf_status,
            is_cacheable=cacheable,
            audit_result=result,
            cdn_provider=cdn,
        )

    def test_all_hits(self) -> None:
        assets = [self._make_asset() for _ in range(10)]
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary(assets)
        assert summary.total_assets == 10
        assert summary.cacheable_assets == 10
        assert summary.hit == 10
        assert summary.hit_ratio == 100.0

    def test_mixed_statuses(self) -> None:
        assets = [
            self._make_asset(CfCacheStatus.HIT.value),
            self._make_asset(CfCacheStatus.HIT.value),
            self._make_asset(CfCacheStatus.MISS.value, result=AuditResult.WARNING.value),
            self._make_asset(CfCacheStatus.DYNAMIC.value, result=AuditResult.WARNING.value),
            self._make_asset(CfCacheStatus.BYPASS.value, result=AuditResult.WARNING.value),
        ]
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary(assets)
        assert summary.total_assets == 5
        assert summary.hit == 2
        assert summary.miss == 1
        assert summary.dynamic == 1
        assert summary.bypass == 1
        assert summary.warnings == 3
        assert summary.hit_ratio == 40.0

    def test_no_cacheable(self) -> None:
        assets = [
            self._make_asset(
                CfCacheStatus.NONE.value,
                cacheable=False,
                result=AuditResult.INFO.value,
            ),
        ]
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary(assets)
        assert summary.cacheable_assets == 0
        assert summary.hit_ratio == 0.0

    def test_cdn_breakdown(self) -> None:
        assets = [
            self._make_asset(cdn="cloudflare"),
            self._make_asset(cdn="cloudflare"),
            self._make_asset(cdn="cloudfront"),
        ]
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary(assets)
        assert summary.cdn_breakdown["cloudflare"] == 2
        assert summary.cdn_breakdown["cloudfront"] == 1

    def test_framework_propagated(self) -> None:
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary([], framework=FrameworkHint.NEXTJS)
        assert summary.framework_hint == FrameworkHint.NEXTJS

    def test_error_count(self) -> None:
        assets = [
            self._make_asset(result=AuditResult.ERROR.value),
            self._make_asset(result=AuditResult.ERROR.value),
            self._make_asset(result=AuditResult.PASS.value),
        ]
        config = ScanConfig(target_url="https://example.com")
        analyzer = Analyzer.__new__(Analyzer)
        summary = analyzer.build_summary(assets)
        assert summary.errors == 2
