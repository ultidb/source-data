class Team:
    def __init__(self, name, seed, url, id=None, roster=[]):
        self.name = name
        self.id = id
        self.url = url
        self.seed = seed
        self.roster = roster
        self.nickname = ""
        self.info = None

    def to_string(self):
        return ("Team: " + self.name)

    def isSameTeam(self, other):
        return self.id == other.id

    def roster_to_string(self):
        s = ""
        if (self.roster != None):
            for player in self.roster:
                s += player.to_string() + "\n"

        return s

    def csvFormat(self):
        s = [[self.name, self.seed]]
        if self.info != None:
            s += self.info.to_csv()
        for player in self.roster:
            s.append(player.to_csv())
        return s

class TeamNameOnly:
    def __init__(self, name):
        self.name = name

    def to_string(self):
        return ("Team: " + self.name)

    def isSameTeam(self, other):
        return self.name == other.name

    def csvFormat(self):
        return [[self.name]]


# game class stores both participating teams, both their scores, and the date of the game


class Game:
    def __init__(self, teamA, teamB, teamA_score, teamB_score, datetime, status, round=None):
        self.teamA = teamA
        self.teamB = teamB
        self.teamA_score = teamA_score
        self.teamB_score = teamB_score
        self.datetime = datetime
        self.status = status
        self.round = round

    # allows sorting by datetime
    def __lt__(self, other):
        return self.datetime < other.datetime

    def to_string(self):
        return ("Game: " + str(self.datetime.strftime("%b %d %H:%M")) + " | " + self.teamA.name + " vs. " + self.teamB.name + ", " + self.teamA_score + " - " + self.teamB_score)

    def to_csv(self):
        datestring = "TBA"
        if (self.datetime != None):
            datestring = self.datetime.strftime("%m/%d/%Y, %H:%M")
        return [self.teamA.name, self.teamB.name, self.teamA_score, self.teamB_score, datestring, self.round, self.status]


# player instance stores their name, number and team
class Player:
    def __init__(self, number, name):
        self.name = name
        self.number = number

    def __it__(self, other):
        return self.number < other.number

    def to_string(self):
        return (self.number + " " + self.name)

    def to_csv(self):
        return [self.number, self.name]

# Instance of a  team over the course of a season


class TeamInfo:
    def __init__(self, nickname, location, coaches, website, facebook, twitter):
        self.nickname = nickname
        self.location = location
        self.coaches = coaches
        self.website = website
        self.facebook = facebook
        self.twitter = twitter

    def to_csv(self):
        output = [['teamInfo',self.nickname, self.location, self.website, self.facebook, self.twitter]]
        if (len(self.coaches) > 1):
            output.append(self.coaches)

        return output

# tournment class stores the name of the tournament, the teams, the games, the date, and the division (one tournament can only hold one division of a tournament)


class Tournament:
    def __init__(self, name, url, city, state, startDate, endDate, teams, datetime, division, stages):
        self.name = name
        self.url = url
        self.teams = teams
        self.stages = stages
        self.division = division  # Age - Gender
        self.city = city
        self.state = state
        self.startDate = startDate
        self.endDate = endDate
        self.datetime = datetime

    def __it__(self, other):
        return self.datetime < other.datetime

    def to_string(self):
        s = self.name + "\n" + self.division + "\n\n"
        for team in self.teams:
            s += team.name + ":\n"
        return s


class Stage:
    def __init__(self, name):
        self.name = name


class Clusters(Stage):
    def __init__(self, name, clusters):
        super().__init__(name)
        self.clusters = clusters

    def csvFormat(self):
        out = [['clusters', self.name]]
        for clusters in self.clusters:
            out += clusters.csvFormat()
        return out


class Cluster():
    def __init__(self, name, games):
        self.name = name
        self.games = games

    def csvFormat(self):
        out = [['cluster', self.name]]
        for game in self.games:
            out.append(game.to_csv())

        return out


class Pools(Stage):
    def __init__(self, name, pools):
        super().__init__(name)
        self.pools = pools

    def csvFormat(self):
        out = [['pools', self.name]]
        for pool in self.pools:
            out.append(['pool', pool.name])
            out += pool.csvFormat()
        return out


class Pool:
    def __init__(self, name, teams, games=None):
        self.name = name
        self.teams = teams  # array of teams sorted by seed
        self.games = games

    def csvFormat(self):
        out = []
        for game in self.games:
            out.append(game.to_csv())

        return out


class Brackets(Stage):
    def __init__(self, name, brackets):
        super().__init__(name)
        self.brackets = brackets

    def csvFormat(self):
        out = [['brackets', self.name]]
        for bracket in self.brackets:
            out += bracket.csvFormat()
        return out


class Bracket:
    def __init__(self, name, games):
        self.name = name
        self.games = games

    def csvFormat(self):
        out = [['bracket', self.name]]
        for game in self.games:
            out.append(game.to_csv())

        return out
    