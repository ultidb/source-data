"""Golden-fixture tests for sources/usau/ (Phase 3, MULTI-SOURCE-REDESIGN.md).

Fixtures under tests/fixtures/html/usau/ are real, saved USAU pages (trimmed
of <script>/<style>/comments/<head> -- see the README in that directory for
the exact trim procedure and how to verify a new trim is behavior-preserving
before checking it in). Asserts parse -> models -> wire-format JSON against
checked-in golden JSON, per MULTI-SOURCE-REDESIGN.md's testing strategy
item 5.

Known gaps (see tests/fixtures/html/usau/README.md): no clusters-stage
tournament fixture, and no legacy SeasonId-calendar-shape fixture. Both
would require a live Selenium+Tor fetch not set up in this environment.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from core.serialize import tournament_to_document
from sources.usau.parse import (
    addInfoToTeam,
    addRosterToTeam,
    parseNewSchedule,
    parseTournament,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html" / "usau"
TOURNAMENT_DIR = FIXTURES_DIR / "tournament"
CALENDAR_DIR = FIXTURES_DIR / "calendar"

SOURCE_URL = "https://play.usaultimate.org/events/Commonwealth-Cup-2026-Weekend-1/schedule/Women/CollegeWomen/"
SOURCE_EVENT_ID = "Commonwealth-Cup-2026-Weekend-1CollegeWomen"
INFO = {"url": SOURCE_URL, "city": "Axton", "state": "VA", "startDate": "2026-02-21", "endDate": "2026-02-22"}
SCRAPED_AT = datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)


def test_tournament_fixture_directory_is_populated():
    assert (TOURNAMENT_DIR / "tournament.html").exists()
    team_files = sorted((TOURNAMENT_DIR / "teams").glob("*.html"))
    assert len(team_files) >= 4, "expected several real team pages"


def test_tournament_parses_to_golden_document():
    tournament_html = (TOURNAMENT_DIR / "tournament.html").read_bytes()
    team_files = {p.stem: p.read_bytes() for p in (TOURNAMENT_DIR / "teams").glob("*.html")}

    tournament = parseTournament(tournament_html, INFO, "x", 2026)
    assert tournament is not None
    assert {s.name for s in tournament.stages} == {"W PP", "W BP"}

    for team in tournament.teams:
        if team.name == "TEAM_NAME_NOT_FOUND":
            continue
        assert team.name in team_files, f"missing fixture team page for {team.name!r}"
        soup = BeautifulSoup(team_files[team.name], "html.parser")
        addRosterToTeam(soup, team)
        addInfoToTeam(soup, team)

    doc = tournament_to_document(
        tournament,
        source="usau",
        source_event_id=SOURCE_EVENT_ID,
        source_url=SOURCE_URL,
        scraped_at=SCRAPED_AT,
    )
    actual = doc.model_dump(by_alias=True, mode="json")
    golden = json.loads((TOURNAMENT_DIR / "golden_document.json").read_text(encoding="utf-8"))
    assert actual == golden


def test_calendar_fixture_directory_is_populated():
    assert (CALENDAR_DIR / "college_schedule.html").exists()
    assert (CALENDAR_DIR / "club_schedule.html").exists()


def test_calendar_parses_to_golden_list():
    college_html = (CALENDAR_DIR / "college_schedule.html").read_bytes()
    club_html = (CALENDAR_DIR / "club_schedule.html").read_bytes()

    actual = parseNewSchedule(college_html, 2026, True) + parseNewSchedule(club_html, 2026, False)
    golden = json.loads((CALENDAR_DIR / "golden_calendar.json").read_text(encoding="utf-8"))
    assert actual == golden
