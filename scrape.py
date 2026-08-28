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
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import time
import uuid
from sources.usau.parse import (
    addRosterToTeam,
    addInfoToTeam,
    parseNewSchedule,
    parseTournament,
    parseTournamentCalendar,
    getUrlDivisionSuffix,
)
from tor import torIsRunning, startTorServer
from config import get_season_id


load_dotenv()
config = None

# Singleton Chrome driver for Selenium requests
_selenium_driver = None


class ScrapeOptions:
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
    NEW_COLLEGE_SCHEDULE = 4
    NEW_CLUB_SCHEDULE = 5


def writeContentToFile(path, file, content):
    Path(path).mkdir(parents=True, exist_ok=True)

    # TODO: Trim excess data from html files

    with open(path + file, "wb") as f:
        f.write(content)


def makeProxiedRequest(url):
    try:
        return makeProxiedRequestSelenium(url)
    except Exception as e:
        log.error(f"Failed to load {url}: {e}")
        raise
    # log.debug(f"Making proxied request to {url}")

    # if not torIsRunning():
    #     tor_process = startTorServer()
    #     atexit.register(tor_process.kill)

    # response = requests.get(
    #     url,
    #     proxies={"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"},
    #     timeout=600,
    # )

    # if response.status_code != 200:
    #     log.error(f"Request to {url} failed with status code {response.status_code}")
    #     log.error(json.dumps(response, indent=2))
    #     sys.exit(1)

    # return response.content


def getSeleniumDriver():
    """Get or create the singleton Chrome driver instance"""
    global _selenium_driver

    if _selenium_driver is None:
        log.debug("Initializing Chrome driver")

        if not torIsRunning():
            tor_process = startTorServer()
            atexit.register(tor_process.kill)

        chrome_options = Options()
        # Configure Tor SOCKS5 proxy
        chrome_options.add_argument("--proxy-server=socks5://127.0.0.1:9050")
        # Additional options for headless Chrome
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        _selenium_driver = webdriver.Chrome(options=chrome_options)
        _selenium_driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Register cleanup on exit
        atexit.register(cleanupSeleniumDriver)

    return _selenium_driver


def cleanupSeleniumDriver():
    """Cleanup the singleton Chrome driver on exit"""
    global _selenium_driver
    if _selenium_driver is not None:
        log.debug("Cleaning up Chrome driver")
        try:
            _selenium_driver.quit()
        except:
            pass
        _selenium_driver = None


