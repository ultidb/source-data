"""Legacy CSV -> wire-format `Document` converter (Phase 3 gate,
MULTI-SOURCE-REDESIGN.md).

`read_legacy_csv` mirrors `ultidb/api/etl/reader_legacy.go`'s
`ReadLegacyDocument` exactly -- same sentinel handling, same char-splat
detection, same per-record field layout, same round-only-for-brackets rule,
same TBA/unparseable-datetime handling. Where the Go source is authoritative
this module follows its structure function-for-function (see the docstrings
below for the correspondence) so the two stay easy to diff against each
other by hand.

Two deliberate differences from the Go reader, both noted inline where they
occur:

- Malformed *documents* (fewer than 3 records; unparseable tournament,
  start, or end dates) raise `ValueError`, matching Go's `(nil, error)`
  case. Malformed *rows* within an otherwise-parseable document are skipped
  and appended to a `warnings` list (mutated in place), matching Go's
  `Document.Warnings` -- except `core.schema.Document` has no `warnings`
  field (it isn't part of the wire format the Go writer accepts), so
  warnings are a side channel here rather than a document attribute.
- A team named in a game with no matching roster block -- which the Go
  writer would catch downstream in `applyDocument` -- is caught immediately
  here as a `pydantic.ValidationError`, because `core.schema.Document`
  validates team references at construction time (see
  `core/schema.py`'s `_validate_team_references`). This is *earlier* than
  Go's equivalent check, not different in kind: both reject the same
  malformed documents, this one just does it before the bytes are ever
  written to disk. See `convert_legacy`'s docstring for how the driver
  handles this (record + continue, never invent data to satisfy
  validation).
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from core.emit import _sanitize_key
from core.schema import (
    Document,
    Event,
    GameDoc,
    GroupDoc,
    PlayerDoc,
    StageDoc,
    TeamDoc,
    TeamInfoDoc,
)

log = logging.getLogger(__name__)

# Plural stage-type -> singular group-header keyword, mirroring
# reader_legacy.go's singularStageSeparator.
_STAGE_SINGULAR = {"pools": "pool", "brackets": "bracket", "clusters": "cluster"}


def _joined(line: List[str]) -> str:
    """Sentinels are char-splatted by the Python writer
    (`writer.writerow("break")` writes b,r,e,a,k); "".join(line) == "break"
    also matches the un-splatted single-cell form ["break"], so this one
    check covers both -- mirrors reader_legacy.go's
    `strings.Join(line, "") == "break"`."""
    return "".join(line)


def _legacy_source_event_id(path) -> str:
    """csv/2025/2025-Annual-Magic-City-InviteCollegeMen.csv ->
    2025/2025-Annual-Magic-City-InviteCollegeMen.

    reader_legacy.go's legacySourceEventID does this with literal
    TrimPrefix("/")/TrimPrefix("csv/")/TrimSuffix(".csv") string surgery on
    the repo-relative path it's always called with. We get the same result
    more robustly (works for absolute paths too, which the Go version
    doesn't handle) by taking the immediate parent directory name (the
    year) and the file stem -- equivalent for the csv/<year>/<file>.csv
    layout this reader is always invoked against.
    """
    p = Path(path)
    return f"{p.parent.name}/{p.stem}"


def _parse_legacy_event_info(info: List[str]):
    """records[0] = [name, divisionLabel, day, month, year].

    Mirrors reader_legacy.go's parseLegacyEventInfo. Returns
    (name, division, season). Division is returned RAW -- verbatim, not
    trimmed or lowercased (CONTRACT.md section 1).
    """
    if len(info) < 5:
        raise ValueError(f"tournament info line has {len(info)} fields, need at least 5: {info!r}")

    name = info[0].strip()
    try:
        day = int(info[2].strip())
        month = int(info[3].strip())
        year = int(info[4].strip())
        event_date = date(year, month, day)
    except (ValueError, IndexError) as exc:
        # Note: Go's time.Parse("2-1-2006", ...) is more lenient than
        # Python's date() constructor about out-of-range days (e.g. Go
        # normalizes "30-2-2025" by rolling over into March; Python raises).
        # No real row in the 2014-2026 corpus exercises this, so we choose
        # to fail loudly rather than replicate Go's rollover -- see the
        # task report for detail.
        raise ValueError(f"unable to parse tournament date from {info!r}: {exc}") from exc

    year_string = str(event_date.year)
    if year_string not in name:
        name = f"{name} {year_string}"

    return name, info[1], event_date.year


def _parse_team_info_line(line: List[str]) -> Optional[TeamInfoDoc]:
    """['teamInfo', nickname, location, website, facebook, twitter, ...]."""

    def field(idx: int) -> str:
        return line[idx] if idx < len(line) else ""

    info = TeamInfoDoc(
        nickname=field(1), location=field(2), website=field(3), facebook=field(4), twitter=field(5)
    )
    if info == TeamInfoDoc():
        return None
    return info


@dataclass
class _TeamBuilder:
    name: str
    seed: int = 0
    info: Optional[TeamInfoDoc] = None
    coaches: List[str] = field(default_factory=list)
    roster: List[PlayerDoc] = field(default_factory=list)

    def to_doc(self) -> TeamDoc:
        return TeamDoc(
            name=self.name, seed=self.seed, info=self.info, coaches=self.coaches, roster=self.roster
        )


def _parse_seed(raw: str) -> int:
    """Go: strconv.ParseInt(strings.TrimSpace(line[1]), 10, 16) -- on
    failure, or out of uint16 range, the seed silently stays the zero
    value."""
    try:
        v = int(raw.strip())
    except ValueError:
        return 0
    return v if 0 <= v <= 65535 else 0


def _parse_teams_and_stages(lines: List[List[str]], warnings: List[str]):
    """records[3:]: team blocks, then (after a "stages" sentinel) the
    stages/groups/games section. Mirrors reader_legacy.go's
    parseLegacyTeamsAndStages."""
    teams: List[TeamDoc] = []
    current: Optional[_TeamBuilder] = None
    next_team = True
    stages_idx = -1

    for i, line in enumerate(lines):
        if _joined(line) == "stages":
            stages_idx = i
            break

        if next_team:
            name = line[0].strip() if len(line) >= 1 else ""
            seed = _parse_seed(line[1]) if len(line) >= 2 else 0
            current = _TeamBuilder(name=name, seed=seed)
            next_team = False
            continue

        if current is None:
            warnings.append(f"team section line {i}: line outside any team block, skipped: {line!r}")
            continue

        if len(line) >= 1 and line[0] == "teamInfo":
            current.info = _parse_team_info_line(line)
            continue

        if len(line) >= 1 and line[0] == "coaches":
            for coach in line[1:]:
                coach = coach.strip()
                if coach:
                    current.coaches.append(coach)
            continue

        if _joined(line) == "break":
            teams.append(current.to_doc())
            current = None
            next_team = True
            continue

        # player line: [number, playerName] -- neither field is stripped,
        # matching reader_legacy.go's DocPlayer{Number: line[0], Name: line[1]}.
        if len(line) < 2:
            warnings.append(f"team section line {i}: malformed player row, skipped: {line!r}")
            continue
        current.roster.append(PlayerDoc(number=line[0], name=line[1]))

    # A trailing team block with no closing "break" before "stages" (or
    # before the input ends) must still be emitted.
    if current is not None:
        teams.append(current.to_doc())

    stages: List[StageDoc] = []
    if stages_idx >= 0:
        stages = _parse_legacy_stages(lines[stages_idx + 1 :], warnings)
    else:
        warnings.append('no "stages" sentinel found; document has no stages section')

    return teams, stages


@dataclass
class _GroupBuilder:
    name: str
    games: List[GameDoc] = field(default_factory=list)


@dataclass
class _StageBuilder:
    type: str
    name: str
    groups: List[_GroupBuilder] = field(default_factory=list)


def _parse_legacy_stages(lines: List[List[str]], warnings: List[str]) -> List[StageDoc]:
    """Mirrors reader_legacy.go's parseLegacyStages: a stage's groups end at
    a "break" line, or at the next stage header, whichever comes first
    (the next-stage-header stop is a deliberate defensive addition on the Go
    side, not a reproduction of the original CSV-writer bug -- see that
    function's comment)."""
    stages: List[StageDoc] = []
    stage: Optional[_StageBuilder] = None
    group: Optional[_GroupBuilder] = None

    def flush_group():
        nonlocal group
        if stage is not None and group is not None:
            stage.groups.append(group)
            group = None

    def flush_stage():
        nonlocal stage
        flush_group()
        if stage is not None:
            stages.append(
                StageDoc(
                    type=stage.type,
                    name=stage.name,
                    groups=[
                        GroupDoc(name=g.name, is_championship=False, games=g.games)
                        for g in stage.groups
                    ],
                )
            )
            stage = None

    for i, line in enumerate(lines):
        if len(line) == 0:
            continue

        if line[0] in ("pools", "brackets", "clusters"):
            flush_stage()
            name = line[1].strip() if len(line) >= 2 else ""
            stage = _StageBuilder(type=line[0], name=name)
            continue

        if _joined(line) == "break":
            flush_stage()
            continue

        if stage is None:
            warnings.append(f"stages section line {i}: line outside any stage, skipped: {line!r}")
            continue

        group_separator = _STAGE_SINGULAR.get(stage.type, "")
        if group_separator and line[0] == group_separator:
            flush_group()
            name = line[1].strip() if len(line) >= 2 else ""
            # isChampionship is always False from the legacy format -- the
            # CSV cannot express it.
            group = _GroupBuilder(name=name)
            continue

        if group is None:
            warnings.append(f"stages section line {i}: game row outside any group, skipped: {line!r}")
            continue

        game = _parse_legacy_game_row(line, stage.type)
        if game is None:
            warnings.append(f"stages section line {i}: malformed game row, skipped: {line!r}")
            continue
        group.games.append(game)

    flush_stage()
    return stages


def _parse_uint16(raw: str) -> int:
    """Go: strconv.ParseUint(line[N], 10, 16) -- deliberately NOT
    whitespace-trimmed first (unlike the seed field), so " 5" fails in Go
    and must fail here too, even though Python's int() would otherwise
    silently accept it."""
    if raw != raw.strip():
        return 0
    try:
        v = int(raw)
    except ValueError:
        return 0
    return v if 0 <= v <= 65535 else 0


def _parse_legacy_game_row(line: List[str], stage_type: str) -> Optional[GameDoc]:
    """[team1, team2, score1, score2, datetime, round, status]. Bounds-checks
    every index; a row with fewer than 4 usable columns is rejected."""
    if len(line) < 4:
        return None

    team1 = line[0].strip()
    team2 = line[1].strip()
    score1 = _parse_uint16(line[2])
    score2 = _parse_uint16(line[3])

    game_datetime = None
    if len(line) >= 5:
        raw = line[4].strip(' "')
        try:
            parsed = datetime.strptime(raw, "%m/%d/%Y, %H:%M")
            game_datetime = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            game_datetime = None  # includes the literal "TBA"

    # Round is only populated when the enclosing stage type is "brackets" --
    # pools and clusters get "". Preserves reader_legacy.go's
    # `if stageType == "brackets"` behavior exactly.
    round_ = ""
    if stage_type == "brackets" and len(line) >= 6:
        round_ = line[5].strip()

    status = ""
    if len(line) >= 7:
        status = line[6].strip()

    return GameDoc(
        team1=team1, team2=team2, score1=score1, score2=score2,
        datetime=game_datetime, round=round_, status=status,
    )


def read_legacy_csv(path, *, warnings: Optional[List[str]] = None) -> Document:
    """Translate one legacy CSV file into a validated wire-format
    `Document`, matching `ultidb/api/etl/reader_legacy.go`'s
    `ReadLegacyDocument` field-for-field.

    Raises `ValueError` for the defects Go treats as document-fatal (fewer
    than 3 records; unparseable tournament/start/end dates) and
    `pydantic.ValidationError` if the assembled document fails wire-format
    validation (duplicate team names, or a game naming a team absent from
    the roster section -- both real, both known to occur in the corpus).
    Malformed individual rows are skipped and appended to `warnings`
    (mutated in place if provided, otherwise collected and discarded).
    """
    if warnings is None:
        warnings = []

    path = Path(path)
    with path.open(newline="", encoding="utf-8") as f:
        records = list(csv.reader(f))

    if len(records) < 3:
        raise ValueError(
            f"legacy document requires at least 3 records (info/location/url), got {len(records)}"
        )

    name, division, season = _parse_legacy_event_info(records[0])

    location = records[1]
    if len(location) < 4:
        raise ValueError(
            f"legacy document location/date line has {len(location)} fields, "
            f"need at least 4: {location!r}"
        )
    city = location[0].strip()
    state = location[1].strip()

    start_raw = location[2].strip()
    try:
        start_date = date.fromisoformat(start_raw)
    except ValueError as exc:
        raise ValueError(f"unable to parse tournament start date {start_raw!r}: {exc}") from exc

    end_raw = location[3].strip()
    try:
        end_date = date.fromisoformat(end_raw)
    except ValueError as exc:
        raise ValueError(f"unable to parse tournament end date {end_raw!r}: {exc}") from exc

    source_url = ""
    if len(records[2]) >= 1:
        source_url = records[2][0].strip()

    teams, stages = _parse_teams_and_stages(records[3:], warnings)

    event = Event(
        name=name, division=division, season=season, city=city, state=state,
        country="", start_date=start_date, end_date=end_date,
    )

    doc = Document(
        schema_version="1.0",
        source="usau",
        source_event_id=_legacy_source_event_id(path),
        source_url=source_url,
        scraped_at=datetime.now(timezone.utc),
        event=event,
        teams=teams,
        stages=stages,
    )

    for w in warnings:
        log.debug("%s: %s", path, w)

    return doc


# --------------------------------------------------------------------------
# Directory-walking driver
# --------------------------------------------------------------------------


@dataclass
class ConversionResult:
    path: Path
    outcome: str  # "converted" | "failed" | "skipped"
    document: Optional[Document] = None
    written_path: Optional[Path] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class ConversionSummary:
    results: List[ConversionResult] = field(default_factory=list)

    @property
    def converted(self) -> List[ConversionResult]:
        return [r for r in self.results if r.outcome == "converted"]

    @property
    def failed(self) -> List[ConversionResult]:
        return [r for r in self.results if r.outcome == "failed"]

    @property
    def skipped(self) -> List[ConversionResult]:
        return [r for r in self.results if r.outcome == "skipped"]


def iter_legacy_csv_files(csv_root, year: Optional[int] = None) -> Iterator[Path]:
    """Yield every *.csv under csv_root/<year>/ (or every numbered year
    directory, sorted, if `year` is None) in the csv/<year>/<file>.csv
    layout. Does not filter `_calendar.csv` -- that's convert_legacy's job,
    since skipping it is a reportable outcome, not silent."""
    csv_root = Path(csv_root)
    if year is not None:
        year_dirs = [csv_root / str(year)]
    else:
        year_dirs = sorted(
            (p for p in csv_root.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: p.name,
        )
    for year_dir in year_dirs:
        if not year_dir.exists():
            continue
        for path in sorted(year_dir.glob("*.csv")):
            yield path


def _write_legacy_document(doc: Document, out_dir: Path) -> Path:
    """Write doc to out_dir/<source>/<year>/<key>.json.

    Deliberately does NOT call core.emit.write_document: that function
    always nests output under base_dir/"data"/<source>/<year>/, which is
    right for the live-scrape pipeline (base_dir defaults to the repo root,
    so the visible result is data/<source>/...). Here, `--out`'s own
    *default value* is already "data/" (see cli.py's convert-legacy) --
    routing that through write_document would double it to data/data/....
    So `--out` is treated as the output root directly, and only the
    filename-sanitizing logic is reused from core.emit for consistency.
    """
    year = doc.event.start_date.year
    key = _sanitize_key(doc.source_event_id)
    out_path = Path(out_dir) / doc.source / str(year) / f"{key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = doc.model_dump(by_alias=True, mode="json")
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def convert_legacy(
    csv_root,
    *,
    year: Optional[int] = None,
    out_dir: Optional[Path] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> ConversionSummary:
    """Convert csv_root/<year>/*.csv (or the whole corpus if `year` is
    None) into wire-format Documents.

    `_calendar.csv` is always skipped (recorded with outcome "skipped").
    `limit`, if given, caps the number of *attempted* conversions (skips
    don't count against it) -- for spot checks, not for excluding files.
    Unless `dry_run`, each successfully-converted document is written to
    out_dir/<source>/<year>/<key>.json.

    Never raises for a single bad file: a document that fails to parse (bad
    dates, too few records) or fails wire-format validation (e.g. a game
    naming a team with no roster block -- real, and known to occur in this
    corpus) is recorded as outcome "failed" with the reason, and the driver
    moves on. This function does not, and must not, invent data to make a
    bad document validate.
    """
    out_dir = Path(out_dir) if out_dir is not None else Path("data")
    summary = ConversionSummary()
    count = 0

    for path in iter_legacy_csv_files(csv_root, year):
        if path.name == "_calendar.csv":
            summary.results.append(ConversionResult(path=path, outcome="skipped"))
            continue

        if limit is not None and count >= limit:
            break
        count += 1

        warnings: List[str] = []
        try:
            doc = read_legacy_csv(path, warnings=warnings)
        except Exception as exc:  # noqa: BLE001 -- one bad file must not kill the run
            summary.results.append(
                ConversionResult(path=path, outcome="failed", error=str(exc), warnings=warnings)
            )
            continue

        written = None
        if not dry_run:
            written = _write_legacy_document(doc, out_dir)

        summary.results.append(
            ConversionResult(
                path=path, outcome="converted", document=doc, written_path=written, warnings=warnings
            )
        )

    return summary
