from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    FriendRequestCreate,
    FriendRequestOut,
    FriendRequestResult,
    FriendRequestsOut,
    RespondRequest,
    UserOut,
)
from app.db.database import get_db
from app.db.models import Friendship, User
from app.services import auth_service, friend_service

router = APIRouter(tags=["friends"])


def _request_out(friendship: Friendship, viewer_id: int, db: Session) -> FriendRequestOut:
    """Render a friendship from one person's point of view."""
    other_id = (
        friendship.user_b_id
        if friendship.user_a_id == viewer_id
        else friendship.user_a_id
    )
    other = auth_service.get_user_by_id(db, other_id)
    requester = auth_service.get_user_by_id(db, friendship.requested_by_id)
    return FriendRequestOut(
        id=friendship.id,
        status=friendship.status,
        created_at=friendship.created_at,
        requested_by=UserOut.model_validate(requester),
        other_user=UserOut.model_validate(other),
    )


@router.get("/users/search", response_model=list[UserOut])
def search_users(
    q: str = Query(min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Find people to add as friends, by username or display name."""
    return friend_service.search_users(db, q, exclude_user_id=current_user.id)


@router.get("/friends", response_model=list[UserOut])
def list_friends(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return friend_service.list_friends(db, current_user.id)


@router.get("/friends/requests", response_model=FriendRequestsOut)
def list_requests(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Pending requests, split into ones sent to me and ones I sent."""
    buckets = friend_service.list_requests(db, current_user.id)
    return FriendRequestsOut(
        incoming=[_request_out(f, current_user.id, db) for f in buckets["incoming"]],
        outgoing=[_request_out(f, current_user.id, db) for f in buckets["outgoing"]],
    )


@router.post(
    "/friends/requests",
    response_model=FriendRequestResult,
    status_code=status.HTTP_201_CREATED,
)
def send_request(
    payload: FriendRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ask someone to be friends. If they already asked you, this accepts."""
    target = auth_service.get_user_by_username(db, payload.username)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No user with that username"
        )

    try:
        friendship, result = friend_service.send_request(db, current_user, target)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return FriendRequestResult(
        result=result, request=_request_out(friendship, current_user.id, db)
    )


@router.post("/friends/requests/{friendship_id}/respond", response_model=FriendRequestResult)
def respond_to_request(
    friendship_id: int,
    payload: RespondRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    friendship = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if friendship is None or current_user.id not in (
        friendship.user_a_id,
        friendship.user_b_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found"
        )

    try:
        updated = friend_service.respond_to_request(
            db, current_user.id, friendship, payload.accept
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if updated is None:
        return FriendRequestResult(result="declined")
    return FriendRequestResult(
        result="accepted", request=_request_out(updated, current_user.id, db)
    )


@router.delete("/friends/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_friend(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unfriend someone. Their past challenge entries stay put."""
    if not friend_service.remove_friend(db, current_user.id, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="You are not friends"
        )
