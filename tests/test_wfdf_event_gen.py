"""Tests for the WFDF event-list loader (sources/wfdf/events.py's
`load_events`) and the `wfdf-event` CLI helper's derivation/discovery logic
(sources/wfdf/event_gen.py).

No network anywhere in this file: discovery is driven by small HTML
snippets served through a fake `transport` callable, and derivation is
driven by the real checked-in WUCC2026_reference.json fixture (already used
by tests/test_wfdf.py) plus small hand-built payloads for the mapping edge
cases.
"""
from __future__ import annotations

import json
import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

from sources.wfdf.event_gen import (
    SeasonIdDiscoveryError,
    derive_event,
    discover_season_id,
    event_to_yaml_block,
    expand_name,
    infer_national_team_division,
    map_gender,
)
from sources.wfdf.events import (
    EVENTS_YAML_PATH,
    WFDF_EVENTS,
    EventsValidationError,
    WfdfEvent,
    WfdfSeries,
    load_events,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "sources" / "wfdf" / "fixtures"
WUCC_REFERENCE = json.loads((FIXTURES_DIR / "WUCC2026_reference.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# load_events() -- YAML -> WfdfEvent round trip
# ---------------------------------------------------------------------------


class TestLoadEvents:
    def test_loads_the_real_events_yaml_without_error(self):
        # WFDF_EVENTS is load_events() run at import time -- just confirm
        # the checked-in file is itself valid.
        events = load_events()
        assert len(events) >= 2
        assert {e.season_id for e in events} >= {"WUCC2026", "wjuc2026"}

    def test_wjuc_entry_loads_as_international_u20_not_plain_international(self):
        # WJUC is a U20 (Junior) national-teams event -- events.yaml's entry
        # must load with the age-grouped division, not plain
        # "international", or a country's WJUC squad would collide with its
        # senior national squad on the team match key (ingest-contract.md
        # section 4 "Team identity").
        wjuc = next(e for e in WFDF_EVENTS if e.season_id == "wjuc2026")
        assert wjuc.division == "international-u20"
        assert wjuc.division not in ("club", "international")

    def test_wucc_entry_loads_with_pinned_values(self):
        # Behaviour-preserving refactor: the WUCC entry must come out of
        # events.yaml with exactly the values the hardcoded Python list used
        # to carry -- pin them so a YAML typo or reformatting can't silently
        # change WUCC's real data.
        wucc = next(e for e in WFDF_EVENTS if e.season_id == "WUCC2026")
        assert wucc.year == 2026
        assert wucc.base_url == "https://results.wfdf.sport/wucc-2026"
        assert wucc.division == "club"
        assert wucc.name == "World Ultimate Club Championships 2026"
        assert wucc.city == "Limerick"
        assert wucc.state == ""
        assert wucc.country == "Ireland"
        assert wucc.start_date == date(2026, 8, 15)
        assert wucc.end_date == date(2026, 8, 22)
        assert wucc.data_path == "live/data"
        assert wucc.series == [
            WfdfSeries(series_id=1001, name="Mixed", gender="mixed"),
            WfdfSeries(series_id=1002, name="Open", gender="open"),
            WfdfSeries(series_id=1000, name="Women's", gender="womens"),
        ]

    def test_round_trip_through_a_scratch_yaml_file(self, tmp_path):
        # A minimal, valid entry loads into exactly the WfdfEvent/WfdfSeries
        # it describes.
        p = tmp_path / "events.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport/test-2030/"
                  season_id: "TEST2030"
                  division: "club"
                  name: "Test Event 2030"
                  city: "Testville"
                  state: ""
                  country: "Testland"
                  start_date: 2030-01-01
                  end_date: 2030-01-07
                  series:
                    - series_id: 1
                      name: "Mixed"
                      gender: "mixed"
                """
            ),
            encoding="utf-8",
        )
        events = load_events(p)
        assert len(events) == 1
        e = events[0]
        assert e.year == 2030
        # base_url's trailing slash is normalised away, same as the
        # dataclass constructor does directly.
        assert e.base_url == "https://example.wfdf.sport/test-2030"
        assert e.season_id == "TEST2030"
        assert e.division == "club"
        assert e.start_date == date(2030, 1, 1)
        assert e.end_date == date(2030, 1, 7)
        assert e.series == [WfdfSeries(series_id=1, name="Mixed", gender="mixed")]

    @pytest.mark.parametrize("division", ["international-u20", "international-u24"])
    def test_loader_accepts_the_new_age_grouped_international_divisions(self, tmp_path, division):
        p = tmp_path / "events.yaml"
        p.write_text(
            textwrap.dedent(
                f"""\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "TEST2030"
                  division: "{division}"
                  series:
                    - {{series_id: 1, name: "Mixed", gender: "mixed"}}
                """
            ),
            encoding="utf-8",
        )
        events = load_events(p)
        assert events[0].division == division

    def test_string_dates_are_accepted_alongside_native_yaml_dates(self, tmp_path):
        p = tmp_path / "events.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "TEST2030"
                  division: "club"
                  start_date: "2030-01-01"
                  end_date: "2030-01-07"
                  series:
                    - {series_id: 1, name: "Mixed", gender: "mixed"}
                """
            ),
            encoding="utf-8",
        )
        events = load_events(p)
        assert events[0].start_date == date(2030, 1, 1)
        assert events[0].end_date == date(2030, 1, 7)

    @pytest.mark.parametrize(
        "yaml_text,expected_message_fragment",
        [
            # Missing required field.
            (
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  division: "club"
                  series: [{series_id: 1, name: "Mixed", gender: "mixed"}]
                """,
                "missing required field",
            ),
            # Unrecognized division.
            (
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "BAD2030"
                  division: "not-a-real-division"
                  series: [{series_id: 1, name: "Mixed", gender: "mixed"}]
                """,
                "not one of the accepted wire division names",
            ),
            # Unrecognized gender.
            (
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "BAD2030"
                  division: "club"
                  series: [{series_id: 1, name: "Mixed", gender: "not-a-real-gender"}]
                """,
                "not one of the accepted wire gender names",
            ),
            # Empty series list.
            (
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "BAD2030"
                  division: "club"
                  series: []
                """,
                "'series' must be a non-empty list",
            ),
            # Unparseable date.
            (
                """\
                - year: 2030
                  base_url: "https://example.wfdf.sport"
                  season_id: "BAD2030"
                  division: "club"
                  start_date: "not-a-date"
                  series: [{series_id: 1, name: "Mixed", gender: "mixed"}]
                """,
                "not a parseable ISO date",
            ),
        ],
    )
    def test_invalid_entries_raise_naming_the_offending_event(
        self, tmp_path, yaml_text, expected_message_fragment
    ):
        p = tmp_path / "events.yaml"
        p.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        with pytest.raises(EventsValidationError) as exc_info:
            load_events(p)
        message = str(exc_info.value)
        assert expected_message_fragment in message
        # The offending event is named -- either its season_id, or its
        # position in the file when season_id itself is what's missing.
        assert "BAD2030" in message or "entry #0" in message

    def test_events_yaml_path_points_at_the_real_checked_in_file(self):
        assert EVENTS_YAML_PATH.name == "events.yaml"
        assert EVENTS_YAML_PATH.exists()


# ---------------------------------------------------------------------------
# derive_event() -- driven by the real WUCC2026_reference.json fixture. This
# is the strongest test here: the generator must regenerate the entry we
# already trust (events.yaml's checked-in WUCC2026 block).
# ---------------------------------------------------------------------------


class TestDeriveEventFromRealWuccFixture:
    def test_regenerates_the_known_good_wucc_entry(self):
        derived = derive_event(
            WUCC_REFERENCE,
            base_url="https://results.wfdf.sport/wucc-2026",
        )
        event = derived.event

        assert event.season_id == "WUCC2026"
        assert event.year == 2026
        assert event.start_date == date(2026, 8, 15)
        assert event.end_date == date(2026, 8, 22)
        # isnationalteams is 0 in the real WUCC payload -> "club".
        assert event.division == "club"
        assert derived.division_source == "isnationalteams"
        # "WUCC 2026" -> expanded, year preserved.
        assert event.name == "World Ultimate Club Championships 2026"
        assert derived.name_expanded is True
        # No venue in the payload -- left blank for a human to fill in.
        assert event.city == ""
        assert event.state == ""
        assert event.country == ""
        # All 3 series map cleanly; no unmapped genders, no warnings, safe
        # to write.
        assert derived.unmapped_series == []
        assert derived.warnings == []
        assert derived.is_safe_to_write is True
        assert sorted((s.series_id, s.name, s.gender) for s in event.series) == [
            (1000, "Women's", "womens"),
            (1001, "Mixed", "mixed"),
            (1002, "Open", "open"),
        ]

    def test_division_override_beats_isnationalteams(self):
        derived = derive_event(
            WUCC_REFERENCE, base_url="https://results.wfdf.sport/wucc-2026", division="masters"
        )
        assert derived.event.division == "masters"
        assert derived.division_source == "override"

    def test_name_override_beats_expansion(self):
        derived = derive_event(
            WUCC_REFERENCE, base_url="https://results.wfdf.sport/wucc-2026", name="Custom Name 2026"
        )
        assert derived.event.name == "Custom Name 2026"

    def test_city_state_country_pass_through_when_given(self):
        derived = derive_event(
            WUCC_REFERENCE,
            base_url="https://results.wfdf.sport/wucc-2026",
            city="Limerick",
            country="Ireland",
        )
        assert derived.event.city == "Limerick"
        assert derived.event.country == "Ireland"

    def test_event_to_yaml_block_is_parseable_and_matches_the_derived_event(self):
        derived = derive_event(
            WUCC_REFERENCE,
            base_url="https://results.wfdf.sport/wucc-2026",
            city="Limerick",
            country="Ireland",
        )
        block = event_to_yaml_block(derived.event)
        parsed = yaml.safe_load(block)
        assert isinstance(parsed, list) and len(parsed) == 1
        entry = parsed[0]
        assert entry["season_id"] == "WUCC2026"
        assert entry["division"] == "club"
        assert entry["name"] == "World Ultimate Club Championships 2026"
        assert entry["start_date"] == date(2026, 8, 15)
        assert entry["end_date"] == date(2026, 8, 22)
        assert len(entry["series"]) == 3


# ---------------------------------------------------------------------------
# derive_event() -- WJUC-shaped payload (national-teams event): division
# must come out an age-grouped "international-*" division, not "club", when
# isnationalteams=1. Which specific "international-*" division is suggested
# from season.name's text via infer_national_team_division (see event_gen.py)
# -- WJUC is age-grouped (U20), so it must NOT come out as plain
# "international": that would collide a country's WJUC squad with the same
# country's senior national squad on the team match key (ingest-contract.md
# section 4 "Team identity").
# ---------------------------------------------------------------------------


class TestDeriveEventNationalTeams:
    WJUC_LIKE_PAYLOAD = {
        "season": {
            "season_id": "wjuc2026",
            "name": "WJUC 2026",
            "starttime": "2026-07-11 00:00:00",
            "endtime": "2026-07-18 00:00:00",
            "isnationalteams": 1,
        },
        "series": [
            {"series_id": 1, "name": "Mixed"},
            {"series_id": 2, "name": "Open"},
            {"series_id": 3, "name": "Women's"},
        ],
    }

    def test_isnationalteams_one_maps_to_international_u20_for_wjuc(self):
        derived = derive_event(self.WJUC_LIKE_PAYLOAD, base_url="https://wjuc.wfdf.sport")
        assert derived.event.division == "international-u20"
        assert derived.event.division not in ("club", "international")
        assert derived.division_source == "isnationalteams"

    def test_wjuc_abbreviation_expands_with_year_preserved(self):
        derived = derive_event(self.WJUC_LIKE_PAYLOAD, base_url="https://wjuc.wfdf.sport")
        assert derived.event.name == "World Junior Ultimate Championships 2026"

    def test_missing_isnationalteams_requires_division_override(self):
        payload = {
            "season": dict(self.WJUC_LIKE_PAYLOAD["season"]),
            "series": self.WJUC_LIKE_PAYLOAD["series"],
        }
        del payload["season"]["isnationalteams"]
        with pytest.raises(ValueError, match="isnationalteams"):
            derive_event(payload, base_url="https://wjuc.wfdf.sport")
        # ...but works fine with an explicit override. The override beats
        # the name-based suggestion entirely -- passing "international" here
        # (rather than "international-u20") must be honored verbatim.
        derived = derive_event(payload, base_url="https://wjuc.wfdf.sport", division="international")
        assert derived.event.division == "international"
        assert derived.division_source == "override"

    def test_ambiguous_isnationalteams_requires_division_override(self):
        payload = {
            "season": dict(self.WJUC_LIKE_PAYLOAD["season"], isnationalteams=2),
            "series": self.WJUC_LIKE_PAYLOAD["series"],
        }
        with pytest.raises(ValueError, match="isnationalteams"):
            derive_event(payload, base_url="https://wjuc.wfdf.sport")

    def test_senior_worlds_event_maps_to_plain_international(self):
        # WUGC (World Ultimate Championships) is WFDF's senior/open
        # national-teams event -- no junior/U20/U24 indicator in the name,
        # so it must fall through to plain "international", same as before
        # this change.
        payload = {
            "season": dict(self.WJUC_LIKE_PAYLOAD["season"], season_id="WUGC2028", name="WUGC 2028"),
            "series": self.WJUC_LIKE_PAYLOAD["series"],
        }
        derived = derive_event(payload, base_url="https://wugc.wfdf.sport")
        assert derived.event.division == "international"

    def test_u24_named_event_maps_to_international_u24(self):
        payload = {
            "season": dict(self.WJUC_LIKE_PAYLOAD["season"], season_id="WU242027", name="WU24 2027"),
            "series": self.WJUC_LIKE_PAYLOAD["series"],
        }
        derived = derive_event(payload, base_url="https://wu24.wfdf.sport")
        assert derived.event.division == "international-u24"


# ---------------------------------------------------------------------------
# infer_national_team_division() directly.
# ---------------------------------------------------------------------------


class TestInferNationalTeamDivision:
    @pytest.mark.parametrize(
        "season_name",
        ["WJUC 2026", "World Junior Ultimate Championships 2026", "U20 Worlds 2026", "u-20 Championship"],
    )
    def test_u20_indicators(self, season_name):
        assert infer_national_team_division(season_name) == "international-u20"

    @pytest.mark.parametrize(
        "season_name",
        ["WU24 2027", "wu-24 2027", "U24 Championship", "Under-24 Worlds"],
    )
    def test_u24_indicators(self, season_name):
        assert infer_national_team_division(season_name) == "international-u24"

    @pytest.mark.parametrize("season_name", ["WUGC 2028", "World Ultimate Championships 2028", "World Games 2029", ""])
    def test_no_age_indicator_falls_back_to_plain_international(self, season_name):
        assert infer_national_team_division(season_name) == "international"


# ---------------------------------------------------------------------------
# Gender mapping (map_gender) -- the apostrophe case, and unmapped names
# producing a TODO marker that refuses --write.
# ---------------------------------------------------------------------------


class TestGenderMapping:
    @pytest.mark.parametrize(
        "series_name,expected",
        [
            ("Mixed", "mixed"),
            ("Open", "open"),
            ("Women's", "womens"),  # apostrophe does NOT survive .lower() -- explicit table entry
            ("women's", "womens"),
            ("Men", "open"),
            ("Men's", "open"),
            ("Boys", "boys"),
            ("Girls", "girls"),
        ],
    )
    def test_known_series_names_map_correctly(self, series_name, expected):
        assert map_gender(series_name) == expected

    def test_unmapped_series_name_returns_none(self):
        assert map_gender("Nonbinary") is None

    def test_unmapped_gender_in_derive_event_produces_todo_and_warning(self):
        payload = {
            "season": {
                "season_id": "TEST2027",
                "name": "TESTABBR 2027",
                "starttime": "2027-05-01 00:00:00",
                "endtime": "2027-05-05 00:00:00",
                "isnationalteams": 0,
            },
            "series": [
                {"series_id": 1, "name": "Mixed"},
                {"series_id": 2, "name": "Nonbinary"},
            ],
        }
        derived = derive_event(payload, base_url="https://example.wfdf.sport/test")
        nonbinary = next(s for s in derived.event.series if s.name == "Nonbinary")
        assert nonbinary.gender == "TODO"
        assert derived.unmapped_series == ["Nonbinary"]
        assert any("Nonbinary" in w for w in derived.warnings)
        assert derived.is_safe_to_write is False

    def test_unknown_abbreviation_is_left_raw_with_a_warning(self):
        payload = {
            "season": {
                "season_id": "TEST2027",
                "name": "TESTABBR 2027",
                "starttime": "2027-05-01 00:00:00",
                "endtime": "2027-05-05 00:00:00",
                "isnationalteams": 0,
            },
            "series": [{"series_id": 1, "name": "Mixed"}],
        }
        derived = derive_event(payload, base_url="https://example.wfdf.sport/test")
        assert derived.event.name == "TESTABBR 2027"  # unchanged, not guessed
        assert derived.name_expanded is False
        assert any("TESTABBR" in w for w in derived.warnings)
        # A safe-to-write event (no unmapped genders) can still carry an
        # unexpanded-name warning -- the two are independent.
        assert derived.is_safe_to_write is True


# ---------------------------------------------------------------------------
# expand_name() directly.
# ---------------------------------------------------------------------------


class TestExpandName:
    @pytest.mark.parametrize(
        "raw,expected_name,expected_flag",
        [
            ("WUCC 2026", "World Ultimate Club Championships 2026", True),
            ("WMUCC 2027", "World Masters Ultimate Club Championships 2027", True),
            ("WJUC 2026", "World Junior Ultimate Championships 2026", True),
            ("WUGC 2028", "World Ultimate Championships 2028", True),
            ("SOMETHING 2029", "SOMETHING 2029", False),
            ("", "", False),
        ],
    )
    def test_expand_name(self, raw, expected_name, expected_flag):
        name, expanded = expand_name(raw)
        assert name == expected_name
        assert expanded is expected_flag


# ---------------------------------------------------------------------------
# discover_season_id() -- small HTML snippets, no network.
# ---------------------------------------------------------------------------


def _static_transport(html: str):
    def transport(url: str) -> bytes:
        return html.encode("utf-8")

    return transport


class TestDiscoverSeasonId:
    def test_finds_live_season_id_in_an_inline_config_blob(self):
        html = """
        <html><head><script>
        window.__APP_CONFIG__ = {"LIVE_SEASON_ID":"WUCC2026","OTHER":"x"};
        </script></head></html>
        """
        season_id = discover_season_id("https://results.wfdf.sport/wucc-2026", _static_transport(html))
        assert season_id == "WUCC2026"

    def test_falls_back_to_a_literal_reference_json_filename(self):
        html = '<link rel="prefetch" href="/live/data/WJUC2026_reference.json">'
        season_id = discover_season_id("https://wjuc.wfdf.sport", _static_transport(html))
        assert season_id == "WJUC2026"

    def test_prefers_live_season_id_over_a_literal_filename_when_both_present(self):
        html = (
            '<script>window.x = {"LIVE_SEASON_ID":"REAL2026"};</script>'
            '<link href="/data/DECOY2026_reference.json">'
        )
        season_id = discover_season_id("https://example.wfdf.sport", _static_transport(html))
        assert season_id == "REAL2026"

    def test_raises_naming_what_was_tried_when_neither_pattern_matches(self):
        html = "<html><body>Nothing useful here.</body></html>"
        with pytest.raises(SeasonIdDiscoveryError) as exc_info:
            discover_season_id("https://example.com", _static_transport(html))
        message = str(exc_info.value)
        assert "https://example.com" in message
        assert "LIVE_SEASON_ID" in message
        assert "_reference.json" in message
        assert "--season-id" in message

    def test_no_network_used(self):
        # `_static_transport` never touches the network -- this is really
        # just documenting the guarantee discover_season_id relies on: it
        # only ever calls the injected transport, never `requests` itself.
        calls = []

        def transport(url):
            calls.append(url)
            return b'{"LIVE_SEASON_ID":"X2026"}'

        discover_season_id("https://x.example", transport)
        assert calls == ["https://x.example"]
