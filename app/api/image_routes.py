import logging
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.entry_routes import load_period
from app.api.schemas import EntryImageOut, PresignedUrlOut, UserOut
from app.db.models import Entry, EntryImage, User, generate_uuid
from app.db.database import get_db
from app.services import auth_service, minio_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["images"])

# Proof screenshots, so a few MB is plenty.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/gif": "gif",
}
PRESIGNED_URL_TTL = 3600


async def _read_image(upload: UploadFile) -> tuple[bytes, str, str]:
    """Validate an upload and return (bytes, mime type, extension)."""
    mime_type = (upload.content_type or "").lower()
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type '{mime_type}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )

    data = await upload.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)}MB",
        )

    # Trust the declared type over the filename, but keep the real extension
    # when it agrees.
    extension = ALLOWED_MIME_TYPES[mime_type]
    suffix = os.path.splitext(upload.filename or "")[1].lstrip(".").lower()
    if suffix in ALLOWED_MIME_TYPES.values():
        extension = suffix
    return data, mime_type, extension


def _load_own_entry(db: Session, entry_uuid: str, user: User) -> Entry:
    entry = db.query(Entry).filter(Entry.entry_uuid == entry_uuid).first()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found"
        )
    if entry.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only attach proof to your own entry",
        )
    return entry


@router.post(
    "/entries/{entry_uuid}/images",
    response_model=EntryImageOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_entry_image(
    entry_uuid: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach a proof screenshot to my entry. Always optional."""
    entry = _load_own_entry(db, entry_uuid, current_user)
    load_period(db, entry.period_id, current_user, for_write=True)

    data, mime_type, extension = await _read_image(file)
    image_uuid = generate_uuid()
    object_key = minio_service.entry_image_key(
        challenge_id=entry.period.challenge_id,
        user_id=current_user.id,
        entry_uuid=entry.entry_uuid,
        image_uuid=image_uuid,
        extension=extension,
    )

    if not minio_service.get_minio_service().upload_bytes(data, object_key, mime_type):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the image right now",
        )

    image = EntryImage(
        image_uuid=image_uuid,
        entry_id=entry.id,
        user_id=current_user.id,
        image_name=file.filename,
        mime_type=mime_type,
        file_size=len(data),
        file_format=extension,
        minio_object_key=object_key,
        minio_bucket=minio_service.MINIO_BUCKET,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    logger.info(f"{current_user.username} uploaded proof {object_key}")
    return image


@router.get("/entries/{entry_uuid}/images", response_model=list[EntryImageOut])
def list_entry_images(
    entry_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Proof attached to an entry. Visible to everyone in the challenge."""
    entry = db.query(Entry).filter(Entry.entry_uuid == entry_uuid).first()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found"
        )
    load_period(db, entry.period_id, current_user)
    return entry.images


@router.get("/images/{image_uuid}/url", response_model=PresignedUrlOut)
def get_image_url(
    image_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A temporary link to view a proof image."""
    image = db.query(EntryImage).filter(EntryImage.image_uuid == image_uuid).first()
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
    # Anyone in the challenge may look; load_period does that check.
    load_period(db, image.entry.period_id, current_user)

    url = minio_service.generate_presigned_url(
        image.minio_object_key, PRESIGNED_URL_TTL
    )
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not generate an image link right now",
        )
    return PresignedUrlOut(
        image_uuid=image.image_uuid, url=url, expires_in=PRESIGNED_URL_TTL
    )


@router.delete("/images/{image_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of my proof images."""
    image = db.query(EntryImage).filter(EntryImage.image_uuid == image_uuid).first()
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
    if image.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own images",
        )

    object_key = image.minio_object_key
    db.delete(image)
    db.commit()
    minio_service.delete_image(object_key)


@router.post("/users/me/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set my profile picture. Replaces the previous one."""
    data, mime_type, extension = await _read_image(file)
    previous_key = current_user.avatar_object_key

    object_key = minio_service.avatar_key(current_user.id, generate_uuid(), extension)
    if not minio_service.get_minio_service().upload_bytes(data, object_key, mime_type):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the image right now",
        )

    current_user.avatar_object_key = object_key
    db.commit()
    db.refresh(current_user)

    # Only one avatar per user, so the old object is now unreachable.
    if previous_key and previous_key != object_key:
        minio_service.delete_image(previous_key)

    return current_user


@router.get("/users/{user_id}/avatar/url", response_model=PresignedUrlOut)
def get_avatar_url(
    user_id: int,
    expires_in: int = Query(default=PRESIGNED_URL_TTL, ge=60, le=86400),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A temporary link to someone's profile picture."""
    user = auth_service.get_user_by_id(db, user_id)
    if user is None or not user.avatar_object_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No avatar for that user"
        )

    url = minio_service.generate_presigned_url(user.avatar_object_key, expires_in)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not generate an image link right now",
        )
    return PresignedUrlOut(url=url, expires_in=expires_in)
