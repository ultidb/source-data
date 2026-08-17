#!/usr/bin/env python
"""
Unified CLI for the USAU scraper.

Usage:
    python cli.py scrape year 2026
    python cli.py scrape tournament <url> -y 2026
    python cli.py scrape full --commit --post
    python cli.py serve --no-scheduler
"""

import atexit
import json
import logging as log
import subprocess
from datetime import datetime
from pathlib import Path

import click
import requests

from config import get_config, get_secrets


# Setup logging
def setup_logging(debug: bool = False):
    level = log.DEBUG if debug else log.INFO
    log.basicConfig(
        level=level,
        format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
def cli():
    """USAU Tournament Scraper CLI."""
    pass


@cli.group()
def scrape():
    """Scraping commands."""
    pass


@scrape.command("year")
@click.argument("year", type=int)
@click.option(
    "--source",
    default="usau",
    help=(
        "Source id to scrape (default: usau). NOTE: USAU is not ported onto the "
        "sources/ plugin path yet -- that's Phase 3 of MULTI-SOURCE-REDESIGN.md. "
        "--source=usau (the default) always uses today's existing parse.py/scrape.py "
        "CSV code path, unchanged. Any other registered source id (see `scraper "
        "sources`) uses the new core.Source plugin path: discover -> fetch_event -> "
        "parse_event -> tournament_to_document -> write_document."
    ),
)
@click.option("-d", "--disable-cache", is_flag=True, help="Ignore cached HTML files")
@click.option("-o", "--overwrite", is_flag=True, help="Overwrite existing CSV files")
@click.option("--calendar-only", is_flag=True, help="Only scrape calendar, not tournaments (usau only)")
@click.option(
    "--post", is_flag=True, help="POST emitted documents to the ingest API (registry-backed sources only)"
)
@click.option(
    "--out",
    "out_dir",
    default=None,
    type=click.Path(),
    help="Output directory for emitted documents (registry-backed sources only; default: repo root, "
    "writing data/<source>/<year>/*.json)",
)
@click.option(
    "--api-url",
    default=None,
    help="Ingest API base URL for --post (default: API_URL from .env). Matches `post-documents`.",
)
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_year_cmd(
    year: int,
    source: str,
    disable_cache: bool,
    overwrite: bool,
    calendar_only: bool,
    post: bool,
    out_dir: str,
    api_url: str,
    debug: bool,
):
    """Scrape all tournaments for a given year."""
    setup_logging(debug)

    if source == "usau":
        # USAU is not ported yet (Phase 3) -- keep delegating to today's
        # existing CSV scrape code path unchanged. See the --source help text.
        from scrape import ScrapeOptions, scrapeCurrentYear

        log.info(f"Scraping year: {year}")
        config = ScrapeOptions(year, disable_cache, overwrite, live=False, calendarOnly=calendar_only)
        scrapeCurrentYear(config)
        return

    _scrape_year_with_source(source, year, post=post, out_dir=out_dir, api_url=api_url)


def _scrape_year_with_source(
    source_id: str, year: int, *, post: bool, out_dir: str, api_url: str = None
):
    """Drive a registry-backed (non-usau) Source through the full pipeline:
    discover -> fetch_event -> parse_event -> tournament_to_document ->
    write_document, optionally POSTing the results."""
    import sources  # noqa: F401  (import side effect: registers every known source)
    from core.cache import FileCache
    from core.emit import write_document
    from core.fetch import RequestsTransport
    from core.registry import get_source
    from core.serialize import tournament_to_document

    src = get_source(source_id)
    refs = src.discover(year)
    log.info(f"discovered {len(refs)} event(s) for source={source_id} year={year}")

    documents = []
    for ref in refs:
        key = src.event_key(ref)
        cache = FileCache(source_id, year, key, RequestsTransport())
        pages = src.fetch_event(ref, cache)
        tournament = src.parse_event(pages, ref, year)
        if tournament is None:
            log.warning(f"parse_event returned None for {ref.url!r}, skipping")
            continue

        doc = tournament_to_document(
            tournament, source=source_id, source_event_id=key, source_url=ref.url
        )
        path = write_document(doc, base_dir=Path(out_dir) if out_dir else None)
        log.info(f"wrote {path}")
        documents.append(doc)

    log.info(f"scraped {len(documents)} document(s) for source={source_id} year={year}")

    if post:
        import os

        from core.ingest_client import post_documents

        secrets = get_secrets()
        resolved_api_url = api_url or secrets.api_url
        if not resolved_api_url:
            raise click.UsageError("no API URL: pass --api-url or set API_URL in .env")

        result = post_documents(
            documents,
            source=source_id,
            api_url=resolved_api_url,
            token=secrets.ingest_token,
        )
        log.info(f"posted {len(documents)} document(s): {result}")


@scrape.command("tournament")
@click.argument("url")
@click.option("-y", "--year", type=int, required=True, help="Year for the tournament")
@click.option("-d", "--disable-cache", is_flag=True, help="Ignore cached HTML files")
@click.option("-o", "--overwrite", is_flag=True, help="Overwrite existing CSV files")
@click.option("-l", "--live", is_flag=True, help="Live scraping mode")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_tournament_cmd(url: str, year: int, disable_cache: bool, overwrite: bool, live: bool, debug: bool):
    """Scrape a single tournament by URL."""
    setup_logging(debug)

    from scrape import ScrapeOptions, scrapeTournament, readInfoFromCalendarCSV

    log.info(f"Scraping tournament: {url}")
    config = ScrapeOptions(year, disable_cache, overwrite, live=live, calendarOnly=False)
    tournament_info = readInfoFromCalendarCSV(year, url)

    # Skip if tournament not found in calendar
    if tournament_info is None:
        log.error(f"Tournament URL not found in calendar CSV: {url}")
        log.error(f"Please scrape the calendar first: python cli.py scrape calendar -y {year}")
        return

    scrapeTournament(config, tournament_info, 0, 1)


@scrape.command("calendar")
@click.option("-y", "--year", type=int, default=None, help="Year to scrape (default: current year)")
@click.option("-d", "--disable-cache", is_flag=True, help="Ignore cached HTML files")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_calendar_cmd(year: int, disable_cache: bool, debug: bool):
    """Scrape only the tournament calendar."""
    setup_logging(debug)

    from datetime import date
    from scrape import ScrapeOptions, scrapeCurrentYear

    if year is None:
        year = date.today().year

    log.info(f"Scraping calendar for year: {year}")
    config = ScrapeOptions(year, disable_cache, overwriteCSVs=False, live=False, calendarOnly=True)
    scrapeCurrentYear(config)


@scrape.command("retry")
@click.argument("year", type=int)
@click.option("-d", "--disable-cache", is_flag=True, help="Ignore cached HTML files")
@click.option("-o", "--overwrite", is_flag=True, help="Overwrite existing CSV files")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_retry_cmd(year: int, disable_cache: bool, overwrite: bool, debug: bool):
    """Retry failed tournaments from errors.txt."""
    setup_logging(debug)

    from scrape import ScrapeOptions, retryErrors

    log.info(f"Retrying failed tournaments for year: {year}")
    config = ScrapeOptions(year, disable_cache, overwrite, live=False, calendarOnly=False)
    retryErrors(config)


@scrape.command("full")
@click.option("-y", "--year", type=int, default=None, help="Year to scrape (default: current year)")
@click.option("--commit", is_flag=True, help="Commit changes to git and push")
@click.option("--post", is_flag=True, help="Post updated CSVs to API")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_full_cmd(year: int, commit: bool, post: bool, debug: bool):
    """Full scrape workflow: scrape all tournaments, optionally commit and post."""
    setup_logging(debug)

    from datetime import date
    from scrape import ScrapeOptions, scrapeCurrentYear
    from tor import startTorServer, torIsRunning

    if year is None:
        year = date.today().year

    log.info(f"=== Starting {year} Tournament Scraper ===")

    # Setup Tor
    if not torIsRunning():
        log.info("Starting Tor server...")
        tor_process = startTorServer()
        atexit.register(tor_process.kill)
    else:
        log.info("Tor already running")

    # Scrape
    log.info(f"Scraping all {year} tournaments...")
    config = ScrapeOptions(year, disableCache=True, overwriteCSVs=True, live=False, calendarOnly=False)
    scrapeCurrentYear(config)
    log.info("Finished scraping tournaments")

    # Get updated CSVs
    csvs = _list_updated_csvs()

    if len(csvs) > 0:
        # Bug fix (documented in MULTI-SOURCE-REDESIGN.md's "Pre-existing
        # bugs" list): this used to call CLI-local _commit_to_git() /
        # _post_to_receiver(), which duplicated app.py's commitToGit() /
        # postUpdatedCsvListToAPI() minus the db.py bookkeeping -- so
        # CLI-triggered posts silently skipped failure tracking
        # (db.updateFailedCSVs / db.updateSuccesfulCSVs, consumed by
        # db.listFailedCSVs / app.resendFailedCSVs). Importing app.py's
        # versions instead of reimplementing them means CLI-triggered posts
        # get the same failure tracking as the scheduler-triggered ones.
        # Lazy-imported (matching this file's existing convention, e.g. the
        # `serve`/`videos` commands below) so importing cli.py itself
        # doesn't drag in Flask/apscheduler for commands that never call
        # this path.
        if commit:
            from app import commitToGit

            commitToGit("csv")
        if post:
            from app import postUpdatedCsvListToAPI

            postUpdatedCsvListToAPI(csvs)
    else:
        log.info("No updated CSVs found, skipping commit and post")

    log.info("=== Done ===")


def _list_updated_csvs():
    """Get list of updated CSV files from git status."""
    proc = subprocess.run(["git", "status", "-s"], capture_output=True)
    status = proc.stdout.decode("utf-8")
    output = []
    for line in status.split("\n"):
        items = line.strip().split(" ")
        filename = items[-1]
        if len(items) > 1 and not filename.endswith("_calendar.csv") and filename.startswith("csv/"):
            output.append(filename)
    log.info(f"Found {len(output)} updated CSV files")
    return output


@cli.command()
@click.option("-h", "--host", default=None, help="Host to bind to")
@click.option("-p", "--port", type=int, default=None, help="Port to bind to")
@click.option("--no-scheduler", is_flag=True, help="Disable background scheduler")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def serve(host: str, port: int, no_scheduler: bool, debug: bool):
    """Run the Flask server."""
    setup_logging(debug)

    config = get_config()
    secrets = get_secrets()

    # Use config defaults if not provided
    if host is None:
        host = secrets.host or config.app.server.host
    if port is None:
        port = config.app.server.port

    from app import app, prodSetup

    if not no_scheduler:
        prodSetup()

    app.run(host=host, port=port)


@cli.command()
@click.option("--commit", is_flag=True, help="Commit changes to git and push")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def videos(commit: bool, debug: bool):
    """Scrape videos from Ultiworld and Vimeo."""
    setup_logging(debug)

    from video.video import scrapeVideos

    log.info("Scraping videos...")
    scrapeVideos()
    log.info("Done scraping videos")

    if commit:
        from app import commitToGit, listUpdatedVideos

        csvs = listUpdatedVideos()
        if len(csvs) > 0:
            log.info("Committing video changes to git...")
            commitToGit("video/csv")
            log.info("Pushed to origin/live")
        else:
            log.info("No updated video CSVs found, skipping commit")


@cli.command("test-ingest")
@click.argument("csv_paths", nargs=-1, required=False)
@click.option("--api-url", default="http://localhost:3030", help="API base URL")
@click.option("--year", "-y", type=int, default=None, help="Post all CSVs from a year directory")
@click.option("--sample", "-s", is_flag=True, help="Only post 10 sample CSVs from the year")
@click.option("--dry-run", is_flag=True, help="Dry run mode (don't actually ingest)")
@click.option("--no-players", is_flag=True, help="Skip player updates")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def test_ingest(csv_paths: tuple, api_url: str, year: int, sample: bool, dry_run: bool, no_players: bool, debug: bool):
    """Post test CSVs to the ingest endpoint.

    Examples:
        scraper test-ingest csv/2026/tournament.csv
        scraper test-ingest csv/2026/*.csv
        scraper test-ingest -y 2026
        scraper test-ingest -y 2026 --sample
        scraper test-ingest -y 2026 --dry-run
    """
    setup_logging(debug)

    import glob

    paths = list(csv_paths)

    # If year specified, find all CSVs for that year
    if year is not None:
        year_csvs = glob.glob(f"csv/{year}/*.csv")
        # Exclude calendar files
        year_csvs = [p for p in year_csvs if not p.endswith("_calendar.csv")]
        paths.extend(year_csvs)

    # Limit to 10 if sample mode
    if sample and len(paths) > 10:
        paths = paths[:10]
        log.info("Sample mode: limiting to 10 CSVs")

    if not paths:
        log.error("No CSV files specified. Use CSV_PATHS or --year")
        return

    # Remove duplicates and sort
    paths = sorted(set(paths))

    log.info(f"Posting {len(paths)} CSV(s) to {api_url}/v1/ingest")
    for p in paths:
        log.info(f"  - {p}")

    payload = {
        "paths": paths,
        "updatePlayers": not no_players,
        "checkExisting": True,
        "dryRun": dry_run,
    }

    try:
        r = requests.post(
            f"{api_url}/v1/ingest",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        if r.status_code == 204:
            log.info("Success: CSVs ingested")
        else:
            log.error(f"API returned {r.status_code}: {r.text}")
    except requests.exceptions.ConnectionError:
        log.error(f"Could not connect to {api_url}")
    except Exception as e:
        log.error(f"Error: {e}")


@cli.command("sources")
def sources_cmd():
    """List registered sources (see sources/README.md to add one)."""
    import sources  # noqa: F401  (import side effect: registers every known source)
    from core.registry import list_sources

    registered = list_sources()
    if not registered:
        log.info("no sources registered")
        return
    for s in registered:
        click.echo(s.id)


@cli.command("post-documents")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--source", required=True, help="Source id these documents belong to")
@click.option("--api-url", default=None, help="API base URL (default: API_URL from .env)")
@click.option("--dry-run", is_flag=True, help="Dry run mode (don't actually ingest)")
@click.option("--no-check-existing", is_flag=True, help="Don't match against existing tournaments")
@click.option("--no-update-players", is_flag=True, help="Skip player updates")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def post_documents_cmd(
    paths: tuple,
    source: str,
    api_url: str,
    dry_run: bool,
    no_check_existing: bool,
    no_update_players: bool,
    debug: bool,
):
    """POST already-emitted wire-format JSON documents (as written by
    `scrape --source=... year ...` or `convert-legacy`) to the v2 ingest
    API, via core.ingest_client. Reads the auth token from INGEST_TOKEN in
    .env or the environment.

    Examples:
        scraper post-documents --source usau data/usau/2025/*.json
        scraper post-documents --source example data/example/2026/tiny-invite.json --dry-run
    """
    setup_logging(debug)

    import os

    from core.ingest_client import IngestError, post_documents
    from core.schema import Document

    secrets = get_secrets()
    resolved_api_url = api_url or secrets.api_url
    if not resolved_api_url:
        raise click.UsageError("no API URL: pass --api-url or set API_URL in .env")

    docs = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        docs.append(Document.model_validate(data))

    log.info(f"Posting {len(docs)} document(s) to {resolved_api_url}/v2/ingest")
    try:
        result = post_documents(
            docs,
            source=source,
            api_url=resolved_api_url,
            token=secrets.ingest_token,
            dry_run=dry_run,
            check_existing=not no_check_existing,
            update_players=not no_update_players,
        )
    except IngestError as e:
        log.error(f"post-documents failed: {e}")
        raise SystemExit(1)

    log.info(f"posted {len(docs)} document(s): {result}")


@cli.command("convert-legacy")
@click.option("--year", type=int, default=None, help="Convert a single year (e.g. 2025)")
@click.option("--all", "convert_all", is_flag=True, help="Convert the whole corpus (all years under csv/)")
@click.option(
    "--out",
    "out_dir",
    default="data",
    type=click.Path(),
    help="Output directory; documents are written to <out>/<source>/<year>/<key>.json (default: data/)",
)
@click.option("--limit", type=int, default=None, help="Convert only the first N files, for spot checks")
@click.option("--dry-run", is_flag=True, help="Validate and report, write nothing")
@click.option("--csv-root", default="csv", type=click.Path(), help="Root of the legacy CSV corpus")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def convert_legacy_cmd(
    year: int, convert_all: bool, out_dir: str, limit: int, dry_run: bool, csv_root: str, debug: bool
):
    """Convert the legacy CSV corpus (csv/<year>/*.csv, 2014-2026) into
    wire-format JSON documents, through the same models.Tournament ->
    core.schema.Document path a live scrape uses (the Phase 3 gate in
    MULTI-SOURCE-REDESIGN.md). Implementation lives in core/legacy.py; this
    is a thin CLI wrapper.

    A document that fails to parse or fails wire-format validation (e.g. a
    game naming a team with no roster block -- real, and known to occur in
    this corpus) is recorded as a failure and the conversion continues; no
    data is invented to force a bad document to validate. `_calendar.csv`
    is always skipped.

    Examples:
        scraper convert-legacy --year 2025
        scraper convert-legacy --all --dry-run
        scraper convert-legacy --year 2025 --limit 20
    """
    setup_logging(debug)

    if not year and not convert_all:
        raise click.UsageError("specify --year YYYY or --all")
    if year and convert_all:
        raise click.UsageError("--year and --all are mutually exclusive")

    from core.legacy import convert_legacy

    summary = convert_legacy(
        Path(csv_root), year=year, out_dir=Path(out_dir), limit=limit, dry_run=dry_run
    )

    for r in summary.skipped:
        log.info(f"SKIPPED   {r.path}")
    for r in summary.failed:
        log.error(f"FAILED    {r.path}: {r.error}")
    if debug:
        for r in summary.converted:
            for w in r.warnings:
                log.debug(f"  {r.path}: {w}")

    log.info(
        f"convert-legacy: converted={len(summary.converted)} "
        f"failed={len(summary.failed)} skipped={len(summary.skipped)}"
    )
    if summary.failed:
        log.info(f"{len(summary.failed)} failure(s):")
        for r in summary.failed:
            log.info(f"  {r.path}: {r.error}")


if __name__ == "__main__":
    cli()
