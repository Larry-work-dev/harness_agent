"""add users.emp_id + user_permissions (RAG 檢索權限，依 emp_id)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("emp_id", sa.String, nullable=True))
    op.create_unique_constraint("uq_users_emp_id", "users", ["emp_id"])

    op.create_table(
        "user_permissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("emp_id", sa.String, nullable=False, unique=True),
        sa.Column("filter_criteria", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_user_permissions_emp_id", "user_permissions", ["emp_id"])


def downgrade():
    op.drop_index("ix_user_permissions_emp_id", table_name="user_permissions")
    op.drop_table("user_permissions")
    op.drop_constraint("uq_users_emp_id", "users", type_="unique")
    op.drop_column("users", "emp_id")