def makeProxiedRequestSelenium(url):
    log.debug(f"Making proxied Selenium request to {url}")

    driver = getSeleniumDriver()

    # Extract expected tournament slug from URL for validation
    expected_tournament_slug = None
    if "/events/" in url:
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part == "events" and i + 1 < len(parts):
                expected_tournament_slug = parts[i + 1]
                break

    max_retries = 3
    for retry in range(max_retries):
        try:
            # Store the current page source before navigating to detect when it changes
            old_page_source = driver.page_source if driver.current_url != "data:," else ""

            driver.get(url)

            # Wait for the page content to actually change by checking that the URL loaded
            # and the page source is different from before
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.current_url == url or url in d.current_url
                )
            except Exception:
                log.warning(f"URL did not fully load: expected {url}, got {driver.current_url}")

            # Additional wait for page content to fully load before reading page_source.
            # Without this wait, the driver may return stale content from the
            # previous page, causing tournament data to be written to wrong files.
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "breadcrumbs"))
                )
            except Exception:
                # If breadcrumbs not found, wait for any content indicator
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except Exception:
                    pass

            # Wait for the page source to actually change from the previous page
            max_attempts = 20
            for attempt in range(max_attempts):
                current_page_source = driver.page_source
                if current_page_source != old_page_source:
                    break
                time.sleep(0.1)
            else:
                log.warning(f"Page source did not change after {max_attempts} attempts for URL: {url}")

            # Small delay to ensure dynamic content has loaded
            time.sleep(0.5)

            # Get the page source
            page_source = driver.page_source

            # CRITICAL: Validate that the loaded content matches the requested tournament
            if expected_tournament_slug:
                # Parse the page to verify tournament identity
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page_source, "html.parser")

                # Check breadcrumbs for tournament link
                breadcrumbs = soup.find("div", {"class": "breadcrumbs"})
                if breadcrumbs:
                    tournament_links = breadcrumbs.find_all("a")
                    if len(tournament_links) >= 2:
                        tournament_link = tournament_links[1]
                        if tournament_link.get("href"):
                            actual_slug = tournament_link["href"].rstrip("/").split("/")[-1]

                            if actual_slug != expected_tournament_slug:
                                log.warning(
                                    f"Content mismatch on attempt {retry + 1}/{max_retries}: "
                                    f"expected tournament '{expected_tournament_slug}' but got '{actual_slug}'. "
                                    f"Retrying..."
                                )
                                if retry < max_retries - 1:
                                    # Force reload by adding a small delay and clearing any cached state
                                    time.sleep(1)
                                    continue
                                else:
                                    log.error(
                                        f"Failed to load correct tournament after {max_retries} attempts. "
                                        f"Expected '{expected_tournament_slug}' but consistently got '{actual_slug}'"
                                    )
                                    raise Exception("Tournament content validation failed")

            # Convert to bytes to match the return type of makeProxiedRequest
            return page_source.encode('utf-8')

        except Exception as e:
            if retry < max_retries - 1:
                log.warning(f"Attempt {retry + 1} failed, retrying: {e}")
                time.sleep(1)
                continue
            else:
                log.error(f"Selenium request to {url} failed after {max_retries} attempts with error: {e}")
                cleanupSeleniumDriver()
                raise


def loadPage(config, url, pageType, tournamentName=None, teamName=None):
    log.debug(f"loading page: {url}")
    path = f"cache/{config.year}/"
    Path(path).mkdir(parents=True, exist_ok=True)
    file = None

    disableCache = config.disableCache

    if pageType == PageType.YEAR_CALENDAR:
        file = "calendar.html"
    elif pageType == PageType.NEW_COLLEGE_SCHEDULE:
        file = "college_schedule.html"
    elif pageType == PageType.NEW_CLUB_SCHEDULE:
        file = "club_schedule.html"
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
    divisionSuffix = getUrlDivisionSuffix(tournamentInfo["url"])
    if divisionSuffix:
        tournamentName += "-" + divisionSuffix
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
            print(f"scraping {tournaments[i]['url']}")
            scrapeTournament(config, tournaments[i], i, total)
        except Exception as e:
            log.error(e)
            errors.append(tournaments[i]["url"].replace("\n", "") + f" {e}")

    with open(f"errors{config.year}.txt", "w") as f:
        f.write("\n".join(errors))


def scrapeYear(config):
    seasonId = get_season_id(config.year)
    if seasonId is None:
        log.error(f"Invalid year: {config.year}")
        return

    calendarUrl = f"https://play.usaultimate.org/events/tournament/?ViewAll=true&IsLeagueType=false&IsClinic=false&FilterByCategory=AE&SeasonId={seasonId}"
    print(calendarUrl)

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


def scrapeCurrentYear(config):
    collegeScheduleUrl = "https://usaultimate.org/college/schedule/"
    clubScheduleUrl = "https://usaultimate.org/club/schedule/"

    collegeScheduleContent = loadPage(
        config, collegeScheduleUrl, PageType.NEW_COLLEGE_SCHEDULE
    )
    clubScheduleContent = loadPage(config, clubScheduleUrl, PageType.NEW_CLUB_SCHEDULE)

    collegeSchedule = parseNewSchedule(collegeScheduleContent, config.year, True)
    clubSchedule = parseNewSchedule(clubScheduleContent, config.year, False)
    pages = collegeSchedule + clubSchedule

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


