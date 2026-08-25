"""
Aggregate queries — the numbers the UI will draw graphs from.

All of these are read-only and derive everything from `entries` joined to
`challenge_periods`, so editing an old entry is reflected immediately.
"""

from datetime import timedelta
from typing import Optional

from sqlalchemy import case, func as f
from sqlalchemy.orm import Session

from app.challenges.periods import as_utc, utcnow
from app.db.models import (
    Challenge,
    ChallengeParticipant,
    ChallengePeriod,
    Entry,
    Friendship,
    User,
)
from app.enums import ChallengeStatus, FriendshipStatus, ParticipantStatus
from app.services import challenge_service

# A period counts as "hit" when the entry reached the target for that period.
_HIT = Entry.value >= ChallengePeriod.target_value


def _streaks(hits_by_index: dict[int, bool], newest_index: Optional[int]) -> tuple[int, int]:
    """(current, longest) runs of consecutive target hits.

    A period the user never logged breaks the run. The newest period is skipped
    for the current streak when it's unlogged, so a challenge that hasn't been
    logged *yet today* doesn't read as a broken streak.
    """
    if not hits_by_index:
        return 0, 0

    longest = run = 0
    for index in sorted(hits_by_index):
        run = run + 1 if hits_by_index[index] else 0
        longest = max(longest, run)

    current = 0
    indices = sorted(hits_by_index, reverse=True)
    for position, index in enumerate(indices):
        if position == 0 and index == newest_index and not hits_by_index[index]:
            continue  # today, not logged yet
        if not hits_by_index[index]:
            break
        current += 1

    return current, longest


def leaderboard(db: Session, challenge: Challenge) -> list[dict]:
    """One row per participant, best first. Participants with no entries included."""
    rows = (
        db.query(
            Entry.user_id.label("user_id"),
            f.count(Entry.id).label("entries_logged"),
            f.coalesce(f.sum(Entry.value), 0.0).label("total_value"),
            f.max(Entry.value).label("best_value"),
            f.avg(Entry.value).label("average_value"),
            f.sum(case((Entry.is_winner.is_(True), 1), else_=0)).label("wins"),
            f.sum(case((_HIT, 1), else_=0)).label("targets_hit"),
        )
        .join(ChallengePeriod, ChallengePeriod.id == Entry.period_id)
        .filter(ChallengePeriod.challenge_id == challenge.id)
        .group_by(Entry.user_id)
        .all()
    )
    totals = {row.user_id: row for row in rows}

    # Per-user hit map for streaks
    hit_rows = (
        db.query(ChallengePeriod.period_index, Entry.user_id, _HIT.label("hit"))
        .join(Entry, Entry.period_id == ChallengePeriod.id)
        .filter(ChallengePeriod.challenge_id == challenge.id)
        .all()
    )
    hits: dict[int, dict[int, bool]] = {}
    for period_index, user_id, hit in hit_rows:
        hits.setdefault(user_id, {})[period_index] = bool(hit)

    newest_index = (
        db.query(f.max(ChallengePeriod.period_index))
        .filter(ChallengePeriod.challenge_id == challenge.id)
        .scalar()
    )

    participants = (
        db.query(ChallengeParticipant, User)
        .join(User, User.id == ChallengeParticipant.user_id)
        .filter(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.status != ParticipantStatus.DECLINED,
        )
        .all()
    )

    board = []
    for participant, user in participants:
        row = totals.get(user.id)
        current_streak, longest_streak = _streaks(hits.get(user.id, {}), newest_index)
        board.append(
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "participant_status": participant.status,
                "entries_logged": int(row.entries_logged) if row else 0,
                "wins": int(row.wins or 0) if row else 0,
                "targets_hit": int(row.targets_hit or 0) if row else 0,
                "total_value": float(row.total_value or 0.0) if row else 0.0,
                "best_value": float(row.best_value) if row and row.best_value is not None else 0.0,
                "average_value": float(row.average_value) if row and row.average_value is not None else 0.0,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
            }
        )

    board.sort(
        key=lambda r: (r["wins"], r["targets_hit"], r["total_value"]), reverse=True
    )
    return board


