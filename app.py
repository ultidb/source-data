import json
import logging as log
import atexit
import csv
import subprocess
import time

import requests
from flask import Flask
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

from config import get_config, get_secrets
from scrape import scrapeListOfTournamentUrls, ScrapeOptions
from tor import startTorServer, torIsRunning
from video.video import scrapeVideos, scrapeUltiworldAndSave
import db

from core.pipeline import run_pipeline
from sources.wfdf.events import (
    ongoing_events as wfdf_ongoing_events,
    recently_ended_events as wfdf_recently_ended_events,
    upcoming_events as wfdf_upcoming_events,
)
from sources.wfdf.source import WfdfSource

# Runtime state
ongoingTournaments = []
upcomingTournaments = []
recentlyEndedTournaments = []
year = str(date.today().year)

# Load secrets from config module
_secrets = get_secrets()
_app_config = get_config()
COMMIT_AND_PUSH = _secrets.commit_and_push
POST_TO_API = _secrets.post_to_api
LOAD_CALENDAR_ON_START = _secrets.load_cal_on_start
HOST = _secrets.host
API_URL = _secrets.api_url


def print_date_time():
    print(time.strftime("%A, %d. %B %Y %I:%M:%S %p"))


def isOngoing(startDate, endDate):
    return startDate.date() <= datetime.today().date() <= endDate.date()


def isUpcoming(startDate):
    start = startDate.date()
    today = datetime.today().date()

    return start > today and start <= (today + timedelta(days=10))


def isRecentlyEnded(endDate):
    end = endDate.date()
    today = datetime.today().date()

    return end < today and end >= (today - timedelta(days=30))


