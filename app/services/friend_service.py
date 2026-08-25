"""Friendships. Every read and write goes through `friendship_pair()`."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.db.models import Friendship, User, friendship_pair
from app.enums import FriendshipStatus


def get_friendship(db: Session, user_id_1: int, user_id_2: int) -> Optional[Friendship]:
    a, b = friendship_pair(user_id_1, user_id_2)
    return (
        db.query(Friendship)
        .filter(Friendship.user_a_id == a, Friendship.user_b_id == b)
        .first()
    )


def are_friends(db: Session, user_id_1: int, user_id_2: int) -> bool:
    friendship = get_friendship(db, user_id_1, user_id_2)
    return friendship is not None and friendship.status == FriendshipStatus.ACCEPTED


def friend_ids(db: Session, user_id: int) -> list[int]:
    """Ids of everyone this user is actually friends with."""
    other = case(
        (Friendship.user_a_id == user_id, Friendship.user_b_id),
        else_=Friendship.user_a_id,
    )
    rows = (
        db.query(other)
        .filter(
            Friendship.status == FriendshipStatus.ACCEPTED,
            or_(Friendship.user_a_id == user_id, Friendship.user_b_id == user_id),
        )
        .all()
    )
    return [row[0] for row in rows]


def list_friends(db: Session, user_id: int) -> list[User]:
    ids = friend_ids(db, user_id)
    if not ids:
        return []
    return db.query(User).filter(User.id.in_(ids)).order_by(User.display_name).all()


def list_requests(db: Session, user_id: int) -> dict[str, list[Friendship]]:
    """Pending requests, split by who sent them."""
    pending = (
        db.query(Friendship)
        .filter(
            Friendship.status == FriendshipStatus.PENDING,
            or_(Friendship.user_a_id == user_id, Friendship.user_b_id == user_id),
        )
        .all()
    )
    return {
        "incoming": [f for f in pending if f.requested_by_id != user_id],
        "outgoing": [f for f in pending if f.requested_by_id == user_id],
    }


def send_request(db: Session, requester: User, target: User) -> tuple[Friendship, str]:
    """Send a friend request.

    Returns the row and what happened: "created", "already_pending",
    "already_friends", or "accepted" — the last one when the other person had
    already requested us, which makes the second request an acceptance.
    """
    if requester.id == target.id:
        raise ValueError("You cannot add yourself as a friend")

    existing = get_friendship(db, requester.id, target.id)
    if existing:
        if existing.status == FriendshipStatus.ACCEPTED:
            return existing, "already_friends"
        if existing.status == FriendshipStatus.BLOCKED:
            raise ValueError("This friendship is blocked")
        if existing.requested_by_id == requester.id:
            return existing, "already_pending"
        # They asked us first — treat this as accepting.
        existing.status = FriendshipStatus.ACCEPTED
        existing.responded_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing, "accepted"

    a, b = friendship_pair(requester.id, target.id)
    friendship = Friendship(
        user_a_id=a,
        user_b_id=b,
        requested_by_id=requester.id,
        status=FriendshipStatus.PENDING,
    )
    db.add(friendship)
    db.commit()
    db.refresh(friendship)
    return friendship, "created"


def respond_to_request(
    db: Session, user_id: int, friendship: Friendship, accept: bool
) -> Optional[Friendship]:
    """Accept or decline a request. Declining deletes the row so it can be re-sent."""
    if friendship.requested_by_id == user_id:
        raise ValueError("You cannot respond to your own request")
    if user_id not in (friendship.user_a_id, friendship.user_b_id):
        raise ValueError("This request is not yours to respond to")
    if friendship.status != FriendshipStatus.PENDING:
        raise ValueError("This request is no longer pending")

    if not accept:
        db.delete(friendship)
        db.commit()
        return None

    friendship.status = FriendshipStatus.ACCEPTED
    friendship.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(friendship)
    return friendship


def remove_friend(db: Session, user_id: int, other_id: int) -> bool:
    friendship = get_friendship(db, user_id, other_id)
    if not friendship:
        return False
    db.delete(friendship)
    db.commit()
    return True


def search_users(db: Session, query: str, exclude_user_id: int, limit: int = 20) -> list[User]:
    """Find people to add, by username or display name."""
    pattern = f"%{query.strip()}%"
    return (
        db.query(User)
        .filter(
            User.id != exclude_user_id,
            or_(User.username.ilike(pattern), User.display_name.ilike(pattern)),
        )
        .order_by(User.username)
        .limit(limit)
        .all()
    )
