"""Pydantic models and enumerations for cf-cache-audit.

Defines the core data structures used across every module: asset
classification, cache status, scan configuration, and the full audit
report schema that backs both terminal display and JSON/CSV export.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AssetType(str, enum.Enum):
    """Broad category for a discovered asset."""

    HTML = "html"
    CSS = "css"
    JAVASCRIPT = "javascript"
    IMAGE = "image"
    FONT = "font"
    VIDEO = "video"
    AUDIO = "audio"
    SVG = "svg"
    MANIFEST = "manifest"
    IFRAME = "iframe"
    OTHER = "other"


class CfCacheStatus(str, enum.Enum):
    """Cloudflare ``cf-cache-status`` header values."""

    HIT = "HIT"
    MISS = "MISS"
    EXPIRED = "EXPIRED"
    REVALIDATED = "REVALIDATED"
    STALE = "STALE"
    BYPASS = "BYPASS"
    DYNAMIC = "DYNAMIC"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"  # header absent


class AuditResult(str, enum.Enum):
    """Per-asset audit verdict."""

    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    INFO = "INFO"


class CdnProvider(str, enum.Enum):
    """Known CDN providers detected via response headers."""

    CLOUDFLARE = "cloudflare"
    AKAMAI = "akamai"
    FASTLY = "fastly"
    CLOUDFRONT = "cloudfront"
    GOOGLE = "google"
    AZURE = "azure"
    BUNNY = "bunny"
    STACKPATH = "stackpath"
    KEYCDN = "keycdn"
    SUCURI = "sucuri"
    UNKNOWN = "unknown"
    NONE = "none"


class FrameworkHint(str, enum.Enum):
    """Static-site / framework hints detected during crawl."""

    NEXTJS = "nextjs"
    REACT = "react"
    ANGULAR = "angular"
    VUE = "vue"
    WORDPRESS = "wordpress"
    DRUPAL = "drupal"
    STATIC = "static"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Cacheable extension set
# ---------------------------------------------------------------------------

CACHEABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".css", ".js", ".mjs", ".cjs",
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".ogg", ".mp3", ".wav", ".flac",
    ".json", ".xml", ".txt", ".map",
    ".pdf",
})

CACHEABLE_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/css",
    "application/javascript", "text/javascript", "application/x-javascript",
    "image/png", "image/jpeg", "image/webp", "image/avif", "image/gif",
    "image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon", "image/bmp",
    "font/woff", "font/woff2", "font/ttf", "font/otf",
    "application/font-woff", "application/font-woff2",
    "application/x-font-ttf", "application/x-font-opentype",
    "video/mp4", "video/webm", "video/ogg",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/flac",
    "application/json", "application/xml", "text/xml",
    "application/pdf",
    "application/manifest+json",
})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class AssetInfo(BaseModel):
    """Metadata collected for a single discovered asset."""

    url: str
    asset_type: AssetType = AssetType.OTHER
    http_status: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    cache_control: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    age: str | None = None
    cf_cache_status: CfCacheStatus = CfCacheStatus.NONE
    cf_ray: str | None = None
    cdn_provider: CdnProvider = CdnProvider.NONE
    is_cacheable: bool = False
    audit_result: AuditResult = AuditResult.INFO
    audit_message: str = ""
    error: str | None = None
    discovered_from: str | None = None

    # Warm-cache fields
    warm_cache_results: list[WarmCacheAttempt] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class WarmCacheAttempt(BaseModel):
    """Result of a single warm-cache probe."""

    attempt: int
    cf_cache_status: CfCacheStatus = CfCacheStatus.NONE
    http_status: int | None = None
    response_time_ms: float | None = None

    model_config = ConfigDict(use_enum_values=True)


# Rebuild AssetInfo so the forward-ref to WarmCacheAttempt resolves.
AssetInfo.model_rebuild()


class CloudflareInfo(BaseModel):
    """Summary of Cloudflare presence and features."""

    detected: bool = False
    cf_ray_present: bool = False
    server_header: str | None = None
    apo_detected: bool = False
    cache_rules_hints: list[str] = Field(default_factory=list)
    additional_notes: list[str] = Field(default_factory=list)


class ScanConfig(BaseModel):
    """Runtime configuration parsed from CLI arguments."""

    target_url: str
    depth: int = 3
    timeout: int = 15
    workers: int = 20
    follow_subdomains: bool = False
    warm_cache: bool = False
    warm_cache_attempts: int = 3
    verbose: bool = False
    json_output: str | None = None
    csv_output: str | None = None
    xlsx_output: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (compatible; cf-cache-audit/1.0; "
        "+https://github.com/cf-cache-audit)"
    )

    @field_validator("target_url")
    @classmethod
    def _normalise_url(cls, v: str) -> str:
        """Ensure the target URL has a scheme."""
        if not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v.rstrip("/")


class AuditSummary(BaseModel):
    """High-level statistics for the completed scan."""

    total_assets: int = 0
    cacheable_assets: int = 0
    hit: int = 0
    miss: int = 0
    expired: int = 0
    revalidated: int = 0
    stale: int = 0
    bypass: int = 0
    dynamic: int = 0
    unknown: int = 0
    none_status: int = 0
    errors: int = 0
    warnings: int = 0
    hit_ratio: float = 0.0
    cdn_breakdown: dict[str, int] = Field(default_factory=dict)
    framework_hint: FrameworkHint = FrameworkHint.UNKNOWN


class AuditReport(BaseModel):
    """Complete audit report — serialisable to JSON."""

    website: str
    scan_started: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    scan_finished: str | None = None
    cloudflare: CloudflareInfo = Field(default_factory=CloudflareInfo)
    summary: AuditSummary = Field(default_factory=AuditSummary)
    assets: list[AssetInfo] = Field(default_factory=list)
    sitemap_urls: list[str] = Field(default_factory=list)
    robots_txt: str | None = None
    framework: FrameworkHint = FrameworkHint.UNKNOWN
    config: dict[str, Any] = Field(default_factory=dict)
