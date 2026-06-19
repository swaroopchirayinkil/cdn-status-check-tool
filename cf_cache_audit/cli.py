"""CLI entry-point for cf-cache-audit.

Parses command-line arguments, orchestrates the crawl → analyse → report
pipeline, and manages the Rich live display (progress bars, status).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone

import aiohttp
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from cf_cache_audit import __version__
from cf_cache_audit.analyzer import Analyzer
from cf_cache_audit.crawler import Crawler
from cf_cache_audit.models import (
    AuditReport,
    ScanConfig,
)
from cf_cache_audit.reporter import (
    console,
    export_csv,
    export_json,
    export_xlsx,
    print_asset_table,
    print_cloudflare_status,
    print_summary,
    print_warm_cache_table,
)
from cf_cache_audit.utils import RateLimiter

logger = logging.getLogger("cf_cache_audit")


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

_BANNER = r"""
[bold cyan]
   ___  ___        ___           _            _             _ _ _
  / __\/ __\      / __\__ _  ___| |__   ___  / \  _   _  __| (_) |_
 / /  / _\ _____ / /  / _` |/ __| '_ \ / _ \/  \| | | |/ _` | | __|
/ /___/ / |_____/ /__| (_| | (__| | | |  __/ /\  \ |_| | (_| | | |_
\____/\/        \____/\__,_|\___|_| |_|\___\/  \/ \__,_|\__,_|_|\__|
[/bold cyan]
[dim]v{version}  —  Cloudflare Cache Analysis Tool[/dim]
"""


def _print_banner() -> None:
    console.print(_BANNER.format(version=__version__))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="cf-cache-audit",
        description=(
            "Analyse a website's assets and determine which are served "
            "from Cloudflare cache and which are not."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  cf-cache-audit https://example.com\n"
            "  cf-cache-audit example.com --depth 2 --workers 30 --json report.json\n"
            "  cf-cache-audit https://shop.example.com --warm-cache --csv report.csv\n"
            "  cf-cache-audit https://example.com --follow-subdomains --verbose\n"
        ),
    )
    parser.add_argument(
        "url",
        help="Domain name or URL to audit (e.g. https://example.com)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=3,
        help="Maximum crawl depth for internal pages (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="HTTP request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Number of concurrent workers (default: 20)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        dest="json_output",
        help="Export full report to a JSON file",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        dest="csv_output",
        help="Export asset data to a CSV file",
    )
    parser.add_argument(
        "--xlsx",
        metavar="FILE",
        dest="xlsx_output",
        help="Export asset data to an Excel (.xlsx) file",
    )
    parser.add_argument(
        "--follow-subdomains",
        action="store_true",
        default=False,
        help="Crawl pages on subdomains of the target domain",
    )
    parser.add_argument(
        "--warm-cache",
        action="store_true",
        default=False,
        help="Test cache warm-up by requesting assets multiple times",
    )
    parser.add_argument(
        "--warm-cache-attempts",
        type=int,
        default=3,
        help="Number of warm-cache probe attempts (default: 3)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Show detailed audit messages and debug logging",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


# ---------------------------------------------------------------------------
# Async pipeline
# ---------------------------------------------------------------------------

async def run_audit(config: ScanConfig) -> AuditReport:
    """Execute the full crawl → analyse → report pipeline."""
    report = AuditReport(
        website=config.target_url,
        config=config.model_dump(),
    )

    # Rate limiter: cap at 50 req/s with worker concurrency
    rate_limiter = RateLimiter(max_concurrent=config.workers, rate=50.0)

    connector = aiohttp.TCPConnector(
        limit=config.workers * 2,
        limit_per_host=10,
        enable_cleanup_closed=True,
        ssl=True,
    )

    headers = {"User-Agent": config.user_agent}

    async with aiohttp.ClientSession(
        connector=connector,
        headers=headers,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    ) as session:
        # ---- Progress display -------------------------------------------
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

        # ---- Phase 1: Cloudflare detection ------------------------------
        console.print("[bold cyan]Phase 1:[/bold cyan] Detecting Cloudflare …")
        analyzer = Analyzer(config, session, rate_limiter)
        cf_info = await analyzer.detect_cloudflare_on_target()
        report.cloudflare = cf_info
        print_cloudflare_status(cf_info)

        # ---- Phase 2: Crawling ------------------------------------------
        console.print("[bold cyan]Phase 2:[/bold cyan] Crawling website …")
        crawl_task_id = progress.add_task("Crawling pages …", total=None)

        def crawl_progress(msg: str) -> None:
            progress.update(crawl_task_id, description=msg)

        crawler = Crawler(
            config, session, rate_limiter, progress_callback=crawl_progress
        )

        with progress:
            discovered = await crawler.crawl()
            progress.update(
                crawl_task_id,
                completed=len(discovered),
                total=len(discovered),
                description=f"Crawl complete — {len(discovered)} assets found",
            )

        report.framework = crawler.framework
        report.robots_txt = crawler.robots_txt
        report.sitemap_urls = crawler.sitemap_urls

        console.print(
            f"  [green]✔[/green] Discovered [bold]{len(discovered)}[/bold] "
            f"assets across [bold]{len(crawler.visited_pages)}[/bold] pages\n"
        )

        if not discovered:
            console.print("[yellow]No assets found — nothing to analyse.[/yellow]")
            return report

        # ---- Phase 3: Validation ----------------------------------------
        console.print(
            "[bold cyan]Phase 3:[/bold cyan] Validating cache status …"
        )
        validate_task_id = progress.add_task(
            "Validating assets …", total=len(discovered)
        )

        def validate_progress(completed: int, total: int) -> None:
            progress.update(validate_task_id, completed=completed, total=total)

        analyzer.progress_callback = validate_progress

        with progress:
            assets = await analyzer.analyse(discovered)

        report.assets = assets

        # ---- Phase 4: Summary -------------------------------------------
        console.print("\n[bold cyan]Phase 4:[/bold cyan] Generating report …\n")
        summary = analyzer.build_summary(assets, framework=crawler.framework)
        report.summary = summary
        report.scan_finished = datetime.now(timezone.utc).isoformat()

        # ---- Display results --------------------------------------------
        print_asset_table(assets, verbose=config.verbose)

        if config.warm_cache:
            print_warm_cache_table(assets)

        print_summary(summary)

        # ---- Exports ----------------------------------------------------
        if config.json_output:
            export_json(report, config.json_output)
        if config.csv_output:
            export_csv(report, config.csv_output)
        if config.xlsx_output:
            export_xlsx(report, config.xlsx_output)

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    _print_banner()

    config = ScanConfig(
        target_url=args.url,
        depth=args.depth,
        timeout=args.timeout,
        workers=args.workers,
        follow_subdomains=args.follow_subdomains,
        warm_cache=args.warm_cache,
        warm_cache_attempts=args.warm_cache_attempts,
        verbose=args.verbose,
        json_output=args.json_output,
        csv_output=args.csv_output,
        xlsx_output=args.xlsx_output,
    )

    console.print(f"[bold]Target:[/bold] {config.target_url}")
    console.print(
        f"[bold]Config:[/bold] depth={config.depth}  workers={config.workers}  "
        f"timeout={config.timeout}s  warm-cache={config.warm_cache}"
    )
    console.print()

    t0 = time.monotonic()

    try:
        report = asyncio.run(run_audit(config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as exc:
        console.print(f"\n[red bold]Fatal error:[/red bold] {exc}")
        if args.verbose:
            console.print_exception()
        sys.exit(1)

    elapsed = time.monotonic() - t0
    console.print(
        f"[dim]Audit completed in {elapsed:.1f}s[/dim]\n"
    )


if __name__ == "__main__":
    main()
