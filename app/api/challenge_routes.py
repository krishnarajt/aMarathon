import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import get_current_user
from app.api.schemas import (
    ChallengeCreate,
    ChallengeDetailOut,
    ChallengeOut,
    ChallengeUpdate,
    InviteRequest,
    ParticipantOut,
    PeriodOut,
    RespondRequest,
    UserOut,
)
from app.challenges.periods import as_utc, utcnow
from app.db.database import get_db
from app.db.models import ChallengePeriod, Entry, EntryImage, User
from app.enums import ParticipantStatus
from app.services import auth_service, challenge_service, friend_service, minio_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/challenges", tags=["challenges"])


def load_challenge(
    db: Session, challenge_uuid: str, user: User, require_accepted: bool = True
):
    """Resolve a challenge the user is actually part of.

    Non-participants get a 404 rather than a 403 — no reason to confirm that a
    challenge exists to someone with nothing to do with it.
    """
    challenge = challenge_service.get_challenge(db, challenge_uuid)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found"
        )

    participant = challenge_service.get_participant(db, challenge.id, user.id)
    if participant is None or participant.status == ParticipantStatus.LEFT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found"
        )

    if require_accepted and participant.status != ParticipantStatus.ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accept the invitation to this challenge first",
        )
    return challenge, participant


def require_creator(challenge, user: User):
    if challenge.created_by_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the person who created this challenge can do that",
        )


def _challenge_out(challenge, participant=None) -> ChallengeOut:
    out = ChallengeOut.model_validate(challenge)
    out.my_status = participant.status if participant else None
    return out


def _resolve_friends(db: Session, current_user: User, usernames: list[str]) -> list[int]:
    """Turn usernames into ids, insisting they're accepted friends."""
    user_ids = []
    for raw in usernames:
        username = raw.strip().lower()
        target = auth_service.get_user_by_username(db, username)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No user named '{username}'",
            )
        if target.id != current_user.id and not friend_service.are_friends(
            db, current_user.id, target.id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can only add friends to a challenge — '{username}' is not one yet",
            )
        user_ids.append(target.id)
    return user_ids


def _periods_query(db: Session, challenge_id: int):
    return (
        db.query(ChallengePeriod)
        .options(
            selectinload(ChallengePeriod.entries).selectinload(Entry.images),
            selectinload(ChallengePeriod.entries).selectinload(Entry.user),
        )
        .filter(ChallengePeriod.challenge_id == challenge_id)
    )


@router.post("", response_model=ChallengeOut, status_code=status.HTTP_201_CREATED)
def create_challenge(
    payload: ChallengeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a challenge and invite friends to it."""
    invitee_ids = _resolve_friends(db, current_user, payload.invite)

    challenge = challenge_service.create_challenge(
        db,
        current_user,
        {
            "name": payload.name.strip(),
            "description": payload.description,
            "metric": payload.metric.value,
            "unit": payload.unit.value,
            "generator": payload.generator.value,
            "generator_config": payload.generator_config,
            "target_min": payload.target_min,
            "target_max": payload.target_max,
            "win_rule": payload.win_rule.value,
            "win_rule_config": payload.win_rule_config,
            "period_type": payload.period_type.value,
            "starts_at": as_utc(payload.starts_at) if payload.starts_at else utcnow(),
            "ends_at": as_utc(payload.ends_at) if payload.ends_at else None,
        },
        invitee_ids,
    )

    # Generate the first target right away so the challenge isn't empty.
    challenge_service.ensure_periods(db, challenge)
    logger.info(
        f"User {current_user.username} created challenge {challenge.challenge_uuid} "
        f"({challenge.metric}/{challenge.generator}) with {len(invitee_ids)} invitee(s)"
    )
    participant = challenge_service.get_participant(db, challenge.id, current_user.id)
    return _challenge_out(challenge, participant)


@router.get("", response_model=list[ChallengeOut])
def list_challenges(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Every challenge I'm in, including invitations I haven't answered."""
    challenges = challenge_service.list_challenges_for_user(db, current_user.id)
    out = []
    for challenge in challenges:
        participant = challenge_service.get_participant(
            db, challenge.id, current_user.id
        )
        if participant and participant.status == ParticipantStatus.ACCEPTED:
            challenge_service.ensure_periods(db, challenge)
        out.append(_challenge_out(challenge, participant))
    return out


@router.get("/{challenge_uuid}", response_model=ChallengeDetailOut)
def get_challenge(
    challenge_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Challenge, participants, and the period in progress.

    Reading a challenge is what triggers target generation, so an invitee who
    hasn't accepted can still see today's number.
    """
    challenge, participant = load_challenge(
        db, challenge_uuid, current_user, require_accepted=False
    )
    period = challenge_service.current_period(db, challenge)

    participants = [
        ParticipantOut(
            user=UserOut.model_validate(row.user),
            status=row.status,
            joined_at=row.joined_at,
        )
        for row in challenge_service.list_participants(db, challenge.id)
    ]

    period_out = None
    if period is not None:
        period_out = PeriodOut.model_validate(
            _periods_query(db, challenge.id)
            .filter(ChallengePeriod.id == period.id)
            .first()
        )

    return ChallengeDetailOut(
        challenge=_challenge_out(challenge, participant),
        participants=participants,
        current_period=period_out,
    )


@router.patch("/{challenge_uuid}", response_model=ChallengeOut)
def update_challenge(
    challenge_uuid: str,
    payload: ChallengeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change a challenge's settings.

    Changes apply to periods generated from now on. Periods already played keep
    the target and win rule they were played under.
    """
    challenge, participant = load_challenge(db, challenge_uuid, current_user)
    require_creator(challenge, current_user)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(challenge, field, value.value if hasattr(value, "value") else value)

    if challenge.target_max < challenge.target_min:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_max must be greater than or equal to target_min",
        )
    if challenge.ends_at is not None and as_utc(challenge.ends_at) <= as_utc(
        challenge.starts_at
    ):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ends_at must be after starts_at",
        )

    db.commit()
    db.refresh(challenge)
    return _challenge_out(challenge, participant)


