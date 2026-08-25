from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import jwt
import hashlib
import secrets
import logging
from sqlalchemy.orm import Session

from app.db.models import User, UserSettings, RefreshToken

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # Never fall back to a constant: if the secret failed to reach the
    # container the app would keep serving and sign tokens with a key anyone
    # could read out of this repo. Refusing to start makes that visible.
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with `openssl rand -hex 32` and "
        "supply it via the environment (in k8s it comes from the amarathon-env "
        "ExternalSecret)."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Password hashing. No rules on what a password may be, only on how it's stored.
SALT_LENGTH = 32  # bytes
HASH_ITERATIONS = 100000  # OWASP recommended minimum


def get_password_hash(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and a fresh random salt."""
    salt = secrets.token_bytes(SALT_LENGTH)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, HASH_ITERATIONS
    ).hex()
    # Format: iterations$salt$hash — self-describing, so the cost can be
    # raised later without invalidating existing hashes.
    return f"{HASH_ITERATIONS}${salt.hex()}${password_hash}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a stored hash."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False

        iterations = int(parts[0])
        salt = bytes.fromhex(parts[1])
        stored_hash = parts[2]

        computed_hash = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, iterations
        ).hex()

        # Constant-time comparison to prevent timing attacks
        return secrets.compare_digest(computed_hash, stored_hash)
    except (ValueError, IndexError):
        return False


def create_access_token(user_id: int, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "type": "access"},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(db: Session, user_id: int) -> str:
    """Create a refresh token, store it, and sweep this user's expired ones."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    # jti keeps the encoded token unique. Without it the payload is a pure
    # function of (user_id, exp), exp only has second resolution, and two
    # logins in the same second would collide on the unique token index.
    token = jwt.encode(
        {
            "sub": str(user_id),
            "exp": expires_at,
            "type": "refresh",
            "jti": secrets.token_urlsafe(16),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    try:
        db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.expires_at <= now,
        ).delete(synchronize_session=False)
    except Exception as e:
        logger.warning(f"Failed to clean expired tokens for user {user_id}: {e}")

    db.add(RefreshToken(user_id=user_id, token=token, expires_at=expires_at))
    db.commit()
    return token


def verify_access_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return int(payload.get("sub"))
    except jwt.InvalidTokenError:
        return None


def verify_refresh_token(db: Session, token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        user_id = int(payload.get("sub"))
    except jwt.InvalidTokenError:
        return None

    # A signed-but-revoked token must not work, so the row has to exist too.
    db_token = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == token,
            RefreshToken.user_id == user_id,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    return user_id if db_token else None


def revoke_refresh_token(db: Session, token: str) -> bool:
    db_token = db.query(RefreshToken).filter(RefreshToken.token == token).first()
    if not db_token:
        return False
    db.delete(db_token)
    db.commit()
    return True


def revoke_all_user_tokens(db: Session, user_id: int) -> None:
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete()
    db.commit()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, username: str, password: str, display_name: str) -> User:
    """Create a user and their settings row."""
    user = User(
        username=username,
        display_name=display_name or username,
        password_hash=get_password_hash(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(UserSettings(user_id=user.id))
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def change_password(db: Session, user: User, new_password: str) -> None:
    """Set a new password and log every session out."""
    user.password_hash = get_password_hash(new_password)
    db.commit()
    revoke_all_user_tokens(db, user.id)
