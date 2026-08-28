"""Round-trip tests: models.py domain objects -> tournament_to_document ->
Document.model_dump -> document_to_tournament.

See core/serialize.py's module docstring for the (documented, intentional)
fields that don't survive a perfect round trip.
"""
from datetime import datetime

import pytest

import models
from core.serialize import document_to_tournament, tournament_to_document


def _build_pools_tournament():
    t1 = models.Team("Vicious Cycle", 4, "https://example.com/t1", id="t1")
    t1.roster = [models.Player("0", "Caio Rudloff"), models.Player("00", "Second Player")]
    t1.info = models.TeamInfo("Vicious", "Gainesville, Florida", ["Jane Doe"], "", "", "")

    t2 = models.Team("Truck Stop", 1, "https://example.com/t2", id="t2")
    t2.roster = [models.Player("7", "Third Player")]

    game1 = models.Game(t1, t2, 13, 11, datetime(2025, 7, 26, 9, 0), "Final")
    game2 = models.Game(t2, t1, "W", "L", None, "Scheduled")  # TBA game, non-digit score
    pool = models.Pool("Pool A", [t1, t2], [game1, game2])
    pools_stage = models.Pools("Pool Play", [pool])

    tournament = models.Tournament(
        "Swan Boat 2025",
        "https://play.usaultimate.org/events/x",
        "Apopka",
        "FL",
        "2025-07-26",
        "2025-07-27",
        [t1, t2],
        datetime(2025, 7, 26),
        "Club - Men",
        [pools_stage],
    )
    return tournament, t1, t2


def _build_brackets_and_clusters_tournament():
    t1 = models.Team("A Team", 1, "", id=None)
    t1.roster = []
    t2 = models.Team("B Team", 2, "", id=None)
    t2.roster = []

    bracket_game = models.Game(t1, t2, 15, 10, datetime(2025, 8, 1, 12, 0), "Final", round="Quarterfinals")
    bracket = models.Bracket("Championship Bracket", [bracket_game])
    brackets_stage = models.Brackets("Bracket Play", [bracket])

    cluster_game = models.Game(t2, t1, 8, 15, datetime(2025, 8, 2, 10, 30), "Final")
    cluster = models.Cluster("Cluster 1", [cluster_game])
    clusters_stage = models.Clusters("Cluster Play", [cluster])

    tournament = models.Tournament(
        "Some Event",
        "https://example.com/event",
        "Somewhere",
        "CA",
        "2025-08-01",
        "2025-08-02",
        [t1, t2],
        datetime(2025, 8, 1),
        "Masters - Mixed",
        [brackets_stage, clusters_stage],
    )
    return tournament, t1, t2