@router.delete("/{challenge_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_challenge(
    challenge_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a challenge, its periods, entries and proof images."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user)
    require_creator(challenge, current_user)

    # Collect object keys before the rows cascade away, otherwise the images
    # are orphaned in MinIO with nothing left pointing at them.
    keys = [
        row[0]
        for row in db.query(EntryImage.minio_object_key)
        .join(Entry, Entry.id == EntryImage.entry_id)
        .join(ChallengePeriod, ChallengePeriod.id == Entry.period_id)
        .filter(ChallengePeriod.challenge_id == challenge.id)
        .all()
    ]

    db.delete(challenge)
    db.commit()

    if keys:
        minio_service.delete_images(keys)
    logger.info(f"Deleted challenge {challenge_uuid} and {len(keys)} image(s)")


@router.post("/{challenge_uuid}/participants", response_model=list[ParticipantOut])
def invite_participants(
    challenge_uuid: str,
    payload: InviteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invite more friends to a running challenge."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user)
    user_ids = _resolve_friends(db, current_user, payload.invite)

    added = challenge_service.invite_users(db, challenge, current_user, user_ids)
    return [
        ParticipantOut(
            user=UserOut.model_validate(row.user),
            status=row.status,
            joined_at=row.joined_at,
        )
        for row in added
    ]


@router.post("/{challenge_uuid}/respond", response_model=ChallengeOut)
def respond_to_invite(
    challenge_uuid: str,
    payload: RespondRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept or decline an invitation to a challenge."""
    challenge, participant = load_challenge(
        db, challenge_uuid, current_user, require_accepted=False
    )
    if participant.status not in (
        ParticipantStatus.INVITED,
        ParticipantStatus.DECLINED,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have no outstanding invitation to this challenge",
        )

    participant = challenge_service.respond_to_invite(db, participant, payload.accept)
    return _challenge_out(challenge, participant)


@router.post("/{challenge_uuid}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_challenge(
    challenge_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leave a challenge. Entries already logged stay, so history holds up."""
    challenge, participant = load_challenge(db, challenge_uuid, current_user)
    if challenge.created_by_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The creator cannot leave; delete the challenge instead",
        )
    challenge_service.leave_challenge(db, participant)


@router.get("/{challenge_uuid}/periods", response_model=list[PeriodOut])
def list_periods(
    challenge_uuid: str,
    limit: int = Query(default=30, ge=1, le=365),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Period history, newest first — everyone's numbers per day."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user, require_accepted=False)
    challenge_service.ensure_periods(db, challenge)

    return (
        _periods_query(db, challenge.id)
        .order_by(ChallengePeriod.period_index.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/{challenge_uuid}/current", response_model=PeriodOut)
def get_current_period(
    challenge_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Today's number and what everyone has logged against it so far."""
    challenge, _ = load_challenge(db, challenge_uuid, current_user, require_accepted=False)
    period = challenge_service.current_period(db, challenge)
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This challenge hasn't started yet",
        )
    return (
        _periods_query(db, challenge.id)
        .filter(ChallengePeriod.id == period.id)
        .first()
    )
