from calendar import calendar
from fileinput import filename
import queue
import string
from bs4 import BeautifulSoup, NavigableString, Tag, element
import logging as log
import csv
from datetime import datetime, timedelta
from models import *
from config import get_division_path

# Team class, allows storage of name and roster

statusOptions = [
    "Final",
    "In Progress",
    "Scheduled",
    "Cancelled",
]


# exception type for when tournamnet page contains either no data or all 0-0 games


class NoValidGamesException(Exception):
    pass


# exception for when scraping of a tournamnet fails while scraping team page


class InternetConnectionFailedException(Exception):
    pass


# NEW CODE STARTS HERE
def convertTeamLinkToTeam(link, teams):
    id = link["href"].split("=")[1]
    if id in teams:
        return teams[id]

    stripped = link.contents[0].strip()
    seed = 0
    name = ""

    if stripped[-1] == ")":
        index = 2
        while stripped[-index].isdigit():
            index += 1

        seed = int(stripped[-(index - 1) : -1])
        name = stripped[:-index].strip()

    name = name.replace("/", "-")
    if name == "":
        name = "TEAM_NAME_NOT_FOUND"
    url = f"https://play.usaultimate.org{link['href']}"
    teams[id] = Team(name, seed, url, id)
    return teams[id]


def extract_nickname(team_name):
    opening_index = team_name.find("(")
    closing_index = team_name.rfind(")")

    # If both parentheses were found
    if opening_index != -1 and closing_index != -1:
        nickname = team_name[opening_index + 1 : closing_index]

        # Ignore interior parentheses if the text inside is only one character
        if "(" in nickname and ")" in nickname:
            start_index = nickname.find("(")
            end_index = nickname.rfind(")")
            if end_index - start_index > 1:
                nickname = nickname[start_index + 1 : end_index]

        return nickname.strip()
    else:
        return ""


def addRosterToTeam(soup, team):
    roster = []

    # Find the roster table by looking for one with "No." and "Player" headers
    roster_table = None
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
            if "No." in headers and "Player" in headers:
                roster_table = table
                break

    if roster_table is None:
        log.debug(f"No roster table found for team {team.name} at {team.url}")
        return

    tableRows = roster_table.findAll("tr")[1:]  # skip header row

    for row in tableRows:
        cells = row.findAll("td")
        if len(cells) < 2:
            continue

        # Skip rows that contain team links (standings data, not roster)
        if cells[0].find("a", href=lambda h: h and "/events/teams/" in h):
            log.debug(f"Skipping standings row for team {team.name}")
            continue

        number = cells[0].get_text(strip=True)
        name = cells[1].get_text(strip=True)

        # Skip if name looks like a record (e.g., "5 - 0")
        if " - " in name and name.replace(" - ", "").replace(" ", "").isdigit():
            continue

        roster.append(Player(number, name))

    team.roster = roster


def addInfoToTeam(soup, team):
    info = soup.find("div", {"class": "profile_info"})
    if not info:
        team.info = TeamInfo("", "", [], "", "", "")
        return
    nickname = ""
    try:
        nameContents = info.find("h4").find("a").contents[0].strip()
        nickname = extract_nickname(nameContents)
    except:
        log.error(f"Failed to parse nickname for team {team.name} at {team.url}")
        pass

    locationInfo = info.find("p", {"class": "team_city"})
    location = ""
    if locationInfo:
        location = locationInfo.contents[0]
    entries = info.findAll("dl")
    website = ""
    facebook = ""
    twitter = ""
    coaches = ["coaches"]
    for entry in entries:
        if entry.find("dt").contents[0] == "Coaches:":
            coaches += parseCoaches(entry.find("dd").contents)
        elif entry.find("dt").contents[0] == "Website:":
            website = entry.find("a")["href"]
            if website.startswith("modules/common/"):
                website = website[15:]
        elif entry.find("dt").contents[0] == "Facebook:":
            facebook = entry.find("a")["href"]
            if facebook.startswith("modules/common/"):
                facebook = facebook[15:]
        elif entry.find("dt").contents[0] == "Twitter:":
            twitter = entry.find("a")["href"]
            if twitter.startswith("modules/common/"):
                twitter = twitter[15:]

    team.info = TeamInfo(
        nickname,
        location.strip(),
        coaches,
        website.strip(),
        facebook.strip(),
        twitter.strip(),
    )


