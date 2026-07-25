import pytest
from bs4.element import NavigableString

from parse import extract_nickname, convertNonDigitScore, parseCoaches, stagesHaveGames
from models import Pools, Pool, Brackets, Bracket, Clusters, Cluster, Game, Team


class TestExtractNickname:
    def test_simple_nickname(self):
        assert extract_nickname("University of Oregon (Fugue)") == "Fugue"

    def test_no_parentheses(self):
        assert extract_nickname("University of Oregon") == ""

    def test_empty_parentheses(self):
        assert extract_nickname("Team ()") == ""

    def test_whitespace_handling(self):
        assert extract_nickname("Team ( Nickname )") == "Nickname"

    def test_nested_parentheses_single_char(self):
        # When nested content is > 1 char (including parens), extracts inner
        assert extract_nickname("Team (Nick(A)name)") == "A"

    def test_nested_parentheses_multiple_chars(self):
        # Multiple characters inside nested parens should extract inner
        assert extract_nickname("Team (Outer (Inner))") == "Inner"

    def test_opening_only(self):
        assert extract_nickname("Team (Incomplete") == ""

    def test_closing_only(self):
        assert extract_nickname("Team Incomplete)") == ""


class TestConvertNonDigitScore:
    def test_uppercase_w(self):
        assert convertNonDigitScore("W") == 1

    def test_lowercase_w(self):
        assert convertNonDigitScore("w") == 1

    def test_win(self):
        assert convertNonDigitScore("Win") == 1

    def test_win_lowercase(self):
        assert convertNonDigitScore("win") == 1

    def test_loss(self):
        assert convertNonDigitScore("L") == 0

    def test_empty_string(self):
        assert convertNonDigitScore("") == 0

    def test_other_value(self):
        assert convertNonDigitScore("forfeit") == 0


class TestParseCoaches:
    def test_single_coach(self):
        items = [NavigableString("John Smith")]
        assert parseCoaches(items) == ["John Smith"]

    def test_multiple_coaches(self):
        items = [NavigableString("John Smith"), NavigableString("Jane Doe")]
        assert parseCoaches(items) == ["John Smith", "Jane Doe"]

    def test_coach_with_title(self):
        items = [NavigableString("John Smith (Head Coach)")]
        assert parseCoaches(items) == ["John Smith"]

    def test_coach_with_whitespace(self):
        items = [NavigableString("  John Smith  ")]
        assert parseCoaches(items) == ["John Smith"]

    def test_empty_string_filtered(self):
        items = [NavigableString("John Smith"), NavigableString(""), NavigableString("Jane Doe")]
        assert parseCoaches(items) == ["John Smith", "Jane Doe"]

    def test_non_navigable_string_filtered(self):
        items = [NavigableString("John Smith"), "Not a NavigableString", NavigableString("Jane Doe")]
        assert parseCoaches(items) == ["John Smith", "Jane Doe"]

    def test_empty_list(self):
        assert parseCoaches([]) == []


class TestStagesHaveGames:
    @pytest.fixture
    def mock_game(self):
        team_a = Team("Team A", 1, "http://example.com/a")
        team_b = Team("Team B", 2, "http://example.com/b")
        from datetime import datetime
        return Game(team_a, team_b, "15", "10", datetime.now(), "Final")

    def test_pools_with_games(self, mock_game):
        pool = Pool("Pool A", [], [mock_game])
        pools_stage = Pools("Pools", [pool])
        assert stagesHaveGames([pools_stage]) is True

    def test_pools_without_games(self):
        pool = Pool("Pool A", [], [])
        pools_stage = Pools("Pools", [pool])
        assert stagesHaveGames([pools_stage]) is False

    def test_brackets_with_games(self, mock_game):
        bracket = Bracket("Championship", [mock_game])
        brackets_stage = Brackets("Brackets", [bracket])
        assert stagesHaveGames([brackets_stage]) is True

    def test_brackets_without_games(self):
        bracket = Bracket("Championship", [])
        brackets_stage = Brackets("Brackets", [bracket])
        assert stagesHaveGames([brackets_stage]) is False

    def test_clusters_with_games(self, mock_game):
        cluster = Cluster("Cluster 1", [mock_game])
        clusters_stage = Clusters("Clusters", [cluster])
        assert stagesHaveGames([clusters_stage]) is True

    def test_clusters_without_games(self):
        cluster = Cluster("Cluster 1", [])
        clusters_stage = Clusters("Clusters", [cluster])
        assert stagesHaveGames([clusters_stage]) is False

    def test_empty_pools_list(self):
        pools_stage = Pools("Pools", [])
        assert stagesHaveGames([pools_stage]) is False

    def test_empty_stages(self):
        assert stagesHaveGames([]) is False

    def test_mixed_stages_one_with_games(self, mock_game):
        empty_pool = Pool("Pool A", [], [])
        pools_stage = Pools("Pools", [empty_pool])
        bracket = Bracket("Championship", [mock_game])
        brackets_stage = Brackets("Brackets", [bracket])
        assert stagesHaveGames([pools_stage, brackets_stage]) is True
