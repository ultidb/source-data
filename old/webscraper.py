from calendar import calendar
from fileinput import filename
import queue
from bs4 import BeautifulSoup
import requests
import cchardet
import csv
from datetime import datetime

division_dict = {
    "College - Men" : "Men/CollegeMen/",
    "College - Men's" : "Men/CollegeMen/",
    "D-I Men" : "Men/CollegeMen/d_i_men/",
    "D-III Men" : "Men/CollegeMen/d_iii_men/",
    "Men's Lower Division" : "Men/CollegeMen/men_s_lower_division/",
    "Men's Developmental Division" : "Men/CollegeMen/men_s_developmental_division/",
    "College Women" : "Women/CollegeWomen/",
    "College - Women" : "Women/CollegeWomen/",
    "College - Women's" : "Women/CollegeWomen/",
    "College Women's  (Division I)" : "Women/CollegeWomen/division_i/",
    "College - Mixed" : "mixed/College-Mixed/",
    "D-I Women" : "Women/CollegeWomen/d_i_women/",
    "D-III Women" : "Women/CollegeWomen/d_iii_women/",
    "Womxn's Lower Division" : "Women/CollegeWomen/womxn_s_lower_division/",
    "Womxn's Upper Division" : "Men/CollegeMen/men_s_upper_division/",
    "Club - Men" : "Men/Club-Men/",
    "College - Open" : "Men/Club-Men",
    "Club - Men's" : "Men/Club-Men/",
    "Club - Men Elite" : "Men/Club-Men/elite/",
    "Club - Men Classic" : "Men/Club-Men/classic",
    "College D-I Men" : "Men/CollegeMen/college_d_i_men/",
    "College D-III Men" : "Men/CollegeMen/college_d_iii_men/",
    "College - Men DIII (Division III)" : "Men/CollegeMen/division_iii/",
    "Men's Upper Division" : "Men/CollegeMen/men_s_upper_division/",
    "Mixed" : "mixed/College-Mixed/",
    "Club - Women" : "Women/Club-Women/",
    "Club - Women's" : "Women/Club-Women/",
    "Club - Women  (Classic)" : "Women/Club-Women/classic/",
    "Club - Women Elite" : "Women/Club-Women/elite/",
    "Club Mixed" : "Mixed/Club-mixed/",
    "Club - Mixed Elite" : "mixed/Club-mixed/elite/",
    "Club - Mixed Classic" : "mixed/Club-mixed/classic/",
    "Club - Mixed" : "Mixed/Club-Mixed",
    "Club - Mixed's" : "Mixed/Club-Mixed",
    "High School - Boys" : "Boys/High-School-Boys/",
    "High School - Boys Varsity" : "Boys/High-School-Boys/high_school_boys_varsity/",
    "High School - Boys 2 JV (High School - Boys JV)" : "Boys/High-School-Boys/high_school_boys_jv/",
    "High School - Boys Elk Ridge Park" : "Boys/High-School-Boys/boys_elk_ridge_park/",
    "High School - Boys Sky View" : "Boys/High-School-Boys/boys_sky_view/",
    "High School - Boys  (HS Boys)" : "Boys/High-School-Boys/hs_boys/",
    "High School - Boys B (HS Boys B)" : "Boys/High-School-Boys/hs_boys_b/",
    "HS Open - Div I" : "Boys/High-School-Boys/",
    "High School - Girls Green Canyon High School" : "Girls/High-School-Girls/",
    "High School - Girls" : "Girls/High-School-Girls/",
    "High School - Mixed" : "Mixed/High-School-Mixed/",
    "Masters - Men" : "Men/Masters-Men/",
    "Masters - Mixed" : "Mixed/Masters-Mixed/",
    "Masters - Women" : "Women/Masters-Women/",
    "Grand Masters - Men" : "Men/grand-masters-men/",
    "Grand Masters - Women" : "Women/grand-masters-women/",
    "Grand Masters - Mixed" : "mixed/grand-masters-mixed/",
    "Great Grand Masters - Men" : "Men/great-grand-masters-men/",
    "Great Grand Masters - Women" : "Women/great-grand-masters-women/",
    "Men's Division" : "Men/CollegeMen/",
    "Men A Division" : "Men/Club-Men/a_division/",
    "Men B Division" : "Men/Club-Men/b_division",
    "U20 - Boys" : "Boys/High-School-Boys/",
    "U20 - Girls" : "Girls/High-School-Girls/",
    "Youth Club U-20 - Boys" : "Boys/youth-club-u-20-boys/",
    "U20 Boys Youth Club" : "Boys/youth-club-u-20-boys/",
    "Youth Club U19 - Boys" : "Boys/youth-club-u-20-boys/",
    "Youth Club U-20 - Girls" : "Girls/youth-club-u-20-girls/",
    "U20 Girls Youth Club" : "Girls/youth-club-u-20-girls/",
    "Youth Club U19 - Girls" : "Girls/youth-club-u-20-girls/",
    "Youth Club U-20 - Mixed" : "Mixed/youth-club-u-20-mixed/",
    "U20 Mixed Youth Club" : "Mixed/youth-club-u-20-mixed/",
    "Youth Club U19 - Mixed" : "Mixed/youth-club-u-20-mixed/",
    "Youth Club U-17 - Boys" : "Boys/youth-club-u-17-boys/",
    "Youth Club U16 - Boys" : "Boys/youth-club-u-17-boys/",
    "Youth Club U-17 - Girls" : "Girls/youth-club-u-17-girls/",
    "Youth Club U16 - Girls" : "Girls/youth-club-u-17-girls/",
    "Youth Club Girls - U20 & U17" : "Girls/youth-club-u-20-girls/",
    "Middle School - Mixed" : "mixed/Middle-School-Mixed/",
    "Middle School - Boys" : "Boys/Middle-School-Boys/",
    "Beach - Men" : "Men/Beach-Mens/",
    "Beach - Women" : "Women/Beach-Womens/",
    "Beach - Mixed" : "mixed/Beach-Mixed",
    "Beach Masters - Mixed" : "mixed/Beach-Masters-Mixed/",
    "Beach Grand Masters - Men" : "Men/Beach-Grand-Masters-Men/",
    "Beach Great Grand Masters - Men" : "Men/beach-great-grand-masters-men/"
}

