import logging
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserOut,
    ExternalIdentityOut,
)
from app.db.database import get_db
from app.db.models import ExternalIdentity, User
from app.services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _gateway_identity(request: Request) -> dict[str, str | None]:
    expected = os.getenv("GATEWAY_SHARED_SECRET", "")
    supplied = request.headers.get("x-api-gateway-secret", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint is only available through API Get Away",
        )
    subject = request.headers.get("x-user-sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Authentik through API Get Away first",
        )
    return {
        "subject": subject,
        "email": request.headers.get("x-user-email"),
        "name": request.headers.get("x-user-name"),
    }


def _tokens(db: Session, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id),
        refresh_token=auth_service.create_refresh_token(db, user.id),
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create an account. Usernames are unique; display names are not."""
    try:
        user = auth_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            display_name=(payload.display_name or "").strip() or payload.username,
        )
    except IntegrityError:
        # Let the unique index be the arbiter rather than a check-then-insert,
        # which two simultaneous registrations could both pass.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already taken"
        )

    logger.info(f"Registered user {user.username} (id={user.id})")
    return _tokens(db, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return _tokens(db, user)


@router.post("/authentik/session", response_model=TokenResponse)
def authentik_session(request: Request, db: Session = Depends(get_db)):
    """Open only a previously linked account; never create an empty duplicate."""
    identity = _gateway_identity(request)
    link = db.query(ExternalIdentity).filter_by(
        provider="authentik", subject=identity["subject"]
    ).first()
    if not link:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Authentik account is not linked. Sign in with your username and password, then link it from your profile.",
        )
    return _tokens(db, link.user)


@router.get("/authentik", response_model=ExternalIdentityOut)
def authentik_status(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    link = db.query(ExternalIdentity).filter_by(provider="authentik", user_id=current_user.id).first()
    return ExternalIdentityOut(
        linked=bool(link), email=link.email if link else None, name=link.name if link else None
    )


@router.post("/authentik/link", response_model=ExternalIdentityOut)
def link_authentik(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    identity = _gateway_identity(request)
    existing = db.query(ExternalIdentity).filter_by(
        provider="authentik", subject=identity["subject"]
    ).first()
    if existing and existing.user_id != current_user.id:
        raise HTTPException(status_code=409, detail="This Authentik identity is linked to another account")
    link = existing or db.query(ExternalIdentity).filter_by(
        provider="authentik", user_id=current_user.id
    ).first()
    if link and link.subject != identity["subject"]:
        raise HTTPException(status_code=409, detail="This account already has a different Authentik identity")
    if not link:
        link = ExternalIdentity(provider="authentik", subject=identity["subject"], user_id=current_user.id)
        db.add(link)
    link.email, link.name = identity["email"], identity["name"]
    db.commit()
    return ExternalIdentityOut(linked=True, email=link.email, name=link.name)


@router.delete("/authentik", response_model=ExternalIdentityOut)
def unlink_authentik(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    link = db.query(ExternalIdentity).filter_by(provider="authentik", user_id=current_user.id).first()
    if link:
        db.delete(link)
        db.commit()
    return ExternalIdentityOut(linked=False)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    user_id = auth_service.verify_refresh_token(db, payload.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    # Rotate: the presented token is spent.
    auth_service.revoke_refresh_token(db, payload.refresh_token)
    return _tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    auth_service.revoke_refresh_token(db, payload.refresh_token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name cannot be blank",
            )
        current_user.display_name = display_name
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the password. Every existing session is logged out."""
    if not auth_service.verify_password(
        payload.current_password, current_user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    auth_service.change_password(db, current_user, payload.new_password)
