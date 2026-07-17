"""上傳文件切片存取（app 端 RAG，pgvector 相似度）。綁 conversation_id：
檢索範圍是「這個對話」，不會混進使用者其他對話上傳過的文件；
刪對話時交給 FK ondelete=CASCADE 自動清掉，這裡不用另外處理。"""
from sqlalchemy import select, func, delete
from app.module.models import DocChunk


def add_chunks(db, user_id, conversation_id, source_name, chunks):
    """chunks: list[{content, embedding}]。"""
    for i, c in enumerate(chunks):
        db.add(DocChunk(user_id=user_id, conversation_id=conversation_id, source_name=source_name,
                        chunk_index=i, content=c["content"], embedding=c.get("embedding")))
    db.commit()
    return {"added": len(chunks)}


def search_chunks(db, conversation_id, embedding, k=4):
    dist = DocChunk.embedding.cosine_distance(embedding)
    rows = db.execute(
        select(DocChunk.content, DocChunk.source_name, (1 - dist).label("similarity"))
        .where(DocChunk.conversation_id == conversation_id, DocChunk.embedding.is_not(None))
        .order_by(dist).limit(k)
    ).all()
    return [{"content": r.content, "source_name": r.source_name,
             "similarity": float(r.similarity)} for r in rows]


def list_sources(db, conversation_id):
    rows = db.execute(
        select(DocChunk.source_name, func.count(DocChunk.id).label("chunks"))
        .where(DocChunk.conversation_id == conversation_id).group_by(DocChunk.source_name)
    ).all()
    return [{"source_name": r.source_name, "chunks": int(r.chunks)} for r in rows]


def delete_source(db, conversation_id, source_name):
    db.execute(delete(DocChunk).where(DocChunk.conversation_id == conversation_id,
                                      DocChunk.source_name == source_name))
    db.commit()
    return {"ok": True}