#Team class, allows storage of name and roster
class Team:
    def __init__(self, name, id=None, roster=None):
        self.name = name
        self.id = id
        self.games = []
        self.rating = 1000
        self.game_ratings = []
        self.roster = roster
        self.season = None

    #allows sorting by seed or rating
    def __lt__(self, other):
        #return int(self.seed) < int(other.seed)
        return int(self.rating) > int(other.rating)

    def to_string(self):
        return ("Team: " + self.name)

    def add_game(self, game):
        self.games.append(game)

    def add_roster(self, roster):
        self.roster = roster

    def print_games(self):
        self.games.sort()
        print(self.to_string())
        for game in self.games:
            print(game.to_string())

    def isSameTeam(self,other):
        return self.id == other.id

    def roster_to_string(self):
        s = ""
        if (self.roster != None):
            for player in self.roster:
                s += player.to_string() + "\n"

        return s

    def writeString(self):
        s = [[self.name]]
        for player in self.roster:
            s.append(player.to_csv())
        return s

#game class stores both participating teams, both their scores, and the date of the game
class Game:
    def __init__(self, teamA, teamB, teamA_score, teamB_score, datetime):
        self.teamA = teamA
        self.teamB = teamB
        self.teamA_score = teamA_score
        self.teamB_score = teamB_score
        self.datetime = datetime

    #allows sorting by datetime
    def __lt__(self, other):
        return self.datetime < other.datetime

    def to_string(self):
        return ("Game: " + str(self.datetime.strftime("%b %d %H:%M")) + " | " + self.teamA.name + " vs. " + self.teamB.name + ", " + self.teamA_score + " - " + self.teamB_score)

    def to_csv(self):
        return [self.teamA.name, self.teamB.name, self.teamA_score, self.teamB_score, self.datetime.strftime("%m/%d/%Y, %H:%M")]


#player instance stores their name, number and team
class playerInstance:
    def __init__(self, number, name, team):
        self.name = name
        self.number = number
        self.team = team

    def __it__(self, other):
        return self.number < other.number

    def to_string(self):
        return (self.number + " " + self.name)

    def to_csv(self):
        return [self.number, self.name]

