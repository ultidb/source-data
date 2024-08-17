from datetime import datetime, timedelta
import os
from bs4 import BeautifulSoup
import re

from pathlib import Path
from selenium.webdriver.chrome.options import Options
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from models import *
from scrape import writeTournamentToCSV

PREFIX = "scoreReport/"
CACHE_PREFIX = PREFIX + "html/"
CSV_PREFIX = PREFIX + "csv/"
DIVISIONS = ["college-open", "college-womens", "open", "mixed", "womens"]

def parseBracket(bracketDiv, teams, startDate, endDate):
    try:
        name = bracketDiv.find("div", {"class": "pool-title"}).text
        games = []

        table = bracketDiv.find("table")
        if table is None:
            return None
        rows = table.find_all("tr")

        winners = table.find_all("td", {"class": "b1awh"})

        timeCells = table.find_all("td", {"class": "btm"})
        times = set()
        roundTimesSorted = None
        try: 
            for timeCell in timeCells:
                time = timeCell.text
                if time and time not in times:
                    parts = time.split(" ")
                    times.add(get_datetime(startDate, parts[0], parts[1]))
            roundTimesSorted = sorted(list(times))     
        except:
            pass
        
        defaultGameTime = endDate
 
        i = 0        
        for row in rows:
            cells = row.find_all("td")
            j = 0
            for cell in cells:
                if cell.has_attr("class") and "b1awh" in cell["class"]:
                    winner = cell.text
                    loser = None
                    loserIsUpper = False
                    loserIndex = None

                    up2 = (i - 2 >= 0) and rows[i - 2].find_all("td")
                    down2 = (i + 2 < len(rows)) and rows[i + 2].find_all("td")

                    if up2:
                        startIndex = 0 if j <= 1 else j - 2
                        endIndex = j + 3 if j + 3 < len(cells) else len(cells)
                        neighbors = up2[startIndex:endIndex]
                        for k in range(len(neighbors)):
                            if (
                                neighbors[k].has_attr("class")
                                and "b1ah" in neighbors[k]["class"]
                            ):
                                loser = neighbors[k]
                                loserIsUpper = True
                                loserIndex = startIndex + k

                    if down2:
                        startIndex = 0 if j <= 1 else j - 2
                        endIndex = j + 3 if j + 3 < len(cells) else len(cells)
                        neighbors = down2[startIndex:endIndex]
                        for k in range(len(neighbors)):
                            if (
                                neighbors[k].has_attr("class")
                                and "b1ah" in neighbors[k]["class"]
                            ):
                                loser = neighbors[k]
                                loserIndex = startIndex + k

                    winnerScoreRow = cell.find_previous("tr").find_previous("tr")
                    loserScoreRow = loser.find_next("tr")
                    if loserIsUpper:
                        winnerScoreRow = cell.find_next("tr")
                        loserScoreRow = loser.find_previous("tr").find_previous("tr")

                    winnerScoreCells = winnerScoreRow.find_all("td")
                    winnerScoreCell = None
                    for c in winnerScoreCells[
                        0 if j <= 1 else j - 2 : (
                            j + 3
                            if j + 3 < len(winnerScoreCells)
                            else len(winnerScoreCells)
                        )
                    ]:
                        if c.has_attr("class") and (
                            "bo1" in c["class"] or "b0" in c["class"]
                        ):
                            winnerScoreCell = c

                    loserScoreCells = loserScoreRow.find_all("td")
                    loserScoreCell = None
                    for c in loserScoreCells[
                        0 if loserIndex <= 1 else loserIndex - 2 : (
                            loserIndex + 3
                            if loserIndex + 3 < len(loserScoreCells)
                            else len(loserScoreCells)
                        )
                    ]:
                        if c.has_attr("class") and (
                            "bo1" in c["class"] or "b0" in c["class"]
                        ):
                            loserScoreCell = c


                    x = j
                    if x < 3:
                        x += 1
                    roundIndex = x // 3
                    foundDt = None
                    try:
                        if loserIsUpper:
                            timerowcells = rows[i-3].find_all("td")
                        else:
                            timerowcells = rows[i-1].find_all("td")

                        for y in range(j-1, j+2):
                            if timerowcells[y].has_attr("class") and "btm" in timerowcells[y]["class"] and timerowcells[y].text:
                                time = timerowcells[y].text
                                foundDt = get_datetime(startDate, time.split(" ")[0], time.split(" ")[1])
                                break
                    except:
                        pass
                    
                    if not foundDt:
                        if roundTimesSorted and len(roundTimesSorted) > roundIndex:
                            foundDt = roundTimesSorted[roundIndex]
                        else:
                            foundDt = defaultGameTime
                    
                    game = Game(findOrCreateTeam(winner, teams), findOrCreateTeam(loser.text, teams), winnerScoreCell.text if winnerScoreCell is not None else 'W', loserScoreCell.text if loserScoreCell is not None else 'F', foundDt, 'final', name)
                    games.append(game)
                    
                j += 1
            i += 1
        
        # sort games by datetime
        games.sort(key=lambda x: x.datetime)
    
        return Bracket(name, games)
    except Exception as e:
        print(f"Error parsing bracket {name}: {e}")
        return None

            

