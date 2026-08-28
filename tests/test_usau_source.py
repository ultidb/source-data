"""Unit + full-pipeline tests for sources/usau/source.py's UsauSource.

Golden parse-output correctness lives in test_usau_fixtures.py; this file
covers the Source contract methods themselves (event_key, discover,
fetch_event) using a fake transport over the same checked-in fixtures, plus
one end-to-end discover -> fetch_event -> parse_event -> tournament_to_document
run mirroring test_wfdf.py's TestFullPipeline style.
"""
from pathlib import Path

import pytest

from core.cache import FileCache
from core.schema import Document
from core.serialize import tournament_to_document
from sources.usau.parse import parseTournament
from sources.usau.source import CLUB_SCHEDULE_URL, COLLEGE_SCHEDULE_URL, UsauSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html" / "usau"
TOURNAMENT_HTML = (FIXTURES_DIR / "tournament" / "tournament.html").read_bytes()
COLLEGE_SCHEDULE_HTML = (FIXTURES_DIR / "calendar" / "college_schedule.html").read_bytes()
CLUB_SCHEDULE_HTML = (FIXTURES_DIR / "calendar" / "club_schedule.html").read_bytes()

SOURCE_URL = "https://play.usaultimate.org/events/Commonwealth-Cup-2026-Weekend-1/schedule/Women/CollegeWomen/"
YEAR = 2026


def _team_html_by_url():
    """Discover each real team's URL from the tournament fixture (parsing
    it once, exactly like fetch_event does) and map it to that team's
    checked-in fixture page."""
    info = {"url": SOURCE_URL, "city": "Axton", "state": "VA", "startDate": "2026-02-21", "endDate": "2026-02-22"}
    tournament = parseTournament(TOURNAMENT_HTML, info, "x", YEAR)
    by_url = {}
    for team in tournament.teams:
        if team.name == "TEAM_NAME_NOT_FOUND":
            continue
        path = FIXTURES_DIR / "tournament" / "teams" / f"{team.name}.html"
        by_url[team.url] = path.read_bytes()
    return by_url


TEAM_HTML_BY_URL = _team_html_by_url()


def _fake_transport(urls_seen=None):
    pages = {
        COLLEGE_SCHEDULE_URL: COLLEGE_SCHEDULE_HTML,
        CLUB_SCHEDULE_URL: CLUB_SCHEDULE_HTML,
        SOURCE_URL: TOURNAMENT_HTML,
        **TEAM_HTML_BY_URL,
    }

    def transport(url: str) -> bytes:
        if urls_seen is not None:
            urls_seen.append(url)
        if url not in pages:
            raise AssertionError(f"fake transport got an unexpected URL: {url!r}")
        return pages[url]

    return transport


class TestEventKey:
    def setup_method(self):
        self.source = UsauSource()

    def _ref(self, url):
        from core.source import EventRef

        return EventRef(url=url)

    def test_simple_url(self):
        ref = self._ref(SOURCE_URL)
        assert self.source.event_key(ref) == "Commonwealth-Cup-2026-Weekend-1CollegeWomen"

    def test_d1_suffix(self):
        ref = self._ref("https://play.usaultimate.org/events/YCC-2026/schedule/Boys/youth-club-u-20-boys/di/")
        assert self.source.event_key(ref) == "YCC-2026youth-club-u-20-boys-D1"

    def test_d2_suffix(self):
        ref = self._ref("https://play.usaultimate.org/events/YCC-2026/schedule/Boys/youth-club-u-20-boys/dii/")
        assert self.source.event_key(ref) == "YCC-2026youth-club-u-20-boys-D2"


class TestDiscover:
    def test_discover_uses_new_schedule_shape(self):
        source = UsauSource(transport=_fake_transport())
        refs = source.discover(YEAR)

        assert len(refs) > 200  # matches golden_calendar.json's 277
        commonwealth = [r for r in refs if r.url == SOURCE_URL]
        assert len(commonwealth) == 1
        ref = commonwealth[0]
        assert ref.city == "Axton"
        assert ref.state == "VA"
        assert ref.start_date.isoformat() == "2026-02-21"
        assert ref.end_date.isoformat() == "2026-02-22"
        # fetch_event needs this -- its signature has no `year` param.
        assert ref.extra["year"] == YEAR

    def test_discover_only_requests_the_two_schedule_pages(self):
        urls_seen = []
        source = UsauSource(transport=_fake_transport(urls_seen))
        source.discover(YEAR)
        assert urls_seen == [COLLEGE_SCHEDULE_URL, CLUB_SCHEDULE_URL]


class TestFetchEvent:
    def _ref(self):
        from core.source import EventRef
        from datetime import date

        return EventRef(
            url=SOURCE_URL, city="Axton", state="VA",
            start_date=date(2026, 2, 21), end_date=date(2026, 2, 22),
            extra={"year": YEAR},
        )

    def test_fetches_tournament_and_every_real_team(self, tmp_path):
        urls_seen = []
        source = UsauSource(transport=_fake_transport(urls_seen))
        ref = self._ref()
        cache = FileCache("usau", YEAR, source.event_key(ref), _fake_transport(urls_seen), base_dir=tmp_path)

        pages = source.fetch_event(ref, cache)

        assert urls_seen[0] == SOURCE_URL
        # Every URL after the tournament page is a real team page (discovered
        # from parsing the tournament HTML), and none are requested twice.
        assert set(urls_seen[1:]) == set(TEAM_HTML_BY_URL)
        assert len(urls_seen[1:]) == len(TEAM_HTML_BY_URL)

        assert pages["tournament"] == TOURNAMENT_HTML
        assert len(pages) == 1 + len(TEAM_HTML_BY_URL)

    def test_warms_the_cache(self, tmp_path):
        source = UsauSource(transport=_fake_transport())
        ref = self._ref()
        cache = FileCache("usau", YEAR, source.event_key(ref), _fake_transport(), base_dir=tmp_path)

        pages = source.fetch_event(ref, cache)

        assert cache.get("tournament") == pages["tournament"]
        for key, content in pages.items():
            assert cache.get(key) == content


class TestFullPipeline:
    def test_discover_to_document(self, tmp_path):
        source = UsauSource(transport=_fake_transport())
        refs = source.discover(YEAR)
        ref = next(r for r in refs if r.url == SOURCE_URL)

        key = source.event_key(ref)
        cache = FileCache("usau", YEAR, key, _fake_transport(), base_dir=tmp_path)
        pages = source.fetch_event(ref, cache)
        tournament = source.parse_event(pages, ref, YEAR)

        assert tournament is not None
        doc = tournament_to_document(
            tournament, source="usau", source_event_id=key, source_url=ref.url
        )
        dumped = doc.model_dump(by_alias=True, mode="json")
        Document.model_validate(dumped)  # must not raise
        assert len(doc.teams) == len(TEAM_HTML_BY_URL)
        assert {s.type for s in doc.stages} == {"pools", "brackets"}
