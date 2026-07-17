"""上傳 API：接收圖片/文件，存檔；文件抽字切片 embedding 進 doc_chunks（RAG）。

回傳每個檔的中繼資料（name/kind/mime/path/chars），前端據此顯示 chip 並在送出
訊息時帶回 /chat。文件同時：ephemeral（chat 當回合注入）＋ 持久（此處入 RAG）。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from app.config import create_embedder
from app.module import attachments as att
from app.module import db_client as db
from app.module import ocr
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
            # PDF 抽不到文字層（掃描版、沒有文字只有圖）時，extract_text_smart 會自動 fallback 用 OCR
            text, meta["ocr"] = ocr.extract_text_smart(meta["path"])
            meta["chars"] = len(text)
            chunks = att.chunk_text(text)
            # 進 RAG：切片 embedding 後存 doc_chunks（embedding 失敗則跳過，仍可 ephemeral 用）
            if chunks:
                try:
                    embedder = embedder or create_embedder()
                    vecs = embedder.embed_documents(chunks)
                    db.add_doc_chunks(user["id"], conversation_id, meta["name"],
                                      [{"content": c, "embedding": v} for c, v in zip(chunks, vecs)])
                    meta["ingested"] = len(chunks)
                    log.info("upload: %s %s抽字 %d 字 → 切 %d 片段進 RAG",
                             meta["name"], "OCR " if meta["ocr"] else "", meta["chars"], len(chunks))
                except Exception as e:  # noqa: BLE001
                    meta["ingested"] = 0
                    meta["ingest_error"] = str(e)
                    log.warning("upload: %s 進 RAG 失敗(%s)，仍可 ephemeral 使用", meta["name"], e)
        results.append(meta)
    return {"attachments": results}


@router.get("/attachments/{conversation_id}/{filename}")
def download_attachment(conversation_id: str, filename: str, user=Depends(current_user)):
    """重新進來對話、往上滾動看到先前上傳的附件時，用這個下載原始檔案。"""
    require_conversation(conversation_id, user)   # 確認是本人可存取的對話
    try:
        data = att.read_bytes(f"{conversation_id}/{filename}")
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "找不到檔案")
    display_name = filename.split("__", 1)[-1]
    mime = att.image_mime(filename) if att.kind_of(filename) == "image" else "application/octet-stream"
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(display_name)}"
    })