def parse_dates(date_range, year):
    start_date_str = None
    end_date_str = None
    dates = date_range.split(' - ')
    if len(dates) == 1:
        start_date_str = date_range
        end_date_str = date_range
    if len(dates) == 2:
        start_date_str = dates[0]
        end_date_str = dates[1]

    if (not start_date_str) or (not end_date_str):
        print(f"Error parsing date range: {date_range}")
        return None
    
    # match month word from start_date_str
    month = re.search(r'[A-Za-z]+', start_date_str).group()
    # if end_date_str doesn't start with month, append month to it
    if not re.search(r'[A-Za-z]+', end_date_str):
        end_date_str = f"{month} " + end_date_str
    
    start_date_str += f" {year}"
    end_date_str += f" {year}"

    start_date = datetime.strptime(start_date_str, "%B %d %Y")
    end_date = datetime.strptime(end_date_str, "%B %d %Y")

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

def get_datetime(startDate, dayString, time):
    # Convert dayString to a weekday number
    try:
        day_dict = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
        day_num = day_dict[dayString]

        # Find the first date that matches the weekday number
        current_date = startDate
        iterations = 0
        while current_date.weekday() != day_num:
            current_date += timedelta(days=1)
            iterations += 1


        if iterations > 7:
            print(f"Error: could not find the correct weekday for {dayString} {time} after 7 days")
            return None
        

        # Parse the time string to a datetime.time object
        hour, minute = time[:-1].split(':')
        if time[-1] == 'p' and hour != '12':
            hour = str(int(hour) + 12)
        elif time[-1] == 'a' and hour == '12':
            hour = '00'
        time_obj = datetime.strptime(hour + ':' + minute, '%H:%M').time()

        # Combine the date and time into a single datetime.datetime object
        datetime_obj = datetime.combine(current_date, time_obj)

        return datetime_obj
    except:
        return startDate

def findOrCreateTeam(teamName, teams):
    for team in teams:
        if team.name == teamName:
            return team
    team = TeamNameOnly(teamName)
    teams.append(team)
    return team

