#!/usr/bin/env python
"""
Unified CLI for the USAU scraper.

Usage:
    python cli.py scrape year 2026
    python cli.py scrape year 2026 --source=wfdf --post
    python cli.py serve --no-scheduler
"""

import json
import logging as log
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
        "Source id to scrape (default: usau). Uses the registry-backed core.Source "
        "plugin path: discover -> fetch_event -> parse_event -> tournament_to_document "
        "-> write_document (see `scraper sources` for every registered id)."
    ),
)
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
@click.option(
    "--live",
    is_flag=True,
    help="WFDF/USAU only: force a refetch of live-changing pages (WFDF: reference/games -- pools, "
    "scores, structure; USAU: the tournament schedule/pools/scores page). Roster/team pages still "
    "honour their cache TTL unless --refresh-rosters is also given.",
)
@click.option(
    "--refresh-rosters",
    is_flag=True,
    help="WFDF/USAU only: also force a refetch of every roster/team page, bypassing their cache TTL.",
)
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_year_cmd(
    year: int,
    source: str,
    post: bool,
    out_dir: str,
    api_url: str,
    live: bool,
    refresh_rosters: bool,
    debug: bool,
):
    """Scrape all tournaments for a given year."""
    setup_logging(debug)

    _scrape_year_with_source(
        source, year, post=post, out_dir=out_dir, api_url=api_url,
        live=live, refresh_rosters=refresh_rosters,
    )


def _scrape_year_with_source(
    source_id: str,
    year: int,
    *,
    post: bool,
    out_dir: str,
    api_url: str = None,
    live: bool = False,
    refresh_rosters: bool = False,
):
    """Drive a registry-backed Source through the shared
    core.pipeline.run_pipeline: discover -> fetch_event -> parse_event ->
    tournament_to_document -> write_document, optionally POSTing the
    results. app.py's WFDF/USAU scheduler jobs call the same run_pipeline
    function over a specific event subset -- see core/pipeline.py."""
    import sources  # noqa: F401  (import side effect: registers every known source)
    from core.pipeline import run_pipeline
    from core.registry import get_source

    if (live or refresh_rosters) and source_id not in ("wfdf", "usau"):
        raise click.UsageError(
            f"--live/--refresh-rosters are WFDF/USAU-specific (source={source_id!r} doesn't take them)"
        )

    if source_id == "wfdf":
        # Build a fresh instance rather than mutating the shared registry
        # singleton get_source() would return -- see WfdfSource's live /
        # refresh_rosters constructor args (WFDF source task).
        from sources.wfdf.source import WfdfSource

        src = WfdfSource(live=live, refresh_rosters=refresh_rosters)
    elif source_id == "usau":
        # Same reasoning as the wfdf branch above -- get_source() returns a
        # plain no-args instance, so build UsauSource directly to thread
        # live/refresh_rosters through (see sources/usau/source.py).
        from sources.usau.source import UsauSource

        src = UsauSource(live=live, refresh_rosters=refresh_rosters)
    else:
        src = get_source(source_id)

    resolved_api_url = None
    ingest_token = None
    if post:
        secrets = get_secrets()
        resolved_api_url = api_url or secrets.api_url
        if not resolved_api_url:
            raise click.UsageError("no API URL: pass --api-url or set API_URL in .env")
        ingest_token = secrets.ingest_token

    documents = run_pipeline(
        src,
        year,
        out_dir=Path(out_dir) if out_dir else None,
        post=post,
        api_url=resolved_api_url,
        ingest_token=ingest_token,
    )
    log.info(f"scraped {len(documents)} document(s) for source={source_id} year={year}")


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


@cli.command("wfdf-event")
@click.argument("base_url")
@click.option(
    "--season-id",
    default=None,
    help="Skip season-id discovery and use this id directly (e.g. WUCC2026).",
)
@click.option(
    "--division",
    default=None,
    help="Override the derived division (see ingest-contract.md section 4). Required if "
    "season.isnationalteams is absent or not 0/1.",
)
@click.option("--name", default=None, help="Override the derived (expanded) event name.")
@click.option("--city", default="", help="Venue city -- the WFDF API carries no venue data.")
@click.option("--state", default="", help="Venue state/province.")
@click.option("--country", default="", help="Venue country.")
@click.option(
    "--data-path",
    default="live/data",
    help="Path segment between base_url and '<season_id>_<resource>.json' (default: live/data, "
    "which both WUCC 2026 and WJUC 2026 use).",
)
@click.option(
    "--write",
    "write_flag",
    is_flag=True,
    help="Append the derived entry to sources/wfdf/events.yaml. Refuses if the season id is "
    "already present, or if any series has an unmapped gender.",
)
@click.option("--debug", is_flag=True, help="Enable debug logging")
def wfdf_event_cmd(
    base_url: str,
    season_id: str,
    division: str,
    name: str,
    city: str,
    state: str,
    country: str,
    data_path: str,
    write_flag: bool,
    debug: bool,
):
    """Derive a WfdfEvent for a new WFDF event from its base URL, by reading
    WFDF's own `<season_id>_reference.json`. Fetches through the same
    throttled transport (sources.wfdf.source.WfdfSource.make_transport())
    the scraper itself uses -- this is a handful of requests, not a scrape.

    If season-id discovery fails (see sources/wfdf/event_gen.py for what it
    tries and why), pass --season-id to skip it.

    Examples:
        scraper wfdf-event https://wjuc.wfdf.sport
        scraper wfdf-event https://results.wfdf.sport/wucc-2026 --season-id WUCC2026 --write
    """
    setup_logging(debug)

    import json

    from sources.wfdf.event_gen import (
        SeasonIdDiscoveryError,
        derive_event,
        discover_season_id,
        event_to_yaml_block,
    )
    from sources.wfdf.events import EVENTS_YAML_PATH, load_events
    from sources.wfdf.source import WfdfSource

    wfdf_source = WfdfSource()
    transport = wfdf_source.make_transport()

    resolved_season_id = season_id
    if resolved_season_id is None:
        try:
            resolved_season_id = discover_season_id(base_url, transport)
        except SeasonIdDiscoveryError as e:
            log.error(str(e))
            raise SystemExit(1)
        log.info(f"discovered season id: {resolved_season_id}")

    ref_url = wfdf_source._build_url(base_url, data_path, resolved_season_id, "reference")
    log.info(f"fetching {ref_url}")
    raw = transport(ref_url)
    payload = json.loads(raw.decode("utf-8"))

    try:
        derived = derive_event(
            payload,
            base_url=base_url,
            data_path=data_path,
            division=division,
            name=name,
            city=city,
            state=state,
            country=country,
        )
    except ValueError as e:
        log.error(f"could not derive an event: {e}")
        raise SystemExit(1)

    for w in derived.warnings:
        log.warning(w)

    yaml_block = event_to_yaml_block(derived.event)
    click.echo(yaml_block)

    if write_flag:
        if not derived.is_safe_to_write:
            raise click.UsageError(
                f"refusing to write: series with unmapped gender(s) {derived.unmapped_series} -- "
                f"fix the pasted block above by hand, or extend GENDER_MAP in "
                f"sources/wfdf/event_gen.py, then paste manually."
            )
        existing = load_events()
        if any(e.season_id == derived.event.season_id for e in existing):
            raise click.UsageError(
                f"season_id {derived.event.season_id!r} is already present in "
                f"{EVENTS_YAML_PATH} -- refusing to write a duplicate."
            )
        with open(EVENTS_YAML_PATH, "a", encoding="utf-8") as f:
            f.write("\n" + yaml_block)
        log.info(f"appended to {EVENTS_YAML_PATH}")


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
