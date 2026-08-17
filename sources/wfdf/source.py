"""`WfdfSource`: `core.source.Source` implementation for WFDF's static-JSON
results API (`results.wfdf.sport`).

MVP scope (see `sources/wfdf/events.py`): WUCC 2026 only, one document per
`series` (WFDF's term for gender division -- Mixed/Open/Women's), no
calendar scraping. All parsing logic that doesn't touch the network lives in
`sources/wfdf/parse.py`; this module is just discovery, URL/page fetching,
and gluing `parse.py`'s outputs into a `models.Tournament`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

import models
from core.source import Cache, EventRef, FetchedPages, Source

from sources.wfdf import parse
from sources.wfdf.events import WfdfEvent, events_for_year

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://results.wfdf.sport"

# Seconds to wait between WFDF requests, drawn uniformly at random each time.
# Roughly 1.5-4s over ~138 requests puts a full season scrape around 4-6
# minutes, which is unhurried for data that changes at most a few times a day.
# Override with WFDF_DELAY_MIN / WFDF_DELAY_MAX.
REQUEST_DELAY_RANGE = (
    float(os.environ.get("WFDF_DELAY_MIN", "1.5")),
    float(os.environ.get("WFDF_DELAY_MAX", "4.0")),
)

# How long a cached roster page is considered fresh, in seconds. Rosters
# barely change once a tournament starts -- unlike `_reference`/`_games`
# (pools, scores, structure), which are refetched on every live run -- so
# they're served from cache until they age past this TTL. Override with
# WFDF_ROSTER_MAX_AGE_SECONDS.
ROSTER_MAX_AGE_SECONDS = float(os.environ.get("WFDF_ROSTER_MAX_AGE_SECONDS", str(12 * 60 * 60)))


class WfdfSource(Source):
    id = "wfdf"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        events: Optional[List[WfdfEvent]] = None,
        *,
        live: bool = False,
        refresh_rosters: bool = False,
    ):
        self._base_url = base_url.rstrip("/")
        # Overridable so tests can point at a scratch event list without
        # touching the hardcoded WFDF_EVENTS (mirrors ExampleSource's
        # fixtures_dir override).
        self._events = events

        # `live=True` forces `_reference`/`_games` (pools, scores, game
        # status -- the stuff that changes as a tournament progresses) to
        # always be refetched. `refresh_rosters=True` additionally bypasses
        # the roster TTL below, forcing every roster page to be refetched
        # too. See the WFDF source task for the USAU-mirroring rationale.
        self.live = live
        self.refresh_rosters = refresh_rosters

        # In-process memoization for one scrape run: `_reference` and
        # `_games` are identical across all 3 series of one season and would
        # otherwise be fetched 3x (see the WFDF source task). Keyed by
        # season_id. Not a `Cache` -- this is Python-process-lifetime only,
        # deliberately separate from the on-disk page cache.
        self._reference_bytes: Dict[str, bytes] = {}
        self._games_bytes: Dict[str, bytes] = {}

    # -- Source contract -----------------------------------------------

    def make_transport(self):
        """Throttled transport. A full season is ~138 requests (reference,
        games, and one roster call per team), which back to back is both an
        unnecessary burst on WFDF's servers and an obvious automated
        signature. REQUEST_DELAY_RANGE paces them with jitter; the memoized
        reference/games bytes already remove two thirds of the shared calls.
        """
        from core.fetch import RequestsTransport

        return RequestsTransport(delay_range=REQUEST_DELAY_RANGE)

    def discover(self, year: int) -> List[EventRef]:
        events = self._events if self._events is not None else events_for_year(year)
        refs: List[EventRef] = []
        for event in events:
            if event.year != year:
                continue
            for series in event.series:
                refs.append(
                    EventRef(
                        url=f"{self._base_url}/{event.slug}/",
                        name=None,  # the real event name comes from season.name at parse time
                        division=f"{event.division_label} - {series.name}",
                        city=event.city,
                        state=event.state,
                        country=event.country,
                        start_date=None,
                        end_date=None,
                        extra={
                            "slug": event.slug,
                            "season_id": event.season_id,
                            "series_id": series.series_id,
                            "series_name": series.name,
                        },
                    )
                )
        return refs

    def event_key(self, ref: EventRef) -> str:
        return f"{ref.extra['season_id']}/{ref.extra['series_name']}"

    def fetch_event(self, ref: EventRef, cache: Cache) -> FetchedPages:
        slug = ref.extra["slug"]
        season_id = ref.extra["season_id"]
        series_id = ref.extra["series_id"]

        pages: FetchedPages = {
            "reference": self._fetch_reference(slug, season_id, cache),
            "games": self._fetch_games(slug, season_id, cache),
        }

        reference = json.loads(pages["reference"].decode("utf-8"))
        team_ids = sorted(
            {t["team_id"] for t in reference.get("teams", []) if t.get("series") == series_id}
        )

        # Rosters are the ~136-of-138 majority of a season's requests and
        # barely change once a tournament starts, so they're served from
        # cache unless stale (or refresh_rosters forces it) -- unlike
        # reference/games above, which always refetch when live. This
        # split (and not a second scheduled job kept in sync with the
        # first) is what makes "refresh rosters every N hours" work: it
        # falls out of the cache TTL itself. Tallied and logged below since
        # that count is the whole point of this split.
        served_from_cache = 0
        fetched = 0
        for team_id in team_ids:
            key = f"teams:{team_id}"
            url = self._build_url(slug, season_id, "teams", extra_id=team_id)
            age = cache.age(key)
            if not self.refresh_rosters and age is not None and age <= ROSTER_MAX_AGE_SECONDS:
                served_from_cache += 1
            else:
                fetched += 1
            pages[key] = cache.fetch(
                key, url, refresh=self.refresh_rosters, max_age=ROSTER_MAX_AGE_SECONDS
            )

        log.info(
            "wfdf: %s/%s -- rosters: %d served from cache, %d fetched",
            season_id, ref.extra.get("series_name", ""), served_from_cache, fetched,
        )

        return pages

    def parse_event(
        self, pages: FetchedPages, ref: EventRef, year: int
    ) -> Optional["models.Tournament"]:
        reference = json.loads(pages["reference"].decode("utf-8"))
        games_payload = json.loads(pages["games"].decode("utf-8"))
        games_data = games_payload.get("games", [])
        series_id = ref.extra["series_id"]
        series_name = ref.extra.get("series_name", "")

        teams, teams_by_id = parse.build_teams(reference, series_id)
        if not teams:
            log.warning(
                "wfdf: no teams found for series_id=%s (%s); skipping", series_id, series_name
            )
            return None

        for team_id, team in teams_by_id.items():
            page_key = f"teams:{team_id}"
            team_bytes = pages.get(page_key)
            if team_bytes is None:
                log.warning(
                    "wfdf: missing team page %r for team %r; leaving roster empty",
                    page_key, team.name,
                )
                continue
            team.roster = parse.build_roster(json.loads(team_bytes.decode("utf-8")))

        stages, skipped = parse.build_stages(reference, games_data, series_id, teams_by_id)

        season = reference["season"]
        start_date = parse.parse_wfdf_date(season["starttime"])
        end_date = parse.parse_wfdf_date(season["endtime"])
        tournament_datetime = datetime.combine(start_date, dtime.min)

        log.info(
            "wfdf: %s/%s -- %d team(s), %d stage(s), %d game(s) skipped (unresolved)",
            ref.extra["season_id"], series_name, len(teams), len(stages), skipped,
        )

        tournament = models.Tournament(
            season["name"],
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
        # models.Tournament predates non-USAU sources and has no country
        # field. core.serialize reads it optionally off the instance, so
        # setting it here is what carries country onto the wire.
        tournament.country = ref.country
        return tournament

    # -- internals -------------------------------------------------------

    def _build_url(self, slug: str, season_id: str, resource: str, *, extra_id=None) -> str:
        name = f"{season_id}_{resource}" if extra_id is None else f"{season_id}_{resource}_{extra_id}"
        cache_buster = int(time.time() * 1000)
        return f"{self._base_url}/{slug}/live/data/{name}.json?cb={cache_buster}"

    def _fetch_reference(self, slug: str, season_id: str, cache: Cache) -> bytes:
        cached = self._reference_bytes.get(season_id)
        if cached is not None:
            # Already fetched for another series in this run -- warm this
            # series' own page cache with the same bytes rather than
            # refetching over the network.
            cache.put("reference", cached)
            return cached
        url = self._build_url(slug, season_id, "reference")
        # Always refetch when live (pools/structure change as the
        # tournament progresses); otherwise normal cache behaviour.
        raw = cache.fetch("reference", url, refresh=self.live)
        self._reference_bytes[season_id] = raw
        return raw

    def _fetch_games(self, slug: str, season_id: str, cache: Cache) -> bytes:
        cached = self._games_bytes.get(season_id)
        if cached is not None:
            cache.put("games", cached)
            return cached
        url = self._build_url(slug, season_id, "games")
        # Always refetch when live (scores/status change constantly);
        # otherwise normal cache behaviour.
        raw = cache.fetch("games", url, refresh=self.live)
        self._games_bytes[season_id] = raw
        return raw
