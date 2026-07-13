"""長期記憶存取邏輯（pgvector 相似度查詢）。"""
from sqlalchemy import select
from app.module.models import Memory


def add_memory(db, user_id, content, embedding=None):
    db.add(Memory(user_id=user_id, content=content, embedding=embedding)); db.commit()


def list_memories(db, user_id) -> list:
    rows = db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.id)).all()
    return [{"id": m.id, "content": m.content, "created_at": m.created_at} for m in rows]


def search_memories(db, user_id, embedding, k=5) -> list:
    dist = Memory.embedding.cosine_distance(embedding)
    rows = db.execute(
        select(Memory.content, (1 - dist).label("similarity"))
        .where(Memory.user_id == user_id, Memory.embedding.is_not(None))
        .order_by(dist).limit(k)
    ).all()
    return [{"content": r.content, "similarity": float(r.similarity)} for r in rows]


def delete_memory(db, memory_id, user_id):
    m = db.get(Memory, memory_id)
    if m and m.user_id == user_id: db.delete(m); db.commit()