def parseCoaches(items):  # passing in list of tags with coach info
    output = []
    for item in items:
        if type(item) != element.NavigableString:
            continue
        inner = item.strip()  # remove whitespace
        if inner == "":
            continue

        if inner[-1] == ")":
            index = 2
            while inner[-index] != "(":
                index += 1
            inner = inner[:-index].strip()
        output.append(inner)

    return output


def parseClustersStage(soup, teams, year, startDate):
    clusterTables = soup.find_all("table", {"class": "global_table scores_table"})
    clusters = []
    for clusterTable in clusterTables:
        clusterName = clusterTable.find("th").contents[0]
        clusterGames = parseGameTable(clusterTable, teams, year, startDate)
        if clusterName != None and len(clusterGames) > 0:
            clusters.append(Cluster(clusterName, clusterGames))

    return clusters


def parsePoolTables(soup, teams):
    pools = []
    poolTables = soup.find_all("div", {"class": "pool"})

    for poolTable in poolTables:
        poolName = poolTable.find("h3").contents[0]
        poolTeams = []
        for teamLink in poolTable.find_all("a"):
            poolTeams.append(convertTeamLinkToTeam(teamLink, teams))

        sortedTeams = sorted(poolTeams, key=lambda x: x.seed)
        pools.append(Pool(poolName, sortedTeams))
    return pools


def parseGameTable(soup, teams, year, startDate):
    games = []
    for row in soup.find("tbody").find_all("tr"):
        if row.has_attr("data-game"):
            teamA_score = (
                row.find_all("td")[5]
                .find_all("span", {"class": "isScore"})[0]
                .find("span")
                .contents[0]
            )
            teamB_score = (
                row.find_all("td")[5]
                .find_all("span", {"class": "isScore"})[1]
                .find("span")
                .contents[0]
            )

            # error checker to eliminate forfits and unplayed games
            # if not teamA_score.isdigit() or not teamB_score.isdigit() or (int(teamA_score) <= 1 and int(teamB_score) <= 1):
            #     continue

            # html code which contains team names, scores, and team ids
            gameInfo = row.find_all("td")[3:6]

            # errorr checking to make sure td did not forget to update teams
            if gameInfo[0].contents[0] == "TBD" or gameInfo[1].contents[0] == "TBD":
                continue

            if not gameInfo[0].find("a") or not gameInfo[1].find("a"):
                continue

            teamA = convertTeamLinkToTeam(gameInfo[0].find("a"), teams)
            teamB = convertTeamLinkToTeam(gameInfo[1].find("a"), teams)

            try:
                date = row.find_all("td")[0].find("span").contents[0]
                time = row.find_all("td")[1].find("span").contents[0]
                month = int(date[4 : date.find("/")])
                day = int(date[date.find("/") + 1 : len(date)])
                hour = time[0 : time.find(":")]

                # handle invalid times
                if hour.isdigit():
                    hour = int(hour)
                    minute = int(time[time.find(":") + 1 : time.find(":") + 3])
                    if time[len(time) - 2 : len(time)] == "PM" and hour != 12:
                        hour += 12
                else:
                    hour = 0
                    minute = 0

                game_datetime = datetime(year, month, day, hour, minute)
            except:
                # for games where TD failed to put a date
                game_datetime = startDate

            status = row.find_all("td")[6].find("span")
            if len(status.contents):
                status = status.contents[0]

            # commit games to objects
            game = Game(teamA, teamB, teamA_score, teamB_score, game_datetime, status)
            games.append(game)

    return games


