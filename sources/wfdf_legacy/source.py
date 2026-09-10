"""`WfdfLegacySource`: `core.source.Source` implementation for WFDF's older,
static-HTML "legacy" results platform (`results.wfdf.sport/<slug>/`).

Every event is a finished historical tournament (see
`sources/wfdf_legacy/events.py`'s module docstring) -- there is no `live`/
`refresh_rosters` concept here, unlike `sources.wfdf.source.WfdfSource` and
`sources.usau.source.UsauSource`: every page is fetched once and cached
forever (a human forcing a re-fetch just deletes
`cache/wfdf-legacy/<year>/<key>/`). `cli.py`'s existing `--live`/
`--refresh-rosters` guard already rejects those flags for any source id
other than "wfdf"/"usau", so no `cli.py` changes are needed to wire this up.

All parsing logic that doesn't touch the network lives in
`sources/wfdf_legacy/parse.py`; this module is just discovery, page
fetching, and gluing `parse.py`'s outputs into a `models.Tournament`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

import models
from core.source import Cache, EventRef, FetchedPages, Source

from sources.wfdf_legacy import parse
from sources.wfdf_legacy.events import WfdfLegacyEvent, events_for_year

log = logging.getLogger(__name__)

# Seconds to wait between requests, drawn uniformly at random each time --
# mirrors sources/wfdf/source.py's REQUEST_DELAY_RANGE. Override with
# WFDF_LEGACY_DELAY_MIN / WFDF_LEGACY_DELAY_MAX.
REQUEST_DELAY_RANGE = (
    float(os.environ.get("WFDF_LEGACY_DELAY_MIN", "1.5")),
    float(os.environ.get("WFDF_LEGACY_DELAY_MAX", "4.0")),
)


def _games_url(base_url: str) -> str:
    return f"{base_url}/?view=games&filter=tournaments&group=all"


def _allteams_url(base_url: str, season_id: str) -> str:
    return f"{base_url}/?view=teams&season={season_id}&list=allteams"


def _byseeding_url(base_url: str, season_id: str) -> str:
    return f"{base_url}/?view=teams&season={season_id}&list=byseeding"


def _teamcard_url(base_url: str, team_id: int) -> str:
    return f"{base_url}/?view=teamcard&team={team_id}"


class WfdfLegacySource(Source):
    id = "wfdf-legacy"

    def __init__(self, events: Optional[List[WfdfLegacyEvent]] = None):
        # Overridable so tests can point at a scratch event list, mirroring
        # WfdfSource's `events=` override.
        self._events = events

        # In-process memoization for one scrape run: `games`/`allteams`/
        # `byseeding` are identical across all series of one event and would
        # otherwise be fetched N times (once per series) -- mirrors
        # WfdfSource's `_reference_bytes`/`_games_bytes`. Keyed by season_id.
        self._games_bytes: Dict[str, bytes] = {}
        self._allteams_bytes: Dict[str, bytes] = {}
        self._byseeding_bytes: Dict[str, bytes] = {}

    # -- Source contract -----------------------------------------------

    def make_transport(self):
        from core.fetch import RequestsTransport

        return RequestsTransport(delay_range=REQUEST_DELAY_RANGE)

    def discover(self, year: int) -> List[EventRef]:
        # discover() gets no Cache (mirrors UsauSource.discover's documented
        # "discover() gets no Cache" pattern, NOT WfdfSource.discover's --
        # unlike the newer WFDF JSON API, this source's series/pool
        # structure lives on the same games page as the schedule itself, so
        # discover() has to fetch it to match configured series names
        # against the site's own nav). The fetched bytes are stashed so
        # fetch_event doesn't refetch them.
        events = self._events if self._events is not None else events_for_year(year)
        transport = self.make_transport()

        refs: List[EventRef] = []
        for event in events:
            if event.year != year:
                continue

            games_bytes = transport(_games_url(event.base_url))
            self._games_bytes[event.season_id] = games_bytes
            soup = BeautifulSoup(games_bytes, "html.parser")
            nav = parse.parse_nav(soup)
            nav_by_name = {series.name: series for series in nav.values()}

            missing = [s.name for s in event.series if s.name not in nav_by_name]
            if missing:
                log.error(
                    "wfdf-legacy: %s: configured series %s not found on the games page "
                    "(found: %s); skipping this event",
                    event.season_id, missing, sorted(nav_by_name),
                )
                continue

            for series in event.series:
                nav_series = nav_by_name[series.name]
                refs.append(
                    EventRef(
                        url=event.base_url,
                        name=event.name or None,
                        division=event.division,
                        gender=series.gender,
                        city=event.city,
                        state=event.state,
                        country=event.country,
                        start_date=None,
                        end_date=None,
                        extra={
                            "base_url": event.base_url,
                            "season_id": event.season_id,
                            "series_id": nav_series.series_id,
                            "series_name": series.name,
                            "pool_ids": [p.pool_id for p in nav_series.pools],
                        },
                    )
                )
        return refs

    def event_key(self, ref: EventRef) -> str:
        return f"{ref.extra['season_id']}/{ref.extra['series_name']}"

    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages:
        base_url = ref.extra["base_url"]
        season_id = ref.extra["season_id"]
        series_name = ref.extra["series_name"]

        pages: FetchedPages = {
            "games": self._fetch_shared("games", self._games_bytes, season_id, _games_url(base_url), cache),
            "allteams": self._fetch_shared(
                "allteams", self._allteams_bytes, season_id, _allteams_url(base_url, season_id), cache
            ),
            "byseeding": self._fetch_shared(
                "byseeding", self._byseeding_bytes, season_id, _byseeding_url(base_url, season_id), cache
            ),
        }

        allteams_soup = BeautifulSoup(pages["allteams"], "html.parser")
        teams_by_series = parse.parse_teams_page(allteams_soup)
        team_ids = sorted({row.team_id for row in teams_by_series.get(series_name, [])})

        # No TTL, no refresh flag -- every event here is a finished
        # historical tournament (see the module docstring), so a plain
        # cache.fetch with defaults is correct: fetch once, cache forever.
        #
        # A single team page failing to fetch (network blip, or a genuinely
        # broken upstream page -- confirmed on WU24-2023, where every
        # ?view=teamcard/?view=playerlist request 500s while games/allteams
        # are fine) must not sink the whole event when scores/pools/teams
        # are otherwise fully fetchable. parse_event already treats a page
        # missing from this dict as "leave that team's roster empty" (see
        # its `pages.get(f"team:{team.id}")` check), so catching here and
        # simply not adding the key reuses that same, already-tested path.
        failed = 0
        for team_id in team_ids:
            key = f"team:{team_id}"
            try:
                pages[key] = cache.fetch(key, _teamcard_url(base_url, team_id))
            except Exception:
                log.warning(
                    "wfdf-legacy: %s/%s -- failed to fetch team page for team_id=%s; "
                    "leaving that team's roster empty",
                    season_id, series_name, team_id, exc_info=True,
                )
                failed += 1

        log.info(
            "wfdf-legacy: %s/%s -- %d team page(s), %d failed to fetch",
            season_id, series_name, len(team_ids), failed,
        )

        return pages

    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> Optional["models.Tournament"]:
        series_name = ref.extra["series_name"]
        season_id = ref.extra["season_id"]

        games_soup = BeautifulSoup(pages["games"], "html.parser")
        allteams_soup = BeautifulSoup(pages["allteams"], "html.parser")
        byseeding_soup = BeautifulSoup(pages["byseeding"], "html.parser")

        nav = parse.parse_nav(games_soup)
        nav_series = next((s for s in nav.values() if s.name == series_name), None)
        if nav_series is None:
            log.error(
                "wfdf-legacy: %s: series %r missing from the games page's nav at parse time; skipping",
                season_id, series_name,
            )
            return None

        teams_by_series = parse.parse_teams_page(allteams_soup)
        seeds_by_team_id = parse.parse_seeding_page(byseeding_soup)
        teams, teams_by_raw_name = parse.build_teams(teams_by_series, series_name, seeds_by_team_id)
        if not teams:
            log.warning("wfdf-legacy: %s: no teams found for series %r; skipping", season_id, series_name)
            return None

        for team in teams:
            team_bytes = pages.get(f"team:{team.id}")
            if team_bytes is None:
                log.warning(
                    "wfdf-legacy: missing team page for team %r (id=%r); leaving roster empty",
                    team.name, team.id,
                )
                continue
            team.roster = parse.parse_teamcard(BeautifulSoup(team_bytes, "html.parser"))

        schedule_by_pool_id, pool_headers_by_id = parse.parse_schedule_page(games_soup)
        stages, skipped = parse.build_stages(
            nav_series.pools, schedule_by_pool_id, teams_by_raw_name, series_name, pool_headers_by_id
        )

        game_dates = [
            game.datetime.date()
            for stage in stages
            for group in (getattr(stage, "pools", None) or getattr(stage, "brackets", None) or stage.clusters)
            for game in group.games
            if game.datetime is not None
        ]
        if game_dates:
            start_date = min(game_dates)
            end_date = max(game_dates)
        else:
            # Only reachable if every game on the page had an unparseable
            # time -- fall back to events.yaml's hand-typed (informational)
            # dates rather than leaving the document dateless.
            log.warning(
                "wfdf-legacy: %s/%s -- no game carried a parseable date; falling back to "
                "events.yaml's hand-typed dates",
                season_id, series_name,
            )
            start_date = ref.start_date
            end_date = ref.end_date
        tournament_datetime = datetime.combine(start_date, datetime.min.time())

        log.info(
            "wfdf-legacy: %s/%s -- %d team(s), %d stage(s), %d game(s) skipped (unresolved)",
            season_id, series_name, len(teams), len(stages), skipped,
        )

        tournament = models.Tournament(
            ref.name or f"{season_id} {series_name}",
            ref.url,
            ref.city,
            ref.state,
            start_date.isoformat(),
            end_date.isoformat(),
            teams,
            tournament_datetime,
            ref.division,
            stages,
        )
        tournament.country = ref.country
        tournament.gender = ref.gender
        return tournament

    # -- internals -------------------------------------------------------

    def _fetch_shared(
        self, page_key: str, memo: Dict[str, bytes], season_id: str, url: str, cache: Cache
    ) -> bytes:
        cached = memo.get(season_id)
        if cached is not None:
            # Already fetched for another series in this run -- warm this
            # series' own page cache with the same bytes rather than
            # refetching over the network.
            cache.put(page_key, cached)
            return cached
        raw = cache.fetch(page_key, url)
        memo[season_id] = raw
        return raw
