"""Unit tests for cf_cache_audit.utils."""

from __future__ import annotations

import pytest

from cf_cache_audit.models import AssetType
from cf_cache_audit.utils import (
    classify_asset,
    extract_css_urls,
    extract_js_urls,
    get_domain,
    is_cacheable_asset,
    is_same_origin,
    normalise_url,
    truncate,
)


# ---------------------------------------------------------------------------
# normalise_url
# ---------------------------------------------------------------------------

class TestNormaliseUrl:
    BASE = "https://example.com/page/"

    def test_absolute_url(self) -> None:
        assert normalise_url(self.BASE, "https://cdn.example.com/img.png") == (
            "https://cdn.example.com/img.png"
        )

    def test_relative_url(self) -> None:
        result = normalise_url(self.BASE, "logo.svg")
        assert result == "https://example.com/page/logo.svg"

    def test_root_relative(self) -> None:
        result = normalise_url(self.BASE, "/assets/style.css")
        assert result == "https://example.com/assets/style.css"

    def test_protocol_relative(self) -> None:
        result = normalise_url(self.BASE, "//cdn.example.com/font.woff2")
        assert result == "https://cdn.example.com/font.woff2"

    def test_strips_fragment(self) -> None:
        result = normalise_url(self.BASE, "/page#section")
        assert result is not None
        assert "#" not in result

    def test_preserves_query(self) -> None:
        result = normalise_url(self.BASE, "/api?v=2")
        assert result is not None
        assert "?v=2" in result

    def test_returns_none_for_data_uri(self) -> None:
        assert normalise_url(self.BASE, "data:image/png;base64,abc") is None

    def test_returns_none_for_javascript(self) -> None:
        assert normalise_url(self.BASE, "javascript:void(0)") is None

    def test_returns_none_for_mailto(self) -> None:
        assert normalise_url(self.BASE, "mailto:a@b.com") is None

    def test_returns_none_for_empty(self) -> None:
        assert normalise_url(self.BASE, "") is None

    def test_returns_none_for_fragment_only(self) -> None:
        assert normalise_url(self.BASE, "#top") is None


# ---------------------------------------------------------------------------
# get_domain / is_same_origin
# ---------------------------------------------------------------------------

class TestDomainHelpers:
    def test_get_domain(self) -> None:
        assert get_domain("https://www.example.com/path") == "www.example.com"

    def test_same_origin_exact(self) -> None:
        assert is_same_origin(
            "https://example.com/page",
            "https://example.com",
        )

    def test_same_origin_different(self) -> None:
        assert not is_same_origin(
            "https://other.com/page",
            "https://example.com",
        )

    def test_subdomain_without_flag(self) -> None:
        assert not is_same_origin(
            "https://cdn.example.com/img.png",
            "https://example.com",
            follow_subdomains=False,
        )

    def test_subdomain_with_flag(self) -> None:
        assert is_same_origin(
            "https://cdn.example.com/img.png",
            "https://example.com",
            follow_subdomains=True,
        )


# ---------------------------------------------------------------------------
# classify_asset
# ---------------------------------------------------------------------------

class TestClassifyAsset:
    def test_css_by_extension(self) -> None:
        assert classify_asset("https://x.com/style.css") == AssetType.CSS

    def test_js_by_extension(self) -> None:
        assert classify_asset("https://x.com/app.js") == AssetType.JAVASCRIPT

    def test_mjs(self) -> None:
        assert classify_asset("https://x.com/module.mjs") == AssetType.JAVASCRIPT

    def test_png(self) -> None:
        assert classify_asset("https://x.com/logo.png") == AssetType.IMAGE

    def test_svg(self) -> None:
        assert classify_asset("https://x.com/icon.svg") == AssetType.SVG

    def test_woff2(self) -> None:
        assert classify_asset("https://x.com/font.woff2") == AssetType.FONT

    def test_mp4(self) -> None:
        assert classify_asset("https://x.com/video.mp4") == AssetType.VIDEO

    def test_content_type_fallback(self) -> None:
        assert (
            classify_asset("https://x.com/resource", "text/css")
            == AssetType.CSS
        )

    def test_unknown(self) -> None:
        assert classify_asset("https://x.com/something") == AssetType.OTHER


# ---------------------------------------------------------------------------
# is_cacheable_asset
# ---------------------------------------------------------------------------

class TestIsCacheableAsset:
    def test_css_cacheable(self) -> None:
        assert is_cacheable_asset("https://x.com/style.css") is True

    def test_html_not_cacheable(self) -> None:
        assert is_cacheable_asset("https://x.com/page.html") is False

    def test_content_type_cacheable(self) -> None:
        assert is_cacheable_asset("https://x.com/res", "image/png") is True

    def test_no_info(self) -> None:
        assert is_cacheable_asset("https://x.com/unknown") is False


# ---------------------------------------------------------------------------
# CSS URL extraction
# ---------------------------------------------------------------------------

class TestExtractCssUrls:
    BASE = "https://example.com/css/"

    def test_basic_url(self) -> None:
        css = 'body { background: url("../img/bg.png"); }'
        urls = extract_css_urls(css, self.BASE)
        assert len(urls) == 1
        assert urls[0] == "https://example.com/img/bg.png"

    def test_single_quotes(self) -> None:
        css = "body { background: url('/img/bg.png'); }"
        urls = extract_css_urls(css, self.BASE)
        assert len(urls) == 1

    def test_no_quotes(self) -> None:
        css = "body { background: url(/img/bg.png); }"
        urls = extract_css_urls(css, self.BASE)
        assert len(urls) == 1

    def test_font_face(self) -> None:
        css = """
        @font-face {
            font-family: 'MyFont';
            src: url('/fonts/my.woff2') format('woff2'),
                 url('/fonts/my.woff') format('woff');
        }
        """
        urls = extract_css_urls(css, self.BASE)
        assert len(urls) == 2

    def test_skips_data_uri(self) -> None:
        css = "body { background: url(data:image/png;base64,abc); }"
        urls = extract_css_urls(css, self.BASE)
        assert len(urls) == 0


# ---------------------------------------------------------------------------
# JS URL extraction
# ---------------------------------------------------------------------------

class TestExtractJsUrls:
    BASE = "https://example.com/js/"

    def test_dynamic_import(self) -> None:
        js = 'const m = import("./chunk-abc.js");'
        urls = extract_js_urls(js, self.BASE)
        assert any("chunk-abc.js" in u for u in urls)

    def test_fetch(self) -> None:
        js = 'fetch("/api/data.json")'
        urls = extract_js_urls(js, self.BASE)
        assert any("data.json" in u for u in urls)

    def test_nextjs_path(self) -> None:
        js = 'var p = "/_next/static/chunks/main-abc123.js";'
        urls = extract_js_urls(js, self.BASE)
        assert any("_next/static" in u for u in urls)


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_string(self) -> None:
        assert truncate("hello", 10) == "hello"

    def test_exact_length(self) -> None:
        assert truncate("hello", 5) == "hello"

    def test_long_string(self) -> None:
        result = truncate("hello world", 8)
        assert len(result) == 8
        assert result.endswith("…")
