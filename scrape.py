import sys
import getopt
import csv
from enum import Enum
from bs4 import BeautifulSoup
import requests
import json
import atexit
import logging as log
from pathlib import Path
from os.path import exists
from dotenv import load_dotenv
from parse import (
    addRosterToTeam,
    addInfoToTeam,
    parseTournament,
    parseTournamentCalendar,
)
from tor import torIsRunning, startTorServer


load_dotenv()
config = None

seasonIdMap = {
    2014: 4,
    2015: 5,
    2016: 6,
    2017: 7,
    2018: 8,
    2019: 14,
    2020: 15,
    2021: 16,
    2022: 17,
    2023: 18,
    2024: 19,
    2025: 20,
}


class Config:
    def __init__(self, year, disableCache, overwriteCSVs, live, calendarOnly):
        self.disableCache = disableCache
        self.overwriteCSVs = overwriteCSVs
        self.year = year
        self.live = live
        self.calendarOnly = calendarOnly


class PageType(Enum):
    YEAR_CALENDAR = 1
    TOURNAMENT = 2
    TEAM = 3


def writeContentToFile(path, file, content):
    Path(path).mkdir(parents=True, exist_ok=True)

    # TODO: Trim excess data from html files

    with open(path + file, "wb") as f:
        f.write(content)


def makeProxiedRequest(url):
    log.debug(f"Making proxied request to {url}")

    if not torIsRunning():
        tor_process = startTorServer()
        atexit.register(tor_process.kill)

    response = requests.get(
        url,
        proxies={"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"},
        timeout=600,
    )

    if response.status_code != 200:
        log.error(f"Request to {url} failed with status code {response.status_code}")
        log.error(json.dumps(response, indent=2))
        sys.exit(1)

    return response.content


def loadPage(config, url, pageType, tournamentName=None, teamName=None):
    log.debug(f"loading page: {url}")
    path = f"html/{config.year}/"
    Path(path).mkdir(parents=True, exist_ok=True)
    file = None

    disableCache = config.disableCache

    if pageType == PageType.YEAR_CALENDAR:
        file = "calendar.html"
    elif pageType == PageType.TOURNAMENT:
        if config.live:
            disableCache = True
        path += tournamentName + "/"
        file = "tournament.html"
    elif pageType == PageType.TEAM:
        path += tournamentName + "/"
        file = f"{teamName}.html"

    if not disableCache and exists(path + file):
        log.debug(f"File {path + file} already exists, using local copy")
        with open(path + file, "rb") as f:
            return f.read()

    content = makeProxiedRequest(url)
    writeContentToFile(path, file, content)
    return content


def writeTournamentToCSV(config, tournament, tournamentFilePath):
    path = f"csv/{config.year}/"
    Path(path).mkdir(parents=True, exist_ok=True)

    with open(tournamentFilePath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                tournament.name,
                tournament.division,
                tournament.datetime.day,
                tournament.datetime.month,
                tournament.datetime.year,
            ]
        )
        writer.writerow(
            [
                tournament.city,
                tournament.state,
                tournament.startDate,
                tournament.endDate,
            ]
        )
        writer.writerow([tournament.url])
        for team in tournament.teams:
            writer.writerows(team.csvFormat())
            writer.writerow("break")
        writer.writerow("stages")
        for stage in tournament.stages:
            writer.writerows(stage.csvFormat())
            writer.writerow("break")


def scrapeTeam(config, tournamentName, team):
    teamContent = loadPage(config, team.url, PageType.TEAM, tournamentName, team.name)

    soup = BeautifulSoup(teamContent, "html.parser")
    addRosterToTeam(soup, team)
    addInfoToTeam(soup, team)


def scrapeTournament(config, tournamentInfo, index, total):
    # url in format https://play.usaultimate.org/events/Santa-Barbara-Invite-2022/schedule/Men/CollegeMen/
    parts = tournamentInfo["url"].split("/")
    tournamentName = parts[4].strip() + parts[7].strip()
    tournamentFilePath = f"csv/{config.year}/{tournamentName}.csv"

    log.info(f"Scraping tournament {index + 1}/{total} {tournamentName}")

    if not config.overwriteCSVs and exists(tournamentFilePath):
        log.debug(f"CSV file {tournamentFilePath} already exists, skipping")
        return

    tournamentContent = loadPage(
        config, tournamentInfo["url"], PageType.TOURNAMENT, tournamentName
    )
    tournament = parseTournament(
        tournamentContent, tournamentInfo, tournamentName, config.year
    )
    if tournament is None:
        return

    for team in tournament.teams:
        if team.name != "TEAM_NAME_NOT_FOUND":
            log.info(f"Scraping team page for {team.name}")
            scrapeTeam(config, tournamentName, team)

    writeTournamentToCSV(config, tournament, tournamentFilePath)


