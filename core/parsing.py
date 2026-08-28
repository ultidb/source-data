"""Source-agnostic parsing helpers.

Duplication note (see MULTI-SOURCE-REDESIGN.md "What splits out of parse.py"
and the Phase 1 scope in CONTRACT.md): `extract_nickname`,
`convertNonDigitScore`, `parseCoaches`, and `stagesHaveGames` are copied here
VERBATIM from parse.py rather than moved. USAU still runs on the old
parse.py path until Phase 3, so parse.py is intentionally left untouched and
these four functions now exist in two places. Do not let the two copies
drift; when Phase 3 ports USAU onto sources/usau/ + core/parsing.py, delete
the copies in parse.py and repoint its callers here.

`parse_seeded_name` is new: it lifts the seed-from-trailing-"(n)" extraction
that is inlined inside parse.py's `convertTeamLinkToTeam`, so both the legacy
USAU path (eventually) and future sources can share it.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bs4 import element

from models import Brackets, Clusters, Pools


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


def convertNonDigitScore(score):
    if score == "W" or score == "w" or score == "Win" or score == "win":
        return 1
    else:
        return 0


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


def parse_seeded_name(text: str) -> Tuple[str, Optional[int]]:
    """Extract a trailing "(<digits>)" seed suffix from a team display string.

    Lifted from the seed-parsing logic inlined in parse.py's
    `convertTeamLinkToTeam` (a backward scan of digits from a trailing ')').
    Unlike that inlined version -- which assumes a well-formed "(n)" suffix
    and can raise (IndexError/ValueError) on inputs like "Team()" or a lone
    trailing ")" with no digits before it -- this function requires an
    actual '(' immediately before the digit run and falls back to
    `(text.strip(), None)` when the pattern doesn't match, so it is safe to
    call on arbitrary team-name text.

    Returns (name_without_seed, seed) if a "(<digits>)" suffix is found,
    otherwise (text.strip(), None).

    >>> parse_seeded_name("Vicious Cycle (4)")
    ('Vicious Cycle', 4)
    >>> parse_seeded_name("Vicious Cycle")
    ('Vicious Cycle', None)
    """
    stripped = text.strip()

    if stripped.endswith(")"):
        index = 2
        while index <= len(stripped) and stripped[-index].isdigit():
            index += 1

        has_digits = index > 2
        in_bounds = index <= len(stripped)
        if has_digits and in_bounds and stripped[-index] == "(":
            seed = int(stripped[-(index - 1) : -1])
            name = stripped[:-index].strip()
            return name, seed

    return stripped, None