def parsePoolsStage(soup, teams, year, startDate):
    pools = parsePoolTables(soup, teams)
    poolGameTables = soup.find_all("table", {"class": "global_table scores_table"})

    if len(pools) == 0 and len(poolGameTables) == 0:
        return

    if len(pools) == 0:
        pools = [Pool("Pool A", [], [])]

    for i in range(len(pools)):
        pools[i].games = parseGameTable(poolGameTables[i], teams, year, startDate)
        for game in pools[i].games:
            game.round = pools[i].name

    return pools


def convertNonDigitScore(score):
    if score == "W" or score == "w" or score == "Win" or score == "win":
        return 1
    else:
        return 0


def parseBracketGame(game, teams, year, startDate, roundName):
    game_teams = game.find_all("span", {"class": "team"})

    team_scores = game.find_all("span", {"class": "score"})
    teamA_score = team_scores[0].contents[0].strip()
    teamB_score = team_scores[1].contents[0].strip()
    # error checker to eliminate forfits and unplayed games
    if not teamA_score.isdigit():
        teamA_score = convertNonDigitScore(teamA_score)
    if not teamB_score.isdigit():
        teamB_score = convertNonDigitScore(teamB_score)

    # errorr checking to make sure td did not forget to update teams
    if game_teams[0].contents[0] == "TBD" or game_teams[1].contents[0] == "TBD":
        return

    if not game_teams[0].find("a") or not game_teams[1].find("a"):
        return

    teamA = convertTeamLinkToTeam(game_teams[0].find("a"), teams)
    teamB = convertTeamLinkToTeam(game_teams[1].find("a"), teams)

    # scaping date information from webpage
    game_datetime = None
    try:
        game_date = game.find("span", {"class": "date"}).contents[0].split(" ")
        year = int(game_date[0].split("/")[2])
        month = int(game_date[0].split("/")[0])
        day = int(game_date[0].split("/")[1])
        hour = game_date[1].split(":")[0]

        # handling tournaments that forget to update time of game
        if hour.isdigit():
            hour = int(hour)
            minute = int(game_date[1].split(":")[1])
            if game_date[2] == "PM" and hour != 12:
                hour += 12
        else:
            hour = 0
            minute = 0

        game_datetime = datetime(year, month, day, hour, minute)
    except:
        game_datetime = startDate

    status = ""
    statusSpans = game.find("span", {"class": "game-status"}).contents
    if len(statusSpans) and statusSpans[0] in statusOptions:
        status = statusSpans[0]
    elif game_datetime is None:
        status = "Scheduled"
    else:
        # game ended and one team has a score
        if game_datetime > (datetime.now() + timedelta(hours=2)) and (
            teamA_score > 0 or teamB_score > 0
        ):
            status = "Final"
        else:
            status = "Scheduled"

    # adding game to games list
    game = Game(
        teamA, teamB, teamA_score, teamB_score, game_datetime, status, roundName
    )

    return game


def parseBracket(soup, teams, year, startDate):
    games = []
    columns = soup.find_all("div", {"class": "bracket_col"})
    if len(columns) == 0:
        return

    bracketName = soup.find("h3").find("a").contents[0]

    for column in columns:
        roundName = column.find("h4", {"class": "col_title"}).contents[0]
        columnGames = column.find_all("div", {"class": "bracket_game"})
        for game in columnGames:
            game = parseBracketGame(game, teams, year, startDate, roundName)
            if game != None:
                games.append(game)
    return Bracket(bracketName, games)


def parseBracketStage(soup, teams, year, startDate):
    brackets = []
    bracketSections = soup.find_all("section", {"class": "section page"})
    for bracketSection in bracketSections:
        bracket = parseBracket(bracketSection, teams, year, startDate)
        if bracket != None:
            brackets.append(bracket)

    return brackets