def parsePool(poolDiv, teams, startDate):
    name = poolDiv.find("div", {"class": "pool-title"}).text
    standingsTable = poolDiv.find("table", {"class": "standings"})
    rows = standingsTable.findAll("tr")
    ids = {}
    try:
        for row in rows:
            cells = row.findAll("td")
            if len(cells) == 0:
                continue
            teamId = cells[1].text
            teamName = cells[2].find("a").text
            ids[teamId] = teamName
        
        gameRows = poolDiv.findAll("tr", recursive=False)
        currentDay = None
        gamesPerRound = 0
        games = []
        for row in gameRows:
            if row.find_all(string=["Fld", "Game", "Score"]):
                currentDay = row.find("td").text
                gamesPerRound = len(row.find_all(string="Game"))
            else:
                cells = row.findAll("td")
                if len(cells) == 0:
                    continue
                
                time = cells[0].text
                for i in range(0, (len(cells) - 1) // 3):
                    offset = i * 3
                    field = cells[1+offset].text
                    regexp = '|'.join(ids.keys())
                    teamIds = re.findall(regexp, cells[2+offset].text)
                    if len(teamIds) != 2:
                        print(f"Error parsing pool schedule: {cells}")
                        continue
                    teamAId = teamIds[0]
                    teamBId = teamIds[1]
                    teamA = findOrCreateTeam(ids[teamAId], teams)
                    teamB = findOrCreateTeam(ids[teamBId], teams)
                    scoreString = cells[3+offset].text
                    scores = scoreString.split("-")
                    status = 'final'
                    if len(scores) != 2:
                        scores = ["", ""]
                        status = 'scheduled'

                    datetime = get_datetime(startDate, currentDay, time)
                    game = Game(teamA, teamB, scores[0], scores[1], datetime, status, name)
                    games.append(game)

        return Pool(name, teams, games)
    except Exception as e:
        print(f"Error parsing pool {name}: {e}")
        return None


def parseScoreReport(html, year, division, url):
    print(f"parsing {url}")
    soup = BeautifulSoup(html, "html.parser")
    info = {
        'name': "",
        'city': "",
        'state': "",
        'startDate': "",
        'endDate': ""
    }
    try:

        pools = soup.findAll("div", {"class": "pool"})
        bracketDivs = soup.findAll("div", {"class": "bracket"})
        if len(pools) == 0 and len(bracketDivs) == 0:
            return None
        
        name = soup.select_one("div.aaa > h2")
        if name is not None:
            info["name"] = name.text
        
        notes = soup.select_one("div.notes > div.enotes")

        info["startDate"], info["endDate"] = parse_dates(notes.contents[0], year)

        locationSpan = notes.contents[2]
        location = locationSpan.text.split(", ")
        if len(location) != 2:
            info["city"] = ""
            info["state"] = ""
        else:
            info["city"] = location[0].replace("Location: ", "")
            info["state"] = location[1]

        dt = datetime.strptime(info["startDate"], "%Y-%m-%d")

        # skip out of season college and club tournaments
        if (division == "college-open" or division == "college-womens") and (dt.month > 5 or (dt.month == 5 and dt.day > 31)):
            return None
        if (division == "open" or division == "mixed" or division == "womens") and (dt.month < 6 or dt.month > 10):
            return None 

        fullDiv = division
        if division != "college-open" and division != "college-womens":
            fullDiv = division + " club"

        teams = []
        stages = []
        if pools is not None:
            poolObjs = []
            for pool in pools:
                pool = parsePool(pool, teams, dt)
                if pool is not None:
                    poolObjs.append(pool)
            stages.append(Pools("Pools", poolObjs))
            
        if bracketDivs is not None:
            bracketObjs = []
            for div in bracketDivs:
                bracket = parseBracket(div, teams, dt, datetime.strptime(info["endDate"], "%Y-%m-%d"))
                if bracket is not None:
                    bracketObjs.append(bracket)
            stages.append(Brackets("Bracket", bracketObjs))

        # parseScoreReport(content)
        return Tournament(info["name"], url, info["city"], info["state"], info["startDate"], info["endDate"], teams, dt, fullDiv, stages)
    except:
        return None

def requestScoreReportPage(driver, url):
    delay = 5 # seconds
    driver.get(url)

    waitFor = "aaa"

    try:
        WebDriverWait(driver, delay).until(EC.presence_of_element_located((By.CLASS_NAME, waitFor)))
        content = driver.find_element(By.CLASS_NAME, "content")
        return content.get_attribute("outerHTML")
    except TimeoutException:
        print(f"Loading page {url} took too much time!")

def cachePage(url, content):
    path = CACHE_PREFIX
    urlPath = url.replace("https://scorereport.net/", "")
    filename = urlPath.split("/")[-1]
    path += urlPath.replace(filename, "")
    
    Path(path).mkdir(parents=True, exist_ok=True)

    with open(path + filename + ".html", "w") as f:
        f.write(content)
    
def loadScoreReportPage(driver, url):
    # check if page is cached
    # if cached, load from cache
    # else, request page and cache it
    cachePath = CACHE_PREFIX + url.replace("https://scorereport.net/", "") + ".html"
    html = ""
    try:
        with open(cachePath) as f:
            html = f.read()
            
    except FileNotFoundError:
        html = requestScoreReportPage(driver, url)
        cachePage(url, html)

def saveYearList(year, division, urls):
    path = CSV_PREFIX + year + "/" 
    
    Path(path).mkdir(parents=True, exist_ok=True)
    with open(path + division + ".csv", "w") as f:
        for url in urls:
            f.write(url + "\n")


def scrapeYear(driver, year, division):
    url = f"https://scorereport.net/{year}/{division}/events"
    print("scraping ", url)
    driver.get(url)
    delay = 10
    while True:
        try:
            WebDriverWait(driver, delay).until(EC.presence_of_element_located((By.CLASS_NAME, "content")))
            break
        except TimeoutException:
            print("Loading page took too much time!")
            driver.refresh()
    urls = []
    i = 0
    while True:
        links = driver.find_elements(By.CLASS_NAME, "event-grid")
        if i == len(links) - 1:
            break
        links[i].click()
        try:
            WebDriverWait(driver, delay).until(EC.presence_of_element_located((By.CLASS_NAME, "aaa")))
            content = driver.find_element(By.CLASS_NAME, "content")
            cachePage(driver.current_url, content.get_attribute("outerHTML"))
            current_url = driver.current_url
            urls.append(current_url)
            print(f"cached {i} {current_url}")
        except:
            pass
        driver.execute_script("window.history.go(-1)")
        WebDriverWait(driver, delay).until(EC.presence_of_element_located((By.CLASS_NAME, "content")))
        i += 1

    saveYearList(year, division, urls)
    
def parseYearDivision(year, division):
    path = CACHE_PREFIX + year + "/" + division 
    eventFiles = os.listdir(path)
    for eventFile in eventFiles:
        # if not eventFile == "event13315.html":
            # continue
        csvName = eventFile.replace(".html", ".csv")
        url = "https://scorereport.net/" + year + "/" + division + "/" + eventFile.replace(".html", "")
        with open(path + "/" + eventFile) as f:
            html = f.read()
            # print(html)
            # parseScoreReport(html)
            t = parseScoreReport(html, year, division, url)
            if t is not None:
                writeTournamentToCSV({ 'year': year }, t, CSV_PREFIX + year + "/" + division + "/" + csvName)

    

def scrapeAll():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    driver = webdriver.Chrome(options=options)

    for year in range(2004, 2025):
        for division in DIVISIONS:
            scrapeYear(driver, str(year), division)

def parseAll():
    for year in range(2004, 2014):
        for division in DIVISIONS:
            parseYearDivision(str(year), division)

def removeExistingCsvs():
    for year in range(2004, 2014):
        for division in DIVISIONS:
            path = CSV_PREFIX + str(year) + "/" + division
            try:
                files = os.listdir(path)
                for file in files:
                    os.remove(path + "/" + file)
            except:
                pass

if __name__ == "__main__":
    # with open("/Users/andersjuengst/dev/tmp/2006nats.html") as f:
        # html = f.read()
    # parseYearDivision("2006", "open")
    # parseYearDivision("2013", "college-open")
    removeExistingCsvs()
    parseAll()
    # parseYearDivision("2012", "open")