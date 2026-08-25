import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import get_current_user
from app.api.schemas import EntryOut, EntryUpsert, PeriodOut
from app.db.database import get_db
from app.db.models import ChallengePeriod, Entry, User
from app.enums import ChallengeStatus, ParticipantStatus
from app.services import challenge_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["entries"])


def load_period(db: Session, period_id: int, user: User, for_write: bool = False):
    """Resolve a period the user is allowed to see, and optionally write to."""
    period = challenge_service.get_period(db, period_id)
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Period not found"
        )

    challenge = period.challenge
    participant = challenge_service.get_participant(db, challenge.id, user.id)
    if participant is None or participant.status == ParticipantStatus.LEFT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Period not found"
        )

    if for_write:
        if participant.status != ParticipantStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accept the invitation to this challenge first",
            )
        if challenge.status != ChallengeStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This challenge is {challenge.status} — entries are closed",
            )
    return period


@router.get("/periods/{period_id}", response_model=PeriodOut)
def get_period(
    period_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One period with everyone's entries."""
    load_period(db, period_id, current_user)
    return (
        db.query(ChallengePeriod)
        .options(
            selectinload(ChallengePeriod.entries).selectinload(Entry.images),
            selectinload(ChallengePeriod.entries).selectinload(Entry.user),
        )
        .filter(ChallengePeriod.id == period_id)
        .first()
    )


@router.put("/periods/{period_id}/entry", response_model=EntryOut)
def upsert_entry(
    period_id: int,
    payload: EntryUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log what I did, or correct what I logged before.

    Works for any period that has started, including old ones — the winner for
    that period is recomputed on the spot.
    """
    period = load_period(db, period_id, current_user, for_write=True)
    entry = challenge_service.upsert_entry(
        db, period, current_user.id, payload.value, payload.note
    )
    logger.info(
        f"{current_user.username} logged {payload.value} for period "
        f"{period.period_index} of challenge {period.challenge.challenge_uuid}"
    )
    return entry


@router.delete("/periods/{period_id}/entry", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(
    period_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove my entry for a period. The winner is recomputed without it."""
    period = load_period(db, period_id, current_user, for_write=True)
    entry = (
        db.query(Entry)
        .filter(Entry.period_id == period.id, Entry.user_id == current_user.id)
        .first()
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have no entry for this period",
        )
    challenge_service.delete_entry(db, entry)