def stagesHaveGames(stages):
    for stage in stages:
        if isinstance(stage, Pools) and stage.pools != []:
            if stage.pools[0].games != []:
                return True
        elif isinstance(stage, Brackets) and stage.brackets != []:
            if stage.brackets[0].games != []:
                return True
        elif isinstance(stage, Clusters) and stage.clusters != []:
            if stage.clusters[0].games != []:
                return True
    return False


def parseTournament(html, info, fileName, year):
    teams = {}
    # opening html parser
    soup = BeautifulSoup(html, "html.parser")

    # finding tournament name
    try:
        tournamentName = str(
            soup.find("div", {"class": "breadcrumbs"}).find_all("a")[1].contents[0]
        )
    except:
        url = info["url"]
        log.error(f"Error parsing tournament name from {url}")
        return None

    # finding stages of tournament (tabs)
    stageTabs = soup.find("ul", {"class": "tabsLeft tabs"})
    if stageTabs == None:
        log.debug(f"No stages found for {tournamentName}")
        return None

    stageNames = []
    stageNameIndex = 0
    for a in stageTabs.find_all("a"):
        stageNames.append(a.contents[0])

    if len(stageNames) == 0:
        log.debug(f"No stage names found for {tournamentName}")
        return None

    slides = soup.find("div", {"class": "slides"})

    stages = []

    # create datetime from start date
    startDate = info["startDate"]
    dt = datetime.strptime(startDate, "%Y-%m-%d")

    for slide in slides.children:
        if isinstance(slide, NavigableString):
            continue
        if isinstance(slide, Tag):
            slideId = slide.get("id")
            name = stageNames[stageNameIndex]
            stageNameIndex += 1
            if slideId == "poolSlide":
                stage = parsePoolsStage(slide, teams, year, dt)
                if stage != None:
                    stages.append(Pools(name, stage))
            elif slideId == "bracketSlide":
                stages.append(Brackets(name, parseBracketStage(slide, teams, year, dt)))
            elif slideId == "clusterSlide":
                stages.append(
                    Clusters(name, parseClustersStage(slide, teams, year, dt))
                )

    teams = list(teams.values())
    division = soup.find("h1", {"class": "title"}).contents[0]

    if not stagesHaveGames(stages):
        log.debug(f"No games found for {tournamentName}")
        return None

    return Tournament(
        tournamentName,
        info["url"],
        info["city"],
        info["state"],
        info["startDate"],
        info["endDate"],
        teams,
        dt,
        division,
        stages,
    )


def pullLinksFromCalendar(calendar):
    # list of links to specific divisons within tournaments
    page_links = []

    # find specific tournament divison pages that should have teams
    for t in calendar:
        try:
            cells = t.findAll("td")
            city = cells[2].contents[0].strip()
            state = cells[3].contents[0].strip()
            dateString = cells[5].contents[0].strip()
        except Exception as e:
            continue

        dates = dateString.split(" - ")
        startDate = ""
        endDate = ""
        if len(dates) == 2:
            startDate = dates[0]
            endDate = dates[1]
        elif len(dates) == 1:
            startDate = dates[0]
            endDate = dates[0]

        if startDate != "":
            startDate = datetime.strptime(startDate, "%b %d, %Y").strftime("%Y-%m-%d")
        if endDate != "":
            endDate = datetime.strptime(endDate, "%b %d, %Y").strftime("%Y-%m-%d")

        # iterate through each divison of a tournament
        for num in t.findAll("li"):
            # link to overall tournament page
            link = t.find("a").get("href")

            # check to see if a division has more than 1 team in the division
            if int(num.contents[1].contents[0][1:-1]) > 1:
                # add link of specific divison page to list
                division_name = num.contents[0].strip()
                division_path = get_division_path(division_name)
                if division_path is None:
                    log.debug(f"Unknown division: {division_name} from {link}")
                    continue

                url = link + "/schedule/" + division_path
                lower = url.lower()
                if not url.startswith("https://play.usaultimate.org"):
                    url = "https://play.usaultimate.org" + url

                if not ("high-school" in lower or "middle-school" in lower):
                    d = {
                        "city": city,
                        "state": state,
                        "startDate": startDate,
                        "endDate": endDate,
                        "url": url,
                    }
                    page_links.append(d)

    log.info(f"Found {len(page_links)} tournament pages to scrape")

    return page_links


