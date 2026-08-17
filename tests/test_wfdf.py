"""Tests for sources/wfdf/ -- discovery, pure mapping (parse.py), and the
full offline pipeline (discover -> fetch_event -> parse_event ->
tournament_to_document -> validate -> write_document -> read back ->
re-validate).

Everything is driven from the real checked-in fixtures under
sources/wfdf/fixtures/ (copied verbatim from a live results.wfdf.sport scrape
of WUCC 2026 -- see that directory and the WFDF source task for provenance).
No network is used anywhere in this file: the end-to-end tests use a fake
`transport` callable that serves fixture bytes by URL instead of a real
`core.fetch.RequestsTransport`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import models
from core.cache import FileCache
from core.emit import write_document
from core.schema import Document
from core.serialize import tournament_to_document
from sources.wfdf import parse
from sources.wfdf.events import (
    WFDF_EVENTS,
    WfdfEvent,
    WfdfSeries,
    events_for_year,
    ongoing_events,
    recently_ended_events,
    upcoming_events,
)
from sources.wfdf.source import WfdfSource

YEAR = 2026
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "sources" / "wfdf" / "fixtures"

REFERENCE = json.loads((FIXTURES_DIR / "WUCC2026_reference.json").read_text(encoding="utf-8"))
GAMES = json.loads((FIXTURES_DIR / "WUCC2026_games.json").read_text(encoding="utf-8"))["games"]
TEAM_1019 = json.loads((FIXTURES_DIR / "WUCC2026_teams_1019.json").read_text(encoding="utf-8"))

REFERENCE_BYTES = (FIXTURES_DIR / "WUCC2026_reference.json").read_bytes()
GAMES_BYTES = (FIXTURES_DIR / "WUCC2026_games.json").read_bytes()
TEAM_1019_BYTES = (FIXTURES_DIR / "WUCC2026_teams_1019.json").read_bytes()

SERIES_MIXED = 1001
SERIES_OPEN = 1002
SERIES_WOMENS = 1000

US_TEAM_NAMES_BY_SERIES = {
    SERIES_MIXED: {"Hybrid", "shame.", "XIST"},
    SERIES_OPEN: {"Chicago Machine", "PoNY", "Revolver"},
    SERIES_WOMENS: {"Brute Squad", "Fury", "Scandal"},
}


def _fixture_transport(url: str) -> bytes:
    """Fake `fetch(url) -> bytes` transport for the end-to-end tests: serves
    the real fixtures for `_reference`/`_games`/`_teams_1019`, and a minimal
    valid-but-empty roster for every other team id (we only have one real
    team fixture on disk; the other ~135 teams don't matter for what these
    tests assert)."""
    if "_teams_1019" in url:
        return TEAM_1019_BYTES
    if "_teams_" in url:
        return json.dumps({"players": []}).encode("utf-8")
    if "_reference" in url:
        return REFERENCE_BYTES
    if "_games" in url:
        return GAMES_BYTES
    raise AssertionError(f"unexpected URL in fixture transport: {url!r}")


# ---------------------------------------------------------------------------
# discover() / event_key()
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_returns_one_ref_per_series_for_2026(self):
        source = WfdfSource()
        refs = source.discover(2026)
        assert len(refs) == 3
        series_names = {r.extra["series_name"] for r in refs}
        assert series_names == {"Mixed", "Open", "Women's"}

    def test_returns_empty_for_other_years(self):
        source = WfdfSource()
        assert source.discover(2025) == []
        assert source.discover(2027) == []

    def test_division_label_per_series(self):
        source = WfdfSource()
        refs = {r.extra["series_name"]: r for r in source.discover(2026)}
        assert refs["Mixed"].division == "World Ultimate Club Championships - Mixed"
        assert refs["Open"].division == "World Ultimate Club Championships - Open"
        assert refs["Women's"].division == "World Ultimate Club Championships - Women's"

    def test_event_key_is_stable_and_series_scoped(self):
        # event_key is the document identity (sourceEventId) that ingest run
        # items key on and that the on-disk cache path derives from -- these
        # literal strings must stay byte-identical across the slug->base_url
        # refactor (WfdfEvent.slug removed in favor of WfdfEvent.base_url),
        # or every cached page and previously ingested document is orphaned.
        source = WfdfSource()
        refs = {r.extra["series_name"]: r for r in source.discover(2026)}
        assert source.event_key(refs["Mixed"]) == "WUCC2026/Mixed"
        assert source.event_key(refs["Open"]) == "WUCC2026/Open"
        assert source.event_key(refs["Women's"]) == "WUCC2026/Women's"

    def test_venue_comes_from_the_event_entry_not_the_api(self):
        # The WFDF API carries no venue info at all (reservations[].location
        # is always null), so city/state/country are hand-entered on the
        # WfdfEvent and must reach the ref. WUCC 2026 is in Limerick.
        source = WfdfSource()
        ref = source.discover(2026)[0]
        assert ref.city == "Limerick"
        assert ref.country == "Ireland"
        assert ref.state == ""  # not meaningful outside the US

    def test_venue_defaults_to_blank_when_the_event_does_not_set_one(self):
        # An event whose venue we don't know must stay blank rather than
        # inheriting another event's, or inventing one.
        event = replace(WFDF_EVENTS[0], city="", state="", country="")
        ref = WfdfSource(events=[event]).discover(2026)[0]
        assert (ref.city, ref.state, ref.country) == ("", "", "")

    def test_event_name_overrides_wfdfs_abbreviation(self):
        # WFDF's season.name is "WUCC 2026"; we display the expanded form.
        # The year must survive: the Go writer takes event.name verbatim and
        # matches tournaments on name+division+gender, so dropping it would
        # make a later edition update this one's rows.
        source = WfdfSource()
        ref = source.discover(2026)[0]
        assert ref.name == "World Ultimate Club Championships 2026"
        assert "2026" in ref.name

    def test_event_name_falls_back_to_season_name_when_unset(self):
        event = replace(WFDF_EVENTS[0], name="")
        ref = WfdfSource(events=[event]).discover(2026)[0]
        assert ref.name is None  # parse_event then falls back to season.name


# ---------------------------------------------------------------------------
# WfdfEvent.base_url / data_path -> WfdfSource._build_url. WFDF events don't
# all live under one host with the event as a path segment (WUCC 2026 is
# path-style: "https://results.wfdf.sport/wucc-2026"; WJUC 2026 is
# subdomain-style: "https://wjuc.wfdf.sport", event at the host root) -- see
# the WFDF source task. These pin the exact URL strings so the base_url
# refactor is provably behaviour-preserving for WUCC.
# ---------------------------------------------------------------------------


def _strip_cb(url: str) -> str:
    return url.partition("?cb=")[0]


class TestBuildUrl:
    def test_path_style_base_url_matches_pre_refactor_urls(self):
        # Literal pin of the URL the pre-refactor code produced:
        # f"{base_url}/{slug}/live/data/{name}.json?cb=<millis>".
        source = WfdfSource()
        url = source._build_url(
            "https://results.wfdf.sport/wucc-2026", "live/data", "WUCC2026", "reference"
        )
        assert _strip_cb(url) == "https://results.wfdf.sport/wucc-2026/live/data/WUCC2026_reference.json"
        assert url.rpartition("?cb=")[2].isdigit()

    def test_path_style_games_url_matches_pre_refactor_urls(self):
        source = WfdfSource()
        url = source._build_url(
            "https://results.wfdf.sport/wucc-2026", "live/data", "WUCC2026", "games"
        )
        assert _strip_cb(url) == "https://results.wfdf.sport/wucc-2026/live/data/WUCC2026_games.json"

    def test_subdomain_style_base_url_no_doubled_or_missing_slashes(self):
        source = WfdfSource()
        url = source._build_url("https://wjuc.wfdf.sport", "live/data", "WJUC2026", "reference")
        assert _strip_cb(url) == "https://wjuc.wfdf.sport/live/data/WJUC2026_reference.json"

    def test_trailing_slash_on_base_url_argument_is_normalised(self):
        source = WfdfSource()
        no_slash = source._build_url(
            "https://results.wfdf.sport/wucc-2026", "live/data", "WUCC2026", "reference"
        )
        with_slash = source._build_url(
            "https://results.wfdf.sport/wucc-2026/", "live/data", "WUCC2026", "reference"
        )
        assert _strip_cb(no_slash) == _strip_cb(with_slash)

    def test_wfdf_event_normalises_trailing_slash_on_base_url_field(self):
        # WfdfEvent itself normalises too (both event construction forms
        # must agree, not just _build_url's own defensive rstrip).
        with_slash = WfdfEvent(
            year=2026,
            base_url="https://results.wfdf.sport/wucc-2026/",
            season_id="WUCC2026",
            division_label="X",
        )
        without_slash = WfdfEvent(
            year=2026,
            base_url="https://results.wfdf.sport/wucc-2026",
            season_id="WUCC2026",
            division_label="X",
        )
        assert with_slash.base_url == without_slash.base_url == "https://results.wfdf.sport/wucc-2026"

    def test_custom_data_path_is_honoured(self):
        source = WfdfSource()
        url = source._build_url("https://host.example", "custom/path", "SEASON", "reference")
        assert _strip_cb(url) == "https://host.example/custom/path/SEASON_reference.json"

    def test_default_data_path_on_wfdf_event_matches_wucc_layout(self):
        event = WfdfEvent(
            year=2026, base_url="https://host.example", season_id="SEASON", division_label="X"
        )
        assert event.data_path == "live/data"

    def test_roster_url_form_under_path_style_base_url(self):
        source = WfdfSource()
        url = source._build_url(
            "https://results.wfdf.sport/wucc-2026", "live/data", "WUCC2026", "teams", extra_id=1019
        )
        assert (
            _strip_cb(url)
            == "https://results.wfdf.sport/wucc-2026/live/data/WUCC2026_teams_1019.json"
        )

    def test_roster_url_form_under_subdomain_style_base_url(self):
        source = WfdfSource()
        url = source._build_url(
            "https://wjuc.wfdf.sport", "live/data", "WJUC2026", "teams", extra_id=42
        )
        assert _strip_cb(url) == "https://wjuc.wfdf.sport/live/data/WJUC2026_teams_42.json"


# ---------------------------------------------------------------------------
# parse.build_teams
# ---------------------------------------------------------------------------


class TestBuildTeams:
    @pytest.mark.parametrize(
        "series_id,expected_count",
        [(SERIES_MIXED, 48), (SERIES_OPEN, 48), (SERIES_WOMENS, 40)],
    )
    def test_team_counts_match_fixture(self, series_id, expected_count):
        teams, _ = parse.build_teams(REFERENCE, series_id)
        assert len(teams) == expected_count

    def test_no_duplicate_team_names_within_a_series(self):
        for series_id in (SERIES_MIXED, SERIES_OPEN, SERIES_WOMENS):
            teams, _ = parse.build_teams(REFERENCE, series_id)
            names = [t.name for t in teams]
            assert len(names) == len(set(names))

    def test_seed_comes_from_rank(self):
        teams, _ = parse.build_teams(REFERENCE, SERIES_MIXED)
        hybrid = next(t for t in teams if t.name == "Hybrid")
        assert hybrid.seed == 1  # rank 1 in the fixture

    def test_location_comes_from_country_name(self):
        teams, _ = parse.build_teams(REFERENCE, SERIES_WOMENS)
        sixers = next(t for t in teams if t.name == "6ixers")
        assert sixers.info.location == "Canada"

    def test_no_coaches(self):
        teams, _ = parse.build_teams(REFERENCE, SERIES_MIXED)
        assert all(t.info.coaches == [] for t in teams)

    @pytest.mark.parametrize("series_id", [SERIES_MIXED, SERIES_OPEN, SERIES_WOMENS])
    def test_us_team_names_preserved_exactly(self, series_id):
        # These specific names are what the Go writer's WUCC->USAU fallback
        # matches on (ingest-contract.md section 4) -- a normalization bug
        # here would silently break cross-source team merging.
        teams, _ = parse.build_teams(REFERENCE, series_id)
        names = {t.name for t in teams}
        assert US_TEAM_NAMES_BY_SERIES[series_id] <= names


# ---------------------------------------------------------------------------
# parse.build_roster
# ---------------------------------------------------------------------------


class TestBuildRoster:
    def test_roster_count_and_shape(self):
        roster = parse.build_roster(TEAM_1019)
        assert len(roster) == len(TEAM_1019["players"])
        assert len(roster) == 24

    def test_number_is_a_string(self):
        roster = parse.build_roster(TEAM_1019)
        assert all(isinstance(p.number, str) for p in roster)
        alicia = next(p for p in roster if p.name == "Alicia Zhang")
        assert alicia.number == "25"

    def test_name_is_first_last(self):
        roster = parse.build_roster(TEAM_1019)
        names = [p.name for p in roster]
        assert "Alicia Zhang" in names


# ---------------------------------------------------------------------------
# parse.build_stages -- counts, skip accounting
# ---------------------------------------------------------------------------


class TestBuildStages:
    @pytest.mark.parametrize(
        "series_id,expected_stage_names,expected_group_count,expected_kept,expected_skipped",
        [
            (SERIES_MIXED, ["Pool Play", "Bracket Play"], 10, 120, 112),
            (SERIES_OPEN, ["Pool Play", "Bracket Play"], 10, 120, 112),
            (
                SERIES_WOMENS,
                ["Pool Play", "Placement Pools", "Bracket Play", "Crossovers & Placement"],
                15,
                80,
                112,
            ),
        ],
    )
    def test_stage_and_game_counts_match_fixture(
        self, series_id, expected_stage_names, expected_group_count, expected_kept, expected_skipped
    ):
        _, teams_by_id = parse.build_teams(REFERENCE, series_id)
        stages, skipped = parse.build_stages(REFERENCE, GAMES, series_id, teams_by_id)

        assert [s.name for s in stages] == expected_stage_names
        assert skipped == expected_skipped

        groups = []
        kept = 0
        for stage in stages:
            stage_groups = getattr(stage, "pools", None) or getattr(stage, "brackets", None) or stage.clusters
            groups.extend(stage_groups)
            for group in stage_groups:
                kept += len(group.games)
        assert len(groups) == expected_group_count
        assert kept == expected_kept

    def test_total_unresolved_games_skipped_across_all_series_matches_fixture(self):
        # 336 of the fixture's 656 games have a falsy hometeam/visitorteam
        # (unresolved bracket slots) -- verified independently against the
        # raw fixture in the WFDF source task.
        total_skipped = 0
        for series_id in (SERIES_MIXED, SERIES_OPEN, SERIES_WOMENS):
            _, teams_by_id = parse.build_teams(REFERENCE, series_id)
            _, skipped = parse.build_stages(REFERENCE, GAMES, series_id, teams_by_id)
            total_skipped += skipped
        assert total_skipped == 336

    def test_placement_pool_y_z_land_in_placement_pools_not_pool_play(self):
        _, teams_by_id = parse.build_teams(REFERENCE, SERIES_WOMENS)
        stages, _ = parse.build_stages(REFERENCE, GAMES, SERIES_WOMENS, teams_by_id)

        pool_play = next(s for s in stages if s.name == "Pool Play")
        placement_pools = next(s for s in stages if s.name == "Placement Pools")

        pool_play_names = {p.name for p in pool_play.pools}
        placement_names = {p.name for p in placement_pools.pools}

        assert "Placement Pool Y" not in pool_play_names
        assert "Placement Pool Z" not in pool_play_names
        assert placement_names == {"Placement Pool Y", "Placement Pool Z"}

    def test_stage_typing(self):
        _, teams_by_id = parse.build_teams(REFERENCE, SERIES_WOMENS)
        stages, _ = parse.build_stages(REFERENCE, GAMES, SERIES_WOMENS, teams_by_id)

        assert isinstance(next(s for s in stages if s.name == "Pool Play"), models.Pools)
        assert isinstance(next(s for s in stages if s.name == "Placement Pools"), models.Pools)
        assert isinstance(next(s for s in stages if s.name == "Bracket Play"), models.Brackets)
        assert isinstance(next(s for s in stages if s.name == "Crossovers & Placement"), models.Clusters)


# ---------------------------------------------------------------------------
# Parent/follower linkage and round derivation (real pool dicts from the
# fixture; the fixture's own bracket games are all still unresolved, so the
# skip-accounting tests above can't exercise round labels end-to-end --
# these test the mapping functions directly instead).
# ---------------------------------------------------------------------------


class TestParentFollowerLinkageAndRounds:
    PARENT = {
        "pool_id": 1026, "poolname": "Playoff (1-32)", "continuingpool": 1,
        "type": 2, "series_id": 1001, "ordering": "I", "isfollower": 0,
    }
    FOLLOWER_QF = {
        "pool_id": 1029, "poolname": "Playoff (1-32) Quarterfinals", "continuingpool": 1,
        "type": 2, "series_id": 1001, "ordering": "I2", "isfollower": 1,
    }

    def test_ordering_based_parent_lookup(self):
        pools_in_series = [self.PARENT, self.FOLLOWER_QF]
        parent = parse._find_parent(self.FOLLOWER_QF, pools_in_series)
        assert parent is self.PARENT

    def test_round_derived_by_stripping_parent_prefix(self):
        round_label = parse._round_for_follower(self.FOLLOWER_QF, self.PARENT)
        assert round_label == "Quarterfinals"

    def test_name_prefix_fallback_used_and_logged_when_ordering_fails(self, caplog):
        # A follower whose ordering doesn't resolve (typo'd/rewired) should
        # still find its parent via name-prefix matching, with a warning.
        broken_follower = dict(self.FOLLOWER_QF, ordering="ZZ9")
        pools_in_series = [self.PARENT, broken_follower]
        with caplog.at_level("WARNING"):
            parent = parse._find_parent(broken_follower, pools_in_series)
        assert parent is self.PARENT
        assert any("falling back to name-prefix match" in r.message for r in caplog.records)

    def test_parent_pool_games_get_empty_round(self):
        games_by_pool = {
            1026: [
                {
                    "game_id": 1, "hometeam": 1131, "visitorteam": 1109,
                    "homescore": 15, "visitorscore": 3, "status": "completed",
                    "time_utc": "2026-08-20 09:00:00",
                }
            ]
        }
        teams_by_id = {
            1131: models.Team("Hybrid", 1, ""),
            1109: models.Team("Aethers Warsaw", 48, ""),
        }
        games, skipped = parse._games_for_pool(1026, games_by_pool, teams_by_id, round_label="")
        assert skipped == 0
        assert len(games) == 1
        assert games[0].round == ""

    def test_follower_pool_games_get_derived_round(self):
        games_by_pool = {
            1029: [
                {
                    "game_id": 2, "hometeam": 1131, "visitorteam": 1109,
                    "homescore": 15, "visitorscore": 12, "status": "completed",
                    "time_utc": "2026-08-21 09:00:00",
                }
            ]
        }
        teams_by_id = {
            1131: models.Team("Hybrid", 1, ""),
            1109: models.Team("Aethers Warsaw", 48, ""),
        }
        round_label = parse._round_for_follower(self.FOLLOWER_QF, self.PARENT)
        games, _ = parse._games_for_pool(1029, games_by_pool, teams_by_id, round_label=round_label)
        assert games[0].round == "Quarterfinals"


# ---------------------------------------------------------------------------
# Status / score / datetime mapping
# ---------------------------------------------------------------------------


class TestStatusScoreDatetime:
    @pytest.mark.parametrize(
        "raw,expected",
        [("completed", "Final"), ("ongoing", "In Progress"), ("scheduled", "Scheduled")],
    )
    def test_known_status_mapping(self, raw, expected):
        assert parse.map_status(raw) == expected

    def test_unknown_status_defaults_to_scheduled_and_logs(self, caplog):
        with caplog.at_level("WARNING"):
            result = parse.map_status("postponed")
        assert result == "Scheduled"
        assert any("unrecognized game status" in r.message for r in caplog.records)

    def test_absent_scores_default_to_zero(self):
        teams_by_id = {1: models.Team("A", 1, ""), 2: models.Team("B", 2, "")}
        games_by_pool = {
            10: [
                {
                    "game_id": 1, "hometeam": 1, "visitorteam": 2,
                    "status": "scheduled", "time_utc": "2026-08-16 09:00:00",
                    # homescore/visitorscore deliberately absent, matching
                    # the fixture's scheduled (not-yet-played) games.
                }
            ]
        }
        games, skipped = parse._games_for_pool(10, games_by_pool, teams_by_id, round_label="")
        assert skipped == 0
        assert games[0].teamA_score == 0
        assert games[0].teamB_score == 0

    def test_time_utc_parses_to_naive_datetime(self):
        from datetime import datetime as dt

        result = parse.parse_wfdf_datetime("2026-08-16 14:00:00")
        assert result == dt(2026, 8, 16, 14, 0, 0)

    def test_missing_time_utc_is_none(self):
        assert parse.parse_wfdf_datetime(None) is None
        assert parse.parse_wfdf_datetime("") is None

    def test_unparseable_time_utc_is_none(self):
        assert parse.parse_wfdf_datetime("not-a-date") is None

    def test_real_game_sample_time_utc_parses(self):
        sample = next(g for g in GAMES if g.get("time_utc"))
        result = parse.parse_wfdf_datetime(sample["time_utc"])
        assert result is not None


# ---------------------------------------------------------------------------
# Unresolved-game skipping
# ---------------------------------------------------------------------------


class TestUnresolvedGamesSkipped:
    def test_falsy_hometeam_or_visitorteam_is_skipped(self):
        teams_by_id = {1: models.Team("A", 1, ""), 2: models.Team("B", 2, "")}
        games_by_pool = {
            5: [
                {"game_id": 1, "hometeam": None, "visitorteam": 2, "status": "scheduled"},
                {"game_id": 2, "hometeam": 1, "visitorteam": None, "status": "scheduled"},
                {"game_id": 3, "hometeam": 0, "visitorteam": 2, "status": "scheduled"},
                {
                    "game_id": 4, "hometeam": 1, "visitorteam": 2, "status": "completed",
                    "homescore": 10, "visitorscore": 5, "time_utc": "2026-08-16 09:00:00",
                },
            ]
        }
        games, skipped = parse._games_for_pool(5, games_by_pool, teams_by_id, round_label="")
        assert skipped == 3
        assert len(games) == 1
        assert games[0].teamA_score == 10


# ---------------------------------------------------------------------------
# Division label -> Go writer's (Division, Gender) mapping
# (ingest-contract.md section 4, reimplemented here read-only for the
# assertion -- this is NOT the source of truth, just a check that our raw
# label resolves the way the Go side's substring matcher resolves it.)
# ---------------------------------------------------------------------------


def _go_gender(label: str) -> str:
    l = label.lower()
    if "women" in l:
        return "Womens"
    if "mixed" in l:
        return "Mixed"
    if "boys" in l:
        return "Boys"
    if "girls" in l:
        return "Girls"
    return "Open"


def _go_division(label: str) -> str:
    l = label.lower()
    if "college" in l:
        return "College"
    if "youth club" in l:
        return "YouthClubU17" if any(s in l for s in ("u17", "u-17", "u16", "u-16")) else "YouthClubU20"
    if "great grand master" in l or "great-grand master" in l or "great grandmaster" in l:
        return "GreatGrandMasters"
    if "grand master" in l or "grand-master" in l or "grandmaster" in l:
        return "GrandMasters"
    if "master" in l:
        return "Masters"
    if "beach" in l:
        return "Beach"
    if "club" in l:
        return "Club"
    if "national team" in l or "international" in l or "world" in l:
        return "International"
    return "Other"


class TestDivisionMapping:
    @pytest.mark.parametrize(
        "series_name,expected_gender",
        [("Mixed", "Mixed"), ("Open", "Open"), ("Women's", "Womens")],
    )
    def test_division_maps_to_club_and_correct_gender(self, series_name, expected_gender):
        label = f"World Ultimate Club Championships - {series_name}"
        assert _go_division(label) == "Club"
        assert _go_gender(label) == expected_gender

    def test_event_name_contains_wucc_for_fallback_team_matching(self):
        # The Go writer's fallbackTeamSources regex matches \bwucc\b on the
        # event name (case-insensitive) to enable WUCC<->USAU team matching.
        assert "wucc" in REFERENCE["season"]["name"].lower()


# ---------------------------------------------------------------------------
# Full offline pipeline: discover -> fetch_event -> parse_event ->
# tournament_to_document -> validate -> write_document -> read back ->
# re-validate.
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_all_three_series_build_valid_documents(self, tmp_path):
        source = WfdfSource()
        refs = source.discover(YEAR)
        assert len(refs) == 3

        documents = {}
        for ref in refs:
            key = source.event_key(ref)
            cache = FileCache("wfdf", YEAR, key, _fixture_transport, base_dir=tmp_path)
            pages = source.fetch_event(ref, cache)
            tournament = source.parse_event(pages, ref, YEAR)
            assert tournament is not None

            doc = tournament_to_document(
                tournament, source="wfdf", source_event_id=key, source_url=ref.url
            )
            dumped = doc.model_dump(by_alias=True, mode="json")
            Document.model_validate(dumped)  # must not raise
            documents[ref.extra["series_name"]] = doc

        assert set(documents) == {"Mixed", "Open", "Women's"}
        # Expanded from WFDF's own "WUCC 2026". This string is load-bearing
        # twice over: the Go writer matches tournaments on name+division+
        # gender (hence the year), and fallbackTeamSources regex-matches it
        # to enable WUCC->USAU team merging (hence "world ultimate club
        # championships" surviving intact). See ingest-contract.md section 4.
        assert documents["Mixed"].event.name == "World Ultimate Club Championships 2026"
        assert documents["Mixed"].event.city == "Limerick"
        assert documents["Mixed"].event.country == "Ireland"
        # Document identity must NOT follow the display name.
        assert documents["Mixed"].source_event_id == "WUCC2026/Mixed"
        assert len(documents["Mixed"].teams) == 48
        assert len(documents["Open"].teams) == 48
        assert len(documents["Women's"].teams) == 40

    def test_womens_pipeline_end_to_end_write_and_read_back(self, tmp_path):
        source = WfdfSource()
        ref = next(r for r in source.discover(YEAR) if r.extra["series_name"] == "Women's")

        key = source.event_key(ref)
        cache = FileCache("wfdf", YEAR, key, _fixture_transport, base_dir=tmp_path)
        pages = source.fetch_event(ref, cache)
        # fetch_event warms the cache
        assert cache.get("reference") == pages["reference"]
        assert cache.get("games") == pages["games"]

        tournament = source.parse_event(pages, ref, YEAR)
        assert tournament is not None
        assert len(tournament.teams) == 40
        assert len(tournament.stages) == 4

        doc = tournament_to_document(
            tournament, source="wfdf", source_event_id=key, source_url=ref.url
        )
        assert doc.source == "wfdf"
        assert doc.event.division == "World Ultimate Club Championships - Women's"
        assert doc.event.start_date.isoformat() == "2026-08-15"
        assert doc.event.end_date.isoformat() == "2026-08-22"

        written_path = write_document(doc, base_dir=tmp_path)
        # core.emit._sanitize_key strips characters outside [A-Za-z0-9._-],
        # so the apostrophe in "Women's" is dropped from the filename.
        assert written_path == tmp_path / "data" / "wfdf" / "2026" / "WUCC2026__Womens.json"
        assert written_path.exists()

        on_disk = json.loads(written_path.read_text(encoding="utf-8"))
        Document.model_validate(on_disk)  # must not raise

        sixers = next(t for t in on_disk["teams"] if t["name"] == "6ixers")
        assert len(sixers["roster"]) == 24
        assert all(isinstance(p["number"], str) for p in sixers["roster"])

        # 9 US teams across all 3 series -- 3 of them (Brute Squad, Fury,
        # Scandal) are in Women's; verify their names survived verbatim.
        team_names = {t["name"] for t in on_disk["teams"]}
        assert {"Brute Squad", "Fury", "Scandal"} <= team_names

        stage_types = [s["type"] for s in on_disk["stages"]]
        assert stage_types == ["pools", "pools", "brackets", "clusters"]

    def test_discover_offline_no_network_used(self):
        # discover() must not require any network access -- it's built
        # entirely from the hardcoded events.py table.
        source = WfdfSource()
        refs = source.discover(YEAR)
        assert len(refs) == 3


# ---------------------------------------------------------------------------
# ongoing_events / upcoming_events / recently_ended_events (sources/wfdf/events.py)
# ---------------------------------------------------------------------------


class TestEventWindowQueries:
    EVENT = WfdfEvent(
        year=2026,
        base_url="https://results.wfdf.sport/wucc-2026",
        season_id="WUCC2026",
        division_label="World Ultimate Club Championships",
        series=[WfdfSeries(series_id=1001, name="Mixed")],
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 22),
    )

    def test_ongoing_start_boundary_inclusive(self):
        assert ongoing_events([self.EVENT], today=date(2026, 8, 15)) == [self.EVENT]

    def test_ongoing_end_boundary_inclusive(self):
        assert ongoing_events([self.EVENT], today=date(2026, 8, 22)) == [self.EVENT]

    def test_ongoing_mid_event(self):
        assert ongoing_events([self.EVENT], today=date(2026, 8, 18)) == [self.EVENT]

    def test_not_ongoing_before_start(self):
        assert ongoing_events([self.EVENT], today=date(2026, 8, 14)) == []

    def test_not_ongoing_after_end(self):
        assert ongoing_events([self.EVENT], today=date(2026, 8, 23)) == []

    def test_upcoming_within_horizon_inclusive_boundary(self):
        # start_date - 10 days == today is the far edge of the default
        # within_days=10 horizon and must still count.
        assert upcoming_events([self.EVENT], today=date(2026, 8, 5), within_days=10) == [self.EVENT]

    def test_upcoming_excludes_today_equals_start(self):
        # An event starting today is ongoing, not upcoming.
        assert upcoming_events([self.EVENT], today=date(2026, 8, 15)) == []

    def test_upcoming_excludes_beyond_horizon(self):
        assert upcoming_events([self.EVENT], today=date(2026, 8, 4), within_days=10) == []

    def test_recently_ended_boundary_inclusive(self):
        assert recently_ended_events([self.EVENT], today=date(2026, 8, 25), within_days=3) == [self.EVENT]

    def test_recently_ended_excludes_today_equals_end(self):
        # An event ending today is ongoing, not recently ended.
        assert recently_ended_events([self.EVENT], today=date(2026, 8, 22), within_days=3) == []

    def test_recently_ended_excludes_beyond_window(self):
        assert recently_ended_events([self.EVENT], today=date(2026, 8, 26), within_days=3) == []

    def test_events_without_dates_are_excluded_everywhere(self):
        dateless = WfdfEvent(
            year=2026, base_url="https://x.example/x", season_id="X", division_label="X",
        )
        assert ongoing_events([dateless], today=date(2026, 8, 18)) == []
        assert upcoming_events([dateless], today=date(2026, 8, 5)) == []
        assert recently_ended_events([dateless], today=date(2026, 8, 25)) == []

    def test_defaults_to_hardcoded_wfdf_events_when_none_given(self):
        # No `events=` override -- must read WFDF_EVENTS (proves default
        # wiring, not just the injected-list path).
        assert ongoing_events(today=date(2026, 8, 18)) == events_for_year(2026)


# ---------------------------------------------------------------------------
# Live fetch policy: reference/games always refetch when live; rosters are
# served from cache unless stale or refresh_rosters forces it. This is the
# headline behaviour of the WFDF source task -- rosters are ~136 of ~138
# requests per event, and must not be refetched on every 10-minute
# "ongoing" run the way reference/games are.
# ---------------------------------------------------------------------------


class _CountingFixtureTransport:
    """Wraps `_fixture_transport` and records every URL it's asked to
    fetch, split out by resource so tests can assert exact call counts per
    page type without a real network."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        return _fixture_transport(url)

    @property
    def reference_calls(self):
        return [u for u in self.calls if "_reference" in u]

    @property
    def games_calls(self):
        return [u for u in self.calls if "_games" in u]

    @property
    def roster_calls(self):
        return [u for u in self.calls if "_teams_" in u]


