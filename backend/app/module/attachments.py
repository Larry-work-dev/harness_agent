"""附件處理：儲存上傳檔、抽出文件文字、圖片轉 data URL、切片。

檔案存後端具名卷 ATTACHMENTS_ROOT/{conversation_id}/{uuid}__{filename}；
DB 只存中繼資料（見 messages.attachments）。路徑一律做安全檢查。
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import uuid
from pathlib import Path

ATTACHMENTS_ROOT = Path(os.environ.get("ATTACHMENTS_ROOT", "/data/attachments"))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DOC_EXTS = {".pdf", ".docx", ".txt", ".md"}
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp"}


def kind_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def image_mime(filename: str) -> str:
    return _MIME.get(Path(filename).suffix.lower(), "application/octet-stream")


def _safe(rel: str) -> Path:
    base = ATTACHMENTS_ROOT.resolve()
    p = (base / rel).resolve()
    if p != base and not str(p).startswith(str(base) + os.sep):
        raise ValueError("非法附件路徑")
    return p


def save_upload(conversation_id: str, filename: str, data: bytes) -> dict:
    """存檔並回中繼資料 dict。"""
    safe_name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", Path(filename).name) or "file"
    rel = f"{conversation_id}/{uuid.uuid4().hex}__{safe_name}"
    p = _safe(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"name": Path(filename).name, "kind": kind_of(filename),
            "mime": image_mime(filename) if kind_of(filename) == "image" else "",
            "path": rel, "bytes": len(data)}


def read_bytes(rel: str) -> bytes:
    return _safe(rel).read_bytes()


def delete_conversation_files(conversation_id: str) -> None:
    """刪對話時一併清掉磁盤上的附件（doc_chunks/messages 交給 DB FK cascade，這裡只管檔案）。"""
    shutil.rmtree(_safe(conversation_id), ignore_errors=True)


def image_data_url(rel: str, mime: str = "") -> str:
    data = read_bytes(rel)
    mime = mime or image_mime(rel)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ---- 文件抽字 ----
def extract_text(rel: str) -> str:
    ext = Path(rel).suffix.lower()
    raw = read_bytes(rel)
    if ext in {".txt", ".md"}:
        return raw.decode("utf-8", errors="replace")
    if ext == ".docx":
        import io
        from docx import Document
        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    if ext == ".pdf":
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return ""


def pdf_page_images(rel: str, dpi: int = 150) -> list[str]:
    """把 PDF 每一頁渲染成 PNG data URL 清單，給掃描版 PDF 的 OCR 備援用。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=read_bytes(rel), filetype="pdf")
    return ["data:image/png;base64," + base64.b64encode(page.get_pixmap(dpi=dpi).tobytes("png")).decode()
            for page in doc]


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += size - overlap
    return chunks
