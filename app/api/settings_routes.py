from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import SettingsOut, SettingsUpdate
from app.db.database import get_db
from app.db.models import User, UserSettings

router = APIRouter(prefix="/settings", tags=["settings"])


def _settings_for(db: Session, user: User) -> UserSettings:
    """Fetch the user's settings row, creating a default one if it's missing."""
    settings = (
        db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    )
    if settings is None:
        settings = UserSettings(user_id=user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("", response_model=SettingsOut)
def get_settings(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _settings_for(db, current_user)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = _settings_for(db, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings
