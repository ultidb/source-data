#!/usr/bin/env python
"""
Script to scrape all 2026 tournaments with players, commit to GitHub, and post to the receiver.

Prerequisites:
- Tor must be installed and available
- Chrome/Chromium must be installed for Selenium
- .env file should contain:
    API_URL=<your api url>

Usage:
    python scrape_2026.py
"""

import subprocess
import json
import logging as log
import requests
import os
from datetime import datetime
from dotenv import load_dotenv

from scrape import Config, scrapeCurrentYear
from tor import startTorServer, torIsRunning
import atexit

# Setup logging
log.basicConfig(
    level=log.INFO,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

# Load environment variables
load_dotenv(override=True)
API_URL = os.getenv("API_URL")

YEAR = 2026


def setup_tor():
    """Start Tor if not already running."""
    if not torIsRunning():
        log.info("Starting Tor server...")
        tor_process = startTorServer()
        atexit.register(tor_process.kill)
    else:
        log.info("Tor already running")


def scrape_all_tournaments():
    """Scrape all 2026 tournaments with player data."""
    log.info(f"Scraping all {YEAR} tournaments...")

    # Config: year, disableCache, overwriteCSVs, live, calendarOnly
    # - disableCache=True: fetch fresh data
    # - overwriteCSVs=True: update existing CSV files
    # - live=False: not a live scrape
    # - calendarOnly=False: scrape tournaments and players, not just calendar
    config = Config(
        year=YEAR,
        disableCache=True,
        overwriteCSVs=True,
        live=False,
        calendarOnly=False
    )

    scrapeCurrentYear(config)
    log.info("Finished scraping tournaments")


def list_updated_csvs():
    """Get list of updated CSV files from git status."""
    proc = subprocess.run(["git", "status", "-s"], capture_output=True)
    status = proc.stdout.decode("utf-8")
    output = []
    for line in status.split("\n"):
        items = line.strip().split(" ")
        filename = items[-1]
        if (
            len(items) > 1
            and not filename.endswith("_calendar.csv")
            and filename.startswith("csv/")
        ):
            output.append(filename)
    log.info(f"Found {len(output)} updated CSV files")
    return output


def commit_to_git():
    """Commit changes to git and push to origin/live."""
    log.info("Committing to git...")
    subprocess.run(["git", "checkout", "live"])
    subprocess.run(["git", "add", "csv"])
    message = f"Scraper run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", message])
    subprocess.run(["git", "push", "origin", "live"])
    log.info("Pushed to origin/live")


def post_to_receiver(csvs):
    """Post updated CSV list to the API receiver."""
    if not API_URL:
        log.error("API_URL not set in .env file, skipping post to receiver")
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

    log.info(f"Posting {len(csvs)} CSVs to {API_URL}/v1/ingest")
    try:
        r = requests.post(
            API_URL + "/v1/ingest",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 204:
            log.error(f"API returned {r.status_code} with message: {r.text}")
        else:
            log.info("Successfully posted to receiver")
    except Exception as e:
        log.error(f"API returned error: {e}")


def main():
    log.info(f"=== Starting {YEAR} Tournament Scraper ===")

    # Step 1: Setup Tor for proxied requests
    setup_tor()

    # Step 2: Scrape all tournaments with players
    scrape_all_tournaments()

    # Step 3: Get list of updated CSVs
    csvs = list_updated_csvs()

    if len(csvs) > 0:
        # Step 4: Commit to GitHub
        commit_to_git()

        # Step 5: Post to the receiver
        post_to_receiver(csvs)
    else:
        log.info("No updated CSVs found, skipping commit and post")

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
