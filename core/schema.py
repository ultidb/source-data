"""Pydantic v2 models mirroring CONTRACT.md section 1 exactly.

This module is the single source of truth for the wire format on the Python
side (the Go side's mirror is `api/etl/document.go`, kept in sync manually —
see CONTRACT.md section 8 for the shared fixture corpus that catches drift).

Field names use the wire (camelCase) names as pydantic aliases; the Python
attribute names are snake_case, and `populate_by_name=True` on every model
lets callers construct instances with either spelling. Always dump with
`Document.model_dump(by_alias=True, mode="json")` to get the wire shape.
"""
from __future__ import annotations

from datetime import date
from datetime import datetime as dt
from datetime import timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

StageType = Literal["pools", "brackets", "clusters"]


def _to_utc_z(value: dt) -> str:
    """RFC3339 with a literal 'Z' for UTC, matching the Go side's time.Time JSON
    encoding for the (always-UTC, per CONTRACT.md section 1) wire format."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class _DocModel(BaseModel):
    """Shared config: snake_case OR camelCase construction, camelCase dumps."""

    model_config = ConfigDict(populate_by_name=True)


class Event(_DocModel):
    name: str
    division: str
    season: int
    city: str
    state: str
    country: str = ""
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")


class TeamInfoDoc(_DocModel):
    nickname: str = ""
    location: str = ""
    website: str = ""
    facebook: str = ""
    twitter: str = ""


class PlayerDoc(_DocModel):
    number: str
    name: str


class TeamDoc(_DocModel):
    name: str
    seed: int = Field(default=0, ge=0, le=65535)
    source_team_id: str = Field(default="", alias="sourceTeamId")
    url: str = ""
    info: Optional[TeamInfoDoc] = None
    coaches: List[str] = Field(default_factory=list)
    roster: List[PlayerDoc] = Field(default_factory=list)


class GameDoc(_DocModel):
    team1: str
    team2: str
    score1: int = Field(ge=0, le=65535)
    score2: int = Field(ge=0, le=65535)
    datetime: Optional[dt] = None
    round: str = ""
    status: str

    @field_serializer("datetime", when_used="json")
    def _serialize_datetime(self, value: Optional[dt]) -> Optional[str]:
        if value is None:
            return None
        return _to_utc_z(value)


class GroupDoc(_DocModel):
    name: str
    is_championship: bool = Field(default=False, alias="isChampionship")
    games: List[GameDoc] = Field(default_factory=list)


class StageDoc(_DocModel):
    type: StageType
    name: str
    groups: List[GroupDoc] = Field(default_factory=list)


class Document(_DocModel):
    schema_version: str = Field(default="1.0", alias="schemaVersion")
    source: str
    source_event_id: str = Field(alias="sourceEventId")
    source_url: str = Field(alias="sourceUrl")
    scraped_at: dt = Field(alias="scrapedAt")
    event: Event
    teams: List[TeamDoc] = Field(default_factory=list)
    stages: List[StageDoc] = Field(default_factory=list)

    @field_serializer("scraped_at", when_used="json")
    def _serialize_scraped_at(self, value: dt) -> str:
        return _to_utc_z(value)

    @model_validator(mode="after")
    def _validate_team_references(self) -> "Document":
        seen: dict[str, str] = {}
        for team in self.teams:
            key = team.name.casefold()
            if key in seen:
                raise ValueError(
                    f"duplicate team name (case-insensitive): {team.name!r} "
                    f"collides with {seen[key]!r}"
                )
            seen[key] = team.name

        for stage in self.stages:
            for group in stage.groups:
                for game in group.games:
                    for label, team_name in (("team1", game.team1), ("team2", game.team2)):
                        if team_name.casefold() not in seen:
                            raise ValueError(
                                f"game {label} {team_name!r} (stage={stage.name!r}, "
                                f"group={group.name!r}) does not match any teams[].name"
                            )
        return self