class TestPoolsRoundTrip:
    @pytest.fixture
    def doc(self):
        tournament, _, _ = _build_pools_tournament()
        return tournament_to_document(
            tournament,
            source="usau",
            source_event_id="Swan-Boat-2025/Club-Men",
            source_url="https://play.usaultimate.org/events/x",
            scraped_at=datetime(2025, 7, 28, 4, 11, 0),
        )

    def test_dumps_without_error_and_validates(self, doc):
        dumped = doc.model_dump(by_alias=True, mode="json")
        assert dumped["source"] == "usau"
        assert dumped["event"]["season"] == 2025

    def test_teams_preserved(self, doc):
        back = document_to_tournament(doc)
        names = sorted(t.name for t in back.teams)
        assert names == ["Truck Stop", "Vicious Cycle"]

    def test_rosters_preserved(self, doc):
        back = document_to_tournament(doc)
        vicious = next(t for t in back.teams if t.name == "Vicious Cycle")
        roster = sorted((p.number, p.name) for p in vicious.roster)
        assert roster == [("0", "Caio Rudloff"), ("00", "Second Player")]

    def test_team_info_and_coaches_preserved(self, doc):
        back = document_to_tournament(doc)
        vicious = next(t for t in back.teams if t.name == "Vicious Cycle")
        assert vicious.info is not None
        assert vicious.info.nickname == "Vicious"
        assert vicious.info.location == "Gainesville, Florida"
        assert vicious.info.coaches == ["Jane Doe"]

    def test_stage_and_group_preserved(self, doc):
        back = document_to_tournament(doc)
        assert len(back.stages) == 1
        assert isinstance(back.stages[0], models.Pools)
        assert back.stages[0].name == "Pool Play"
        assert len(back.stages[0].pools) == 1
        assert back.stages[0].pools[0].name == "Pool A"

    def test_games_preserved(self, doc):
        back = document_to_tournament(doc)
        games = back.stages[0].pools[0].games
        assert len(games) == 2

        final_game = next(g for g in games if g.status == "Final")
        assert final_game.teamA.name == "Vicious Cycle"
        assert final_game.teamB.name == "Truck Stop"
        assert final_game.teamA_score == 13
        assert final_game.teamB_score == 11
        assert final_game.datetime == datetime(2025, 7, 26, 9, 0)

        tba_game = next(g for g in games if g.status == "Scheduled")
        assert tba_game.datetime is None
        # convertNonDigitScore-style "W"/"L" scores are coerced to int on the
        # way to the wire (score1/score2 are ints in the schema); this is a
        # known, intentional lossy conversion, not a round-trip bug.
        assert tba_game.teamA_score == 1
        assert tba_game.teamB_score == 0


class TestBracketsAndClustersRoundTrip:
    @pytest.fixture
    def doc(self):
        tournament, _, _ = _build_brackets_and_clusters_tournament()
        return tournament_to_document(
            tournament,
            source="usau",
            source_event_id="Some-Event/Masters-Mixed",
            source_url="https://example.com/event",
        )

    def test_stage_types(self, doc):
        types = [s.type for s in doc.stages]
        assert types == ["brackets", "clusters"]

    def test_brackets_round_trip(self, doc):
        back = document_to_tournament(doc)
        brackets_stage = back.stages[0]
        assert isinstance(brackets_stage, models.Brackets)
        assert brackets_stage.name == "Bracket Play"
        assert len(brackets_stage.brackets) == 1
        bracket = brackets_stage.brackets[0]
        assert bracket.name == "Championship Bracket"
        game = bracket.games[0]
        assert game.teamA.name == "A Team"
        assert game.teamB.name == "B Team"
        assert game.round == "Quarterfinals"
        assert game.teamA_score == 15
        assert game.teamB_score == 10

    def test_clusters_round_trip(self, doc):
        back = document_to_tournament(doc)
        clusters_stage = back.stages[1]
        assert isinstance(clusters_stage, models.Clusters)
        assert clusters_stage.name == "Cluster Play"
        assert len(clusters_stage.clusters) == 1
        cluster = clusters_stage.clusters[0]
        assert cluster.name == "Cluster 1"
        game = cluster.games[0]
        assert game.teamA.name == "B Team"
        assert game.teamB.name == "A Team"
        # round was None going in for cluster games -> "" on the wire -> None coming back
        assert game.round is None

    def test_division_label_preserved_verbatim(self, doc):
        assert doc.event.division == "Masters - Mixed"
        back = document_to_tournament(doc)
        assert back.division == "Masters - Mixed"


def test_team_with_no_info_and_no_coaches_stays_none():
    tournament, t1, t2 = _build_pools_tournament()
    t2.info = None  # Truck Stop never got a team-page scrape
    doc = tournament_to_document(
        tournament, source="usau", source_event_id="x/y", source_url="https://example.com"
    )
    truck_stop_doc = next(t for t in doc.teams if t.name == "Truck Stop")
    assert truck_stop_doc.info is None
    assert truck_stop_doc.coaches == []

    back = document_to_tournament(doc)
    truck_stop_back = next(t for t in back.teams if t.name == "Truck Stop")
    assert truck_stop_back.info is None
