"""
Database models for Amarathon.

Everything time-related is stored in UTC (`DateTime(timezone=True)`). A
challenge's periods are half-open UTC windows `[period_start, period_end)`, so
the whole world flips to the next target at the same instant. The UI converts
to local time for display only.

Enumerated column values live in app/enums.py.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base
from app.enums import (
    ChallengeStatus,
    FriendshipStatus,
    Generator,
    ParticipantStatus,
    PeriodType,
    Unit,
    WinRule,
)


def generate_uuid():
    """Generate a unique UUID string"""
    return str(uuid.uuid4())


class User(Base):
    """A person. `username` is the unique handle, `display_name` is what the UI shows."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    username = Column(String(100), unique=True, index=True, nullable=False)
    display_name = Column(Text, nullable=False)
    password_hash = Column(String(255), nullable=False)

    # Optional profile picture in MinIO
    avatar_object_key = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    settings = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    created_challenges = relationship(
        "Challenge", back_populates="creator", foreign_keys="Challenge.created_by_id"
    )
    participations = relationship(
        "ChallengeParticipant",
        back_populates="user",
        foreign_keys="ChallengeParticipant.user_id",
        cascade="all, delete-orphan",
    )
    entries = relationship("Entry", back_populates="user", cascade="all, delete-orphan")
    external_identities = relationship(
        "ExternalIdentity", back_populates="user", cascade="all, delete-orphan"
    )


class ExternalIdentity(Base):
    """An optional SSO identity; local password accounts remain authoritative."""
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
        UniqueConstraint("provider", "user_id", name="uq_external_identity_provider_user"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, default="authentik")
    subject = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="external_identities")


class RefreshToken(Base):
    """Stores refresh tokens for JWT authentication"""
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(500), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens")


class UserSettings(Base):
    """Per-user preferences. Display-only — nothing here changes challenge maths."""
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # IANA name, e.g. "Asia/Kolkata". The UI uses it to label UTC periods with a
    # local date; period boundaries themselves stay UTC for everyone.
    timezone = Column(String(64), default="UTC")
    distance_unit = Column(String(10), default=Unit.KM)
    notifications_enabled = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="settings")


def friendship_pair(user_id_1: int, user_id_2: int) -> tuple[int, int]:
    """Order two user ids the way `friendships` stores them (see below)."""
    return (min(user_id_1, user_id_2), max(user_id_1, user_id_2))


class Friendship(Base):
    """One row per friendship, not two.

    The pair is stored canonically sorted (`user_a_id < user_b_id`, enforced by
    a CHECK) so a unique index can prevent duplicates and "are A and B friends"
    is a single lookup instead of an OR over both directions. Use
    `friendship_pair()` before querying or inserting.

    `requested_by_id` carries the direction that the sorted pair throws away,
    which is what makes a pending request renderable ("X wants to be friends").

    Friends of user X:
        SELECT CASE WHEN user_a_id = :x THEN user_b_id ELSE user_a_id END
        FROM friendships
        WHERE status = 'accepted' AND :x IN (user_a_id, user_b_id)
    """
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_friendship_pair"),
        CheckConstraint("user_a_id < user_b_id", name="ck_friendship_ordered"),
        Index("ix_friendships_user_b_status", "user_b_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)

    user_a_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_b_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    requested_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(20), default=FriendshipStatus.PENDING, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    responded_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user_a = relationship("User", foreign_keys=[user_a_id])
    user_b = relationship("User", foreign_keys=[user_b_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])


class Challenge(Base):
    """A competition between a group of friends.

    The same group can have any number of challenges, and a challenge can hold
    any number of friends — both are just rows in `challenge_participants`.

    Two pluggable behaviours, each a name resolved to a function at runtime:

    * `generator` decides the next target. `target_min`/`target_max` are the
      range the creator picked; a random generator draws from it, a training
      plan can clamp to it. `generator_config` holds algorithm-specific extras.
    * `win_rule` decides who won a period.

    Both fields here are the settings *currently in force*. Each period keeps
    its own copy of what it was played under — see ChallengePeriod.
    """
    __tablename__ = "challenges"

    id = Column(Integer, primary_key=True, index=True)
    challenge_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    created_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What's measured and how it's shown
    metric = Column(String(20), nullable=False)  # Metric
    unit = Column(String(10), nullable=False)  # Unit

    # How the next target gets picked
    generator = Column(String(50), default=Generator.RANDOM, nullable=False)
    generator_config = Column(JSON, nullable=True)
    target_min = Column(Float, nullable=False)
    target_max = Column(Float, nullable=False)

    # How the winner gets picked
    win_rule = Column(String(50), default=WinRule.HIGHEST_ABOVE_TARGET, nullable=False)
    win_rule_config = Column(JSON, nullable=True)

    # How long one target lasts. Daily today; the period rows below are already
    # timestamp-based so switching a challenge to hourly needs no schema change.
    period_type = Column(String(20), default=PeriodType.DAILY, nullable=False)

    # UTC. `ends_at` NULL means the challenge runs until someone ends it.
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), default=ChallengeStatus.ACTIVE, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    creator = relationship(
        "User", back_populates="created_challenges", foreign_keys=[created_by_id]
    )
    participants = relationship(
        "ChallengeParticipant", back_populates="challenge", cascade="all, delete-orphan"
    )
    periods = relationship(
        "ChallengePeriod", back_populates="challenge", cascade="all, delete-orphan"
    )


