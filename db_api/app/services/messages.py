"""訊息存取邏輯。"""
from datetime import datetime, timezone
from sqlalchemy import select
from app.module.models import Message, Conversation


def add_message(db, conversation_id, role, content, sources=None, attachments=None):
    db.add(Message(conversation_id=conversation_id, role=role, content=content,
                   sources=sources, attachments=attachments))
    c = db.get(Conversation, conversation_id)
    if c: c.updated_at = datetime.now(timezone.utc)
    db.commit()


def list_messages(db, conversation_id) -> list:
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id)
                      .order_by(Message.id)).all()
    return [{"id": m.id, "role": m.role, "content": m.content,
             "sources": m.sources, "attachments": m.attachments, "created_at": m.created_at} for m in rows]


def list_messages_after(db, conversation_id, after_id) -> list:
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id,
                      Message.id > after_id).order_by(Message.id)).all()
    return [{"id": m.id, "role": m.role, "content": m.content} for m in rows]
