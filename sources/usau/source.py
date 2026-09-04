"""`UsauSource`: `core.source.Source` implementation for USAU tournament
pages (play.usaultimate.org), built on the HTML parsing moved into
sources/usau/parse.py (MULTI-SOURCE-REDESIGN.md Phase 3).

Wired into both cli.py's `scrape year --source=usau` and app.py's USAU
scheduler jobs (`scrapeOngoingUsauEvents` etc.), driven through the shared
`core.pipeline.run_pipeline` like every other registered source. Also
covered directly by golden fixtures (tests/test_usau_fixtures.py).
"""
from __future__ import annotations

import logging as log
from datetime import date
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

import models
from core.source import Cache, EventRef, FetchedPages, Source
from sources.usau.parse import (
    addInfoToTeam,
    addRosterToTeam,
    getUrlDivisionSuffix,
    parseNewSchedule,
    parseTournament,
)

COLLEGE_SCHEDULE_URL = "https://usaultimate.org/college/schedule/"
CLUB_SCHEDULE_URL = "https://usaultimate.org/club/schedule/"


class UsauSource(Source):
    id = "usau"

    def __init__(self, *, transport=None):
        # Overridable so tests can inject a fake transport instead of the
        # real Selenium+Tor stack -- mirrors WfdfSource's events= override.
        self._transport = transport

    # -- Source contract -----------------------------------------------

    def make_transport(self):
        if self._transport is not None:
            return self._transport

        from core.fetch import SeleniumTorTransport

        return SeleniumTorTransport()

    def discover(self, year: int) -> List[EventRef]:
        # discover() gets no Cache (per the Source contract -- WfdfSource's
        # discover() doesn't fetch the network at all), so the two schedule
        # pages are fetched uncached, once per call. This is the new-schedule
        # shape (parseNewSchedule) that today's live scrapeCurrentYear uses;
        # the legacy SeasonId calendar shape (parseTournamentCalendar) has no
        # caller anywhere in the codebase today and isn't wired in here.
        transport = self.make_transport()
        college_html = transport(COLLEGE_SCHEDULE_URL)
        club_html = transport(CLUB_SCHEDULE_URL)

        page_links = parseNewSchedule(college_html, year, isCollege=True) + parseNewSchedule(
            club_html, year, isCollege=False
        )

        refs = []
        for link in page_links:
            refs.append(
                EventRef(
                    url=link["url"],
                    city=link.get("city", ""),
                    state=link.get("state", ""),
                    start_date=_parse_iso_date(link.get("startDate")),
                    end_date=_parse_iso_date(link.get("endDate")),
                    # source-private: fetch_event needs `year` for
                    # parseTournament's pool-game date construction, but
                    # fetch_event's signature (per the Source contract)
                    # doesn't receive it directly.
                    extra={"year": year},
                )
            )
        return refs

    def event_key(self, ref: EventRef) -> str:
        # Same derivation scrapeTournament uses today: url in the form
        # https://play.usaultimate.org/events/<Slug>/schedule/<Gender>/<Division>/
        parts = ref.url.split("/")
        key = parts[4].strip() + parts[7].strip()
        suffix = getUrlDivisionSuffix(ref.url)
        if suffix:
            key += "-" + suffix
        return key

    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages:
        tournament_bytes = cache.fetch("tournament", ref.url)
        pages: FetchedPages = {"tournament": tournament_bytes}

        # Parse once, discarding the roster-less Tournament, purely to
        # discover each team's id/url -- mirrors WfdfSource.fetch_event's
        # double-parse of `reference` to get team_ids, rather than inventing
        # a new partial-parse API just for this.
        provisional = parseTournament(
            tournament_bytes, self._info_dict(ref), "", ref.extra.get("year", 0)
        )
        if provisional is None:
            return pages

        for team in provisional.teams:
            if team.name == "TEAM_NAME_NOT_FOUND":
                continue
            key = f"team:{team.id}"
            pages[key] = cache.fetch(key, team.url)

        return pages

    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> Optional["models.Tournament"]:
        tournament_bytes = pages.get("tournament")
        if tournament_bytes is None:
            return None

        tournament = parseTournament(tournament_bytes, self._info_dict(ref), "", year)
        if tournament is None:
            return None

        for team in tournament.teams:
            if team.name == "TEAM_NAME_NOT_FOUND":
                continue
            team_bytes = pages.get(f"team:{team.id}")
            if team_bytes is None:
                log.warning(
                    "usau: missing team page for team %r (id=%r); leaving roster empty",
                    team.name, team.id,
                )
                continue
            soup = BeautifulSoup(team_bytes, "html.parser")
            addRosterToTeam(soup, team)
            addInfoToTeam(soup, team)

        return tournament

    # -- internals -------------------------------------------------------

    def _info_dict(self, ref: EventRef) -> Dict[str, str]:
        """The `info` dict parseTournament expects: {"url","city","state",
        "startDate","endDate"} -- the same shape scrapeTournament builds
        from the calendar CSV today, sourced here from EventRef fields."""
        return {
            "url": ref.url,
            "city": ref.city,
            "state": ref.state,
            "startDate": ref.start_date.isoformat() if ref.start_date else "",
            "endDate": ref.end_date.isoformat() if ref.end_date else "",
        }


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)
