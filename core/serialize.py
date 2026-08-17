"""Maps between `models.py` domain objects and the `core.schema.Document`
wire format.

`tournament_to_document` is the forward direction, used by every source's
scrape pipeline. `document_to_tournament` is the inverse, used by the
equivalence test that Phase 3 needs to prove the JSON path and the legacy CSV
path agree.

Known round-trip lossiness (documented here rather than hidden):

- `GroupDoc.is_championship` is always written `False` by
  `tournament_to_document`, because `models.py`'s `Pool`/`Bracket`/`Cluster`
  classes have no such field today (see MULTI-SOURCE-REDESIGN.md: "the CSV
  format has no way to express today"). `document_to_tournament` therefore
  can't recover a `True` value either -- it's simply not representable in the
  domain model yet.
- `Pool.teams` (the pool's team roster independent of its games) is not part
  of the wire format. `document_to_tournament` reconstructs it as the
  distinct teams appearing in that pool's games, sorted by seed; a pool with
  teams but zero games round-trips with an empty `Pool.teams` list.
- `Game.round == None` and `Game.round == ""` both serialize to the wire
  format's `""` (round is a non-nullable string on the wire). The inverse
  maps `""` back to `None`, so an original `Game.round == ""` (as opposed to
  `None`) is not distinguishable after a round trip -- both become `None`.
- `Team.id` (source-specific link-derived id) round-trips through
  `TeamDoc.source_team_id`, coerced to `str`. A `Team.id` that was `""`
  (rather than `None`) collapses to `None` after a round trip, since
  `source_team_id == ""` is treated as "absent" on the way back.
- `Team.nickname` (a legacy attribute on `models.Team`, separate from
  `TeamInfo.nickname`) is not part of the wire format and is not
  round-tripped; only `TeamInfo.nickname` (-> `TeamDoc.info.nickname`) is.
- Naive `datetime` values are treated as UTC wall-clock on the way out (per
  CONTRACT.md section 1) and stripped back to naive UTC wall-clock on the way
  in, so the *wall clock* round-trips but timezone-awareness itself does not:
  an originally-aware non-UTC datetime is not supported by the domain model
  and is normalized to its UTC wall-clock reading.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional, Union

import models
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

DateLike = Union[str, date, datetime]


def _to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"cannot convert {value!r} to a date")


def _normalize_datetime(value) -> Optional[datetime]:
    """None -> None (TBA). Naive datetimes are treated as UTC wall-clock,
    matching today's Go behavior (see CONTRACT.md section 1)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _from_wire_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """Inverse of _normalize_datetime: strip tzinfo back to a naive
    UTC-wall-clock datetime, matching what the current scraper produces."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _coerce_score(value) -> int:
    """Scores are usually already-digit strings or ints by the time a Game
    reaches here, but forfeit markers ("W"/"L"/"Win"/"win"/...) can still
    show up raw depending on the source, so fall back to the same
    conversion `parseBracketGame` applies inline today."""
    try:
        return int(value)
    except (TypeError, ValueError):
        from core.parsing import convertNonDigitScore

        return convertNonDigitScore(value)


def _team_info_doc(info) -> Optional[TeamInfoDoc]:
    if info is None:
        return None
    fields = (
        info.nickname or "",
        info.location or "",
        info.website or "",
        info.facebook or "",
        info.twitter or "",
    )
    if all(f == "" for f in fields):
        return None
    return TeamInfoDoc(
        nickname=fields[0],
        location=fields[1],
        website=fields[2],
        facebook=fields[3],
        twitter=fields[4],
    )


def _team_doc(team: "models.Team") -> TeamDoc:
    info = getattr(team, "info", None)
    coaches = list(info.coaches) if info is not None and info.coaches else []
    return TeamDoc(
        name=team.name,
        seed=team.seed or 0,
        source_team_id="" if team.id is None else str(team.id),
        url=team.url or "",
        info=_team_info_doc(info),
        coaches=coaches,
        roster=[
            PlayerDoc(number=str(p.number), name=p.name) for p in (team.roster or [])
        ],
    )


def _game_doc(game: "models.Game") -> GameDoc:
    return GameDoc(
        team1=game.teamA.name,
        team2=game.teamB.name,
        score1=_coerce_score(game.teamA_score),
        score2=_coerce_score(game.teamB_score),
        datetime=_normalize_datetime(game.datetime),
        round=game.round or "",
        status=game.status or "",
    )


def _stage_doc(stage) -> StageDoc:
    if isinstance(stage, models.Pools):
        groups = [
            GroupDoc(
                name=pool.name,
                is_championship=False,
                games=[_game_doc(g) for g in (pool.games or [])],
            )
            for pool in stage.pools
        ]
        return StageDoc(type="pools", name=stage.name, groups=groups)

    if isinstance(stage, models.Brackets):
        groups = [
            GroupDoc(
                name=bracket.name,
                is_championship=False,
                games=[_game_doc(g) for g in (bracket.games or [])],
            )
            for bracket in stage.brackets
        ]
        return StageDoc(type="brackets", name=stage.name, groups=groups)

    if isinstance(stage, models.Clusters):
        groups = [
            GroupDoc(
                name=cluster.name,
                is_championship=False,
                games=[_game_doc(g) for g in (cluster.games or [])],
            )
            for cluster in stage.clusters
        ]
        return StageDoc(type="clusters", name=stage.name, groups=groups)

    raise TypeError(f"unrecognized stage type: {type(stage)!r}")