def scrapeCalendar(disableCache=True):
    global year
    year = str(date.today().year)
    log.info(f"scraping calendar for {year}")

    d = "--disableCache"
    if not disableCache:
        d = ""

    subprocess.run(["python", "scrape.py", "-y", year, "--calendarOnly", d])

    global ongoingTournaments
    global upcomingTournaments
    global recentlyEndedTournaments
    ongoingTournaments = []
    upcomingTournaments = []
    recentlyEndedTournaments = []

    with open(f"csv/{year}/_calendar.csv", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for row in reader:
            if row[3] != "":
                startDate = datetime.strptime(row[3], "%Y-%m-%d")
                endDate = datetime.strptime(row[4], "%Y-%m-%d")
                if isOngoing(startDate, endDate):
                    ongoingTournaments.append(
                        {
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                        }
                    )
                elif isUpcoming(startDate):
                    upcomingTournaments.append(
                        {
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                        }
                    )
                elif isRecentlyEnded(endDate):
                    recentlyEndedTournaments.append(
                        {
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                        }
                    )

    log.info(f"found {len(ongoingTournaments)} ongoing tournaments")
    log.info(f"found {len(upcomingTournaments)} upcoming tournaments")
    log.info(f"found {len(recentlyEndedTournaments)} recently ended tournaments")


def scrapeOngoingTournaments():
    config = ScrapeOptions(int(year), False, True, True, False)
    scrapeListOfTournamentUrls(config, ongoingTournaments)
    commitAndPush(True)


def scrapeOngoingTournamentsRefreshTeams():
    config = ScrapeOptions(int(year), True, True, True, False)
    scrapeListOfTournamentUrls(config, ongoingTournaments)
    commitAndPush(True)


def scrapeUpcomingTournaments():
    config = ScrapeOptions(int(year), True, True, False, False)
    scrapeListOfTournamentUrls(config, upcomingTournaments)
    commitAndPush()


def scrapeRecentlyEndedTournaments():
    config = ScrapeOptions(int(year), True, True, True, False)
    scrapeListOfTournamentUrls(config, recentlyEndedTournaments)
    # commitAndPush()


def _run_wfdf_events(events, *, live, refresh_rosters):
    """Drive core.pipeline.run_pipeline over a specific WFDF event subset
    (ongoing / upcoming -- see sources/wfdf/events.py), mirroring
    scrapeOngoingTournaments/scrapeOngoingTournamentsRefreshTeams/
    scrapeUpcomingTournaments above but through the shared Source pipeline
    instead of the CSV path.

    Events are grouped by year since WfdfSource.discover(year) is
    year-scoped (WFDF calls one event a "season", not a year -- see
    sources/wfdf/events.py). One WfdfSource per year-group is constructed
    with `events=` already filtered to just this subset, so discover()
    only returns refs for the events we actually want to touch.

    A whole year-group failing (e.g. a bad secrets config) is caught and
    logged so it can't kill the scheduler thread; a single event failing
    within a group is already caught inside core.pipeline.run_pipeline.
    """
    if not events:
        log.info(f"wfdf: no events to scrape (live={live}, refresh_rosters={refresh_rosters})")
        return

    by_year = {}
    for event in events:
        by_year.setdefault(event.year, []).append(event)

    secrets = get_secrets()
    any_documents = False
    for scrape_year, year_events in sorted(by_year.items()):
        src = WfdfSource(events=year_events, live=live, refresh_rosters=refresh_rosters)
        try:
            documents = run_pipeline(
                src,
                scrape_year,
                post=POST_TO_API,
                api_url=secrets.api_url,
                ingest_token=secrets.ingest_token,
            )
            any_documents = any_documents or bool(documents)
        except Exception:
            log.exception(f"wfdf: pipeline failed for year={scrape_year}")

    if COMMIT_AND_PUSH and any_documents:
        commitWfdfData()


def commitWfdfData():
    """WFDF emits versioned JSON under data/<source>/<year>/, not CSV under
    csv/ (see MULTI-SOURCE-REDESIGN.md's repo layout). listUpdatedFiles()
    and commitToGit() already take an arbitrary path/directory -- neither
    is actually CSV-specific -- so this reuses them directly rather than
    duplicating commitAndPush()'s CSV-only listUpdatedCsvs()/v1-ingest path,
    which does not apply here (WFDF posts self-contained v2 documents, not
    CSV path suffixes)."""
    docs = listUpdatedFiles("data/")
    if len(docs) > 0:
        commitToGit("data")


def scrapeOngoingWfdfEvents():
    _run_wfdf_events(wfdf_ongoing_events(), live=True, refresh_rosters=False)


def scrapeWfdfEventsRefreshRosters():
    _run_wfdf_events(wfdf_ongoing_events(), live=True, refresh_rosters=True)


def scrapeUpcomingWfdfEvents():
    _run_wfdf_events(wfdf_upcoming_events(), live=False, refresh_rosters=False)


def scrapeAndPushVideos():
    scrapeVideos()
    commitAndPushVideos()

def scrapeOneTournamentByUrl(url):
    with open(f"csv/{year}/_calendar.csv", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        tournaments = []
        for row in reader:
            if row[0] == url:
                tournaments.append({
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                        })
        config = ScrapeOptions(int(year), True, True, False, False)
        scrapeListOfTournamentUrls(config, tournaments)


def commitAndPushVideos():
    csvs = listUpdatedVideos()
    if len(csvs) > 0:
        if COMMIT_AND_PUSH:
            commitToGit("video/csv")
        if POST_TO_API:
            postUpdatedCsvListToAPI(csvs)


def commitAndPush(isOngoing=False):
    csvs = listUpdatedCsvs()
    if len(csvs) > 0:
        if COMMIT_AND_PUSH:
            commitToGit("csv")
        if POST_TO_API:
            time.sleep(5)
            if isOngoing:
                postUpdatedCsvListToAPI(csvs, False, True, False)
            else:
                postUpdatedCsvListToAPI(csvs)


def commitToGit(directory):
    subprocess.run(["git", "checkout", "live"])
    subprocess.run(["git", "add", directory])
    message = f"Scraper run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", message])
    subprocess.run(["git", "push", "origin", "live"])


def listUpdatedCsvs():
    return listUpdatedFiles("csv/")


def listUpdatedVideos():
    return listUpdatedFiles("video/csv/")


