"""Tests for sources/wfdf_legacy/ -- discovery, pure parsing (parse.py), and
the full offline pipeline (discover -> fetch_event -> parse_event ->
tournament_to_document -> validate -> write_document -> read back ->
re-validate).

Everything is driven from the real checked-in fixtures under
sources/wfdf_legacy/fixtures/ (a live results.wfdf.sport/wuc scrape of
WUC 2024 -- see that directory and the WFDF-legacy source task for
provenance). No network is used anywhere in this file: the end-to-end tests
use a fake `transport` callable keyed by URL substring, mirroring
tests/test_wfdf.py's `_fixture_transport` idiom.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import models
from core.cache import FileCache
from core.emit import write_document
from core.schema import Document
from core.serialize import tournament_to_document
from sources.wfdf_legacy import parse
from sources.wfdf_legacy.events import (
    EventsValidationError,
    WFDF_LEGACY_EVENTS,
    WfdfLegacyEvent,
    WfdfLegacySeries,
    events_for_year,
    load_events,
)
from sources.wfdf_legacy.source import WfdfLegacySource

YEAR = 2024
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "sources" / "wfdf_legacy" / "fixtures"

GAMES_HTML = (FIXTURES_DIR / "wuc2024_games.html").read_bytes()
ALLTEAMS_HTML = (FIXTURES_DIR / "wuc2024_allteams.html").read_bytes()
BYSEEDING_HTML = (FIXTURES_DIR / "wuc2024_byseeding.html").read_bytes()
TEAMCARD_60_HTML = (FIXTURES_DIR / "wuc2024_teamcard_60.html").read_bytes()  # USA Open
TEAMCARD_6_HTML = (FIXTURES_DIR / "wuc2024_teamcard_6.html").read_bytes()  # Austria Open

GAMES_SOUP = BeautifulSoup(GAMES_HTML, "html.parser")
ALLTEAMS_SOUP = BeautifulSoup(ALLTEAMS_HTML, "html.parser")
BYSEEDING_SOUP = BeautifulSoup(BYSEEDING_HTML, "html.parser")

WUC2024_EVENT = next(e for e in WFDF_LEGACY_EVENTS if e.season_id == "WUC2024")


def _fixture_transport(url: str) -> bytes:
    """Fake `fetch(url) -> bytes` transport for the end-to-end tests: serves
    the real fixtures for games/allteams/byseeding/team=60/team=6, and a
    minimal valid-but-empty roster for every other team (we only have two
    real team-card fixtures on disk; WUC2024 has ~90 teams across its 3
    configured series and the other ~88 don't matter for what these tests
    assert)."""
    if "view=teamcard" in url:
        m = re.search(r"team=(\d+)", url)
        team_id = int(m.group(1)) if m else None
        if team_id == 60:
            return TEAMCARD_60_HTML
        if team_id == 6:
            return TEAMCARD_6_HTML
        return b"<html><table><tr><th>#</th><th>Name</th></tr></table></html>"
    if "view=games" in url:
        return GAMES_HTML
    if "list=allteams" in url:
        return ALLTEAMS_HTML
    if "list=byseeding" in url:
        return BYSEEDING_HTML
    raise AssertionError(f"unexpected URL in fixture transport: {url!r}")


# ---------------------------------------------------------------------------
# events.py
# ---------------------------------------------------------------------------


class TestEvents:
    def test_wuc2024_loaded_with_three_series(self):
        assert WUC2024_EVENT.division == "international"
        assert WUC2024_EVENT.country == "Australia"
        names = {s.name for s in WUC2024_EVENT.series}
        assert names == {"Open", "Women's", "Mixed"}
        # Guts is intentionally never configured.
        assert "Guts Open" not in names
        assert "Guts Women's" not in names

    def test_genders_are_explicit_and_correct(self):
        by_name = {s.name: s for s in WUC2024_EVENT.series}
        assert by_name["Open"].gender == "open"
        assert by_name["Women's"].gender == "womens"
        assert by_name["Mixed"].gender == "mixed"

    def test_events_for_year_filters_correctly(self):
        # 2024 carries two real events (WUC2024 and WJUC2024) -- assert
        # WUC2024_EVENT is among them rather than pinning an exact list, so
        # this doesn't need updating every time another 2024 event is added.
        assert WUC2024_EVENT in events_for_year(2024)
        assert all(e.year == 2024 for e in events_for_year(2024))
        assert events_for_year(1999) == []

    def test_base_url_trailing_slash_normalised(self):
        event = WfdfLegacyEvent(
            year=2024,
            base_url="https://results.wfdf.sport/wuc/",
            season_id="WUC2024",
            division="international",
            series=[WfdfLegacySeries(name="Open", gender="open")],
        )
        assert event.base_url == "https://results.wfdf.sport/wuc"

    def test_missing_required_field_raises(self, tmp_path):
        bad = tmp_path / "events.yaml"
        bad.write_text("- year: 2024\n  base_url: 'https://x.example'\n", encoding="utf-8")
        with pytest.raises(EventsValidationError):
            load_events(bad)

    def test_unrecognized_gender_raises(self, tmp_path):
        bad = tmp_path / "events.yaml"
        bad.write_text(
            "- year: 2024\n"
            "  base_url: 'https://x.example'\n"
            "  season_id: 'X2024'\n"
            "  division: 'international'\n"
            "  series:\n"
            "    - name: 'Open'\n"
            "      gender: 'not-a-real-gender'\n",
            encoding="utf-8",
        )
        with pytest.raises(EventsValidationError):
            load_events(bad)

    def test_unrecognized_division_raises(self, tmp_path):
        bad = tmp_path / "events.yaml"
        bad.write_text(
            "- year: 2024\n"
            "  base_url: 'https://x.example'\n"
            "  season_id: 'X2024'\n"
            "  division: 'not-a-real-division'\n"
            "  series:\n"
            "    - name: 'Open'\n"
            "      gender: 'open'\n",
            encoding="utf-8",
        )
        with pytest.raises(EventsValidationError):
            load_events(bad)

    def test_empty_series_list_raises(self, tmp_path):
        bad = tmp_path / "events.yaml"
        bad.write_text(
            "- year: 2024\n"
            "  base_url: 'https://x.example'\n"
            "  season_id: 'X2024'\n"
            "  division: 'international'\n"
            "  series: []\n",
            encoding="utf-8",
        )
        with pytest.raises(EventsValidationError):
            load_events(bad)


# ---------------------------------------------------------------------------
# parse.parse_nav
# ---------------------------------------------------------------------------


class TestParseNav:
    def test_all_five_wuc2024_series_present(self):
        nav = parse.parse_nav(GAMES_SOUP)
        names = {s.name for s in nav.values()}
        assert names == {"Mixed", "Open", "Women's", "Guts Open", "Guts Women's"}

    def test_series_ids_match_verified_facts(self):
        nav = parse.parse_nav(GAMES_SOUP)
        by_name = {s.name: sid for sid, s in nav.items()}
        assert by_name == {
            "Mixed": 1,
            "Open": 2,
            "Women's": 3,
            "Guts Open": 6,
            "Guts Women's": 7,
        }

    def test_open_pool_names_and_ids(self):
        nav = parse.parse_nav(GAMES_SOUP)
        open_series = nav[2]
        pools = {p.pool_id: p.name for p in open_series.pools}
        assert pools == {
            1: "Pool A", 2: "Pool B", 3: "Pool C", 4: "Pool D",
            11: "Pool E", 12: "Pool F", 13: "Pool G", 14: "Pool H",
            22: "Crossover", 24: "Playoff (1-8)", 25: "Playoff (9-16)",
        }

    def test_mixed_has_pool_j_after_the_playoffs(self):
        # Real, slightly odd nav ordering -- "Pool J" (pool_id=38) appears
        # in the sidebar after both Mixed playoff entries, not alongside
        # Pools A-H. Confirms parse_nav doesn't assume pools sort before
        # playoffs in the source HTML.
        nav = parse.parse_nav(GAMES_SOUP)
        mixed_series = next(s for s in nav.values() if s.name == "Mixed")
        assert mixed_series.pools[-1] == parse.NavPool(38, "Pool J")

    def test_guts_pools_are_present_in_nav_but_never_configured(self):
        # Guts pools are perfectly parseable -- they're excluded downstream
        # by events.yaml simply never configuring "Guts Open"/"Guts Women's"
        # as a series, not by parse_nav filtering them out.
        nav = parse.parse_nav(GAMES_SOUP)
        guts_open = next(s for s in nav.values() if s.name == "Guts Open")
        assert any(p.name == "Brackets GM16 (Final)" for p in guts_open.pools)

    def test_missing_sidebar_raises(self):
        soup = BeautifulSoup("<html><body>no sidebar here</body></html>", "html.parser")
        with pytest.raises(ValueError):
            parse.parse_nav(soup)


# ---------------------------------------------------------------------------
# parse.classify_stage
# ---------------------------------------------------------------------------


class TestClassifyStage:
    @pytest.mark.parametrize(
        "pool_name,expected_kind,expected_group,expected_round",
        [
            # Real pool names pulled directly from the WUC2024 fixture (see
            # the WFDF-legacy source task's "Key verified facts").
            ("Pool A", "pool", "Pool A", ""),
            ("Pool J", "pool", "Pool J", ""),
            ("Crossover", "cluster", "Crossover", ""),
            ("Playoff (1-8)", "bracket", "Playoff (1-8)", ""),
            ("Playoff (1-8) Semifinals", "bracket", "Playoff (1-8)", "Semifinals"),
            ("Playoff (1-8) Finals", "bracket", "Playoff (1-8)", "Finals"),
            ("Playoff (9-16) Semifinals", "bracket", "Playoff (9-16)", "Semifinals"),
            ("Playoff (9-16) Finals", "bracket", "Playoff (9-16)", "Finals"),
            ("Playoff (1-4) Finals", "bracket", "Playoff (1-4)", "Finals"),
            ("Playoff (5-8) Finals", "bracket", "Playoff (5-8)", "Finals"),
            ("Playoff (9-12) Finals", "bracket", "Playoff (9-12)", "Finals"),
            # Real pool names pulled from the WU24 2025 fixture -- a pool
            # name can carry its own placement-range suffix and still just
            # be ordinary pool play ("Pool " prefix is enough).
            ("Pool R (17-21)", "pool", "Pool R (17-21)", ""),
            ("Pool E (9-13)", "pool", "Pool E (9-13)", ""),
            # WU24 2025's Open series has a single-elimination round that
            # doesn't fit the numbered "Playoff (a-b)" shape at all --
            # see _NAMED_BRACKET_STAGES_CASEFOLDED. WJUC2024 spells the same
            # stage "Pre-quarters" (lowercase q) -- matched case-insensitively.
            ("Pre-Quarters", "bracket", "Pre-Quarters", ""),
            ("Pre-quarters", "bracket", "Pre-quarters", ""),
            # WU24-2023 uses "Bracket(s) (a-b)" instead of "Playoff (a-b)" --
            # singular for its Open series, plural for Mixed/Women's, on the
            # very same event. Same shape, different literal word.
            ("Bracket (1-8)", "bracket", "Bracket (1-8)", ""),
            ("Bracket (1-8) Semifinals", "bracket", "Bracket (1-8)", "Semifinals"),
            ("Brackets (1-8)", "bracket", "Brackets (1-8)", ""),
            ("Brackets (9-16) Finals", "bracket", "Brackets (9-16)", "Finals"),
        ],
    )
    def test_real_pool_names_classify_correctly(
        self, pool_name, expected_kind, expected_group, expected_round
    ):
        result = parse.classify_stage(pool_name)
        assert result.kind == expected_kind
        assert result.group_name == expected_group
        assert result.round_label == expected_round

    def test_malformed_name_raises(self):
        with pytest.raises(parse.UnrecognizedStageShapeError):
            parse.classify_stage("Round Robin Standings")

    def test_malformed_name_raises_carries_pool_name(self):
        with pytest.raises(parse.UnrecognizedStageShapeError) as excinfo:
            parse.classify_stage("Something Unexpected")
        assert excinfo.value.pool_name == "Something Unexpected"


# ---------------------------------------------------------------------------
# parse.parse_teams_page / parse_seeding_page
# ---------------------------------------------------------------------------


class TestParseTeamsPage:
    def test_team_counts_by_series(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        assert len(teams_by_series["Open"]) == 16
        assert len(teams_by_series["Women's"]) == 12
        assert len(teams_by_series["Mixed"]) == 21

    def test_raw_name_is_division_suffixed_country_is_bare(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        australia = next(r for r in teams_by_series["Open"] if r.team_id == 2)
        assert australia.raw_name == "Australia Open"
        assert australia.country == "Australia"

    def test_country_with_comma_preserved_verbatim(self):
        # "Hong Kong, China" -- a country name containing a comma -- must
        # not be mangled by any comma-based splitting.
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        hk = next(r for r in teams_by_series["Open"] if r.country.startswith("Hong Kong"))
        assert hk.country == "Hong Kong, China"
        assert hk.raw_name == "Hong Kong, China Open"

    def test_guts_series_present_but_separate(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        assert "Guts Open" in teams_by_series
        assert "Guts Women's" in teams_by_series


class TestParseSeedingPage:
    def test_pot_numbers_are_not_unique_within_a_series(self):
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        open_ids = {r.team_id for r in teams_by_series["Open"]}
        open_pots = [seeds[tid] for tid in open_ids if tid in seeds]
        # Four different Open teams all share pot "1." -- a genuine seeding
        # pot, not a unique rank.
        assert open_pots.count(1) == 4

    def test_known_team_seeds(self):
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        assert seeds[2] == 1  # Australia Open
        assert seeds[60] == 1  # USA Open
        assert seeds[6] == 3  # Austria Open

    def test_dash_pot_is_silently_skipped_not_a_parse_warning(self, caplog):
        # WUC2024's Guts teams carry "-" instead of a pot number on this
        # page -- that's "no pot assigned", not a parse failure.
        with caplog.at_level("WARNING"):
            seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        guts_ids = {r.team_id for r in teams_by_series["Guts Open"]}
        assert not (guts_ids & set(seeds))
        assert not any("could not parse seeding pot number" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# parse.parse_teamcard
# ---------------------------------------------------------------------------


class TestParseTeamcard:
    def test_usa_open_roster(self):
        soup = BeautifulSoup(TEAMCARD_60_HTML, "html.parser")
        roster = parse.parse_teamcard(soup)
        assert len(roster) == 24
        names = {p.name for p in roster}
        assert "Jonathan Nethercutt" in names

    def test_austria_open_roster(self):
        soup = BeautifulSoup(TEAMCARD_6_HTML, "html.parser")
        roster = parse.parse_teamcard(soup)
        assert len(roster) == 27

    def test_numbers_are_strings(self):
        soup = BeautifulSoup(TEAMCARD_60_HTML, "html.parser")
        roster = parse.parse_teamcard(soup)
        assert all(isinstance(p.number, str) for p in roster)
        nethercutt = next(p for p in roster if p.name == "Jonathan Nethercutt")
        assert nethercutt.number == "7"

    def test_malformed_nested_tables_do_not_pull_in_stats_rows(self):
        # The real page never closes several <table>s, which makes
        # html.parser merge the roster header with a bunch of unrelated
        # stats-table headers into one bloated <tr> -- parse_teamcard must
        # not follow that merged table and pick up "players" like
        # "United States of America Open 15(11)" or "Total".
        soup = BeautifulSoup(TEAMCARD_60_HTML, "html.parser")
        roster = parse.parse_teamcard(soup)
        names = {p.name for p in roster}
        assert not any("United States of America" in n for n in names)
        assert "Total" not in names
        assert "Links" not in names

    def test_no_roster_table_returns_empty_list(self):
        soup = BeautifulSoup("<html><body>nothing here</body></html>", "html.parser")
        assert parse.parse_teamcard(soup) == []


# ---------------------------------------------------------------------------
# parse.parse_schedule_page
# ---------------------------------------------------------------------------


class TestParseSchedulePage:
    def test_pool_a_open_game_count_and_date(self):
        schedule_by_pool_id, _ = parse.parse_schedule_page(GAMES_SOUP)
        pool1_games = schedule_by_pool_id[1]  # Open Pool A
        assert len(pool1_games) == 6
        assert all(g.date.year == 2024 for g in pool1_games)
        # Comment says "date:1.9." (no year); the <h3> "Sun 1.9.2024" is
        # what supplies the year.
        assert pool1_games[0].date.isoformat() == "2024-09-01"

    def test_spirit_score_is_not_included_in_score_text(self):
        schedule_by_pool_id, _ = parse.parse_schedule_page(GAMES_SOUP)
        game = schedule_by_pool_id[1][0]  # USA 15(11) - 7(10) Austria
        assert game.score1_text == "15"
        assert game.score2_text == "7"

    def test_game_id_extracted_from_gameplay_link(self):
        schedule_by_pool_id, _ = parse.parse_schedule_page(GAMES_SOUP)
        game = schedule_by_pool_id[1][0]
        assert game.game_id == 2

    def test_min_max_dates_span_the_full_tournament(self):
        schedule_by_pool_id, _ = parse.parse_schedule_page(GAMES_SOUP)
        all_dates = [g.date for games in schedule_by_pool_id.values() for g in games]
        assert min(all_dates).isoformat() == "2024-08-31"
        assert max(all_dates).isoformat() == "2024-09-07"

    def test_pool_headers_captured_for_bracket_followers_missing_from_nav(self):
        # These 11 pool ids (Semifinals/Finals follower pools) have NO nav
        # sidebar entry at all -- parse_schedule_page's header map is the
        # only place their name exists (see the parse.py module docstring).
        _, pool_headers_by_id = parse.parse_schedule_page(GAMES_SOUP)
        assert pool_headers_by_id[39] == "Mixed Playoff (1-8) Semifinals"
        assert pool_headers_by_id[26] == "Open Playoff (1-8) Semifinals"
        assert pool_headers_by_id[33] == "Women's Playoff (1-4) Finals"

    def test_regular_pool_header_is_series_prefixed(self):
        _, pool_headers_by_id = parse.parse_schedule_page(GAMES_SOUP)
        assert pool_headers_by_id[1] == "Open Pool A"
        assert pool_headers_by_id[7] == "Mixed Pool A"


# ---------------------------------------------------------------------------
# parse.derive_status
# ---------------------------------------------------------------------------


class TestDeriveStatus:
    def test_both_scores_present_is_final(self):
        assert parse.derive_status("15", "7") == "Final"

    def test_missing_score_is_scheduled(self):
        assert parse.derive_status("", "") == "Scheduled"
        assert parse.derive_status("15", "") == "Scheduled"

    def test_non_digit_score_is_scheduled(self):
        assert parse.derive_status("W", "L") == "Scheduled"


# ---------------------------------------------------------------------------
# parse.build_teams
# ---------------------------------------------------------------------------


class TestBuildTeams:
    def test_team_name_is_bare_country_not_suffixed_string(self):
        # The single most important correctness detail in the whole source
        # (see the parse.py module docstring's "Team identity fix").
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        teams, _ = parse.build_teams(teams_by_series, "Open", seeds)
        names = {t.name for t in teams}
        assert "Australia" in names
        assert "Australia Open" not in names

    def test_teams_by_raw_name_resolves_the_suffixed_schedule_string(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        teams, teams_by_raw_name = parse.build_teams(teams_by_series, "Open", seeds)
        australia = teams_by_raw_name["Australia Open"]
        assert australia.name == "Australia"

    def test_seed_comes_from_seeding_page(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        teams, _ = parse.build_teams(teams_by_series, "Open", seeds)
        australia = next(t for t in teams if t.name == "Australia")
        assert australia.seed == 1

    def test_seed_defaults_to_zero_when_team_absent_from_seeding_map(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        teams, _ = parse.build_teams(teams_by_series, "Open", {})
        assert all(t.seed == 0 for t in teams)

    def test_team_counts_match_fixture(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        assert len(parse.build_teams(teams_by_series, "Open", seeds)[0]) == 16
        assert len(parse.build_teams(teams_by_series, "Women's", seeds)[0]) == 12
        assert len(parse.build_teams(teams_by_series, "Mixed", seeds)[0]) == 21

    def test_unconfigured_series_name_yields_no_teams(self):
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        teams, teams_by_raw_name = parse.build_teams(teams_by_series, "Not A Series", {})
        assert teams == []
        assert teams_by_raw_name == {}


# ---------------------------------------------------------------------------
# parse.build_stages
# ---------------------------------------------------------------------------


class TestBuildStages:
    def _build(self, series_name):
        nav = parse.parse_nav(GAMES_SOUP)
        nav_series = next(s for s in nav.values() if s.name == series_name)
        schedule_by_pool_id, pool_headers_by_id = parse.parse_schedule_page(GAMES_SOUP)
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        _, teams_by_raw_name = parse.build_teams(teams_by_series, series_name, seeds)
        return parse.build_stages(
            nav_series.pools, schedule_by_pool_id, teams_by_raw_name, series_name, pool_headers_by_id
        )

    def test_open_stage_shapes_and_game_counts(self):
        stages, skipped = self._build("Open")
        assert [s.name for s in stages] == ["Pools", "Brackets", "Clusters"]
        assert skipped == 0

        pools_stage = next(s for s in stages if s.name == "Pools")
        assert isinstance(pools_stage, models.Pools)
        assert {p.name: len(p.games) for p in pools_stage.pools} == {
            "Pool A": 6, "Pool B": 6, "Pool C": 6, "Pool D": 6,
            "Pool E": 6, "Pool F": 6, "Pool G": 6, "Pool H": 6,
        }

        brackets_stage = next(s for s in stages if s.name == "Brackets")
        assert isinstance(brackets_stage, models.Brackets)
        assert {b.name: len(b.games) for b in brackets_stage.brackets} == {
            "Playoff (1-8)": 12,
            "Playoff (9-16)": 12,
        }

        clusters_stage = next(s for s in stages if s.name == "Clusters")
        assert isinstance(clusters_stage, models.Clusters)
        assert {c.name: len(c.games) for c in clusters_stage.clusters} == {"Crossover": 4}

    def test_open_bracket_games_include_follower_round_labels(self):
        # 12 games under "Playoff (1-8)" = the parent pool's own games plus
        # its Semifinals/Finals followers -- these followers have no nav
        # entry at all (see TestParseSchedulePage), so this also proves the
        # fallback wiring end-to-end, not just at the parse_schedule_page level.
        stages, _ = self._build("Open")
        brackets_stage = next(s for s in stages if s.name == "Brackets")
        bracket = next(b for b in brackets_stage.brackets if b.name == "Playoff (1-8)")
        round_labels = {g.round for g in bracket.games}
        assert round_labels == {"", "Semifinals", "Finals"}

    def test_womens_stage_shapes_and_game_counts(self):
        stages, skipped = self._build("Women's")
        assert [s.name for s in stages] == ["Pools", "Brackets", "Clusters"]
        assert skipped == 0
        total_games = sum(
            len(g.games)
            for stage in stages
            for g in (getattr(stage, "pools", None) or getattr(stage, "brackets", None) or stage.clusters)
        )
        assert total_games == 58

    def test_mixed_has_no_clusters_stage(self):
        # Mixed has no Crossover pool in the fixture -- Clusters must be
        # omitted entirely, not emitted empty.
        stages, skipped = self._build("Mixed")
        assert [s.name for s in stages] == ["Pools", "Brackets"]
        assert skipped == 0
        total_games = sum(
            len(g.games)
            for stage in stages
            for g in (getattr(stage, "pools", None) or getattr(stage, "brackets", None) or [])
        )
        assert total_games == 103

    def test_mixed_pool_j_is_a_pool_not_dropped(self):
        stages, _ = self._build("Mixed")
        pools_stage = next(s for s in stages if s.name == "Pools")
        assert "Pool J" in {p.name for p in pools_stage.pools}

    def test_unresolved_team_name_is_skipped_and_counted(self):
        nav = parse.parse_nav(GAMES_SOUP)
        nav_series = next(s for s in nav.values() if s.name == "Open")
        schedule_by_pool_id, pool_headers_by_id = parse.parse_schedule_page(GAMES_SOUP)
        # Empty teams_by_raw_name -- every game's team names fail to
        # resolve.
        stages, skipped = parse.build_stages(
            nav_series.pools, schedule_by_pool_id, {}, "Open", pool_headers_by_id
        )
        assert skipped == 76
        pools_stage = next(s for s in stages if s.name == "Pools")
        assert all(len(p.games) == 0 for p in pools_stage.pools)

    def test_unrecognized_stage_shape_skips_just_that_pool(self, caplog):
        bad_pool = parse.NavPool(999, "Not A Real Stage Shape")
        good_pool = parse.NavPool(1, "Pool A")
        schedule_by_pool_id, _ = parse.parse_schedule_page(GAMES_SOUP)
        schedule_by_pool_id = {1: schedule_by_pool_id[1]}
        schedule_by_pool_id[999] = [
            parse.RawGame(999, schedule_by_pool_id[1][0].date, "10:00", "A", "B", "15", "10", 12345)
        ]
        teams_by_series = parse.parse_teams_page(ALLTEAMS_SOUP)
        seeds = parse.parse_seeding_page(BYSEEDING_SOUP)
        _, teams_by_raw_name = parse.build_teams(teams_by_series, "Open", seeds)

        # No pool_headers_by_id here -- this test isolates classify_stage's
        # own error path, not the nav-fallback mechanism (which would
        # otherwise pull in every other real "Open "-prefixed pool header
        # from the full fixture as an "extra" pool, since only pool_id 1 and
        # 999 are in nav_pools_for_series below).
        with caplog.at_level("ERROR"):
            stages, skipped = parse.build_stages(
                [good_pool, bad_pool], schedule_by_pool_id, teams_by_raw_name, "Open"
            )

        assert skipped == 1  # the one game under pool 999, not the good pool's games
        pools_stage = next(s for s in stages if s.name == "Pools")
        assert len(pools_stage.pools) == 1
        assert any("unrecognized stage shape" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestFullPipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_discover_returns_exactly_three_refs_no_guts(self):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        refs = source.discover(YEAR)
        assert len(refs) == 3
        series_names = {r.extra["series_name"] for r in refs}
        assert series_names == {"Open", "Women's", "Mixed"}

    def test_event_key_values(self):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        refs = {r.extra["series_name"]: r for r in source.discover(YEAR)}
        assert source.event_key(refs["Open"]) == "WUC2024/Open"
        assert source.event_key(refs["Women's"]) == "WUC2024/Women's"
        assert source.event_key(refs["Mixed"]) == "WUC2024/Mixed"

    def test_full_round_trip_all_three_series(self, tmp_path, caplog):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]

        with caplog.at_level("ERROR"):
            refs = source.discover(YEAR)
            assert len(refs) == 3

            documents = {}
            for ref in refs:
                key = source.event_key(ref)
                cache = FileCache("wfdf-legacy", YEAR, key, _fixture_transport, base_dir=tmp_path)
                pages = source.fetch_event(ref, cache)
                tournament = source.parse_event(pages, ref, YEAR)
                assert tournament is not None

                doc = tournament_to_document(
                    tournament, source="wfdf-legacy", source_event_id=key, source_url=ref.url
                )
                dumped = doc.model_dump(by_alias=True, mode="json")
                Document.model_validate(dumped)  # must not raise
                documents[ref.extra["series_name"]] = doc

        assert not caplog.records, [r.message for r in caplog.records]

        assert set(documents) == {"Open", "Women's", "Mixed"}
        assert documents["Open"].event.division == "international"
        assert documents["Women's"].event.division == "international"
        assert documents["Mixed"].event.division == "international"
        assert documents["Open"].event.gender == "open"
        assert documents["Women's"].event.gender == "womens"
        assert documents["Mixed"].event.gender == "mixed"

        assert len(documents["Open"].teams) == 16
        assert len(documents["Women's"].teams) == 12
        assert len(documents["Mixed"].teams) == 21

        # Dates come from the page's own <h3> headers (min/max across every
        # game), NOT events.yaml's hand-typed start_date/end_date.
        assert documents["Open"].event.start_date.isoformat() == "2024-08-31"
        assert documents["Open"].event.end_date.isoformat() == "2024-09-07"

        assert documents["Open"].source_event_id == "WUC2024/Open"

    def test_write_document_paths_and_apostrophe_stripping(self, tmp_path):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        refs = {r.extra["series_name"]: r for r in source.discover(YEAR)}

        written = {}
        for name, ref in refs.items():
            key = source.event_key(ref)
            cache = FileCache("wfdf-legacy", YEAR, key, _fixture_transport, base_dir=tmp_path)
            pages = source.fetch_event(ref, cache)
            tournament = source.parse_event(pages, ref, YEAR)
            doc = tournament_to_document(
                tournament, source="wfdf-legacy", source_event_id=key, source_url=ref.url
            )
            written[name] = write_document(doc, base_dir=tmp_path)

        assert written["Open"] == tmp_path / "data" / "wfdf-legacy" / "2024" / "WUC2024__Open.json"
        # core.emit._sanitize_key strips the apostrophe in "Women's".
        assert written["Women's"] == tmp_path / "data" / "wfdf-legacy" / "2024" / "WUC2024__Womens.json"
        assert written["Mixed"] == tmp_path / "data" / "wfdf-legacy" / "2024" / "WUC2024__Mixed.json"
        for path in written.values():
            assert path.exists()

    def test_written_document_round_trips_through_validation(self, tmp_path):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        ref = next(r for r in source.discover(YEAR) if r.extra["series_name"] == "Open")

        key = source.event_key(ref)
        cache = FileCache("wfdf-legacy", YEAR, key, _fixture_transport, base_dir=tmp_path)
        pages = source.fetch_event(ref, cache)
        tournament = source.parse_event(pages, ref, YEAR)
        doc = tournament_to_document(tournament, source="wfdf-legacy", source_event_id=key, source_url=ref.url)
        path = write_document(doc, base_dir=tmp_path)

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        Document.model_validate(on_disk)  # must not raise

        team_names = {t["name"] for t in on_disk["teams"]}
        assert "Australia" in team_names
        assert "Australia Open" not in team_names

        usa = next(t for t in on_disk["teams"] if t["name"] == "United States of America")
        assert len(usa["roster"]) == 24

    def test_fetch_event_warms_the_shared_page_cache(self, tmp_path):
        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        ref = next(r for r in source.discover(YEAR) if r.extra["series_name"] == "Open")

        key = source.event_key(ref)
        cache = FileCache("wfdf-legacy", YEAR, key, _fixture_transport, base_dir=tmp_path)
        pages = source.fetch_event(ref, cache)
        assert cache.get("games") == pages["games"]
        assert cache.get("allteams") == pages["allteams"]
        assert cache.get("byseeding") == pages["byseeding"]

    def test_broken_team_page_leaves_that_roster_empty_not_whole_event(self, tmp_path, caplog):
        # Confirmed live on WU24-2023: every ?view=teamcard/?view=playerlist
        # request 500s while games/allteams are fine -- a single team page
        # failing to fetch must degrade to "that team has no roster", not
        # sink the whole event (see fetch_event's try/except).
        def flaky_transport(url: str) -> bytes:
            if "view=teamcard" in url and "team=60" in url:
                raise ConnectionError("simulated upstream 500")
            return _fixture_transport(url)

        source = WfdfLegacySource(events=[WUC2024_EVENT])
        source.make_transport = lambda: flaky_transport  # type: ignore[assignment]
        ref = next(r for r in source.discover(YEAR) if r.extra["series_name"] == "Open")

        key = source.event_key(ref)
        cache = FileCache("wfdf-legacy", YEAR, key, flaky_transport, base_dir=tmp_path)
        with caplog.at_level("WARNING"):
            pages = source.fetch_event(ref, cache)
        assert "team:60" not in pages
        assert any("failed to fetch team page for team_id=60" in r.message for r in caplog.records)

        tournament = source.parse_event(pages, ref, YEAR)
        assert tournament is not None
        usa = next(t for t in tournament.teams if t.name == "United States of America")
        assert usa.roster == []
        # Every other team in the same document is unaffected -- Austria
        # (team_id=6) has its own real teamcard fixture, unrelated to the
        # simulated failure above (team_id=60).
        austria = next(t for t in tournament.teams if t.name == "Austria")
        assert austria.roster != []

    def test_missing_configured_series_skips_event_not_whole_run(self, caplog):
        bogus_event = WfdfLegacyEvent(
            year=YEAR,
            base_url=WUC2024_EVENT.base_url,
            season_id=WUC2024_EVENT.season_id,
            division=WUC2024_EVENT.division,
            series=[WfdfLegacySeries(name="Nonexistent Series", gender="open")],
        )
        source = WfdfLegacySource(events=[bogus_event])
        source.make_transport = lambda: _fixture_transport  # type: ignore[assignment]
        with caplog.at_level("ERROR"):
            refs = source.discover(YEAR)
        assert refs == []
        assert any("not found on the games page" in r.message for r in caplog.records)


class TestRegistration:
    def test_wfdf_legacy_is_registered(self):
        import sources  # noqa: F401  (import side effect: registers every known source)
        from core.registry import list_sources

        assert "wfdf-legacy" in {s.id for s in list_sources()}
