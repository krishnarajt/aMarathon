"""
Period boundary maths. Pure functions, no database.

A challenge is a sequence of half-open UTC windows `[start, end)`. Window 0
starts at `starts_at` snapped down to a period boundary, so a daily challenge
always turns over at 00:00 UTC no matter what time of day it was created — the
whole world sees the new number at the same instant.
"""

from datetime import datetime, timedelta, timezone

from app.enums import PeriodType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime) -> datetime:
    """Naive datetimes are treated as UTC.

    Postgres hands back tz-aware values, SQLite (local dev) does not, and
    calling .astimezone() on a naive value would silently assume the server's
    local zone.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def period_length(period_type: str) -> timedelta:
    if period_type == PeriodType.HOURLY:
        return timedelta(hours=1)
    if period_type == PeriodType.WEEKLY:
        return timedelta(weeks=1)
    return timedelta(days=1)


def floor_to_period(moment: datetime, period_type: str) -> datetime:
    """Snap an instant down to the start of the period containing it."""
    m = as_utc(moment)
    if period_type == PeriodType.HOURLY:
        return m.replace(minute=0, second=0, microsecond=0)

    day = m.replace(hour=0, minute=0, second=0, microsecond=0)
    if period_type == PeriodType.WEEKLY:
        return day - timedelta(days=day.weekday())  # Monday
    return day


def period_anchor(challenge) -> datetime:
    """Start of period 0."""
    return floor_to_period(challenge.starts_at, challenge.period_type)


def period_window(challenge, index: int) -> tuple[datetime, datetime]:
    """The `[start, end)` window of a given period index."""
    start = period_anchor(challenge) + index * period_length(challenge.period_type)
    return start, start + period_length(challenge.period_type)


def period_index_at(challenge, moment: datetime) -> int:
    """Which period index contains `moment`. Negative if before the challenge."""
    elapsed = as_utc(moment) - period_anchor(challenge)
    if elapsed < timedelta(0):
        return -1
    return int(elapsed // period_length(challenge.period_type))