def listUpdatedFiles(path):
    proc = subprocess.run(["git", "status", "-s"], capture_output=True)
    status = proc.stdout.decode("utf-8")
    output = []
    for line in status.split("\n"):
        items = line.strip().split(" ")
        filename = items[-1]
        if (
            len(items) > 1
            and not filename.endswith("_calendar.csv")
            and filename.startswith(path)
        ):
            output.append(filename)
    return output


def resendFailedCSVs():
    postUpdatedCsvListToAPI(db.listFailedCSVs())


def postUpdatedCsvListToAPI(csvs, UpdatePlayers=True, checkExisting=True, DryRun=False):
    payload = {
        "paths": csvs,
        "updatePlayers": UpdatePlayers,
        "checkExisting": checkExisting,
        "dryRun": DryRun,
    }
    log.info(f"posting {len(csvs)} csvs to ingest endpoint")
    try:
        r = requests.post(
            API_URL + "/v1/ingest",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 204:
            log.error(f"api returned {r.status_code} with message: {r.text}")
            db.updateFailedCSVs(csvs)
        else:
            db.updateSuccesfulCSVs(csvs)
    except Exception as e:
        log.error(f"api returned error: {e}")
        db.updateFailedCSVs(csvs)


def setupTor():
    if not torIsRunning():
        log.info("Starting Tor server...")
        tor_process = startTorServer()
        atexit.register(tor_process.kill)


def setup_scheduler(config=None):
    """Set up background scheduler with configurable intervals."""
    if config is None:
        config = _app_config

    sched_config = config.scheduler
    # Single-worker executor: jobs queue and run one at a time instead of
    # overlapping, since several of them run `git commit`/`git push` against
    # the same working directory and can't safely run concurrently.
    scheduler = BackgroundScheduler(executors={"default": ThreadPoolExecutor(max_workers=1)})
    scheduler.add_job(func=scrapeCalendar, trigger="interval", hours=sched_config.calendar_interval_hours)
    scheduler.add_job(func=scrapeOngoingTournaments, trigger="interval", minutes=sched_config.ongoing_interval_minutes)
    scheduler.add_job(func=scrapeOngoingTournamentsRefreshTeams, trigger="interval", hours=sched_config.ongoing_team_refresh_interval_hours)
    scheduler.add_job(func=scrapeUpcomingTournaments, trigger="interval", hours=sched_config.upcoming_interval_hours)
    scheduler.add_job(func=scrapeRecentlyEndedTournaments, trigger="interval", hours=sched_config.recently_ended_interval_hours)
    scheduler.add_job(func=scrapeAndPushVideos, trigger="interval", hours=sched_config.videos_interval_hours)
    scheduler.add_job(func=scrapeOngoingWfdfEvents, trigger="interval", minutes=sched_config.wfdf_ongoing_interval_minutes)
    scheduler.add_job(func=scrapeWfdfEventsRefreshRosters, trigger="interval", hours=sched_config.wfdf_roster_refresh_interval_hours)
    scheduler.add_job(func=scrapeUpcomingWfdfEvents, trigger="interval", hours=sched_config.wfdf_upcoming_interval_hours)
    scheduler.start()
    scheduler.print_jobs()

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())


def prodSetup(config=None):
    """Production setup: initialize database, Tor, scheduler, and scrape calendar."""
    db.create_tournaments_db()
    setupTor()
    setup_scheduler(config)
    scrapeCalendar(True)
    scrapeUpcomingTournaments()
    # scrapeRecentlyEndedTournaments()


log.basicConfig(
    level=log.INFO,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)


def create_app(config=None):
    """Flask application factory."""
    flask_app = Flask(__name__)

    @flask_app.route("/health-check")
    def healthCheck():
        output = {
            "ongoingTournaments": len(ongoingTournaments),
            "upcomingTournaments": len(upcomingTournaments),
            "recentlyEndedTournaments": len(recentlyEndedTournaments),
            "wfdfOngoingEvents": len(wfdf_ongoing_events()),
            "wfdfUpcomingEvents": len(wfdf_upcoming_events()),
            "wfdfRecentlyEndedEvents": len(wfdf_recently_ended_events()),
        }
        return output

    @flask_app.after_request
    def add_cors_header(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return flask_app


# Default app instance for backward compatibility
app = create_app()
