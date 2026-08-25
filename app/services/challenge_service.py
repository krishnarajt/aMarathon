"""
Challenge orchestration: creating challenges, filling in missing periods, and
recomputing winners.

Periods are generated lazily. Nothing runs on a timer — whenever a challenge is
read, any period whose window has already started gets its target generated and
stored. That is idempotent, survives the pod restarting over a period boundary,
and needs no scheduler.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.challenges.generators import generate_target
from app.challenges.periods import (
    as_utc,
    period_index_at,
    period_window,
    utcnow,
)
from app.challenges.win_rules import pick_winners
from app.db.models import Challenge, ChallengeParticipant, ChallengePeriod, Entry, User
from app.enums import ChallengeStatus, ParticipantStatus

logger = logging.getLogger(__name__)

# Backstop on a single lazy backfill. A daily challenge would need ~3 years of
# neglect to reach this; an hourly one about six weeks.
MAX_PERIODS_PER_CALL = 1000


def get_challenge(db: Session, challenge_uuid: str) -> Optional[Challenge]:
    return (
        db.query(Challenge)
        .filter(Challenge.challenge_uuid == challenge_uuid)
        .first()
    )


def get_participant(
    db: Session, challenge_id: int, user_id: int
) -> Optional[ChallengeParticipant]:
    return (
        db.query(ChallengeParticipant)
        .filter(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == user_id,
        )
        .first()
    )


def list_participants(db: Session, challenge_id: int) -> list[ChallengeParticipant]:
    return (
        db.query(ChallengeParticipant)
        .filter(ChallengeParticipant.challenge_id == challenge_id)
        .all()
    )


def accepted_user_ids(db: Session, challenge_id: int) -> list[int]:
    rows = (
        db.query(ChallengeParticipant.user_id)
        .filter(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.status == ParticipantStatus.ACCEPTED,
        )
        .all()
    )
    return [row[0] for row in rows]


def list_challenges_for_user(db: Session, user_id: int) -> list[Challenge]:
    """Every challenge the user is in, including ones they haven't accepted yet."""
    return (
        db.query(Challenge)
        .join(ChallengeParticipant, ChallengeParticipant.challenge_id == Challenge.id)
        .filter(
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.status != ParticipantStatus.LEFT,
        )
        .order_by(Challenge.created_at.desc())
        .all()
    )


def create_challenge(
    db: Session, creator: User, fields: dict, invitee_ids: list[int]
) -> Challenge:
    """Create a challenge. The creator joins accepted; everyone else is invited."""
    challenge = Challenge(created_by_id=creator.id, **fields)
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    now = utcnow()
    db.add(
        ChallengeParticipant(
            challenge_id=challenge.id,
            user_id=creator.id,
            status=ParticipantStatus.ACCEPTED,
            joined_at=now,
        )
    )
    for user_id in dict.fromkeys(invitee_ids):  # de-dupe, keep order
        if user_id == creator.id:
            continue
        db.add(
            ChallengeParticipant(
                challenge_id=challenge.id,
                user_id=user_id,
                status=ParticipantStatus.INVITED,
                invited_by_id=creator.id,
            )
        )
    db.commit()
    db.refresh(challenge)
    return challenge


def invite_users(
    db: Session, challenge: Challenge, inviter: User, user_ids: list[int]
) -> list[ChallengeParticipant]:
    """Add people to an existing challenge, skipping anyone already in it."""
    added = []
    for user_id in dict.fromkeys(user_ids):
        existing = get_participant(db, challenge.id, user_id)
        if existing:
            # Someone who left can be invited back.
            if existing.status == ParticipantStatus.LEFT:
                existing.status = ParticipantStatus.INVITED
                existing.invited_by_id = inviter.id
                added.append(existing)
            continue
        participant = ChallengeParticipant(
            challenge_id=challenge.id,
            user_id=user_id,
            status=ParticipantStatus.INVITED,
            invited_by_id=inviter.id,
        )
        db.add(participant)
        added.append(participant)
    db.commit()
    return added


def respond_to_invite(
    db: Session, participant: ChallengeParticipant, accept: bool
) -> ChallengeParticipant:
    participant.status = (
        ParticipantStatus.ACCEPTED if accept else ParticipantStatus.DECLINED
    )
    participant.joined_at = utcnow() if accept else None
    db.commit()
    db.refresh(participant)
    return participant


def leave_challenge(db: Session, participant: ChallengeParticipant) -> None:
    """Mark someone as gone but keep their entries, so past days still make sense."""
    participant.status = ParticipantStatus.LEFT
    db.commit()


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

def list_periods(db: Session, challenge_id: int) -> list[ChallengePeriod]:
    return (
        db.query(ChallengePeriod)
        .filter(ChallengePeriod.challenge_id == challenge_id)
        .order_by(ChallengePeriod.period_index)
        .all()
    )


