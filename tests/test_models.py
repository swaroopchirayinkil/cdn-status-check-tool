"""Unit tests for cf_cache_audit.models."""

from __future__ import annotations

import json

import pytest

from cf_cache_audit.models import (
    AssetInfo,
    AssetType,
    AuditReport,
    AuditResult,
    AuditSummary,
    CfCacheStatus,
    CloudflareInfo,
    ScanConfig,
    WarmCacheAttempt,
)


# ---------------------------------------------------------------------------
# ScanConfig
# ---------------------------------------------------------------------------

class TestScanConfig:
    """Validate URL normalisation and defaults."""

    def test_adds_https_scheme(self) -> None:
        cfg = ScanConfig(target_url="example.com")
        assert cfg.target_url == "https://example.com"

    def test_preserves_http(self) -> None:
        cfg = ScanConfig(target_url="http://example.com")
        assert cfg.target_url == "http://example.com"

    def test_strips_trailing_slash(self) -> None:
        cfg = ScanConfig(target_url="https://example.com/")
        assert cfg.target_url == "https://example.com"

    def test_default_values(self) -> None:
        cfg = ScanConfig(target_url="example.com")
        assert cfg.depth == 3
        assert cfg.timeout == 15
        assert cfg.workers == 20
        assert cfg.follow_subdomains is False
        assert cfg.warm_cache is False


# ---------------------------------------------------------------------------
# AssetInfo
# ---------------------------------------------------------------------------

class TestAssetInfo:
    """AssetInfo creation and serialisation."""

    def test_defaults(self) -> None:
        a = AssetInfo(url="https://cdn.example.com/logo.png")
        assert a.asset_type == AssetType.OTHER
        assert a.cf_cache_status == CfCacheStatus.NONE.value
        assert a.is_cacheable is False
        assert a.audit_result == AuditResult.INFO.value

    def test_full_construction(self) -> None:
        a = AssetInfo(
            url="https://example.com/style.css",
            asset_type=AssetType.CSS,
            http_status=200,
            content_type="text/css",
            cf_cache_status=CfCacheStatus.HIT,
            is_cacheable=True,
            audit_result=AuditResult.PASS,
            audit_message="Served from cache",
        )
        assert a.is_cacheable is True
        assert a.cf_cache_status == CfCacheStatus.HIT.value

    def test_json_round_trip(self) -> None:
        a = AssetInfo(
            url="https://example.com/app.js",
            asset_type=AssetType.JAVASCRIPT,
            http_status=200,
        )
        data = a.model_dump(mode="json")
        restored = AssetInfo(**data)
        assert restored.url == a.url
        assert restored.asset_type == a.asset_type


# ---------------------------------------------------------------------------
# WarmCacheAttempt
# ---------------------------------------------------------------------------

class TestWarmCacheAttempt:
    def test_basic(self) -> None:
        wa = WarmCacheAttempt(
            attempt=1,
            cf_cache_status=CfCacheStatus.MISS,
            http_status=200,
            response_time_ms=42.3,
        )
        assert wa.attempt == 1
        assert wa.response_time_ms == 42.3


# ---------------------------------------------------------------------------
# AuditReport
# ---------------------------------------------------------------------------

class TestAuditReport:
    def test_serialisation(self) -> None:
        report = AuditReport(
            website="https://example.com",
            cloudflare=CloudflareInfo(detected=True),
            summary=AuditSummary(total_assets=5, hit=3, cacheable_assets=4),
            assets=[
                AssetInfo(url="https://example.com/logo.svg"),
            ],
        )
        data = report.model_dump(mode="json")
        assert data["website"] == "https://example.com"
        assert data["cloudflare"]["detected"] is True
        assert len(data["assets"]) == 1

        # JSON serialisable
        text = json.dumps(data, default=str)
        assert '"website"' in text


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TestEnums:
    def test_cf_cache_status_values(self) -> None:
        assert CfCacheStatus.HIT.value == "HIT"
        assert CfCacheStatus.DYNAMIC.value == "DYNAMIC"

    def test_audit_result_values(self) -> None:
        assert AuditResult.PASS.value == "PASS"
        assert AuditResult.WARNING.value == "WARNING"