def tournament_to_document(
    tournament: "models.Tournament",
    *,
    source: str,
    source_event_id: str,
    source_url: str,
    scraped_at: Optional[datetime] = None,
) -> Document:
    """Map a `models.Tournament` (and the source-provided identity fields
    that the domain model itself doesn't carry) onto the wire `Document`."""
    start = _to_date(tournament.startDate)
    end = _to_date(tournament.endDate)

    if scraped_at is None:
        scraped_at = datetime.now(timezone.utc)

    event = Event(
        name=tournament.name,
        # Legacy/compound label (e.g. "College - Men") when the source
        # doesn't set `gender` below; a clean division name (e.g. "club")
        # when it does. Either way this is the raw source value, preserved
        # verbatim.
        division=tournament.division,
        season=start.year,
        city=tournament.city or "",
        state=tournament.state or "",
        # models.Tournament has no country attribute -- USAU is domestic and
        # never needed one. International sources set it on the instance, so
        # read it optionally rather than hardcoding "" and stranding the field.
        country=getattr(tournament, "country", "") or "",
        # models.Tournament has no gender attribute either, for the same
        # reason -- only a source that knows division and gender as two
        # separate facts (WFDF) sets this on the instance. Read it
        # optionally, following the exact same pattern as country above.
        gender=getattr(tournament, "gender", "") or "",
        start_date=start,
        end_date=end,
    )

    teams = [_team_doc(t) for t in tournament.teams]
    stages = [_stage_doc(s) for s in tournament.stages]

    return Document(
        source=source,
        source_event_id=source_event_id,
        source_url=source_url,
        scraped_at=scraped_at,
        event=event,
        teams=teams,
        stages=stages,
    )


def _team_from_doc(team_doc: TeamDoc) -> "models.Team":
    team = models.Team(
        team_doc.name,
        team_doc.seed,
        team_doc.url,
        id=(team_doc.source_team_id or None),
    )
    team.roster = [models.Player(p.number, p.name) for p in team_doc.roster]
    if team_doc.info is not None or team_doc.coaches:
        info = team_doc.info
        team.info = models.TeamInfo(
            nickname=info.nickname if info else "",
            location=info.location if info else "",
            coaches=list(team_doc.coaches),
            website=info.website if info else "",
            facebook=info.facebook if info else "",
            twitter=info.twitter if info else "",
        )
    return team


def _game_from_doc(game_doc: GameDoc, teams_by_name: dict) -> "models.Game":
    team_a = teams_by_name[game_doc.team1.casefold()]
    team_b = teams_by_name[game_doc.team2.casefold()]
    return models.Game(
        team_a,
        team_b,
        game_doc.score1,
        game_doc.score2,
        _from_wire_datetime(game_doc.datetime),
        game_doc.status,
        round=(game_doc.round or None),
    )


def document_to_tournament(doc: Document) -> "models.Tournament":
    """Inverse of `tournament_to_document`. See the module docstring for the
    fields that cannot be perfectly round-tripped."""
    teams = [_team_from_doc(t) for t in doc.teams]
    teams_by_name = {t.name.casefold(): t for t in teams}

    stages = []
    for stage_doc in doc.stages:
        if stage_doc.type == "pools":
            pools = []
            for group in stage_doc.groups:
                games = [_game_from_doc(g, teams_by_name) for g in group.games]
                pool_team_ids = {}
                for g in games:
                    pool_team_ids[id(g.teamA)] = g.teamA
                    pool_team_ids[id(g.teamB)] = g.teamB
                pool_teams = sorted(pool_team_ids.values(), key=lambda t: t.seed)
                pools.append(models.Pool(group.name, pool_teams, games))
            stages.append(models.Pools(stage_doc.name, pools))

        elif stage_doc.type == "brackets":
            brackets = [
                models.Bracket(
                    group.name, [_game_from_doc(g, teams_by_name) for g in group.games]
                )
                for group in stage_doc.groups
            ]
            stages.append(models.Brackets(stage_doc.name, brackets))

        elif stage_doc.type == "clusters":
            clusters = [
                models.Cluster(
                    group.name, [_game_from_doc(g, teams_by_name) for g in group.games]
                )
                for group in stage_doc.groups
            ]
            stages.append(models.Clusters(stage_doc.name, clusters))

        else:
            raise ValueError(f"unknown stage type: {stage_doc.type!r}")

    start = doc.event.start_date
    end = doc.event.end_date
    tournament_datetime = datetime.combine(start, time.min)

    return models.Tournament(
        doc.event.name,
        doc.source_url,
        doc.event.city,
        doc.event.state,
        start.isoformat(),
        end.isoformat(),
        teams,
        tournament_datetime,
        doc.event.division,
        stages,
    )