def timeseries(db: Session, challenge: Challenge, limit: Optional[int] = None) -> list[dict]:
    """Period-by-period targets and everyone's values — the shape a chart wants."""
    query = (
        db.query(ChallengePeriod)
        .filter(ChallengePeriod.challenge_id == challenge.id)
        .order_by(ChallengePeriod.period_index.desc())
    )
    if limit:
        query = query.limit(limit)
    periods = list(reversed(query.all()))
    if not periods:
        return []

    period_ids = [period.id for period in periods]
    rows = (
        db.query(Entry, User)
        .join(User, User.id == Entry.user_id)
        .filter(Entry.period_id.in_(period_ids))
        .all()
    )
    by_period: dict[int, list[tuple]] = {}
    for entry, user in rows:
        by_period.setdefault(entry.period_id, []).append((entry, user))

    series = []
    for period in periods:
        entries = [
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "value": entry.value,
                "is_winner": entry.is_winner,
                "hit_target": entry.value >= period.target_value,
            }
            for entry, user in by_period.get(period.id, [])
        ]
        series.append(
            {
                "period_id": period.id,
                "period_index": period.period_index,
                "period_start": as_utc(period.period_start),
                "period_end": as_utc(period.period_end),
                "target_value": period.target_value,
                "win_rule": period.win_rule,
                "entries": entries,
            }
        )
    return series


def user_overview(db: Session, user_id: int) -> dict:
    """Totals across every challenge a user takes part in."""
    challenge_counts = (
        db.query(Challenge.status, f.count(Challenge.id))
        .join(ChallengeParticipant, ChallengeParticipant.challenge_id == Challenge.id)
        .filter(
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.status == ParticipantStatus.ACCEPTED,
        )
        .group_by(Challenge.status)
        .all()
    )
    by_status = {status: count for status, count in challenge_counts}

    totals = (
        db.query(
            f.count(Entry.id).label("entries_logged"),
            f.sum(case((Entry.is_winner.is_(True), 1), else_=0)).label("wins"),
            f.sum(case((_HIT, 1), else_=0)).label("targets_hit"),
        )
        .join(ChallengePeriod, ChallengePeriod.id == Entry.period_id)
        .filter(Entry.user_id == user_id)
        .one()
    )

    # Steps and distance can't be summed together, so split by metric.
    per_metric = (
        db.query(Challenge.metric, f.coalesce(f.sum(Entry.value), 0.0))
        .join(ChallengePeriod, ChallengePeriod.challenge_id == Challenge.id)
        .join(Entry, Entry.period_id == ChallengePeriod.id)
        .filter(Entry.user_id == user_id)
        .group_by(Challenge.metric)
        .all()
    )

    return {
        "challenges_active": by_status.get(ChallengeStatus.ACTIVE.value, 0),
        "challenges_total": sum(by_status.values()),
        "entries_logged": int(totals.entries_logged or 0),
        "wins": int(totals.wins or 0),
        "targets_hit": int(totals.targets_hit or 0),
        "totals_by_metric": {metric: float(total) for metric, total in per_metric},
        "generated_at": utcnow(),
    }


def activity_history(db: Session, user_id: int, days: int = 90) -> list[dict]:
    """One row per day the user logged something, newest last.

    Bucketed by the period's own start date rather than when the entry was
    typed, so correcting Tuesday's number on Friday still counts as Tuesday.
    The aggregation happens in Python because date truncation is spelled
    differently on every dialect, and a single user's history is small.
    """
    cutoff = utcnow() - timedelta(days=days)

    rows = (
        db.query(
            ChallengePeriod.period_start,
            Challenge.metric,
            Entry.value,
            Entry.is_winner,
            ChallengePeriod.target_value,
        )
        .join(Entry, Entry.period_id == ChallengePeriod.id)
        .join(Challenge, Challenge.id == ChallengePeriod.challenge_id)
        .filter(Entry.user_id == user_id, ChallengePeriod.period_start >= cutoff)
        .all()
    )

    by_day: dict[str, dict] = {}
    for period_start, metric, value, is_winner, target in rows:
        key = as_utc(period_start).date().isoformat()
        day = by_day.setdefault(
            key,
            {"date": key, "entries": 0, "targets_hit": 0, "wins": 0, "totals_by_metric": {}},
        )
        day["entries"] += 1
        day["wins"] += 1 if is_winner else 0
        day["targets_hit"] += 1 if value >= target else 0
        day["totals_by_metric"][metric] = day["totals_by_metric"].get(metric, 0.0) + float(value)

    return [by_day[key] for key in sorted(by_day)]


