"""
Every enumerated value used by Amarathon.

These are `StrEnum`s, so a member *is* a string: SQLAlchemy stores it in a plain
`String` column, `json.dumps` handles it, and Pydantic validates request bodies
against it for free. Reading a row back gives a plain `str`, and comparing that
to a member still works (`row.status == FriendshipStatus.ACCEPTED`).

Deliberately not Postgres ENUM types — adding a challenge type or a new winner
rule should be a code change, not a migration.
"""

from enum import StrEnum


class _Enum(StrEnum):
    """StrEnum plus a couple of conveniences for validation."""

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]

    @classmethod
    def has(cls, value: str) -> bool:
        return value in cls._value2member_map_


class Metric(_Enum):
    """What a challenge measures."""
    STEPS = "steps"
    DISTANCE = "distance"


class Unit(_Enum):
    """Display unit for a metric. STEPS pairs with Metric.STEPS."""
    STEPS = "steps"
    KM = "km"
    MI = "mi"


class Generator(_Enum):
    """Algorithm that produces a period's target.

    Each value maps to one function in app/challenges/generators.py. Adding
    MARATHON_TRAINING or SPRINT_TRAINING later means a new member here and a new
    function there — the schema doesn't move.
    """
    RANDOM = "random"


class WinRule(_Enum):
    """How the winner of a period is decided.

    Each value maps to one function in app/challenges/win_rules.py. A challenge
    holds the rule currently in force; every period snapshots the rule it was
    played under, so changing the rule mid-challenge never rewrites past days.
    """

    # Highest number wins, target or no target.
    HIGHEST_VALUE = "highest_value"
    # Must hit the target; among those who did, the highest wins. Nobody hit it
    # -> nobody wins.
    HIGHEST_ABOVE_TARGET = "highest_above_target"
    # Everyone who hit the target wins. Co-op flavoured.
    ALL_WHO_HIT_TARGET = "all_who_hit_target"
    # Earliest to cross the target wins, using Entry.target_reached_at.
    FIRST_TO_TARGET = "first_to_target"


class PeriodType(_Enum):
    """How long one period of a challenge lasts."""
    DAILY = "daily"
    HOURLY = "hourly"
    WEEKLY = "weekly"


class ChallengeStatus(_Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FriendshipStatus(_Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class ParticipantStatus(_Enum):
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    LEFT = "left"