def ensure_periods(
    db: Session, challenge: Challenge, now: Optional[datetime] = None
) -> list[ChallengePeriod]:
    """Generate any period whose window has started but has no row yet."""
    now = now or utcnow()

    if challenge.status != ChallengeStatus.ACTIVE:
        return list_periods(db, challenge.id)

    # Don't generate past the end of the challenge. The period containing
    # ends_at still counts — that last day is played.
    horizon = now
    if challenge.ends_at is not None:
        horizon = min(now, as_utc(challenge.ends_at))

    last_index = period_index_at(challenge, horizon)
    if last_index < 0:
        return []  # hasn't started yet

    existing = list_periods(db, challenge.id)
    known = {period.period_index for period in existing}
    missing = [i for i in range(last_index + 1) if i not in known]
    if not missing:
        return existing

    if len(missing) > MAX_PERIODS_PER_CALL:
        logger.warning(
            f"Challenge {challenge.challenge_uuid} is behind by {len(missing)} "
            f"periods; generating the oldest {MAX_PERIODS_PER_CALL} this call."
        )
        missing = missing[:MAX_PERIODS_PER_CALL]

    fresh = []
    for index in missing:
        start, end = period_window(challenge, index)
        fresh.append(
            ChallengePeriod(
                challenge_id=challenge.id,
                period_index=index,
                period_start=start,
                period_end=end,
                target_value=generate_target(challenge, index, start),
                # Snapshot the rule in force now, so changing the challenge's
                # rule later never re-judges this period.
                win_rule=challenge.win_rule,
                win_rule_config=challenge.win_rule_config,
            )
        )

    try:
        with db.begin_nested():
            db.add_all(fresh)
        db.commit()
    except IntegrityError:
        # Two requests raced to generate the same period. The unique index on
        # (challenge_id, period_index) settled it; re-read the winner's rows.
        db.rollback()
        logger.info(
            f"Concurrent period generation for {challenge.challenge_uuid}; "
            "keeping the rows that landed first."
        )

    return list_periods(db, challenge.id)


def current_period(
    db: Session, challenge: Challenge, now: Optional[datetime] = None
) -> Optional[ChallengePeriod]:
    """The period happening right now, generating it if needed."""
    now = now or utcnow()
    periods = ensure_periods(db, challenge, now)
    index = period_index_at(challenge, now)
    for period in periods:
        if period.period_index == index:
            return period
    return periods[-1] if periods else None  # challenge is over


def get_period(db: Session, period_id: int) -> Optional[ChallengePeriod]:
    return db.query(ChallengePeriod).filter(ChallengePeriod.id == period_id).first()


def list_entries(db: Session, period_id: int) -> list[Entry]:
    return db.query(Entry).filter(Entry.period_id == period_id).all()


# ---------------------------------------------------------------------------
# Entries and scoring
# ---------------------------------------------------------------------------

def evaluate_period(
    db: Session, period: ChallengePeriod, commit: bool = True
) -> list[Entry]:
    """Recompute `is_winner` for every entry in a period."""
    entries = list_entries(db, period.id)
    winners = pick_winners(period, entries)
    winner_ids = {entry.id for entry in winners}

    for entry in entries:
        should_win = entry.id in winner_ids
        if entry.is_winner != should_win:
            entry.is_winner = should_win

    period.winner_evaluated_at = utcnow()
    if commit:
        db.commit()
    return winners


def upsert_entry(
    db: Session,
    period: ChallengePeriod,
    user_id: int,
    value: float,
    note: Optional[str] = None,
) -> Entry:
    """Record or edit what a user did in a period, then re-pick the winner."""
    entry = (
        db.query(Entry)
        .filter(Entry.period_id == period.id, Entry.user_id == user_id)
        .first()
    )
    if entry is None:
        entry = Entry(period_id=period.id, user_id=user_id, value=value, note=note)
        db.add(entry)
    else:
        entry.value = value
        entry.note = note

    # Stamped once, on first crossing, and never moved: editing a value upward
    # next week must not backdate someone into first place. A value later
    # edited back below the target keeps the stamp, but the FIRST_TO_TARGET rule
    # also checks the value, so a stale stamp can't win on its own.
    if entry.target_reached_at is None and value >= period.target_value:
        entry.target_reached_at = utcnow()

    db.flush()  # entry needs an id before winners can be flagged
    evaluate_period(db, period, commit=False)
    db.commit()
    db.refresh(entry)
    return entry


def delete_entry(db: Session, entry: Entry) -> None:
    """Remove an entry and re-pick the period's winner without it."""
    period = entry.period
    db.delete(entry)
    db.flush()
    evaluate_period(db, period, commit=False)
    db.commit()
