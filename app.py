from inspect import Signature
from flask import Flask
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess, time, atexit, csv
from tor import startTorServer
from scrape import scrapeListOfTournamentUrls, Config
import logging as log
import pygit2

ongoingTournaments = []
upcomingTournaments = []
year = str(date.today().year) 

def print_date_time():
    print(time.strftime("%A, %d. %B %Y %I:%M:%S %p"))

def isOngoing(startDate, endDate):
    return startDate.date() <= datetime.today().date() <= endDate.date()

def isUpcoming(startDate):
    start = startDate.date()
    today = datetime.today().date()

    return start > today and start <= (today + timedelta(days=10))

def scrapeCalendar():
    print("scraping calendar")
    global year
    year = str(date.today().year)
    subprocess.run(["python", "scrape.py", "-y", year, "--debug", "-d", "--calendarOnly"])

    global ongoingTournaments
    global upcomingTournaments
    ongoingTournaments = []
    upcomingTournaments = []

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
    print(ongoingTournaments)
    print(upcomingTournaments)
    

def scrapeOngoingTournaments():
    global year
    config = Config(int(year), False, True, True, False)
    global ongoingTournaments
    scrapeListOfTournamentUrls(config, ongoingTournaments)

def scrapeUpcomingTournaments():
    global year
    config = Config(int(year), True, True, False, False)
    global upcomingTournaments
    scrapeListOfTournamentUrls(config, upcomingTournaments)

def commitToGit():
    subprocess.run(["git", "checkout", "live"])
    subprocess.run(["git", "add", "."])
    message = f"Scraper run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", message])
    subprocess.run(["git", "push", "origin", "live"])    

log.basicConfig(
        level=log.DEBUG,
        format='[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
tor_process = startTorServer()
atexit.register(tor_process.kill)
scheduler = BackgroundScheduler()
scheduler.add_job(func=scrapeCalendar, trigger="interval", days=1)
scheduler.add_job(func=scrapeOngoingTournaments, trigger="interval", minutes=5)
scheduler.add_job(func=scrapeUpcomingTournaments, trigger="interval", hours=12)
scheduler.start()
scheduler.print_jobs()

# Initial run on startup
commitToGit()
# scrapeUpcomingTournaments()
# scrapeOngoingTournaments()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

app = Flask(__name__)

@app.route("/")
def hello_world():
    return "<p>Hello, World!</p>"