def scrapeListOfTournamentUrls(config, tournaments):
    errors = []
    total = len(tournaments)
    for i in range(len(tournaments)):
        try:
            scrapeTournament(config, tournaments[i], i, total)
        except Exception as e:
            log.error(e)
            errors.append(tournaments[i]["url"].replace("\n", "") + f" {e}")

    with open(f"errors{config.year}.txt", "w") as f:
        f.write("\n".join(errors))


def scrapeYear(config):
    try:
        seasonId = seasonIdMap[config.year]
    except KeyError:
        log.error(f"Invalid year: {config.year}")
        return

    calendarUrl = f"https://play.usaultimate.org/events/tournament/?ViewAll=true&IsLeagueType=false&IsClinic=false&FilterByCategory=AE&SeasonId={seasonId}"

    pages = parseTournamentCalendar(
        loadPage(config, calendarUrl, PageType.YEAR_CALENDAR)
    )

    path = f"csv/{config.year}/"
    Path(path).mkdir(parents=True, exist_ok=True)
    with open(path + "_calendar.csv", "w", newline="") as f:
        writer = csv.writer(f)
        for pageInfo in pages:
            writer.writerow(
                [
                    pageInfo["url"],
                    pageInfo["city"],
                    pageInfo["state"],
                    pageInfo["startDate"],
                    pageInfo["endDate"],
                ]
            )

    if not config.calendarOnly:
        scrapeListOfTournamentUrls(config, pages)


def retryErrors(config):
    retrylines = []
    retryUrls = []
    with open(f"errors{config.year}.txt", "r") as f:
        retrylines = f.readlines()
    for line in retrylines:
        url = line.split(" ")[0].replace("\n", "")
        retryUrls.append(url)

    print(retryUrls)
    with open(f"csv/{config.year}/_calendar.csv", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for row in reader:
            if row[0] in retryUrls:
                scrapeTournament(
                    config,
                    {
                        "url": row[0],
                        "city": row[1],
                        "state": row[2],
                        "startDate": row[3],
                        "endDate": row[4],
                    },
                    0,
                    0,
                )
        # scrapeListOfTournamentUrls(config, urls)


def readInfoFromCalendarCSV(year, url):
    with open(f"csv/{year}/_calendar.csv", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for row in reader:
            if row[0] == url:
                return {
                    "city": row[1],
                    "state": row[2],
                    "startDate": row[3],
                    "endDate": row[4],
                    "url": row[0],
                }


def main(argv):
    disableCache = False
    overwrite = False
    retry = False
    year = None
    tournament = None
    debug = False
    live = False
    calendarOnly = False
    tournamentInfo = {}
    opts, args = getopt.getopt(
        argv,
        "lhdory:t:",
        [
            "live",
            "help",
            "disableCache",
            "overwrite",
            "retry",
            "year=",
            "tournament=",
            "debug",
            "calendarOnly",
        ],
    )
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print("-h --help | view help options")
            print("-l", "--live | scrape live data (used by app.py)")
            print("-d", "--disableCache | ignore cached html files")
            print("-o", "--overwrite | overwrite existing csv files")
            print("-r", "--retry | retry failed tournaments from errors.txt")
            print("-y", "--year | specify year to scrape")
            print("--debug | enable debug logging")
            sys.exit()
        elif opt in ("-d", "--disableCache"):
            disableCache = True
            print("disableCache: " + str(disableCache))
        elif opt in ("-o", "--overwrite"):
            overwrite = True
            print("overwriting existing csv files: " + str(overwrite))
        elif opt in ("-r", "--retry"):
            retry = True
            print("retrying failed tournaments")
        elif opt in ("-y", "--year"):
            year = arg
        elif opt in ("-t", "--tournament"):
            tournament = arg
        elif opt in ("--debug"):
            debug = True
        elif opt in ("-l", "--live"):
            live = True
        elif opt in ("--calendarOnly"):
            calendarOnly = True

    level = log.INFO
    if debug:
        level = log.DEBUG
    log.basicConfig(
        level=level,
        format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    if year == None:
        print("Please specify a to scrape!")
        print("Use --year (-y)")
        sys.exit()

    config = Config(int(year), disableCache, overwrite, live, calendarOnly)

    if retry:
        retryErrors(config)
    elif tournament != None:
        tournamentInfo = readInfoFromCalendarCSV(year, tournament)
        scrapeTournament(config, tournamentInfo, 0, 1)
    else:
        log.info("Scraping year: " + year)
        scrapeYear(config)


if __name__ == "__main__":
    main(sys.argv[1:])