def _team_ids_for_series(series_id: int):
    return sorted({t["team_id"] for t in REFERENCE.get("teams", []) if t.get("series") == series_id})


def _team_id_from_roster_url(url: str) -> int:
    # e.g. ".../live/data/WUCC2026_teams_1019.json?cb=1234567890" -> 1019
    suffix = url.split("_teams_", 1)[1]
    return int(suffix.split(".json", 1)[0])


class TestLiveFetchPolicy:
    SERIES_ID = SERIES_WOMENS  # smallest team count (40) among the 3 series

    def test_live_with_warm_roster_cache_fetches_only_reference_and_games(self, tmp_path):
        transport = _CountingFixtureTransport()
        source = WfdfSource(live=True)
        ref = next(r for r in source.discover(YEAR) if r.extra["series_id"] == self.SERIES_ID)
        key = source.event_key(ref)
        cache = FileCache("wfdf", YEAR, key, transport, base_dir=tmp_path)

        # Pre-warm every roster page for this series -- fresh (just written).
        for team_id in _team_ids_for_series(self.SERIES_ID):
            cache.put(f"teams:{team_id}", b'{"players": []}')

        pages = source.fetch_event(ref, cache)

        assert len(transport.reference_calls) == 1
        assert len(transport.games_calls) == 1
        assert transport.roster_calls == []
        assert len(transport.calls) == 2
        # fetch_event still returns every roster page (served from cache).
        assert all(f"teams:{tid}" in pages for tid in _team_ids_for_series(self.SERIES_ID))

    def test_refresh_rosters_true_fetches_every_roster_even_if_fresh(self, tmp_path):
        transport = _CountingFixtureTransport()
        source = WfdfSource(live=True, refresh_rosters=True)
        ref = next(r for r in source.discover(YEAR) if r.extra["series_id"] == self.SERIES_ID)
        key = source.event_key(ref)
        cache = FileCache("wfdf", YEAR, key, transport, base_dir=tmp_path)

        team_ids = _team_ids_for_series(self.SERIES_ID)
        for team_id in team_ids:
            cache.put(f"teams:{team_id}", b'{"players": []}')  # fresh

        source.fetch_event(ref, cache)

        assert len(transport.roster_calls) == len(team_ids)

    def test_stale_roster_entries_are_refetched_fresh_ones_are_not(self, tmp_path, monkeypatch):
        import sources.wfdf.source as wfdf_source_module

        monkeypatch.setattr(wfdf_source_module, "ROSTER_MAX_AGE_SECONDS", 100)

        transport = _CountingFixtureTransport()
        source = WfdfSource(live=True)
        ref = next(r for r in source.discover(YEAR) if r.extra["series_id"] == self.SERIES_ID)
        key = source.event_key(ref)
        cache = FileCache("wfdf", YEAR, key, transport, base_dir=tmp_path)

        team_ids = _team_ids_for_series(self.SERIES_ID)
        stale_ids = set(team_ids[:5])
        fresh_ids = set(team_ids[5:])

        now = time.time()
        for team_id in team_ids:
            cache.put(f"teams:{team_id}", b'{"players": []}')
            path = cache._path_for(f"teams:{team_id}")
            if team_id in stale_ids:
                os.utime(path, (now - 200, now - 200))  # older than the 100s TTL
            # fresh_ids left at "just written" mtime.

        source.fetch_event(ref, cache)

        fetched_team_ids = {_team_id_from_roster_url(u) for u in transport.roster_calls}
        assert fetched_team_ids == stale_ids

    def test_not_live_uses_normal_cache_behaviour_for_reference_and_games(self, tmp_path):
        # live=False (the default) -- a warm reference/games cache must be
        # served without hitting the network at all.
        transport = _CountingFixtureTransport()
        source = WfdfSource(live=False)
        ref = next(r for r in source.discover(YEAR) if r.extra["series_id"] == self.SERIES_ID)
        key = source.event_key(ref)
        cache = FileCache("wfdf", YEAR, key, transport, base_dir=tmp_path)
        cache.put("reference", REFERENCE_BYTES)
        cache.put("games", GAMES_BYTES)
        for team_id in _team_ids_for_series(self.SERIES_ID):
            cache.put(f"teams:{team_id}", b'{"players": []}')

        source.fetch_event(ref, cache)

        assert transport.reference_calls == []
        assert transport.games_calls == []
        assert transport.roster_calls == []


class TestThreeSeriesMemoizationUnderLive:
    def test_reference_and_games_fetched_once_across_all_three_series(self, tmp_path):
        transport = _CountingFixtureTransport()
        source = WfdfSource(live=True)
        refs = source.discover(YEAR)
        assert len(refs) == 3

        for ref in refs:
            key = source.event_key(ref)
            cache = FileCache("wfdf", YEAR, key, transport, base_dir=tmp_path)
            source.fetch_event(ref, cache)

        assert len(transport.reference_calls) == 1
        assert len(transport.games_calls) == 1
