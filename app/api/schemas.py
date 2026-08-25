"""Request and response bodies. Enums come straight from app/enums.py."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import (
    ChallengeStatus,
    Generator,
    Metric,
    PeriodType,
    Unit,
    WinRule,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth / users
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    # No rules on the password itself, deliberately.
    password: str = Field(min_length=1)
    display_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("Username cannot be blank")
        if any(char.isspace() for char in value):
            raise ValueError("Username cannot contain spaces")
        return value


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        return value.strip().lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


class UserOut(ORMModel):
    id: int
    user_uuid: str
    username: str
    display_name: str
    avatar_object_key: Optional[str] = None
    created_at: Optional[datetime] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class ExternalIdentityOut(BaseModel):
    linked: bool
    email: Optional[str] = None
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsOut(ORMModel):
    timezone: str
    distance_unit: str
    notifications_enabled: bool


class SettingsUpdate(BaseModel):
    timezone: Optional[str] = Field(default=None, max_length=64)
    distance_unit: Optional[Unit] = None
    notifications_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------

class FriendRequestCreate(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        return value.strip().lower()


class RespondRequest(BaseModel):
    accept: bool


class FriendRequestOut(BaseModel):
    id: int
    status: str
    created_at: Optional[datetime] = None
    requested_by: UserOut
    other_user: UserOut


class FriendRequestsOut(BaseModel):
    incoming: list[FriendRequestOut]
    outgoing: list[FriendRequestOut]


class FriendRequestResult(BaseModel):
    result: str  # created | already_pending | already_friends | accepted
    request: Optional[FriendRequestOut] = None


# ---------------------------------------------------------------------------
# Challenges
# ---------------------------------------------------------------------------

class ChallengeCreate(BaseModel):
    name: str = Field(min_length=1)
    description: Optional[str] = None
    metric: Metric
    unit: Optional[Unit] = None
    generator: Generator = Generator.RANDOM
    generator_config: Optional[dict[str, Any]] = None
    target_min: float = Field(gt=0)
    target_max: float = Field(gt=0)
    win_rule: WinRule = WinRule.HIGHEST_ABOVE_TARGET
    win_rule_config: Optional[dict[str, Any]] = None
    period_type: PeriodType = PeriodType.DAILY
    starts_at: Optional[datetime] = None  # defaults to now (UTC)
    ends_at: Optional[datetime] = None
    # Usernames of friends to invite
    invite: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranges_and_unit(self):
        if self.target_max < self.target_min:
            raise ValueError("target_max must be greater than or equal to target_min")
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")

        # Steps are steps; distance is km or miles.
        if self.unit is None:
            self.unit = Unit.STEPS if self.metric == Metric.STEPS else Unit.KM
        elif self.metric == Metric.STEPS and self.unit != Unit.STEPS:
            raise ValueError("A steps challenge must use the 'steps' unit")
        elif self.metric == Metric.DISTANCE and self.unit == Unit.STEPS:
            raise ValueError("A distance challenge must use 'km' or 'mi'")
        return self


class ChallengeUpdate(BaseModel):
    """Only the creator may call this. Changes apply to future periods."""
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    generator: Optional[Generator] = None
    generator_config: Optional[dict[str, Any]] = None
    target_min: Optional[float] = Field(default=None, gt=0)
    target_max: Optional[float] = Field(default=None, gt=0)
    win_rule: Optional[WinRule] = None
    win_rule_config: Optional[dict[str, Any]] = None
    ends_at: Optional[datetime] = None
    status: Optional[ChallengeStatus] = None


class InviteRequest(BaseModel):
    invite: list[str] = Field(min_length=1)


class ParticipantOut(BaseModel):
    user: UserOut
    status: str
    joined_at: Optional[datetime] = None


class ChallengeOut(ORMModel):
    id: int
    challenge_uuid: str
    name: str
    description: Optional[str] = None
    metric: str
    unit: str
    generator: str
    generator_config: Optional[dict[str, Any]] = None
    target_min: float
    target_max: float
    win_rule: str
    win_rule_config: Optional[dict[str, Any]] = None
    period_type: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    status: str
    created_at: Optional[datetime] = None
    creator: UserOut
    my_status: Optional[str] = None


class EntryOut(ORMModel):
    id: int
    entry_uuid: str
    period_id: int
    user_id: int
    value: float
    note: Optional[str] = None
    is_winner: bool
    target_reached_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    images: list["EntryImageOut"] = Field(default_factory=list)
    user: Optional[UserOut] = None


class PeriodOut(ORMModel):
    id: int
    challenge_id: int
    period_index: int
    period_start: datetime
    period_end: datetime
    target_value: float
    win_rule: str
    winner_evaluated_at: Optional[datetime] = None
    entries: list[EntryOut] = Field(default_factory=list)


class ChallengeDetailOut(BaseModel):
    challenge: ChallengeOut
    participants: list[ParticipantOut]
    current_period: Optional[PeriodOut] = None


# ---------------------------------------------------------------------------
# Entries and images
# ---------------------------------------------------------------------------

class EntryUpsert(BaseModel):
    value: float = Field(ge=0)
    note: Optional[str] = None


class EntryImageOut(ORMModel):
    id: int
    image_uuid: str
    entry_id: int
    image_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    created_at: Optional[datetime] = None


class PresignedUrlOut(BaseModel):
    image_uuid: Optional[str] = None
    url: str
    expires_in: int


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class LeaderboardRow(BaseModel):
    user_id: int
    username: str
    display_name: str
    participant_status: str
    entries_logged: int
    wins: int
    targets_hit: int
    total_value: float
    best_value: float
    average_value: float
    current_streak: int
    longest_streak: int


class TimeseriesEntry(BaseModel):
    user_id: int
    username: str
    display_name: str
    value: float
    is_winner: bool
    hit_target: bool


class TimeseriesPoint(BaseModel):
    period_id: int
    period_index: int
    period_start: datetime
    period_end: datetime
    target_value: float
    win_rule: str
    entries: list[TimeseriesEntry]


class UserOverviewOut(BaseModel):
    challenges_active: int
    challenges_total: int
    entries_logged: int
    wins: int
    targets_hit: int
    totals_by_metric: dict[str, float]
    generated_at: datetime


EntryOut.model_rebuild()


# ---------------------------------------------------------------------------
# Home screen
# ---------------------------------------------------------------------------

class PeriodSummary(ORMModel):
    """A period without its entries — enough to draw a progress ring."""
    id: int
    period_index: int
    period_start: datetime
    period_end: datetime
    target_value: float
    win_rule: str


class StandingRow(BaseModel):
    """One participant's number for the period on show."""
    user_id: int
    username: str
    display_name: str
    value: float
    is_winner: bool
    hit_target: bool


class DashboardChallenge(BaseModel):
    """One card on the home screen: a challenge plus where I stand in it today."""
    challenge: ChallengeOut
    period: Optional[PeriodSummary] = None
    my_value: Optional[float] = None
    my_entry_uuid: Optional[str] = None
    my_note: Optional[str] = None
    hit_target: bool = False
    my_rank: Optional[int] = None
    my_wins: int = 0
    current_streak: int = 0
    participants: int = 0
    logged_count: int = 0
    standings: list[StandingRow] = Field(default_factory=list)


class DashboardOut(BaseModel):
    user: UserOut
    overview: UserOverviewOut
    active: list[DashboardChallenge]
    invites: list[ChallengeOut]
    friend_requests: int
    generated_at: datetime


class ActivityDay(BaseModel):
    """One row of the activity heatmap."""
    date: str  # YYYY-MM-DD, UTC period start
    entries: int
    targets_hit: int
    wins: int
    totals_by_metric: dict[str, float]
