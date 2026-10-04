from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer


class Envelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    site_id: str = Field(min_length=1, max_length=128)
    kind: Literal['event', 'alarm']
    source_guid: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=256)
    occurred_at: datetime
    updated_at: datetime
    message: str = Field(default='', max_length=16000)
    description: str = Field(default='', max_length=32000)
    state: str = Field(default='', max_length=128)
    priority: str = Field(default='', max_length=128)
    camera_id: str | None = None
    location: str = Field(default='', max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)
    deleted: bool = False

    @field_serializer('occurred_at', 'updated_at')
    def timestamp_string(self, value):
        return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')

    @field_validator('occurred_at', 'updated_at')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError('Timezone is required')
        return value.astimezone(timezone.utc)


class Mapping(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_.]{0,255}$')
    type: Literal['string', 'integer', 'number', 'boolean'] = 'string'
    required: bool = False
    enum: dict[str, str] = Field(default_factory=dict)
    format: Literal['value', 'json', 'xml'] = 'value'
    selector: str = Field(default='', pattern=r'^[a-zA-Z0-9_./-]{0,256}$')


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    priority: int = Field(default=0, ge=-1000, le=1000)
    match: dict[str, str]
    mapping: dict[str, Mapping] = Field(default_factory=dict, max_length=64)
    family: str = Field(default='unclassified', max_length=128)
    context: str = Field(default='', max_length=4000)

    @field_validator('match')
    @classmethod
    def safe_match(cls, value):
        if not value or set(value) - {'site_id', 'event_type', 'source_id', 'kind', 'message'}:
            raise ValueError('Match requires site_id/event_type/source_id/kind/message exact values')
        return value


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(default='', max_length=2000)
    site_id: str | None = None
    source_id: str | None = None
    event_type: str | None = Field(default=None, max_length=256)
    event_types: list[str] | None = Field(default=None, max_length=500)
    source_ids: list[str] | None = Field(default=None, max_length=5000)
    entities: list[str] | None = Field(default=None, max_length=100)
    facts: list[list[str]] | None = Field(default=None, max_length=20)
    collapse: bool = False
    # Only the most recent match of each camera (source), newest first: "where was this person last seen, per camera".
    latest_per_source: bool = False
    kind: Literal['event', 'alarm'] | None = None
    family: str | None = None
    state: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)

    @field_validator('start', 'end')
    @classmethod
    def timezone_required(cls, value):
        return Envelope.timezone_required(value) if value else value


class AskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default='', max_length=2000)
    tz_offset_minutes: int = Field(default=420, ge=-840, le=840)
    latest_per_source: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    site_id: str = Field(min_length=1, max_length=128)
    reason: Literal['alarm_changed', 'configuration_changed', 'scheduled', 'startup']
    alarm_id: str | None = Field(default=None, max_length=128)
