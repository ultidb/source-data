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
from core.source import EventRef
from sources.usau.source import UsauSource
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
ongoingUsauEventRefs = []
upcomingUsauEventRefs = []
recentlyEndedUsauEventRefs = []
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
    return startDate <= date.today() <= endDate


def isUpcoming(startDate):
    today = date.today()
    return startDate > today and startDate <= (today + timedelta(days=10))


def isRecentlyEnded(endDate):
    today = date.today()
    return endDate < today and endDate >= (today - timedelta(days=30))


def _usau_event_ref_from_row(row, scrape_year):
    """Same calendar CSV row shape scrapeCalendar()/readInfoFromCalendarCSV
    already read (url, city, state, startDate, endDate as ISO strings) --
    built into an EventRef so the four job functions below can drive
    core.pipeline.run_pipeline's `refs=` escape hatch instead of
    scrape.py's positional-dict shape."""
    return EventRef(
        url=row[0],
        city=row[1],
        state=row[2],
        start_date=date.fromisoformat(row[3]),
        end_date=date.fromisoformat(row[4]),
        extra={"year": scrape_year},
    )


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
    global ongoingUsauEventRefs
    global upcomingUsauEventRefs
    global recentlyEndedUsauEventRefs
    ongoingTournaments = []
    upcomingTournaments = []
    recentlyEndedTournaments = []
    ongoingUsauEventRefs = []
    upcomingUsauEventRefs = []
    recentlyEndedUsauEventRefs = []

    with open(f"csv/{year}/_calendar.csv", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for row in reader:
            if row[3] != "":
                startDate = date.fromisoformat(row[3])
                endDate = date.fromisoformat(row[4])
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
                    ongoingUsauEventRefs.append(_usau_event_ref_from_row(row, int(year)))
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
                    upcomingUsauEventRefs.append(_usau_event_ref_from_row(row, int(year)))
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
                    recentlyEndedUsauEventRefs.append(_usau_event_ref_from_row(row, int(year)))

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


def _run_usau_events(refs, *, label, post=True, commit=True, live=False, refresh_rosters=False):
    """Drive core.pipeline.run_pipeline over a specific USAU EventRef subset
    (ongoing / upcoming / recently-ended -- bucketed by scrapeCalendar()
    into ongoingUsauEventRefs/upcomingUsauEventRefs/
    recentlyEndedUsauEventRefs), mirroring _run_wfdf_events above but for
    UsauSource.

    `post`/`commit` default to True (mirroring scrapeOngoingTournaments/
    scrapeOngoingTournamentsRefreshTeams/scrapeUpcomingTournaments's
    commitAndPush() calls today). `live`/`refresh_rosters` are threaded into
    UsauSource's constructor of the same names (see sources/usau/source.py)
    -- each job below passes the flags matching legacy's `disableCache`
    behaviour for that job (scrape.py:259-260)."""
    if not refs:
        log.info(f"usau: no events to scrape ({label})")
        return

    secrets = get_secrets()
    src = UsauSource(live=live, refresh_rosters=refresh_rosters)
    try:
        documents = run_pipeline(
            src,
            int(year),
            refs=refs,
            post=POST_TO_API and post,
            api_url=secrets.api_url,
            ingest_token=secrets.ingest_token,
        )
    except Exception:
        log.exception(f"usau: pipeline failed ({label})")
        return

    if commit and COMMIT_AND_PUSH and documents:
        commitSourceData()


def scrapeOngoingUsauEvents():
    # live=True: needs fresh scores every run. refresh_rosters left default
    # (team pages served from cache/TTL -- that's what
    # scrapeOngoingUsauEventsRefreshTeams and the TTL constant are for).
    _run_usau_events(ongoingUsauEventRefs, label="ongoing", live=True)


def scrapeOngoingUsauEventsRefreshTeams():
    # live=True (still wants fresh scores on its own less-frequent run) and
    # refresh_rosters=True (forces team pages to bypass the cache/TTL --
    # this is the actual "refresh teams" behaviour).
    _run_usau_events(
        ongoingUsauEventRefs, label="ongoing-refresh-teams", live=True, refresh_rosters=True
    )


def scrapeUpcomingUsauEvents():
    # No flags: upcoming events have no live scores yet, so normal caching
    # is fine -- nothing "live" is being missed.
    _run_usau_events(upcomingUsauEventRefs, label="upcoming")


def scrapeRecentlyEndedUsauEvents():
    # live=True to catch late score corrections, matching legacy's
    # live=True for this job (scrape.py:259-260). post/commit now default
    # to True like the other jobs -- previously this job scraped but never
    # posted or committed (scrapeRecentlyEndedTournaments's commitAndPush()
    # call is commented out in production), a dormant bug, not intentional.
    _run_usau_events(recentlyEndedUsauEventRefs, label="recently-ended", live=True)


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
        commitSourceData()


def commitSourceData():
    """Every core.Source implementation (WFDF, USAU) emits versioned JSON
    under data/<source>/<year>/, not CSV under csv/ (see
    MULTI-SOURCE-REDESIGN.md's repo layout). listUpdatedFiles() and
    commitToGit() already take an arbitrary path/directory -- neither is
    actually CSV-specific -- so this reuses them directly rather than
    duplicating commitAndPush()'s CSV-only listUpdatedCsvs()/v1-ingest path,
    which does not apply here (Source plugins post self-contained v2
    documents, not CSV path suffixes). Source-agnostic (data/ covers every
    source), so both _run_wfdf_events and _run_usau_events call this same
    function rather than each having their own copy."""
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
    scheduler.add_job(func=scrapeOngoingUsauEvents, trigger="interval", minutes=sched_config.ongoing_interval_minutes)
    scheduler.add_job(func=scrapeOngoingUsauEventsRefreshTeams, trigger="interval", hours=sched_config.ongoing_team_refresh_interval_hours)
    scheduler.add_job(func=scrapeUpcomingUsauEvents, trigger="interval", hours=sched_config.upcoming_interval_hours)
    scheduler.add_job(func=scrapeRecentlyEndedUsauEvents, trigger="interval", hours=sched_config.recently_ended_interval_hours)
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
    scrapeUpcomingUsauEvents()
    # scrapeRecentlyEndedUsauEvents()


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
