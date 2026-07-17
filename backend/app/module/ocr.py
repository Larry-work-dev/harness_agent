"""掃描版 PDF 的 OCR 備援：pypdf 抽不到文字層（沒有文字層、只有圖）時，
把每頁渲染成圖片丟給路由表選出的 OCR 模型辨識文字，取代空白結果。

upload（持久 RAG）跟 chat 的 ephemeral 附件注入都走 extract_text_smart()，
避免各自重寫一份「先 extract_text 再判斷要不要 OCR」的邏輯。
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.messages import HumanMessage

from app.config import create_model
from app.module import agent_config as cfg
from app.module import attachments as att
from app.module.logs import get as get_logger

log = get_logger("ocr")

_OCR_INSTRUCTION = (
    "請逐字辨識並輸出這張圖片裡的所有文字，只輸出文字內容本身，"
    "不要加任何說明、標記或翻譯；如果圖片沒有文字就輸出空白。"
)


@lru_cache(maxsize=64)
def ocr_pdf(rel: str) -> str:
    """逐頁把 PDF 渲染成圖片、用 OCR 模型辨識文字後合併。rel 含 uuid，同一份檔案的結果可安全快取，
    避免同一輪對話裡 upload（存 RAG）跟 chat ephemeral 各跑一次 OCR。"""
    pages = att.pdf_page_images(rel)
    primary, fallback = cfg.primary_vision_model("OCR")
    texts = []
    for i, url in enumerate(pages, 1):
        blocks = [{"type": "text", "text": _OCR_INSTRUCTION},
                  {"type": "image_url", "image_url": {"url": url}}]
        out = ""
        for mid in (primary, fallback):
            try:
                out = create_model(spec=cfg.model_spec(mid), temperature=0.0).invoke(
                    [HumanMessage(content=blocks)]).content or ""
                break
            except Exception as e:  # noqa: BLE001
                log.warning("ocr_pdf: 第 %d 頁模型 %s 失敗(%s)", i, mid, e)
        texts.append(out)
    text = "\n\n".join(t for t in texts if t.strip())
    log.info("ocr_pdf: %s 共 %d 頁，OCR 出 %d 字", rel, len(pages), len(text))
    return text


def extract_text_smart(rel: str) -> tuple[str, bool]:
    """抽文件文字；PDF 抽不到文字層（掃描版）時自動 fallback 用 OCR。回 (文字, 是否用了OCR)。"""
    text = att.extract_text(rel)
    if text.strip() or not rel.lower().endswith(".pdf"):
        return text, False
    log.info("extract_text_smart: %s 抽不到文字層，改用 OCR 逐頁辨識", rel)
    try:
        return ocr_pdf(rel), True
    except Exception as e:  # noqa: BLE001
        log.warning("extract_text_smart: %s OCR 失敗(%s)", rel, e)
        return "", False