#Instance of a  team over the course of a season
class TeamInstance:
    def __init__(self,teamID, location, coaches):
        self.teamID = teamID
        self.location = location
        self.coaches = coaches

    def to_csv(self):
        return ([self.teamID,self.location,self.coaches])

#tournment class stores the name of the tournament, the teams, the games, the date, and the division (one tournament can only hold one division of a tournament)
class Tournament:
    def __init__(self, name, teams, games, datetime, division):
        self.name = name
        self.teams = teams
        self.games = games
        self.datetime = datetime
        self.division = division #Age - Gender

    def __it__(self,other):
        return self.datetime < other.datetime

    def to_string(self):
        s = self.name + "\n" + self.division + "\n\n"
        for team in self.teams:
            s += team.name + ":\n"
            s += team.roster_to_string() + "\n"

        s += "Game Results:\n"
        self.games.sort()
        for game in self.games:
            s += game.to_string() + "\n"

        return s

#exception type for when tournamnet page contains either no data or all 0-0 games
class NoValidGamesException(Exception):
    pass

#exception for when scraping of a tournamnet fails while scraping team page
class InternetConnectionFailedException(Exception):
    pass

#scrapes data from tournament url, scraping teams, games, and players
def get_tournament(url, rsession, tyear=None):
    #using dictionary to save time retrieving team data
    teams = {}

    #tournament date information to use for games that are missig data
    if tyear == None:
        tyear = 2020
    t_month = 0
    t_day = 0

    #opening html parser
    soup = BeautifulSoup(rsession.get(url).content, 'html.parser')

    #finding tournament name
    tournament_name = str(soup.find("div", {"class": "breadcrumbs"}).find_all("a")[1].contents[0])

    #finding all pools
    pools_games = soup.find_all("table", {"class": "global_table scores_table"})
    games = []

    #scrapes pools games
    for pool in pools_games:

        for row in pool.find("tbody").find_all("tr"):
            #testing to see if rows have a game listed on them
            if row.has_attr("data-game"):
                #scape scores of games
                teamA_score = row.find_all("td")[5].find_all("span", {"class": "isScore"})[0].find("span").contents[0]
                teamB_score = row.find_all("td")[5].find_all("span", {"class": "isScore"})[1].find("span").contents[0]

                #error checker to eliminate forfits and unplayed games
                if not teamA_score.isdigit() or not teamB_score.isdigit() or (int(teamA_score) <= 1 and int(teamB_score) <= 1):
                    continue

                #html code which contains team names, scores, and team ids
                team_info = row.find_all("td")[3:5]

                #errorr checking to make sure td did not forget to update teams
                if team_info[0].contents[0] == "TBD" or team_info[1].contents[0] == "TBD" or team_info[0].find("a").contents[0][1].isdigit() or team_info[1].find("a").contents[0][1].isdigit():
                    continue

                #collecting data of team names and ids
                teamA_name = team_info[0].find("a").contents[0].rsplit(' ', 1)[0]
                teamB_name = team_info[1].find("a").contents[0].rsplit(' ', 1)[0]
                teamA_id = team_info[0].find("a").get("href").split("=")[1]
                teamB_id = team_info[1].find("a").get("href").split("=")[1]
                teamA, teamB = None, None
                teamA_boolean, teamB_boolean = False, False #track if teams alreaddy exist

                #connecting teams in a game to team in dictionary
                if teamA_name in teams:
                    teamA = teams[teamA_name]
                    teamA_boolean = True
                if teamB_name in teams:
                    teamB = teams[teamB_name]
                    teamB_boolean = True
                
                #adding new teams
                if not teamA_boolean:
                    teamA = Team(teamA_name, teamA_id, None)
                    teams[teamA_name] = teamA
                    roster = []
                    try:
                        team_page = BeautifulSoup(rsession.get("https://play.usaultimate.org"+team_info[0].find("a").get("href")).content, 'html.parser').find("table").findAll("tr")[1:]
                        for page in team_page:    #adding roster info to teams
                            page = page.findAll("td")
                            num = page[0].contents[0]
                            name = page[1].contents[0]
                            roster.append(playerInstance(num,name, teamA_name))
                    except Exception as e:
                        print("https://play.usaultimate.org"+team_info[0].find("a").get("href"))
                        print(e)
                        roster.append(playerInstance(0,"error with roster data",teamA_name))
                    teams[teamA_name].add_roster(roster)

                if not teamB_boolean:
                    teamB = Team(teamB_name, teamB_id, None)
                    teams[teamB_name] = teamB
                    roster = []
                    try:
                        team_page = BeautifulSoup(rsession.get("https://play.usaultimate.org"+team_info[1].find("a").get("href")).content, 'html.parser').find("table").findAll("tr")[1:]
                        for page in team_page:  #Addding roster info to teams
                            page = page.findAll("td")
                            num = page[0].contents[0]
                            name = page[1].contents[0]
                            roster.append(playerInstance(num,name, teamB_name))
                    except Exception as e:
                        print("https://play.usaultimate.org"+team_info[1].find("a").get("href"))
                        print(e)
                        roster.append(playerInstance(0,"error with roster data", teamB_name))
                    teams[teamB_name].add_roster(roster)

                try:
                    date = row.find_all("td")[0].find("span").contents[0]
                    time = row.find_all("td")[1].find("span").contents[0]
                    month = int(date[4:date.find('/')])
                    day = int(date[date.find('/')+1:len(date)])
                    hour = time[0:time.find(":")]

                    #handle invalid times
                    if hour.isdigit():
                        hour = int(hour)
                        minute = int(time[time.find(":")+1:time.find(":")+3])
                        if (time[len(time)-2:len(time)] == "PM" and hour != 12):
                            hour += 12
                    else:
                        hour = 0
                        minute = 0

                    game_datetime = datetime(tyear, month, day, hour, minute)
                except:
                    #for games where TD failed to put a date
                    game_datetime = datetime(tyear,12,25,1,0)

                #commit games to objects
                game = Game(teamA, teamB, teamA_score, teamB_score, game_datetime)
                games.append(game)
                teamA.add_game(game)
                teamB.add_game(game)

    #scrapes bracket and consolation play
    columns = soup.find_all("div", {"class": "bracket_col"})
    for column in columns:
        column_name = column.find("h4", {"class":"col_title"}).contents[0]
        column_games = column.find_all("div", {"class": "bracket_game"})
        for game in column_games:
            #contains all info on team name, ids, and team_pages
            game_teams = game.find_all("span", {"class": "team"})

            team_scores = game.find_all("span", {"class": "score"})
            teamA_score = team_scores[0].contents[0]
            teamB_score = team_scores[1].contents[0]

            #error checker to eliminate forfits and unplayed games
            if not teamA_score.isdigit() or not teamB_score.isdigit() or (int(teamA_score) <= 1 and int(teamB_score) <= 1):
                continue

            #errorr checking to make sure td did not forget to update teams
            if game_teams[0].contents[0] == "TBD" or game_teams[1].contents[0] == "TBD" or game_teams[0].find("a").contents[0][1].isdigit() or game_teams[1].find("a").contents[0][1].isdigit():
                continue

            teamA_name = game_teams[0].contents[0].contents[0].rsplit(' ', 1)[0]
            teamB_name = game_teams[1].contents[0].contents[0].rsplit(' ', 1)[0]
            teamA_id = game_teams[0].contents[0].get("href").split("=")[1]
            teamB_id = game_teams[1].contents[0].get("href").split("=")[1]
            teamA_boolean, teamB_boolean = False, False #track if team needs to be created

            #connecting teams in a game to team in dictionary
            if teamA_name in teams:
                teamA = teams[teamA_name]
                teamA_boolean = True
            if teamB_name in teams:
                teamB = teams[teamB_name]
                teamB_boolean = True

            # add new team to teams
            if teamA_boolean == False:
                teamA = Team(teamA_name, teamA_id,None)
                teams[teamA_name] = teamA
                roster = []
                try:
                    team_page = BeautifulSoup(rsession.get("https://play.usaultimate.org"+game_teams[0].contents[0].get("href")).content, 'html.parser').find("table").findAll("tr")[1:]
                    for page in team_page:   #adding roster info to teams
                        page = page.findAll("td")
                        num = page[0].contents[0]
                        name = page[1].contents[0]
                        roster.append(playerInstance(num,name,teamA_name))
                except Exception as e:
                    print("https://play.usaultimate.org"+game_teams[0].contents[0].get("href"))
                    print(e)
                    roster.append(playerInstance(0,"Error in roster data", teamA_name))
                teams[teamA_name].add_roster(roster)

            if teamB_boolean == False:
                teamB = Team(teamB_name, teamB_id,None)
                teams[teamB_name] = teamB
                roster = []
                try:
                    team_page = BeautifulSoup(rsession.get("https://play.usaultimate.org"+game_teams[1].contents[0].get("href")).content, 'html.parser').find("table").findAll("tr")[1:]
                    for page in team_page:  #adding roster info to teams
                        page = page.findAll("td")
                        num = page[0].contents[0]
                        name = page[1].contents[0]
                        roster.append(playerInstance(num,name,teamB_name))
                except requests.exceptions.RequestException as e:
                    raise InternetConnectionFailedException("Error with request Module, will try and reload tournaments")
                except Exception as e:
                    print("https://play.usaultimate.org"+game_teams[1].contents[0].get("href"))
                    print(e)
                    roster.append(playerInstance(0,"Error in roster data",teamB_name))
                teams[teamB_name].add_roster(roster)

            #scaping date information from webpage
            game_date = game.find("span", {"class": "date"}).contents[0].split(" ")
            year = int(game_date[0].split("/")[2])
            month = int(game_date[0].split("/")[0])
            day = int(game_date[0].split("/")[1])
            hour = game_date[1].split(":")[0]

            #handling tournaments that forget to update time of game
            if hour.isdigit():
                hour = int(hour)
                minute = int(game_date[1].split(":")[1])
                if (game_date[2] == "PM" and hour != 12):
                    hour += 12
            else:
                hour = 0
                minute = 0

            game_datetime = datetime(year, month, day, hour, minute)

            #adding game to games list
            game = Game(teamA, teamB, teamA_score, teamB_score, game_datetime)
            games.append(game)
            teamA.add_game(game)
            teamB.add_game(game)

    #checking to see if any games were succesfully scraped
    if (len(games) == 0):
        raise NoValidGamesException("No legal games were played at this tournament")

    #turned team from dictionary into list of teams objects
    teams = list(teams.values())

    #retrieve divison from header
    division = soup.find("h1", {"class": "title"}).contents[0]

    #retrieve month and day from game
    t_month = games[0].datetime.month
    t_day = games[0].datetime.day

    return Tournament(tournament_name,teams,games,datetime(tyear,t_month,t_day,0,0),division)

