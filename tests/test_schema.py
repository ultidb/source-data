"""Tests for core/schema.py against the canonical wire-format example in
CONTRACT.md section 1."""
import copy

import pytest
from pydantic import ValidationError

from core.schema import Document, Event, GameDoc, GroupDoc, StageDoc, TeamDoc, TeamInfoDoc, PlayerDoc

# The canonical example from CONTRACT.md section 1, completed with the
# "Truck Stop" team object that the example's own `teams` array omits (the
# doc only spells out "Vicious Cycle" for brevity, but game.team2 references
# "Truck Stop", which our own Document.teams<->games validator requires to
# be present). All fields that CONTRACT.md *does* specify -- including every
# field of "Vicious Cycle", the event, the game, and the stage/group shape --
# are reproduced byte-for-byte below; "Truck Stop" gets reasonable filler
# values since none were given.
CANONICAL_EXAMPLE = {
    "schemaVersion": "1.0",
    "source": "usau",
    "sourceEventId": "Swan-Boat-2025/Club-Men",
    "sourceUrl": "https://play.usaultimate.org/events/Swan-Boat-2025/schedule/Men/Club-Men/",
    "scrapedAt": "2025-07-28T04:11:00Z",
    "event": {
        "name": "Swan Boat 2025",
        "division": "Club - Men",
        "season": 2025,
        "city": "Apopka",
        "state": "FL",
        "country": "",
        "startDate": "2025-07-26",
        "endDate": "2025-07-27",
    },
    "teams": [
        {
            "name": "Vicious Cycle",
            "seed": 4,
            "sourceTeamId": "",
            "url": "",
            "info": {
                "nickname": "",
                "location": "Gainesville, Florida",
                "website": "",
                "facebook": "",
                "twitter": "",
            },
            "coaches": ["Jane Doe"],
            "roster": [{"number": "0", "name": "Caio Rudloff"}],
        },
        {
            "name": "Truck Stop",
            "seed": 0,
            "sourceTeamId": "",
            "url": "",
            "info": None,
            "coaches": [],
            "roster": [],
        },
    ],
    "stages": [
        {
            "type": "pools",
            "name": "Pool Play",
            "groups": [
                {
                    "name": "Pool A",
                    "isChampionship": False,
                    "games": [
                        {
                            "team1": "Vicious Cycle",
                            "team2": "Truck Stop",
                            "score1": 13,
                            "score2": 11,
                            "datetime": "2025-07-26T09:00:00Z",
                            "round": "",
                            "status": "Final",
                        }
                    ],
                }
            ],
        }
    ],
}


def test_canonical_example_round_trips_field_for_field():
    doc = Document.model_validate(CANONICAL_EXAMPLE)
    dumped = doc.model_dump(by_alias=True, mode="json")
    assert dumped == CANONICAL_EXAMPLE


def test_snake_case_construction_matches_camel_case_construction():
    doc_camel = Document.model_validate(CANONICAL_EXAMPLE)

    doc_snake = Document(
        schema_version="1.0",
        source="usau",
        source_event_id="Swan-Boat-2025/Club-Men",
        source_url="https://play.usaultimate.org/events/Swan-Boat-2025/schedule/Men/Club-Men/",
        scraped_at="2025-07-28T04:11:00Z",
        event=Event(
            name="Swan Boat 2025",
            division="Club - Men",
            season=2025,
            city="Apopka",
            state="FL",
            country="",
            start_date="2025-07-26",
            end_date="2025-07-27",
        ),
        teams=[
            TeamDoc(
                name="Vicious Cycle",
                seed=4,
                source_team_id="",
                url="",
                info=TeamInfoDoc(
                    nickname="", location="Gainesville, Florida", website="", facebook="", twitter=""
                ),
                coaches=["Jane Doe"],
                roster=[PlayerDoc(number="0", name="Caio Rudloff")],
            ),
            TeamDoc(name="Truck Stop", seed=0, source_team_id="", url="", info=None, coaches=[], roster=[]),
        ],
        stages=[
            StageDoc(
                type="pools",
                name="Pool Play",
                groups=[
                    GroupDoc(
                        name="Pool A",
                        is_championship=False,
                        games=[
                            GameDoc(
                                team1="Vicious Cycle",
                                team2="Truck Stop",
                                score1=13,
                                score2=11,
                                datetime="2025-07-26T09:00:00Z",
                                round="",
                                status="Final",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    assert doc_snake.model_dump(by_alias=True, mode="json") == doc_camel.model_dump(
        by_alias=True, mode="json"
    )


def test_unknown_team_reference_raises():
    bad = copy.deepcopy(CANONICAL_EXAMPLE)
    bad["stages"][0]["groups"][0]["games"][0]["team2"] = "Nonexistent Team"
    with pytest.raises(ValidationError):
        Document.model_validate(bad)


def test_duplicate_team_names_case_insensitive_raises():
    bad = copy.deepcopy(CANONICAL_EXAMPLE)
    dup = copy.deepcopy(bad["teams"][0])
    dup["name"] = "vicious cycle"  # differs only in case from "Vicious Cycle"
    bad["teams"].append(dup)
    with pytest.raises(ValidationError):
        Document.model_validate(bad)


def test_datetime_none_round_trips_as_json_null():
    doc = copy.deepcopy(CANONICAL_EXAMPLE)
    doc["stages"][0]["groups"][0]["games"][0]["datetime"] = None
    doc["stages"][0]["groups"][0]["games"][0]["status"] = "Scheduled"
    parsed = Document.model_validate(doc)
    assert parsed.stages[0].groups[0].games[0].datetime is None
    dumped = parsed.model_dump(by_alias=True, mode="json")
    assert dumped["stages"][0]["groups"][0]["games"][0]["datetime"] is None


def test_stage_type_must_be_one_of_pools_brackets_clusters():
    bad = copy.deepcopy(CANONICAL_EXAMPLE)
    bad["stages"][0]["type"] = "playoffs"
    with pytest.raises(ValidationError):
        Document.model_validate(bad)


def test_player_number_is_string_type():
    doc = Document.model_validate(CANONICAL_EXAMPLE)
    assert isinstance(doc.teams[0].roster[0].number, str)
