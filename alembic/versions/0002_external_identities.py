"""add optional external identity links

Revision ID: 0002_external_identities
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_external_identities"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("name", sa.String(255)),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
        sa.UniqueConstraint("provider", "user_id", name="uq_external_identity_provider_user"),
    )


def downgrade():
    op.drop_table("external_identities")
