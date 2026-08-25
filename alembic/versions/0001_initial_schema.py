"""initial schema

Amarathon's full schema. The CURRENT_TIMESTAMP server defaults are deliberate:
that spelling is valid on both Postgres (deployed) and SQLite (local dev), which
`now()` is not.

Revision ID: 0001
Revises: 
Create Date: 2026-08-22 17:58:23.514139

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_uuid', sa.String(length=36), nullable=True),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('avatar_object_key', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_user_uuid'), 'users', ['user_uuid'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('challenges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('challenge_uuid', sa.String(length=36), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('metric', sa.String(length=20), nullable=False),
    sa.Column('unit', sa.String(length=10), nullable=False),
    sa.Column('generator', sa.String(length=50), nullable=False),
    sa.Column('generator_config', sa.JSON(), nullable=True),
    sa.Column('target_min', sa.Float(), nullable=False),
    sa.Column('target_max', sa.Float(), nullable=False),
    sa.Column('win_rule', sa.String(length=50), nullable=False),
    sa.Column('win_rule_config', sa.JSON(), nullable=True),
    sa.Column('period_type', sa.String(length=20), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_challenges_challenge_uuid'), 'challenges', ['challenge_uuid'], unique=True)
    op.create_index(op.f('ix_challenges_created_by_id'), 'challenges', ['created_by_id'], unique=False)
    op.create_index(op.f('ix_challenges_id'), 'challenges', ['id'], unique=False)
    op.create_index(op.f('ix_challenges_status'), 'challenges', ['status'], unique=False)
    op.create_table('friendships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_a_id', sa.Integer(), nullable=False),
    sa.Column('user_b_id', sa.Integer(), nullable=False),
    sa.Column('requested_by_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('user_a_id < user_b_id', name='ck_friendship_ordered'),
    sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_a_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_b_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_a_id', 'user_b_id', name='uq_friendship_pair')
    )
    op.create_index(op.f('ix_friendships_id'), 'friendships', ['id'], unique=False)
    op.create_index(op.f('ix_friendships_status'), 'friendships', ['status'], unique=False)
    op.create_index(op.f('ix_friendships_user_a_id'), 'friendships', ['user_a_id'], unique=False)
    op.create_index('ix_friendships_user_b_status', 'friendships', ['user_b_id', 'status'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=500), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refresh_tokens_id'), 'refresh_tokens', ['id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token'), 'refresh_tokens', ['token'], unique=True)
    op.create_table('user_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=True),
    sa.Column('distance_unit', sa.String(length=10), nullable=True),
    sa.Column('notifications_enabled', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_settings_id'), 'user_settings', ['id'], unique=False)
    op.create_table('challenge_participants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('challenge_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('invited_by_id', sa.Integer(), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('challenge_id', 'user_id', name='uq_participant_per_challenge')
    )
    op.create_index(op.f('ix_challenge_participants_challenge_id'), 'challenge_participants', ['challenge_id'], unique=False)
    op.create_index(op.f('ix_challenge_participants_id'), 'challenge_participants', ['id'], unique=False)
    op.create_index(op.f('ix_challenge_participants_user_id'), 'challenge_participants', ['user_id'], unique=False)
    op.create_table('challenge_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('challenge_id', sa.Integer(), nullable=False),
    sa.Column('period_index', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('target_value', sa.Float(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('win_rule', sa.String(length=50), nullable=False),
    sa.Column('win_rule_config', sa.JSON(), nullable=True),
    sa.Column('winner_evaluated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['challenge_id'], ['challenges.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('challenge_id', 'period_index', name='uq_period_index_per_challenge'),
    sa.UniqueConstraint('challenge_id', 'period_start', name='uq_period_per_challenge')
    )
    op.create_index(op.f('ix_challenge_periods_challenge_id'), 'challenge_periods', ['challenge_id'], unique=False)
    op.create_index(op.f('ix_challenge_periods_id'), 'challenge_periods', ['id'], unique=False)
    op.create_index(op.f('ix_challenge_periods_period_start'), 'challenge_periods', ['period_start'], unique=False)
    op.create_index('ix_periods_challenge_start', 'challenge_periods', ['challenge_id', 'period_start'], unique=False)
    op.create_table('entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entry_uuid', sa.String(length=36), nullable=True),
    sa.Column('period_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('target_reached_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_winner', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['period_id'], ['challenge_periods.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('period_id', 'user_id', name='uq_entry_per_user_period')
    )
    op.create_index(op.f('ix_entries_entry_uuid'), 'entries', ['entry_uuid'], unique=True)
    op.create_index(op.f('ix_entries_id'), 'entries', ['id'], unique=False)
    op.create_index(op.f('ix_entries_period_id'), 'entries', ['period_id'], unique=False)
    op.create_index(op.f('ix_entries_user_id'), 'entries', ['user_id'], unique=False)
    op.create_index('ix_entries_user_period', 'entries', ['user_id', 'period_id'], unique=False)
    op.create_table('entry_images',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('image_uuid', sa.String(length=36), nullable=True),
    sa.Column('entry_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('image_name', sa.Text(), nullable=True),
    sa.Column('mime_type', sa.String(length=100), nullable=True),
    sa.Column('file_size', sa.Integer(), nullable=True),
    sa.Column('file_format', sa.String(length=10), nullable=True),
    sa.Column('minio_object_key', sa.String(length=500), nullable=False),
    sa.Column('minio_bucket', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['entry_id'], ['entries.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_entry_images_entry_id'), 'entry_images', ['entry_id'], unique=False)
    op.create_index(op.f('ix_entry_images_id'), 'entry_images', ['id'], unique=False)
    op.create_index(op.f('ix_entry_images_image_uuid'), 'entry_images', ['image_uuid'], unique=True)
    op.create_index(op.f('ix_entry_images_user_id'), 'entry_images', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_entry_images_user_id'), table_name='entry_images')
    op.drop_index(op.f('ix_entry_images_image_uuid'), table_name='entry_images')
    op.drop_index(op.f('ix_entry_images_id'), table_name='entry_images')
    op.drop_index(op.f('ix_entry_images_entry_id'), table_name='entry_images')
    op.drop_table('entry_images')
    op.drop_index('ix_entries_user_period', table_name='entries')
    op.drop_index(op.f('ix_entries_user_id'), table_name='entries')
    op.drop_index(op.f('ix_entries_period_id'), table_name='entries')
    op.drop_index(op.f('ix_entries_id'), table_name='entries')
    op.drop_index(op.f('ix_entries_entry_uuid'), table_name='entries')
    op.drop_table('entries')
    op.drop_index('ix_periods_challenge_start', table_name='challenge_periods')
    op.drop_index(op.f('ix_challenge_periods_period_start'), table_name='challenge_periods')
    op.drop_index(op.f('ix_challenge_periods_id'), table_name='challenge_periods')
    op.drop_index(op.f('ix_challenge_periods_challenge_id'), table_name='challenge_periods')
    op.drop_table('challenge_periods')
    op.drop_index(op.f('ix_challenge_participants_user_id'), table_name='challenge_participants')
    op.drop_index(op.f('ix_challenge_participants_id'), table_name='challenge_participants')
    op.drop_index(op.f('ix_challenge_participants_challenge_id'), table_name='challenge_participants')
    op.drop_table('challenge_participants')
    op.drop_index(op.f('ix_user_settings_id'), table_name='user_settings')
    op.drop_table('user_settings')
    op.drop_index(op.f('ix_refresh_tokens_token'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_friendships_user_b_status', table_name='friendships')
    op.drop_index(op.f('ix_friendships_user_a_id'), table_name='friendships')
    op.drop_index(op.f('ix_friendships_status'), table_name='friendships')
    op.drop_index(op.f('ix_friendships_id'), table_name='friendships')
    op.drop_table('friendships')
    op.drop_index(op.f('ix_challenges_status'), table_name='challenges')
    op.drop_index(op.f('ix_challenges_id'), table_name='challenges')
    op.drop_index(op.f('ix_challenges_created_by_id'), table_name='challenges')
    op.drop_index(op.f('ix_challenges_challenge_uuid'), table_name='challenges')
    op.drop_table('challenges')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_user_uuid'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')