def dashboard(db: Session, user: User) -> dict:
    """Everything the home screen draws, in one round trip.

    The alternative is the client fetching every challenge, then its current
    period, then its leaderboard — three requests per challenge on the screen
    people open most often.
    """
    participations = (
        db.query(ChallengeParticipant, Challenge)
        .join(Challenge, Challenge.id == ChallengeParticipant.challenge_id)
        .filter(
            ChallengeParticipant.user_id == user.id,
            ChallengeParticipant.status != ParticipantStatus.LEFT,
        )
        .order_by(Challenge.created_at.desc())
        .all()
    )

    # ChallengeOut carries `my_status`, which isn't a column — stamp it on the
    # instance so the response model picks it up the same way the challenge
    # routes set it.
    for participant, challenge in participations:
        challenge.my_status = participant.status

    invites = [
        challenge
        for participant, challenge in participations
        if participant.status == ParticipantStatus.INVITED
        and challenge.status == ChallengeStatus.ACTIVE
    ]
    joined = [
        challenge
        for participant, challenge in participations
        if participant.status == ParticipantStatus.ACCEPTED
    ]

    active = []
    for challenge in joined:
        if challenge.status != ChallengeStatus.ACTIVE:
            continue
        period = challenge_service.current_period(db, challenge)
        card = {
            "challenge": challenge,
            "period": period,
            "my_value": None,
            "my_entry_uuid": None,
            "my_note": None,
            "hit_target": False,
            "my_rank": None,
            "my_wins": 0,
            "current_streak": 0,
            "participants": 0,
            "logged_count": 0,
            "standings": [],
        }

        card["participants"] = (
            db.query(f.count(ChallengeParticipant.id))
            .filter(
                ChallengeParticipant.challenge_id == challenge.id,
                ChallengeParticipant.status == ParticipantStatus.ACCEPTED,
            )
            .scalar()
            or 0
        )

        # Wins and streak span the whole challenge, so they're worth showing
        # even before anyone has logged today.
        card["my_wins"] = (
            db.query(f.count(Entry.id))
            .join(ChallengePeriod, ChallengePeriod.id == Entry.period_id)
            .filter(
                ChallengePeriod.challenge_id == challenge.id,
                Entry.user_id == user.id,
                Entry.is_winner.is_(True),
            )
            .scalar()
            or 0
        )

        hit_rows = (
            db.query(ChallengePeriod.period_index, _HIT)
            .join(Entry, Entry.period_id == ChallengePeriod.id)
            .filter(
                ChallengePeriod.challenge_id == challenge.id,
                Entry.user_id == user.id,
            )
            .all()
        )
        newest_index = (
            db.query(f.max(ChallengePeriod.period_index))
            .filter(ChallengePeriod.challenge_id == challenge.id)
            .scalar()
        )
        card["current_streak"] = _streaks(
            {index: bool(hit) for index, hit in hit_rows}, newest_index
        )[0]

        if period is not None:
            standings = [
                {
                    "user_id": row_user.id,
                    "username": row_user.username,
                    "display_name": row_user.display_name,
                    "value": float(entry.value),
                    "is_winner": bool(entry.is_winner),
                    "hit_target": entry.value >= period.target_value,
                }
                for entry, row_user in (
                    db.query(Entry, User)
                    .join(User, User.id == Entry.user_id)
                    .filter(Entry.period_id == period.id)
                    .all()
                )
            ]
            standings.sort(key=lambda row: row["value"], reverse=True)

            card["standings"] = standings
            card["logged_count"] = len(standings)
            for rank, row in enumerate(standings, start=1):
                if row["user_id"] == user.id:
                    card["my_value"] = row["value"]
                    card["hit_target"] = row["hit_target"]
                    card["my_rank"] = rank
                    break

            mine = (
                db.query(Entry)
                .filter(Entry.period_id == period.id, Entry.user_id == user.id)
                .first()
            )
            if mine is not None:
                card["my_entry_uuid"] = mine.entry_uuid
                card["my_note"] = mine.note

        active.append(card)

    # Challenges people are actually playing float to the top; the rest keep
    # their newest-first order.
    active.sort(key=lambda card: (card["my_value"] is None, -card["current_streak"]))

    friend_requests = (
        db.query(f.count(Friendship.id))
        .filter(
            Friendship.status == FriendshipStatus.PENDING,
            Friendship.requested_by_id != user.id,
            (Friendship.user_a_id == user.id) | (Friendship.user_b_id == user.id),
        )
        .scalar()
        or 0
    )

    return {
        "user": user,
        "overview": user_overview(db, user.id),
        "active": active,
        "invites": invites,
        "friend_requests": int(friend_requests),
        "generated_at": utcnow(),
    }
