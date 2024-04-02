from bs4 import BeautifulSoup


def parseBracket(bracketDiv):
    name = bracketDiv.find("div", {"class": "pool-title"}).text
    print(name)

    table = bracketDiv.find("table")
    if table is not None:
        rows = table.find_all("tr")

        winners = table.find_all("td", {"class": "b1awh"})

        i = 0
        for row in rows:
            cells = row.find_all("td")
            j = 0
            for cell in cells:
                if cell.has_attr("class") and "b1awh" in cell["class"]:
                    # Do something if cell has class "b1awh"
                    winner = cell.text
                    # if winner != "Chad Larson Experience":
                    #     continue
                    # print(winner)

                    loser = None
                    loserIsUpper = False
                    loserIndex = None

                    up2 = (i - 2 >= 0) and rows[i - 2].find_all("td")
                    down2 = (i + 2 < len(rows)) and rows[i + 2].find_all("td")

                    if up2 is not None:
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

                    if down2 is not None:
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

                    print(
                        f"{winner} def {loser.text}, {winnerScoreCell.text if winnerScoreCell is not None else 'N/A'} - {loserScoreCell.text if loserScoreCell is not None else 'N/A'}"
                    )
                j += 1
            i += 1

    else:
        print("No rows found in the table.")


def parseScoreReport(html):
    soup = BeautifulSoup(html, "html.parser")
    bracketDiv = soup.find("div", {"class": "bracket"})
    if bracketDiv is not None:
        bracket = parseBracket(bracketDiv)
    else:
        bracket = None
    return bracket


if __name__ == "__main__":
    with open("/Users/andersjuengst/dev/tmp/bracketDiv2.html") as f:
        html = f.read()
    parseScoreReport(html)
