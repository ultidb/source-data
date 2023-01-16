import sys
import getopt
import csv
from enum import Enum
import requests
import logging as log
from pathlib import Path
from os.path import exists
from os import getenv
from dotenv import load_dotenv
from parse import addRosterToTeam, parseTournament, parseTournamentCalendar


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
}


class Config:
    def __init__(self, year, disableCache, overwriteCSVs):
        self.disableCache = disableCache
        self.overwriteCSVs = overwriteCSVs
        self.year = year


class PageType(Enum):
    YEAR_CALENDAR = 1
    TOURNAMENT = 2
    TEAM = 3


def writeContentToFile(path, file, content):
    Path(path).mkdir(parents=True, exist_ok=True)

    # TODO: Trim excess data from html files

    with open(path + file, 'wb') as f:
        f.write(content)


def makeProxiedRequest(url):
    log.debug(f"Making proxied request to {url}")

    HTTP_PROXY_URL = getenv('HTTP_PROXY_URL')
    HTTPS_PROXY_URL = getenv('HTTPS_PROXY_URL')

    response = requests.get(
        url,
        proxies={
            "http": HTTP_PROXY_URL,
            "https": HTTPS_PROXY_URL,
        },
        verify='zyte-proxy-ca.crt',
        timeout=600,
    )

    return response.content


def loadPage(config, url, pageType, tournamentName=None, teamName=None):
    log.debug(f'loading page: {url}')
    path = f'html/{config.year}/'
    Path(path).mkdir(parents=True, exist_ok=True)
    file = None

    if pageType == PageType.YEAR_CALENDAR:
        file = 'calendar.html'
    elif pageType == PageType.TOURNAMENT:
        path += tournamentName + '/'
        file = 'tournament.html'
    elif pageType == PageType.TEAM:
        path += tournamentName + '/'
        file = f'{teamName}.html'

    if not config.disableCache and exists(path + file):
        log.debug(f"File {path + file} already exists, using local copy")
        with open(path + file, 'rb') as f:
            return f.read()

    content = makeProxiedRequest(url)
    writeContentToFile(path, file, content)
    return content


def writeTournamentToCSV(config, tournament, tournamentFilePath):
    path = f'csv/{config.year}/'
    Path(path).mkdir(parents=True, exist_ok=True)

    with open(tournamentFilePath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([tournament.name, tournament.division, tournament.datetime.day,
                        tournament.datetime.month, tournament.datetime.year])
        writer.writerow([tournament.url])
        for team in tournament.teams:
            writer.writerows(team.csvFormat())
            writer.writerow("break")
        writer.writerow("stages")
        for stage in tournament.stages:
            writer.writerows(stage.csvFormat())
            writer.writerow("break")


def scrapeTeam(config, tournamentName, team):
    teamContent = loadPage(config, team.url, PageType.TEAM,
                           tournamentName, team.name)
    addRosterToTeam(teamContent, team)


def scrapeTournament(config, url, index, total):
    # url in format https://play.usaultimate.org/events/Santa-Barbara-Invite-2022/schedule/Men/CollegeMen/
    parts = url.split('/')
    tournamentName = parts[4].strip() + parts[7].strip()
    tournamentFilePath = f'csv/{config.year}/{tournamentName}.csv'

    log.info(f'Scraping tournament {index + 1}/{total} {tournamentName}')

    if not config.overwriteCSVs and exists(tournamentFilePath):
        log.debug(f"CSV file {tournamentFilePath} already exists, skipping")
        return

    
    tournamentContent = loadPage(
        config, url, PageType.TOURNAMENT, tournamentName)
    tournament = parseTournament(
        tournamentContent, url, tournamentName, config.year)
    if tournament is None:
        return

    for team in tournament.teams:
        log.info(f"Scraping team page for {team.name}")
        scrapeTeam(config, tournamentName, team)

    writeTournamentToCSV(config, tournament, tournamentFilePath)

def scrapeListOfTournamentUrls(config, urls):
    errors = []
    total = len(urls)
    for i in range(len(urls)):
        try:
            scrapeTournament(config, urls[i], i, total)
        except Exception as e:
            log.error(e)
            errors.append(urls[i].replace('\n', ''))

    with open(f'errors{config.year}.txt', 'w') as f:
        f.write('\n'.join(errors))


def scrapeYear(config):
    try:
        seasonId = seasonIdMap[config.year]
    except KeyError:
        log.error(f'Invalid year: {config.year}')
        return

    calendarUrl = f'https://play.usaultimate.org/events/tournament/?ViewAll=true&IsLeagueType=false&IsClinic=false&FilterByCategory=AE&SeasonId={seasonId}'

    pages = parseTournamentCalendar(
        loadPage(config, calendarUrl, PageType.YEAR_CALENDAR))
    
    with open('tournaments.txt', 'w') as f:
        for page in pages:
            f.write(page + '\n')

    scrapeListOfTournamentUrls(config, pages)

def retryErrors(config):
    with open(f'errors{config.year}.txt', 'r') as f:
        urls = f.readlines()
        scrapeListOfTournamentUrls(config, urls)
    

def main(argv):
    
    disableCache = False
    overwrite = False
    retry = False
    year = None
    tournament = None
    debug = False
    opts, args = getopt.getopt(
        argv, "hdory:t:", ["help", "disableCache", "overwrite", "retry", "year=", "tournament="])
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print('-h --help | view help options')
            print("-d", "--disableCache | ignore cached html files")
            print("-o", "--overwrite | overwrite existing csv files")
            print("-r", "--retry | retry failed tournaments from errors.txt")
            print("-y", "--year | specify year to scrape")
            print("--debug | enable debug logging")
            sys.exit()
        elif opt in ("-d", "--disableCache"):
            disableCache = True
            print('disableCache: ' + str(disableCache))
        elif opt in ("-o", "--overwrite"):
            overwrite = True
            print('overwriting existing csv files: ' + str(overwrite))
        elif opt in ("-r", "--retry"):
            retry = True
            print('retrying failed tournaments')
        elif opt in ("-y", "--year"):
            year = arg
        elif opt in ("-t", "--tournament"):
            tournament = arg
        elif opt in ("--debug"):
            debug = True
    
    level = log.INFO
    if debug:
        level = log.DEBUG
    log.basicConfig(
        level=level,
        format='[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )

    if year == None and tournament == None:
        print("Please specify a year or tournament to scrape!")
        print("Use --year (-y) or --tournament (-t)")
        sys.exit()

    config = Config(int(year), disableCache, overwrite)
    
    if retry:
        retryErrors(config)
    elif tournament != None:
        scrapeTournament(config, tournament, 0, 1)
    else:
        log.info("Scraping year: " + year)
        scrapeYear(config)


if __name__ == "__main__":
    main(sys.argv[1:])
