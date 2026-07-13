"""對話存取邏輯。"""
import uuid
from sqlalchemy import select
from app.module.models import Conversation


def _full(c: Conversation) -> dict:
    return {"id": c.id, "workspace_id": c.workspace_id, "title": c.title,
            "mode": c.mode, "model": c.model, "summary": c.summary,
            "summary_until_id": c.summary_until_id,
            "created_at": c.created_at, "updated_at": c.updated_at}


def create_conversation(db, workspace_id, title="新對話") -> dict:
    c = Conversation(id=uuid.uuid4().hex, workspace_id=workspace_id, title=title)
    db.add(c); db.commit()
    return {"id": c.id, "title": c.title, "workspace_id": c.workspace_id}


def list_conversations(db, workspace_id) -> list:
    rows = db.scalars(select(Conversation).where(Conversation.workspace_id == workspace_id)
                      .order_by(Conversation.updated_at.desc())).all()
    return [{"id": c.id, "title": c.title, "mode": c.mode, "model": c.model,
             "updated_at": c.updated_at} for c in rows]


def get_conversation(db, cid):
    c = db.get(Conversation, cid)
    return _full(c) if c else None


def rename_conversation(db, cid, title):
    c = db.get(Conversation, cid)
    if c: c.title = title; db.commit()


def set_summary(db, cid, summary, summary_until_id):
    c = db.get(Conversation, cid)
    if c: c.summary = summary; c.summary_until_id = summary_until_id; db.commit()


def set_mode(db, cid, mode, model):
    c = db.get(Conversation, cid)
    if c: c.mode = mode; c.model = model; db.commit()


def delete_conversation(db, cid):
    c = db.get(Conversation, cid)
    if c: db.delete(c); db.commit()