def writeTournamentToCSV(filename, teams, games, tournament_name, division, t_day, t_month, t_year):
    """take tournament data and save it in a csv file"""

    #write data from tournaments to csv file
    with open(filename,  'w', newline='') as f:
        #create the csv writer
        writer = csv.writer(f)

        #write tournament general information on first line
        writer.writerow([tournament_name,division,t_day,t_month,t_year])

        #Writer team name and team roster on line
        for team in teams:
            writer.writerows(team.writeString())
            writer.writerow("break")

        #write game data to new line
        for game in games:
            writer.writerow(game.to_csv())

def webscrapeTournamentCalendar(url):
    """scrape tournament data for entire usau tournamnet calendar webpage"""

    #create html parser for specfici url
    soup = BeautifulSoup(requests.get(url).content, 'html.parser')

    #access table of tournmanets
    calendar = soup.find("table", {"class": "global_table"}).findAll("tr")[1:]

    #list of tournament links
    tournament_links = []

    #list of links to specific divisons within tournamnets
    page_links = []

    #find specific tournament divison pages that should have teams
    for t in calendar:

        #iterate through each divison of a tournament
        for num in t.findAll("li"):

            #link to overall tournament page
            link = t.find("a").get("href")

            #check to see if a division has more than 1 team in the division
            if int(num.contents[1].contents[0][1:-1]) > 1:

                # add link of specific divison page to list
                try:
                    page_links.append(link + "/schedule/" + division_dict[num.contents[0].strip()])
                except:
                    print("Unknown division: " + num.contents[0].strip() + " from " + link) 

    num_links = str(len(page_links))
    print("Found " + num_links +  " tounrmanet pages to scrape")

    return page_links