class ChallengeParticipant(Base):
    """Who is in a challenge."""
    __tablename__ = "challenge_participants"
    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_participant_per_challenge"),
    )

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(
        Integer, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status = Column(String(20), default=ParticipantStatus.INVITED, nullable=False)
    invited_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    joined_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    challenge = relationship("Challenge", back_populates="participants")
    user = relationship("User", back_populates="participations", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_id])


class ChallengePeriod(Base):
    """One generated target for one window of a challenge — "today's number".

    Stored as a timestamp window rather than a date so an hourly (or weekly)
    challenge fits the same table. `period_index` is the 0-based position in the
    challenge, handy for ordering and for generators that care how far into a
    plan they are ("day 12 of marathon training").

    `win_rule` is copied here from the challenge when the period is generated,
    and that copy — not the challenge's current value — is what judges this
    period. Winners are recomputed whenever an entry is added or edited, so
    without the snapshot, switching a challenge to a different rule would
    silently re-judge every past day the next time someone fixed a typo in an
    old entry. The generator needs no snapshot for the same reason in reverse:
    its output is already frozen in `target_value`.
    """
    __tablename__ = "challenge_periods"
    __table_args__ = (
        UniqueConstraint("challenge_id", "period_start", name="uq_period_per_challenge"),
        UniqueConstraint("challenge_id", "period_index", name="uq_period_index_per_challenge"),
        Index("ix_periods_challenge_start", "challenge_id", "period_start"),
    )

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(
        Integer, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )

    period_index = Column(Integer, nullable=False)
    # Half-open UTC window: an entry belongs here if period_start <= t < period_end
    period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    period_end = Column(DateTime(timezone=True), nullable=False)

    # The number everyone has to hit. Float covers both steps and kilometres.
    target_value = Column(Float, nullable=False)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())

    # The rule this period is judged by, snapshotted at generation time.
    win_rule = Column(String(50), nullable=False)
    win_rule_config = Column(JSON, nullable=True)

    # Set every time winners are recomputed; lets an edit to an old entry be
    # spotted as "needs re-evaluation".
    winner_evaluated_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    challenge = relationship("Challenge", back_populates="periods")
    entries = relationship("Entry", back_populates="period", cascade="all, delete-orphan")


class Entry(Base):
    """What one user actually did in one period.

    No row means they haven't logged anything yet. The winner for a period is
    stored as `is_winner` on the entries rather than a column on the period:
    ties just flag more than one row, "nobody hit the target" flags none, and
    editing an old entry only means recomputing the flags for that period.

        -- wins per user in a challenge
        SELECT e.user_id, count(*)
        FROM entries e JOIN challenge_periods p ON p.id = e.period_id
        WHERE p.challenge_id = :cid AND e.is_winner
        GROUP BY e.user_id
    """
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("period_id", "user_id", name="uq_entry_per_user_period"),
        Index("ix_entries_user_period", "user_id", "period_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    entry_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    period_id = Column(
        Integer, ForeignKey("challenge_periods.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Steps or kilometres, in the challenge's unit
    value = Column(Float, nullable=False)
    note = Column(Text, nullable=True)

    # When this entry first reached its period's target, for WinRule
    # FIRST_TO_TARGET. Stamped once and left alone, so editing the value upwards
    # later doesn't backdate someone into first place.
    target_reached_at = Column(DateTime(timezone=True), nullable=True)

    # Whether this entry won its period. Recomputed on every insert/edit.
    is_winner = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    period = relationship("ChallengePeriod", back_populates="entries")
    user = relationship("User", back_populates="entries")
    images = relationship(
        "EntryImage", back_populates="entry", cascade="all, delete-orphan"
    )


class EntryImage(Base):
    """Proof screenshot(s) for an entry, stored in MinIO. Always optional."""
    __tablename__ = "entry_images"

    id = Column(Integer, primary_key=True, index=True)
    image_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    entry_id = Column(
        Integer, ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # File metadata
    image_name = Column(Text, nullable=True)  # Original filename
    mime_type = Column(String(100), nullable=True)  # e.g. "image/jpeg"
    file_size = Column(Integer, nullable=True)  # Size in bytes
    file_format = Column(String(10), nullable=True)  # jpg, png, etc.

    # MinIO storage
    minio_object_key = Column(String(500), nullable=False)
    minio_bucket = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    entry = relationship("Entry", back_populates="images")
    user = relationship("User")
