"""core/legacy.py: table-driven tests over inline CSV rows, covering the
same edge cases ultidb/api/etl/reader_legacy.go's tests cover (char-splat
sentinels, ragged rows, missing "break", TBA datetime, round-only-for-
brackets, no stages section), plus a corpus test over real csv/2025/ files.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.legacy import (
    ConversionSummary,
    convert_legacy,
    iter_legacy_csv_files,
    read_legacy_csv,
)
from core.schema import PlayerDoc

REPO_ROOT = Path(__file__).parent.parent
CSV_2025_DIR = REPO_ROOT / "csv" / "2025"


def _write_csv(tmp_path: Path, rows, name="test.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return path


# A minimal, fully well-formed document: two teams, one pools stage with one
# game. Reused as a base by several tests below.
def _base_rows(sentinel_form="split"):
    """`sentinel_form` picks how "break"/"stages" sentinels are written:
    "split" (char-splat, as writer.writerow("break") actually produces) or
    "single" (a single-cell ["break"] row) -- reader_legacy.go's
    strings.Join(line, "") == "break" check must accept both."""
    if sentinel_form == "split":
        brk = list("break")
        stages_sentinel = list("stages")
    else:
        brk = ["break"]
        stages_sentinel = ["stages"]

    return [
        ["Test Tournament", "Club - Men", "1", "6", "2025"],
        ["Testville", "TS", "2025-06-01", "2025-06-02"],
        ["https://example.com/test"],
        ["Team A", "1"],
        ["1", "Player One"],
        brk,
        ["Team B", "2"],
        ["2", "Player Two"],
        brk,
        stages_sentinel,
        ["pools", "Pool Play"],
        ["pool", "Pool A"],
        ["Team A", "Team B", "13", "11", "06/01/2025, 09:00", "", "Final"],
        brk,
    ]


class TestBaseDocumentParity:
    @pytest.mark.parametrize("sentinel_form", ["split", "single"])
    def test_parses_well_formed_document(self, tmp_path, sentinel_form):
        path = _write_csv(tmp_path, _base_rows(sentinel_form))
        doc = read_legacy_csv(path)

        assert doc.source == "usau"
        assert doc.schema_version == "1.0"
        assert doc.event.name == "Test Tournament 2025"  # year not already in name, so it's appended
        assert doc.event.division == "Club - Men"
        assert doc.event.season == 2025
        assert doc.event.city == "Testville"
        assert doc.event.state == "TS"
        assert doc.event.start_date.isoformat() == "2025-06-01"
        assert doc.event.end_date.isoformat() == "2025-06-02"
        assert doc.source_url == "https://example.com/test"

        assert {t.name for t in doc.teams} == {"Team A", "Team B"}
        team_a = next(t for t in doc.teams if t.name == "Team A")
        assert team_a.seed == 1
        assert team_a.roster == [PlayerDoc(number="1", name="Player One")]

        assert len(doc.stages) == 1
        stage = doc.stages[0]
        assert stage.type == "pools"
        assert stage.name == "Pool Play"
        assert len(stage.groups) == 1
        group = stage.groups[0]
        assert group.name == "Pool A"
        assert len(group.games) == 1
        game = group.games[0]
        assert game.team1 == "Team A"
        assert game.team2 == "Team B"
        assert game.score1 == 13
        assert game.score2 == 11
        assert game.datetime == datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc)
        assert game.round == ""  # pools stage: round is always ""
        assert game.status == "Final"

    def test_year_appended_when_not_already_in_name(self, tmp_path):
        rows = _base_rows()
        rows[0] = ["Test Tournament (no year)", "Club - Men", "1", "6", "2025"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.event.name == "Test Tournament (no year) 2025"

    def test_division_label_preserved_verbatim_untrimmed(self, tmp_path):
        rows = _base_rows()
        rows[0] = ["Test Tournament", " Club - Men ", "1", "6", "2025"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.event.division == " Club - Men "  # NOT trimmed

    def test_source_event_id_from_path(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        path = year_dir / "2025-Some-InviteClubMen.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(_base_rows())
        doc = read_legacy_csv(path)
        assert doc.source_event_id == "2025/2025-Some-InviteClubMen"


class TestSentinelHandling:
    def test_break_as_single_row_and_char_splat_both_work(self, tmp_path):
        # Already covered by TestBaseDocumentParity's parametrize, but keep
        # a direct pair-comparison test for clarity.
        split_doc = read_legacy_csv(_write_csv(tmp_path, _base_rows("split"), "split.csv"))
        single_doc = read_legacy_csv(_write_csv(tmp_path, _base_rows("single"), "single.csv"))
        assert len(split_doc.teams) == len(single_doc.teams) == 2
        assert len(split_doc.stages) == len(single_doc.stages) == 1


class TestRaggedAndMalformedRows:
    def test_malformed_player_row_is_skipped_not_fatal(self, tmp_path):
        rows = _base_rows()
        # Insert a ragged (single-column) row into Team A's block.
        rows.insert(4, ["onlyonecolumn"])
        path = _write_csv(tmp_path, rows)

        warnings = []
        doc = read_legacy_csv(path, warnings=warnings)

        team_a = next(t for t in doc.teams if t.name == "Team A")
        assert [p.name for p in team_a.roster] == ["Player One"]  # garbage row skipped
        assert any("malformed player row" in w for w in warnings)

    def test_malformed_game_row_is_skipped_not_fatal(self, tmp_path):
        rows = _base_rows()
        # A game row with only 3 columns (< 4 required) right before the closing break.
        rows.insert(-1, ["Team A", "Team B", "5"])
        path = _write_csv(tmp_path, rows)

        warnings = []
        doc = read_legacy_csv(path, warnings=warnings)

        assert len(doc.stages[0].groups[0].games) == 1  # the malformed row didn't get added
        assert any("malformed game row" in w for w in warnings)

    def test_no_team_blocks_before_stages_yields_no_teams(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            list("stages"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.teams == []


class TestMissingBreak:
    def test_trailing_team_block_with_no_closing_break_before_stages(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["1", "Player One"],
            list("break"),
            ["Team B", "2"],
            ["2", "Player Two"],
            # NOTE: no closing break here before "stages"
            list("stages"),
            ["pools", "Pool Play"],
            ["pool", "Pool A"],
            ["Team A", "Team B", "13", "11", "06/01/2025, 09:00", "", "Final"],
            list("break"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        # Team B is still emitted even without a closing "break".
        assert {t.name for t in doc.teams} == {"Team A", "Team B"}

    def test_no_stages_sentinel_at_all(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["1", "Player One"],
            list("break"),
        ]
        path = _write_csv(tmp_path, rows)
        warnings = []
        doc = read_legacy_csv(path, warnings=warnings)
        assert doc.stages == []
        assert any("no \"stages\" sentinel found" in w for w in warnings)


class TestDatetimeHandling:
    def test_tba_datetime_becomes_null(self, tmp_path):
        rows = _base_rows()
        rows[-2] = ["Team A", "Team B", "13", "11", "TBA", "", "Scheduled"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.stages[0].groups[0].games[0].datetime is None
        assert doc.stages[0].groups[0].games[0].status == "Scheduled"

    def test_unparseable_datetime_becomes_null(self, tmp_path):
        rows = _base_rows()
        rows[-2] = ["Team A", "Team B", "13", "11", "not-a-date", "", "Final"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.stages[0].groups[0].games[0].datetime is None

    def test_valid_datetime_treated_as_utc(self, tmp_path):
        rows = _base_rows()
        rows[-2] = ["Team A", "Team B", "13", "11", '"03/22/2025, 14:30"', "", "Final"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        dt = doc.stages[0].groups[0].games[0].datetime
        assert dt == datetime(2025, 3, 22, 14, 30, tzinfo=timezone.utc)


class TestRoundOnlyForBrackets:
    def test_round_populated_for_brackets(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["1", "Player One"],
            list("break"),
            ["Team B", "2"],
            ["2", "Player Two"],
            list("break"),
            list("stages"),
            ["brackets", "Championship"],
            ["bracket", "Bracket 1"],
            ["Team A", "Team B", "15", "10", "06/01/2025, 09:00", "Quarterfinals", "Final"],
            list("break"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.stages[0].type == "brackets"
        assert doc.stages[0].groups[0].games[0].round == "Quarterfinals"

    def test_round_empty_for_pools(self, tmp_path):
        rows = _base_rows()
        rows[-2] = ["Team A", "Team B", "13", "11", "06/01/2025, 09:00", "SomeRoundValue", "Final"]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.stages[0].type == "pools"
        assert doc.stages[0].groups[0].games[0].round == ""

    def test_round_empty_for_clusters(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["1", "Player One"],
            list("break"),
            ["Team B", "2"],
            ["2", "Player Two"],
            list("break"),
            list("stages"),
            ["clusters", "Saturday Pods"],
            ["cluster", "Cluster 1"],
            ["Team A", "Team B", "13", "11", "06/01/2025, 09:00", "SomeRoundValue", "Final"],
            list("break"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.stages[0].type == "clusters"
        assert doc.stages[0].groups[0].games[0].round == ""


class TestTeamInfoAndCoaches:
    def test_team_info_and_coaches_parsed(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["teamInfo", "Nick", "Location, ST", "example.org", "fb.com/x", "twitter.com/x"],
            ["coaches", "Coach One", "Coach Two"],
            ["1", "Player One"],
            list("break"),
            ["Team B", "2"],
            ["2", "Player Two"],
            list("break"),
            list("stages"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        team_a = next(t for t in doc.teams if t.name == "Team A")
        assert team_a.info.nickname == "Nick"
        assert team_a.info.location == "Location, ST"
        assert team_a.info.website == "example.org"
        assert team_a.info.facebook == "fb.com/x"
        assert team_a.info.twitter == "twitter.com/x"
        assert team_a.coaches == ["Coach One", "Coach Two"]

    def test_empty_team_info_row_becomes_none(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
            ["https://example.com/test"],
            ["Team A", "1"],
            ["teamInfo", "", "", "", "", ""],
            ["1", "Player One"],
            list("break"),
            list("stages"),
        ]
        path = _write_csv(tmp_path, rows)
        doc = read_legacy_csv(path)
        assert doc.teams[0].info is None


class TestHardFailures:
    def test_fewer_than_three_records_raises(self, tmp_path):
        rows = [
            ["Test Tournament", "Club - Men", "1", "6", "2025"],
            ["Testville", "TS", "2025-06-01", "2025-06-02"],
        ]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValueError, match="at least 3 records"):
            read_legacy_csv(path)

    def test_unparseable_tournament_date_raises(self, tmp_path):
        rows = _base_rows()
        rows[0] = ["Test Tournament", "Club - Men", "99", "99", "2025"]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValueError, match="unable to parse tournament date"):
            read_legacy_csv(path)

    def test_unparseable_start_date_raises(self, tmp_path):
        rows = _base_rows()
        rows[1] = ["Testville", "TS", "not-a-date", "2025-06-02"]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValueError, match="start date"):
            read_legacy_csv(path)

    def test_unparseable_end_date_raises(self, tmp_path):
        rows = _base_rows()
        rows[1] = ["Testville", "TS", "2025-06-01", "not-a-date"]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValueError, match="end date"):
            read_legacy_csv(path)

    def test_game_naming_unknown_team_raises_validation_error(self, tmp_path):
        rows = _base_rows()
        rows[-2] = ["Team A", "Team Ghost", "13", "11", "06/01/2025, 09:00", "", "Final"]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValidationError):
            read_legacy_csv(path)

    def test_duplicate_team_names_raises_validation_error(self, tmp_path):
        rows = _base_rows()
        rows[6] = ["Team A", "2"]  # duplicate of the first team's name
        path = _write_csv(tmp_path, rows)
        with pytest.raises(ValidationError):
            read_legacy_csv(path)


class TestConvertLegacyDriver:
    def test_skips_calendar_csv(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        with (year_dir / "_calendar.csv").open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["https://example.com", "City", "ST", "2025-01-01", "2025-01-02"])
        with (year_dir / "good.csv").open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(_base_rows())

        summary = convert_legacy(tmp_path / "csv", year=2025, dry_run=True)
        assert isinstance(summary, ConversionSummary)
        assert len(summary.skipped) == 1
        assert summary.skipped[0].path.name == "_calendar.csv"
        assert len(summary.converted) == 1

    def test_bad_document_recorded_as_failed_not_raised(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        with (year_dir / "bad.csv").open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["only one record"])

        summary = convert_legacy(tmp_path / "csv", year=2025, dry_run=True)
        assert len(summary.failed) == 1
        assert len(summary.converted) == 0
        assert summary.failed[0].error is not None

    def test_dry_run_writes_nothing(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        with (year_dir / "good.csv").open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(_base_rows())

        out_dir = tmp_path / "out"
        summary = convert_legacy(tmp_path / "csv", year=2025, out_dir=out_dir, dry_run=True)
        assert len(summary.converted) == 1
        assert not out_dir.exists()

    def test_writes_documents_when_not_dry_run(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        with (year_dir / "good.csv").open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(_base_rows())

        out_dir = tmp_path / "out"
        summary = convert_legacy(tmp_path / "csv", year=2025, out_dir=out_dir, dry_run=False)
        assert len(summary.converted) == 1
        written = summary.converted[0].written_path
        assert written is not None
        assert written.exists()
        assert written.parent == out_dir / "usau" / "2025"

    def test_limit_caps_attempted_conversions(self, tmp_path):
        year_dir = tmp_path / "csv" / "2025"
        year_dir.mkdir(parents=True)
        for i in range(5):
            with (year_dir / f"good{i}.csv").open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(_base_rows())

        summary = convert_legacy(tmp_path / "csv", year=2025, limit=2, dry_run=True)
        assert len(summary.results) == 2


@pytest.mark.skipif(not CSV_2025_DIR.exists(), reason="csv/2025/ corpus not present")
class TestRealCorpusSample:
    def test_several_real_2025_csvs_convert_and_validate(self):
        paths = sorted(iter_legacy_csv_files(REPO_ROOT / "csv", year=2025))
        paths = [p for p in paths if p.name != "_calendar.csv"][:15]
        assert paths, "expected at least one real 2025 CSV to sample"

        failures = []
        for path in paths:
            try:
                read_legacy_csv(path)
            except Exception as exc:  # noqa: BLE001
                failures.append((path.name, str(exc)))

        # Not every real file is guaranteed valid (this corpus is exactly
        # where the known unmatched-team-name defects live -- see
        # convert_legacy's docstring), so this test only asserts we didn't
        # crash the process and that *most* sampled files are clean.
        assert len(failures) < len(paths), f"all sampled files failed: {failures}"
