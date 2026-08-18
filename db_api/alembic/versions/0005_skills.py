"""add skills（使用者自建 http/prompt/code 技能）

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_skills_user_id", "skills", ["user_id"])
    op.create_index("ix_skills_kind_enabled", "skills", ["kind", "enabled"])


def downgrade():
    op.drop_index("ix_skills_kind_enabled", table_name="skills")
    op.drop_index("ix_skills_user_id", table_name="skills")
    op.drop_table("skills")
