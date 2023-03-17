from inspect import Signature
from flask import Flask
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess, time, atexit, csv
from tor import startTorServer
from scrape import scrapeListOfTournamentUrls, Config
import logging as log
import requests, os
from dotenv import load_dotenv

ongoingTournaments = []
upcomingTournaments = []
recentlyEndedTournaments = []
load_dotenv()
COMMIT_AND_PUSH = os.getenv("COMMIT_AND_PUSH") == "True"
POST_TO_API = os.getenv("POST_TO_API") == "True"
LOAD_CALENDAR_ON_START = os.getenv("LOAD_CAL_ON_START") == "True"
API_URL = os.getenv("API_URL")
year = str(date.today().year) 

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

    return end < today and end >= (today - timedelta(days=2))

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

    with open(f'csv/{year}/_calendar.csv', newline='') as csvfile:
        reader = csv.reader(csvfile, delimiter=',', quotechar='"')
        for row in reader:
            if row[3] != "":
                startDate = datetime.strptime(row[3], '%Y-%m-%d')
                endDate = datetime.strptime(row[4], '%Y-%m-%d')
                if (isOngoing(startDate, endDate)):
                    ongoingTournaments.append({
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                    })
                elif (isUpcoming(startDate)):
                    upcomingTournaments.append({
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                    })
                elif (isRecentlyEnded(endDate)):
                    recentlyEndedTournaments.append({
                            "city": row[1],
                            "state": row[2],
                            "startDate": row[3],
                            "endDate": row[4],
                            "url": row[0],
                    })
                
    log.info(f"found {len(ongoingTournaments)} ongoing tournaments")
    log.info(f"found {len(upcomingTournaments)} upcoming tournaments")
    log.info(f"found {len(recentlyEndedTournaments)} recently ended tournaments")
    

def scrapeOngoingTournaments():
    config = Config(int(year), False, True, True, False)
    scrapeListOfTournamentUrls(config, ongoingTournaments)
    csvs = listUpdatedCsvs()
    if COMMIT_AND_PUSH:
        commitToGit()
    if POST_TO_API:
        postListToAPI(csvs, False, True, False)

def scrapeUpcomingTournaments():
    config = Config(int(year), True, True, False, False)
    scrapeListOfTournamentUrls(config, upcomingTournaments)
    csvs = listUpdatedCsvs()
    if COMMIT_AND_PUSH:
        commitToGit()
    if POST_TO_API:
        postListToAPI(csvs)

def scrapeRecentlyEndedTournaments():
    config = Config(int(year), False, True, True, False)
    scrapeListOfTournamentUrls(config, recentlyEndedTournaments)
    csvs = listUpdatedCsvs()
    if COMMIT_AND_PUSH:
        commitToGit()
    if POST_TO_API:
        postListToAPI(csvs)


def commitToGit():
    subprocess.run(["git", "checkout", "live"])
    subprocess.run(["git", "add", "csv"])
    message = f"Scraper run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", message])
    subprocess.run(["git", "push", "origin", "live"])

def listUpdatedCsvs():
    proc = subprocess.run(['git', 'status', '-s'], capture_output=True)
    status = proc.stdout.decode('utf-8')
    output = []
    for line in status.split('\n'):
        items = line.strip().split(' ')
        if len(items) > 1 and not items[1].endswith('_calendar.csv'):
            output.append(items[1])
    return output

def postListToAPI(csvs, UpdatePlayers=True, checkExisting=True, DryRun=False):
    payload = { "paths": csvs, "updatePlayers": UpdatePlayers, "checkExisting": checkExisting, "dryRun": DryRun }
    log.info(f"posting {len(csvs)} csvs to API")
    try:
        r = requests.post(API_URL, data=payload)
        if r.status_code != 204:
            log.error(f"API returned {r.status_code} with message: {r.text}")
    except Exception as e:
        log.error(f"API returned error: {e}")

def setupTor():
    tor_process = startTorServer()
    atexit.register(tor_process.kill)

def setupSchedule(): 
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=scrapeCalendar, trigger="interval", days=1)
    scheduler.add_job(func=scrapeOngoingTournaments, trigger="interval", minutes=10)
    scheduler.add_job(func=scrapeUpcomingTournaments, trigger="interval", hours=12)
    scheduler.add_job(func=scrapeRecentlyEndedTournaments, trigger="interval", hours=4)
    scheduler.start()
    scheduler.print_jobs()

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())


log.basicConfig(
        level=log.INFO,
        format='[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
app = Flask(__name__)

@app.route("/health-check")
def healthCheck():
    output = {
        "ongoingTournaments": len(ongoingTournaments),
        "upcomingTournaments": len(upcomingTournaments),
        "recentlyEndedTournaments": len(recentlyEndedTournaments),
    }

    return output

if __name__ == "__main__":
    setupTor()
    setupSchedule()

    # Initial run on startup
    scrapeCalendar(LOAD_CALENDAR_ON_START)
    # scrapeRecentlyEndedTournaments()
    # scrapeOngoingTournaments()
    scrapeUpcomingTournaments()

    app.run(port=3031)