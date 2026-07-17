"""scope doc_chunks to conversation_id (RAG was leaking across conversations)

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # 舊資料只綁 user_id，無法回溯屬於哪個對話，直接清空（RAG 片段可重新上傳補回）。
    op.execute("DELETE FROM doc_chunks")
    op.add_column(
        "doc_chunks",
        sa.Column("conversation_id", sa.String,
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
    )


def downgrade():
    op.drop_column("doc_chunks", "conversation_id")
