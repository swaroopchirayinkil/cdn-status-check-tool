# cf-cache-audit

A production-quality Python CLI tool that crawls any website, discovers all static assets, and audits their **Cloudflare cache status**. It tells you exactly which assets are being served from cache and which are not — helping you identify misconfigurations, missing cache rules, and optimisation opportunities.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Options Reference](#cli-options-reference)
- [Usage Examples](#usage-examples)
- [How It Works](#how-it-works)
- [Output Explained](#output-explained)
- [Export Formats](#export-formats)
- [Cloudflare Detection Details](#cloudflare-detection-details)
- [CDN Provider Detection](#cdn-provider-detection)
- [Framework Detection](#framework-detection)
- [Cache Status Reference](#cache-status-reference)
- [Audit Verdicts](#audit-verdicts)
- [Warm-Cache Testing](#warm-cache-testing)
- [Project Structure](#project-structure)
- [Development](#development)
- [Known Limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

### Core Capabilities

| Category | Details |
|---|---|
| **Website Crawling** | Recursive page crawl with configurable depth. Parses HTML to discover all linked assets. |
| **Asset Discovery** | Finds images, CSS, JS, fonts, videos, audio, SVGs, web manifests, iframes, and more. |
| **Deep Inspection** | Downloads CSS files to find `url()` and `@font-face` references. Inspects JS for dynamic imports, chunks, and lazy-loaded assets. |
| **Cloudflare Detection** | Detects Cloudflare proxy, APO (Automatic Platform Optimization), and Cache Rules via response headers. |
| **Multi-CDN Detection** | Identifies 10+ CDN providers (Cloudflare, Akamai, CloudFront, Fastly, Google, Azure, Bunny, StackPath, KeyCDN, Sucuri). |
| **Cache Validation** | HEAD request per asset (falls back to GET if HEAD returns 405). Retries with exponential backoff. |
| **Warm-Cache Testing** | Sends multiple requests to observe MISS → HIT cache warm-up transitions. |
| **Framework Detection** | Identifies Next.js, React, Angular, Vue, WordPress, Drupal, or static sites. |
| **Rich Terminal UI** | Colour-coded tables, progress bars, spinner animations, and a summary panel using the Rich library. |
| **Export** | JSON (full report), CSV (asset data), and Excel `.xlsx` (formatted workbook with hyperlinks). |
| **Performance** | Fully async with `asyncio` + `aiohttp`. Token-bucket rate limiter. Configurable concurrency. |
| **Sitemap & Robots** | Fetches and parses `/sitemap.xml` and `/robots.txt` automatically. |

### What Assets Are Discovered

The tool parses the following HTML elements:

| HTML Tag | Attribute | Asset Type |
|---|---|---|
| `<img>` | `src`, `srcset` | Images |
| `<script>` | `src` | JavaScript |
| `<link rel="stylesheet">` | `href` | CSS |
| `<link rel="icon">` | `href` | Favicon / Images |
| `<link rel="manifest">` | `href` | Web Manifest |
| `<link rel="preload">` | `href` | Fonts, Images, CSS, JS |
| `<video>` | `src`, `poster` | Video, Images |
| `<audio>` | `src` | Audio |
| `<source>` | `src`, `srcset` | Video, Audio, Images |
| `<iframe>` | `src` | Embedded pages |
| `<style>` blocks | `url()` | CSS-referenced assets |
| Inline `style=""` | `url()` | Background images, etc. |

Additionally, for every discovered CSS file, the tool downloads it and extracts:
- `url(...)` references (backgrounds, cursors, etc.)
- `@font-face` font file URLs

For JavaScript files, it performs best-effort extraction of:
- `import("...")` dynamic imports
- `fetch("...")` calls
- Next.js `/_next/static/` chunk paths
- Chunk/bundle/lazy-loaded file path patterns

---

## Installation

### Prerequisites

- **Python 3.12** or newer
- **pip** (Python package manager)

### Option 1: Install from source (recommended)

```bash
# Clone the repository
git clone https://github.com/your-org/cf-cache-audit.git
cd cf-cache-audit

# Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate    # Linux / macOS
# .venv\Scripts\activate     # Windows

# Install the tool (editable mode with dev dependencies)
pip install -e ".[dev]"
```

After installation, the `cf-cache-audit` command is available globally within the virtual environment.

### Option 2: Install dependencies only

```bash
pip install -r requirements.txt
python -m cf_cache_audit --help
```

### Dependencies

| Package | Purpose |
|---|---|
| `aiohttp` | Async HTTP client for high-performance crawling |
| `beautifulsoup4` + `lxml` | HTML parsing and asset extraction |
| `pydantic` | Data validation and serialisation (models) |
| `rich` | Terminal UI — tables, progress bars, panels |
| `openpyxl` | Excel (.xlsx) export with formatting |
| `certifi` | SSL certificate bundle |

---

## Quick Start

### Scan a website

```bash
cf-cache-audit https://example.com
```

### Scan and export to Excel

```bash
cf-cache-audit https://example.com --xlsx report.xlsx
```

### Scan with all exports

```bash
cf-cache-audit https://example.com --json report.json --csv report.csv --xlsx report.xlsx
```

### Run as a Python module

```bash
python -m cf_cache_audit https://example.com
```

---

## CLI Options Reference

```
cf-cache-audit [OPTIONS] URL
```

### Positional Argument

| Argument | Description |
|---|---|
| `url` | **(Required)** Domain name or full URL to audit. If no scheme is provided, `https://` is prepended automatically. |

### Optional Arguments

| Option | Default | Description |
|---|---|---|
| `--depth N` | `3` | Maximum crawl depth for following internal page links. `0` = homepage only. Higher values discover more assets but take longer. |
| `--timeout N` | `15` | HTTP request timeout in seconds per request. Increase for slow servers. |
| `--workers N` | `20` | Number of concurrent async workers. Higher = faster but more aggressive. Reduce if you get rate-limited. |
| `--json FILE` | — | Export the **full audit report** (summary + all assets + Cloudflare info) to a JSON file. |
| `--csv FILE` | — | Export **asset-level data** to a CSV file. One row per asset with all collected headers. |
| `--xlsx FILE` | — | Export to a **formatted Excel workbook** with an "Assets" sheet (colour-coded rows, clickable URL hyperlinks, auto-filters) and a "Summary" sheet. |
| `--follow-subdomains` | `false` | Also crawl pages on subdomains of the target domain (e.g., `cdn.example.com` when scanning `example.com`). |
| `--warm-cache` | `false` | Enable warm-cache testing — sends multiple requests per cacheable asset to observe MISS → HIT transitions. |
| `--warm-cache-attempts N` | `3` | Number of requests per asset during warm-cache testing. |
| `--verbose`, `-v` | `false` | Show detailed audit messages in the table and enable debug logging to stderr. |
| `--version`, `-V` | — | Print version number and exit. |
| `--help`, `-h` | — | Show help message and exit. |

---

## Usage Examples

### Basic audit (homepage + 3 levels deep)

```bash
cf-cache-audit https://example.com
```

### Shallow scan (homepage only)

```bash
cf-cache-audit https://example.com --depth 0
```

### Fast scan with more workers

```bash
cf-cache-audit https://example.com --workers 50 --timeout 10
```

### Deep scan including subdomains

```bash
cf-cache-audit https://example.com --depth 5 --follow-subdomains
```

### Warm-cache analysis

```bash
cf-cache-audit https://example.com --warm-cache --warm-cache-attempts 5
```

### Export to all formats with verbose output

```bash
cf-cache-audit https://example.com \
    --json report.json \
    --csv report.csv \
    --xlsx report.xlsx \
    --verbose
```

### Scan a bare domain (https:// added automatically)

```bash
cf-cache-audit example.com
```

---

## How It Works

The tool runs in **4 phases**:

### Phase 1: Cloudflare Detection

A HEAD request is sent to the target URL. Response headers are inspected for Cloudflare signals (`server: cloudflare`, `cf-ray`, `cf-cache-status`, `cf-apo-via`, `cf-edge-cache`).

### Phase 2: Crawling

1. The homepage HTML is downloaded and parsed.
2. All asset URLs are extracted from HTML tags, inline styles, and `<style>` blocks.
3. Every discovered CSS file is downloaded and parsed for `url()` references.
4. Every discovered JS file is inspected for dynamic imports and chunk paths.
5. `/robots.txt` and `/sitemap.xml` are fetched concurrently.
6. If `--depth > 0`, internal page links (`<a href>`) are followed recursively.

### Phase 3: Cache Validation

For every discovered asset:
1. A **HEAD** request is sent (falls back to **GET** if the server returns 405).
2. Response headers are collected: `content-type`, `cache-control`, `etag`, `cf-cache-status`, etc.
3. The asset is classified as cacheable or not based on file extension and content-type.
4. An audit verdict (PASS / WARNING / ERROR / INFO) is assigned.
5. If `--warm-cache` is enabled, multiple requests are sent to observe cache warm-up.

All requests run concurrently with configurable worker count, rate limiting (50 req/s), and automatic retry with exponential backoff (up to 2 retries).

### Phase 4: Reporting

Results are displayed in a Rich terminal table, followed by a summary panel. If export flags are set, JSON/CSV/Excel files are written.

---

## Output Explained

### Cloudflare Detection Banner

```
╭─── ☁  Cloudflare Detection ─────────────────────────╮
│                                                       │
│  Cloudflare detected: ✔ YES                           │
│    Server header : cloudflare                         │
│    CF-Ray        : present                            │
│    APO           : ✔ detected                         │
│                                                       │
╰───────────────────────────────────────────────────────╯
```

### Asset Table Columns

| Column | Description |
|---|---|
| **#** | Row number |
| **URL** | Full asset URL (shown without truncation) |
| **TYPE** | Asset type: `css`, `javascript`, `image`, `font`, `svg`, `video`, `audio`, `manifest`, `html`, `other` |
| **STATUS** | HTTP status code (200, 304, 404, etc.) |
| **CF CACHE** | Cloudflare cache status: `HIT`, `MISS`, `DYNAMIC`, `BYPASS`, `EXPIRED`, `REVALIDATED`, `STALE`, `UNKNOWN`, `NONE` |
| **CACHEABLE** | Whether the asset *should* be cacheable based on its extension/content-type (`YES` / `no`) |
| **CDN** | Detected CDN provider serving this asset |
| **RESULT** | Audit verdict: `PASS` (green), `WARNING` (yellow), `ERROR` (red), `INFO` (dim) |
| **MESSAGE** | *(verbose mode only)* Detailed explanation of the verdict |

### Summary Panel

```
╭───────── 📊  Audit Summary ──────────╮
│                                       │
│  Total assets      : 484              │
│  Cacheable assets  : 484              │
│                                       │
│  HIT             : 73                 │
│  MISS            : 0                  │
│  EXPIRED         : 1                  │
│  REVALIDATED     : 177                │
│  BYPASS          : 0                  │
│  DYNAMIC         : 231                │
│                                       │
│  Hit ratio       : 15.1%              │
│  Framework       : nextjs             │
│                                       │
│  CDN breakdown:                       │
│    cloudflare      : 483              │
│    none            : 1                │
│                                       │
╰───────────────────────────────────────╯
```

The **hit ratio** is colour-coded:
- **Green** (≥ 80%) — Good cache performance
- **Yellow** (50–79%) — Room for improvement
- **Red** (< 50%) — Poor cache performance, needs attention

---

## Export Formats

### `--json report.json`

Full structured report including metadata, Cloudflare info, summary statistics, and all asset details.

```json
{
  "website": "https://example.com",
  "scan_started": "2026-06-19T12:00:00+00:00",
  "scan_finished": "2026-06-19T12:00:28+00:00",
  "cloudflare": {
    "detected": true,
    "cf_ray_present": true,
    "server_header": "cloudflare",
    "apo_detected": false,
    "cache_rules_hints": [],
    "additional_notes": []
  },
  "summary": {
    "total_assets": 484,
    "cacheable_assets": 484,
    "hit": 73,
    "miss": 0,
    "dynamic": 231,
    "hit_ratio": 15.1
  },
  "assets": [
    {
      "url": "https://example.com/css/main.css",
      "asset_type": "css",
      "http_status": 200,
      "content_type": "text/css",
      "cf_cache_status": "HIT",
      "is_cacheable": true,
      "audit_result": "PASS",
      "audit_message": "Served from cache"
    }
  ]
}
```

### `--csv report.csv`

Flat file with one row per asset. Columns: `url`, `asset_type`, `http_status`, `content_type`, `content_length`, `cache_control`, `etag`, `last_modified`, `age`, `cf_cache_status`, `cf_ray`, `cdn_provider`, `is_cacheable`, `audit_result`, `audit_message`, `error`.

### `--xlsx report.xlsx`

A formatted Excel workbook containing two sheets:

**Assets sheet:**
- Blue header row with white bold text
- Rows colour-coded by audit result (green = PASS, yellow = WARNING, red = ERROR, grey = INFO)
- URL column contains **clickable hyperlinks**
- Auto-filter enabled on all columns
- Frozen header row for easy scrolling
- URL column set to 80 characters wide; other columns auto-sized

**Summary sheet:**
- Website URL, scan timestamps
- Cloudflare detection details
- Full cache status breakdown
- Hit ratio percentage

---

## Cloudflare Detection Details

The tool checks these headers on the target URL:

| Header | Signal |
|---|---|
| `server: cloudflare` | Site is proxied through Cloudflare |
| `cf-ray` | Cloudflare request ID — confirms Cloudflare is in the path |
| `cf-cache-status` | Cloudflare is caching (or evaluating) this response |
| `cf-apo-via` | Cloudflare APO (Automatic Platform Optimization) is active |
| `cf-edge-cache` | APO edge caching is enabled |
| `cache-tag` | Cloudflare Cache Tags are in use |

### Cache Rules Hints

The tool flags potential Cloudflare Cache Rule or Page Rule misconfigurations:

- **BYPASS without `no-cache`**: `cf-cache-status: BYPASS` but `Cache-Control` doesn't contain `no-cache` — suggests a Page Rule or Cache Rule is forcing bypass.
- **DYNAMIC for cacheable content-type**: A CSS, JS, image, or font file returns `cf-cache-status: DYNAMIC` — Cloudflare isn't caching it despite the content type being typically cacheable.

---

## CDN Provider Detection

For every asset, the tool identifies which CDN served it by inspecting response headers:

| CDN | Detection Method |
|---|---|
| **Cloudflare** | `server: cloudflare` |
| **Akamai** | `server: AkamaiGHost` or `x-akamai-transformed` header |
| **CloudFront** | `x-amz-cf-id`, `x-amz-cf-pop`, or `x-cache` containing "cloudfront" |
| **Fastly** | `x-fastly-request-id` or `x-served-by: cache-*` |
| **Google** | `server: gws` or `server: gse` |
| **Azure CDN** | `x-azure-ref` or `x-ms-ref` header |
| **Bunny CDN** | `server: BunnyCDN` or `x-cdn: bunny` |
| **StackPath** | `server: stackpath` |
| **KeyCDN** | `server: keycdn` |
| **Sucuri** | `x-sucuri-id` header |

---

## Framework Detection

Detected from HTML content on the first crawled page:

| Framework | Detection Signal |
|---|---|
| **Next.js** | `/_next/` paths or `__next` in HTML |
| **React** | `data-reactroot` attribute |
| **Angular** | `ng-version` or `ng-app` attributes |
| **Vue** | `data-v-` prefixed attributes |
| **WordPress** | `wp-content` or `wp-includes` paths |
| **Drupal** | `sites/default/files` paths |
| **Static** | None of the above detected |

---

## Cache Status Reference

These are the possible values for the `cf-cache-status` header:

| Status | Meaning |
|---|---|
| **HIT** | Asset was served from Cloudflare's edge cache. ✅ Best case. |
| **MISS** | Asset was not in cache; fetched from origin. Will be cached for next request. |
| **EXPIRED** | Cached copy existed but had expired; re-fetched from origin. |
| **REVALIDATED** | Cached copy was revalidated with the origin (304 Not Modified). |
| **STALE** | Stale cached copy was served (origin may be down). |
| **BYPASS** | Cloudflare was told not to cache this asset (via Cache Rules, Page Rules, or `Cache-Control`). |
| **DYNAMIC** | Cloudflare considers this asset non-cacheable by default (e.g., HTML pages). |
| **NONE** | No `cf-cache-status` header present — asset may not be going through Cloudflare. |

---

## Audit Verdicts

| Verdict | Colour | Meaning |
|---|---|---|
| **PASS** | 🟢 Green | Asset is cacheable AND served from cache (HIT, REVALIDATED, or STALE). |
| **WARNING** | 🟡 Yellow | Asset *should* be cacheable but is NOT being served from cache (MISS, DYNAMIC, BYPASS, EXPIRED). This is the most actionable finding. |
| **ERROR** | 🔴 Red | HTTP error (4xx/5xx) or the request failed entirely. |
| **INFO** | ⚪ Grey | Asset is not expected to be cacheable (e.g., HTML pages), or no cache information available. |

### What to do about WARNINGs

If you see many WARNING results, check:

1. **Cloudflare Cache Rules** — Ensure your static file extensions are included in caching rules.
2. **Page Rules** — Check if any "Bypass Cache" page rules are too broad.
3. **Cache-Control headers** — Your origin server may be sending `no-cache` or `no-store` headers.
4. **Query strings** — Assets with query strings may not be cached by default.
5. **Cookie-based bypasses** — Cloudflare may skip caching if certain cookies are present.

---

## Warm-Cache Testing

When `--warm-cache` is enabled, the tool sends multiple requests (default: 3) to each cacheable asset and records the cache status of each attempt.

### How it works

```
Request #1 → MISS    (asset not in cache, fetched from origin)
Request #2 → HIT     (now served from cache)
Request #3 → HIT     (still cached)
```

### Warm-cache results table

The tool shows a table indicating whether each asset successfully warmed up:
- **✔ YES** — Cache transitioned from MISS/EXPIRED to HIT
- **✔ Already cached** — All attempts returned HIT
- **✘ NO** — Cache never returned HIT (may indicate a configuration issue)

### Usage

```bash
cf-cache-audit https://example.com --warm-cache --warm-cache-attempts 5
```

---

## Project Structure

```
cf-cache-audit/
├── cf_cache_audit/
│   ├── __init__.py         # Package metadata and version
│   ├── __main__.py         # python -m entry point
│   ├── cli.py              # CLI argument parsing, Rich progress, async pipeline
│   ├── crawler.py          # Async website crawler, HTML/CSS/JS parsing
│   ├── analyzer.py         # Cache status validation, warm-cache, summary stats
│   ├── cloudflare.py       # Cloudflare & CDN detection via headers
│   ├── reporter.py         # Rich terminal output, JSON/CSV/Excel export
│   ├── models.py           # Pydantic v2 models, enumerations, constants
│   └── utils.py            # URL helpers, classification, rate limiter, retry
├── tests/
│   ├── test_models.py      # Model creation, validation, serialisation
│   ├── test_utils.py       # URL normalisation, classification, CSS/JS extraction
│   ├── test_cloudflare.py  # Header detection, APO, CDN fingerprinting
│   ├── test_crawler.py     # HTML parsing, srcset, framework detection
│   └── test_analyzer.py    # Summary computation, CDN breakdown
├── pyproject.toml          # PEP 621 project config (dependencies, scripts, tools)
├── requirements.txt        # Dependency pins for quick install
└── README.md               # This file
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests (99 tests)
pytest -v

# Run tests with coverage
pytest --cov=cf_cache_audit --cov-report=term-missing

# Type checking
mypy cf_cache_audit/

# Linting
ruff check cf_cache_audit/
```

---

## Known Limitations

### Crawling

- **JavaScript-rendered pages**: The crawler downloads raw HTML — it does not execute JavaScript. Single-page applications (SPAs) that render content entirely client-side will have fewer assets discovered. The tool partially compensates by inspecting JS files for dynamic imports and chunk paths.
- **Authentication**: Pages behind login walls or requiring cookies/tokens are not supported. The crawler does not maintain authenticated sessions.
- **Rate limiting by the target**: Aggressive crawling (high `--workers`) may trigger rate limiting or WAF blocks from the target site. Reduce workers if you see many 429 or 403 errors.
- **robots.txt compliance**: The tool fetches `robots.txt` for informational purposes but does **not** enforce its crawl rules. Use responsibly.

### Cache Analysis

- **Point-in-time snapshot**: Cache status can change between requests. A MISS on one request may become a HIT seconds later. Use `--warm-cache` to observe this behaviour.
- **Edge-specific results**: Cloudflare cache is per-PoP (Point of Presence). Results reflect the cache state at the edge closest to your location. A different geographic location may see different cache statuses.
- **HEAD vs GET differences**: Some origins or CDN configurations return different headers for HEAD and GET requests. The tool falls back to GET if HEAD returns 405, but subtle header differences may still exist.
- **Dynamic cache keys**: If Cloudflare is configured to vary cache by query string, cookie, or device type, the tool's requests may not match the cache key used for real visitor traffic.

### Asset Discovery

- **Incomplete JS parsing**: JavaScript URL extraction is regex-based (best-effort). It catches common patterns (dynamic imports, fetch calls, Next.js chunks) but cannot fully parse arbitrary JavaScript.
- **No Source Map following**: The tool does not download or parse `.map` files to discover additional source files.
- **CSS `@import`**: Nested `@import` statements in CSS are not currently followed recursively beyond the first level.

### Export

- **Excel URL limit**: Excel cells have a 65,530 character hyperlink limit. Extremely long URLs may not be clickable in the Excel export (the URL text will still be present).

### General

- **No Windows-native binary**: Requires Python 3.12+ installed. No standalone executable is provided.
- **IPv6**: Not explicitly tested on IPv6-only targets.

---

## Error Handling

The tool gracefully handles common failure scenarios without crashing:

| Error | Behaviour |
|---|---|
| **DNS failure** | Asset is marked as ERROR with "Request failed after retries" |
| **SSL certificate errors** | Logged and reported; scan continues |
| **HTTP 403 Forbidden** | Recorded in the table with ERROR verdict |
| **HTTP 404 Not Found** | Recorded in the table with ERROR verdict |
| **Timeout** | Retried up to 2 times with exponential backoff (0.5s, 1s) |
| **Redirect loops** | aiohttp's built-in redirect limit (10) prevents infinite loops |
| **Connection refused** | Logged; asset marked as failed |
| **Invalid URL** | Filtered out during URL normalisation (never queued for validation) |
| **Keyboard interrupt (Ctrl+C)** | Clean shutdown with exit code 130 |

---

## Troubleshooting

### "Too many open files" error

Reduce the worker count:
```bash
cf-cache-audit https://example.com --workers 5
```

Or increase your system's file descriptor limit:
```bash
ulimit -n 4096
```

### Many 403 or 429 errors

The target site may be rate-limiting your requests. Reduce concurrency:
```bash
cf-cache-audit https://example.com --workers 5 --timeout 20
```

### Scan takes too long

Reduce crawl depth or limit to homepage only:
```bash
cf-cache-audit https://example.com --depth 0
```

### No assets found

The site may be a JavaScript SPA that renders content client-side. Try checking the page source in your browser — if there are no `<img>`, `<script>`, or `<link>` tags in the raw HTML, the tool won't find them.

---

## License

MIT
