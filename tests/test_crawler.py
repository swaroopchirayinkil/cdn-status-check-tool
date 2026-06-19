"""Unit tests for cf_cache_audit.crawler (HTML parsing, framework detection)."""

from __future__ import annotations

import pytest

from cf_cache_audit.crawler import (
    detect_framework,
    extract_assets_from_html,
    extract_page_links,
)
from cf_cache_audit.models import AssetType, FrameworkHint


# ---------------------------------------------------------------------------
# extract_assets_from_html
# ---------------------------------------------------------------------------

class TestExtractAssetsFromHtml:
    BASE = "https://example.com/"

    def test_img_src(self) -> None:
        html = '<html><body><img src="/img/logo.png"></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/img/logo.png" in assets
        assert assets["https://example.com/img/logo.png"] == AssetType.IMAGE

    def test_script_src(self) -> None:
        html = '<html><head><script src="/js/app.js"></script></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/js/app.js" in assets
        assert assets["https://example.com/js/app.js"] == AssetType.JAVASCRIPT

    def test_link_stylesheet(self) -> None:
        html = '<html><head><link rel="stylesheet" href="/css/style.css"></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/css/style.css" in assets
        assert assets["https://example.com/css/style.css"] == AssetType.CSS

    def test_link_icon(self) -> None:
        html = '<html><head><link rel="icon" href="/favicon.ico"></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/favicon.ico" in assets
        assert assets["https://example.com/favicon.ico"] == AssetType.IMAGE

    def test_link_manifest(self) -> None:
        html = '<html><head><link rel="manifest" href="/manifest.json"></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/manifest.json" in assets
        assert assets["https://example.com/manifest.json"] == AssetType.MANIFEST

    def test_video_src(self) -> None:
        html = '<html><body><video src="/video/promo.mp4"></video></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/video/promo.mp4" in assets
        assert assets["https://example.com/video/promo.mp4"] == AssetType.VIDEO

    def test_audio_src(self) -> None:
        html = '<html><body><audio src="/audio/track.mp3"></audio></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/audio/track.mp3" in assets
        assert assets["https://example.com/audio/track.mp3"] == AssetType.AUDIO

    def test_source_tag(self) -> None:
        html = """
        <html><body>
            <video>
                <source src="/video/clip.webm" type="video/webm">
            </video>
        </body></html>
        """
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/video/clip.webm" in assets

    def test_iframe_src(self) -> None:
        html = '<html><body><iframe src="https://embed.example.com/widget"></iframe></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://embed.example.com/widget" in assets

    def test_preload_font(self) -> None:
        html = '<html><head><link rel="preload" as="font" href="/fonts/inter.woff2"></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/fonts/inter.woff2" in assets
        assert assets["https://example.com/fonts/inter.woff2"] == AssetType.FONT

    def test_inline_style_url(self) -> None:
        html = '<html><body><div style="background: url(/img/hero.jpg)"></div></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/img/hero.jpg" in assets

    def test_style_block(self) -> None:
        html = """
        <html><head><style>
            body { background: url("/img/pattern.png"); }
        </style></head></html>
        """
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/img/pattern.png" in assets

    def test_srcset(self) -> None:
        html = """
        <html><body>
            <img srcset="/img/small.jpg 480w, /img/large.jpg 1024w"
                 src="/img/default.jpg">
        </body></html>
        """
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://example.com/img/small.jpg" in assets
        assert "https://example.com/img/large.jpg" in assets
        assert "https://example.com/img/default.jpg" in assets

    def test_ignores_data_uri(self) -> None:
        html = '<html><body><img src="data:image/png;base64,abc"></body></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert len(assets) == 0

    def test_external_cdn(self) -> None:
        html = '<html><head><script src="https://cdn.jsdelivr.net/lib.js"></script></head></html>'
        assets = extract_assets_from_html(html, self.BASE)
        assert "https://cdn.jsdelivr.net/lib.js" in assets

    def test_complex_page(self) -> None:
        html = """
        <html>
        <head>
            <link rel="stylesheet" href="/css/main.css">
            <link rel="stylesheet" href="/css/vendor.css">
            <link rel="icon" href="/favicon.ico">
            <script src="/js/app.js"></script>
            <script src="/js/vendor.js"></script>
        </head>
        <body>
            <img src="/img/hero.webp">
            <img src="/img/logo.svg">
            <video src="/video/intro.mp4" poster="/img/poster.jpg"></video>
        </body>
        </html>
        """
        assets = extract_assets_from_html(html, self.BASE)
        assert len(assets) == 9


# ---------------------------------------------------------------------------
# extract_page_links
# ---------------------------------------------------------------------------

class TestExtractPageLinks:
    BASE = "https://example.com/"

    def test_extracts_links(self) -> None:
        html = """
        <html><body>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
            <a href="https://external.com">External</a>
        </body></html>
        """
        links = extract_page_links(html, self.BASE)
        assert "https://example.com/about" in links
        assert "https://example.com/contact" in links
        assert "https://external.com" in links

    def test_ignores_fragments(self) -> None:
        html = '<html><body><a href="#top">Top</a></body></html>'
        links = extract_page_links(html, self.BASE)
        assert len(links) == 0


# ---------------------------------------------------------------------------
# detect_framework
# ---------------------------------------------------------------------------

class TestDetectFramework:
    def test_nextjs(self) -> None:
        html = '<script src="/_next/static/chunks/main.js"></script>'
        assert detect_framework(html, "https://x.com") == FrameworkHint.NEXTJS

    def test_wordpress(self) -> None:
        html = '<link rel="stylesheet" href="/wp-content/themes/style.css">'
        assert detect_framework(html, "https://x.com") == FrameworkHint.WORDPRESS

    def test_drupal(self) -> None:
        html = '<script src="/sites/default/files/js/app.js"></script>'
        assert detect_framework(html, "https://x.com") == FrameworkHint.DRUPAL

    def test_angular(self) -> None:
        html = '<app-root ng-version="17.0.0"></app-root>'
        assert detect_framework(html, "https://x.com") == FrameworkHint.ANGULAR

    def test_vue(self) -> None:
        html = '<div data-v-abc123></div>'
        assert detect_framework(html, "https://x.com") == FrameworkHint.VUE

    def test_static_fallback(self) -> None:
        html = "<html><body>Hello</body></html>"
        assert detect_framework(html, "https://x.com") == FrameworkHint.STATIC
