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
@click.option("-d", "--disable-cache", is_flag=True, help="Ignore cached HTML files")
@click.option("-o", "--overwrite", is_flag=True, help="Overwrite existing CSV files")
@click.option("--calendar-only", is_flag=True, help="Only scrape calendar, not tournaments")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def scrape_year_cmd(year: int, disable_cache: bool, overwrite: bool, calendar_only: bool, debug: bool):
    """Scrape all tournaments for a given year."""
    setup_logging(debug)

    # Import here to avoid circular imports and allow logging setup first
    from scrape import ScrapeOptions, scrapeCurrentYear

    log.info(f"Scraping year: {year}")
    config = ScrapeOptions(year, disable_cache, overwrite, live=False, calendarOnly=calendar_only)
    scrapeCurrentYear(config)


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

    secrets = get_secrets()

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
        if commit:
            _commit_to_git()
        if post:
            _post_to_receiver(csvs, secrets.api_url)
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


def _commit_to_git():
    """Commit changes to git and push to origin/live."""
    log.info("Committing to git...")
    subprocess.run(["git", "checkout", "live"])
    subprocess.run(["git", "add", "csv"])
    message = f"Scraper run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", message])
    subprocess.run(["git", "push", "origin", "live"])
    log.info("Pushed to origin/live")


def _post_to_receiver(csvs: list, api_url: str):
    """Post updated CSV list to the API receiver."""
    if not api_url:
        log.error("API_URL not set, skipping post to receiver")
        return

    if len(csvs) == 0:
        log.info("No CSVs to post")
        return

    payload = {
        "paths": csvs,
        "updatePlayers": True,
        "checkExisting": True,
        "dryRun": False,
    }

    log.info(f"Posting {len(csvs)} CSVs to {api_url}/v1/ingest")
    try:
        r = requests.post(
            api_url + "/v1/ingest",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 204:
            log.error(f"API returned {r.status_code} with message: {r.text}")
        else:
            log.info("Successfully posted to receiver")
    except Exception as e:
        log.error(f"API returned error: {e}")


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
@click.option("--debug", is_flag=True, help="Enable debug logging")
def videos(debug: bool):
    """Scrape videos from Ultiworld and Vimeo."""
    setup_logging(debug)

    from video.video import scrapeVideos

    log.info("Scraping videos...")
    scrapeVideos()
    log.info("Done scraping videos")


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


if __name__ == "__main__":
    cli()
