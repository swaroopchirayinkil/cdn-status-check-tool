"""Unit tests for cf_cache_audit.cloudflare."""

from __future__ import annotations

import pytest

from cf_cache_audit.cloudflare import (
    detect_cdn_provider,
    detect_cloudflare,
    parse_cf_cache_status,
)
from cf_cache_audit.models import CdnProvider, CfCacheStatus


# ---------------------------------------------------------------------------
# detect_cloudflare
# ---------------------------------------------------------------------------

class TestDetectCloudflare:
    def test_detected_via_server_header(self) -> None:
        headers = {"server": "cloudflare", "cf-ray": "abc123"}
        info = detect_cloudflare(headers)
        assert info.detected is True
        assert info.cf_ray_present is True

    def test_detected_via_cf_cache_status(self) -> None:
        headers = {"cf-cache-status": "HIT"}
        info = detect_cloudflare(headers)
        assert info.detected is True

    def test_not_detected(self) -> None:
        headers = {"server": "nginx"}
        info = detect_cloudflare(headers)
        assert info.detected is False

    def test_apo_detected(self) -> None:
        headers = {
            "server": "cloudflare",
            "cf-ray": "abc",
            "cf-apo-via": "origin",
        }
        info = detect_cloudflare(headers)
        assert info.apo_detected is True
        assert any("APO" in n for n in info.additional_notes)

    def test_cache_rule_hint_bypass(self) -> None:
        headers = {
            "server": "cloudflare",
            "cf-cache-status": "BYPASS",
            "cache-control": "max-age=3600",
        }
        info = detect_cloudflare(headers)
        assert len(info.cache_rules_hints) >= 1

    def test_cache_rule_hint_dynamic_for_css(self) -> None:
        headers = {
            "server": "cloudflare",
            "cf-cache-status": "DYNAMIC",
            "content-type": "text/css",
        }
        info = detect_cloudflare(headers)
        assert len(info.cache_rules_hints) >= 1


# ---------------------------------------------------------------------------
# parse_cf_cache_status
# ---------------------------------------------------------------------------

class TestParseCfCacheStatus:
    def test_hit(self) -> None:
        assert parse_cf_cache_status({"cf-cache-status": "HIT"}) == CfCacheStatus.HIT

    def test_miss(self) -> None:
        assert parse_cf_cache_status({"cf-cache-status": "MISS"}) == CfCacheStatus.MISS

    def test_dynamic(self) -> None:
        assert parse_cf_cache_status({"cf-cache-status": "DYNAMIC"}) == CfCacheStatus.DYNAMIC

    def test_case_insensitive(self) -> None:
        assert parse_cf_cache_status({"cf-cache-status": "hit"}) == CfCacheStatus.HIT

    def test_absent(self) -> None:
        assert parse_cf_cache_status({}) == CfCacheStatus.NONE

    def test_unknown_value(self) -> None:
        assert parse_cf_cache_status({"cf-cache-status": "BOGUS"}) == CfCacheStatus.UNKNOWN


# ---------------------------------------------------------------------------
# detect_cdn_provider
# ---------------------------------------------------------------------------

class TestDetectCdnProvider:
    def test_cloudflare(self) -> None:
        assert detect_cdn_provider({"server": "cloudflare"}) == CdnProvider.CLOUDFLARE

    def test_cloudfront_via_header(self) -> None:
        result = detect_cdn_provider({"x-amz-cf-id": "abc123"})
        assert result == CdnProvider.CLOUDFRONT

    def test_fastly(self) -> None:
        result = detect_cdn_provider({"x-fastly-request-id": "xyz"})
        assert result == CdnProvider.FASTLY

    def test_akamai(self) -> None:
        result = detect_cdn_provider({"server": "AkamaiGHost"})
        assert result == CdnProvider.AKAMAI

    def test_none(self) -> None:
        assert detect_cdn_provider({"server": "nginx"}) == CdnProvider.NONE

    def test_azure(self) -> None:
        result = detect_cdn_provider({"x-azure-ref": "abc"})
        assert result == CdnProvider.AZURE
