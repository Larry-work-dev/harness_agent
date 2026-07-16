"""上傳 API：接收圖片/文件，存檔；文件抽字切片 embedding 進 doc_chunks（RAG）。

回傳每個檔的中繼資料（name/kind/mime/path/chars），前端據此顯示 chip 並在送出
訊息時帶回 /chat。文件同時：ephemeral（chat 當回合注入）＋ 持久（此處入 RAG）。
"""
from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.config import create_embedder
from app.module import attachments as att
from app.module import db_client as db
from app.module.deps import current_user, require_conversation
from app.module.logs import get as get_logger

router = APIRouter()
log = get_logger("uploads")


@router.post("/uploads")
async def upload(
    conversation_id: str = Form(...),
    files: list[UploadFile] = File(...),
    user=Depends(current_user),
):
    require_conversation(conversation_id, user)   # 確認是本人可存取的對話
    embedder = None
    results = []
    for f in files:
        data = await f.read()
        meta = att.save_upload(conversation_id, f.filename, data)
        log.info("upload: %s kind=%s bytes=%d", meta["name"], meta["kind"], meta["bytes"])

        if meta["kind"] == "doc":
            text = att.extract_text(meta["path"])
            meta["chars"] = len(text)
            chunks = att.chunk_text(text)
            # 進 RAG：切片 embedding 後存 doc_chunks（embedding 失敗則跳過，仍可 ephemeral 用）
            if chunks:
                try:
                    embedder = embedder or create_embedder()
                    vecs = embedder.embed_documents(chunks)
                    db.add_doc_chunks(user["id"], meta["name"],
                                      [{"content": c, "embedding": v} for c, v in zip(chunks, vecs)])
                    meta["ingested"] = len(chunks)
                    log.info("upload: %s 抽字 %d 字 → 切 %d 片段進 RAG",
                             meta["name"], meta["chars"], len(chunks))
                except Exception as e:  # noqa: BLE001
                    meta["ingested"] = 0
                    meta["ingest_error"] = str(e)
                    log.warning("upload: %s 進 RAG 失敗(%s)，仍可 ephemeral 使用", meta["name"], e)
        results.append(meta)
    return {"attachments": results}