def parseTournamentCalendar(html):
    """scrape tournament data for entire usau tournament calendar webpage"""

    # create html parser for specfici url
    soup = BeautifulSoup(html, "html.parser")

    # access table of tournmanets
    # calendar = soup.find("table", {"class": "global_table"}).findAll("tr")[1:]
    upcoming_calendar = soup.find(
        "table", {"id": "CT_HP_Mid_1_gvCurrentUpcomingEvents"}
    ).findAll("tr")[1:]
    past_events_calendar = soup.find(
        "table", {"id": "CT_HP_Mid_1_gvPastEvents"}
    ).findAll("tr")[1:]
    # list of links to specific divisons within tournaments
    upcoming_links = pullLinksFromCalendar(upcoming_calendar)
    past_links = pullLinksFromCalendar(past_events_calendar)

    return upcoming_links + past_links


def parseNewSchedule(html, year, isCollege=False):
    soup = BeautifulSoup(html, "html.parser")
    page_links = []

    # Find all tournament rows
    tournament_rows = soup.find_all("tr", class_="tournament")

    for row in tournament_rows:
        try:
            # Extract base URL from results link first (for error reporting)
            results_cell = row.find("td", class_="results")
            if not results_cell:
                results_cell = row.find("td", class_="link")
            base_url = results_cell.find("a") if results_cell else None
            if not base_url:
                continue
            base_url = base_url.get("href")

            # Extract date
            date_cell = row.find("td", class_="date")
            date_text = date_cell.find("span", class_="label").next_sibling.strip()
            dates = date_text.replace("–", "-").split("-")
            if len(dates) == 2:
                start_date = dates[0].strip()
                end_date = dates[1].strip()
            else:
                start_date = dates[0].strip()
                end_date = dates[0].strip()

            # Convert dates to YYYY-MM-DD format
            start_month, start_day = start_date.split("/")

            try:
                end_month, end_day = end_date.split("/")
            except:
                end_month = start_month
                end_day = end_date

            start_date = datetime(year, int(start_month), int(start_day)).strftime(
                "%Y-%m-%d"
            )
            end_date = datetime(year, int(end_month), int(end_day)).strftime("%Y-%m-%d")

            # Extract location
            location_cell = row.find("td", class_="location")
            location = location_cell.find("span", class_="label").next_sibling.strip()

            # Handle location parsing - try comma first, then last space
            try:
                city, state = location.split(",")
            except ValueError:
                # If no comma, split on last space
                last_space = location.rfind(" ")
                if last_space != -1:
                    city = location[:last_space]
                    state = location[last_space + 1 :]
                else:
                    # If no space found, treat entire string as city
                    city = location
                    state = ""

            city = city.strip()
            state = state.strip()

            # Extract division
            division_cell = row.find("td", class_="division")
            division = division_cell.find("span", class_="label").next_sibling.strip()

            if not base_url.endswith("/"):
                base_url = base_url + "/"

            if division == "Men" or division == "Women" or division == "Mixed":
                if isCollege:
                    division = "College - " + division
                else:
                    division = "Club - " + division

            # Construct proper URL using config divisions
            division_path = get_division_path(division)
            if division_path is None:
                log.error(f"Unknown division: {division} from {base_url}")
                continue
            url = base_url + "schedule/" + division_path

            # Add to page links
            page_links.append(
                {
                    "city": city,
                    "state": state,
                    "startDate": start_date,
                    "endDate": end_date,
                    "url": url,
                }
            )

        except Exception as e:
            url_info = f" for {base_url}" if 'base_url' in locals() and base_url else ""
            log.error(f"Error parsing tournament row{url_info}: {e}")
            continue

    log.info(f"Found {len(page_links)} tournament pages to scrape")
    return page_links
