"""路由層 —— 依決策樹決定「用哪個模型 profile」或「觸發哪個 workflow」。

安全原則：敏感資料判定必須是本地/規則式，絕不把疑似敏感內容送到雲端模型分類。
判斷點：
  1. 敏感資料（規則）→ 強制走 local profile（最優先，連手動覆寫都蓋不過）。
  2. 意圖命中 → 觸發對應 workflow，跳過生成。
       ‑ 有 embedder 時用「語意比對」：訊息與各 workflow 的 trigger 範例算 cosine，
         取最相近且超過門檻者。trigger 的 embedding 會快取，首次呼叫批次算一次。
       ‑ 沒有 embedder（或 embedding 服務失敗）時，退回關鍵字比對。
  3. 複雜度（啟發式）→ 選 cloud / mid / cheap。
"""
import math
import os
import re

from app.module.workflows import load_workflows

# 敏感資料規則（可自行擴充）
_SENSITIVE_PATTERNS = [
    r"\b[A-Z][12]\d{8}\b",                          # 台灣身分證
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # 信用卡卡號
    r"\b\d{3}-?\d{2}-?\d{4}\b",                      # SSN 形式
]
_SENSITIVE_WORDS = ["機密", "密件", "薪資", "salary", "confidential", "病歷", "身分證"]

# 複雜度啟發式
_TOOL_HINTS = ["查", "檢索", "搜尋", "寄", "email", "計算", "分析", "整理", "報告", "程式", "code"]

# workflow 意圖比對的相似度門檻（可用環境變數調）
WF_MATCH_THRESHOLD = float(os.environ.get("WF_MATCH_THRESHOLD", "0.62"))

# trigger 文字 → embedding 的快取（workflow 是靜態的，算一次即可）
_trigger_emb: dict[str, list[float]] = {}


def detect_sensitive(text: str) -> bool:
    if any(w.lower() in text.lower() for w in _SENSITIVE_WORDS):
        return True
    return any(re.search(p, text) for p in _SENSITIVE_PATTERNS)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _match_keyword(text: str) -> str | None:
    for wf in load_workflows():
        if any(trigger in text for trigger in wf.triggers):
            return wf.name
    return None


def _ensure_trigger_embeddings(embedder) -> None:
    """把所有 workflow 的 trigger 批次 embed 進快取（只算沒算過的）。"""
    missing = [t for wf in load_workflows() for t in wf.triggers if t not in _trigger_emb]
    if not missing:
        return
    embs = embedder.embed_documents(missing)
    for t, e in zip(missing, embs):
        _trigger_emb[t] = e


def match_workflow(text: str, embedder=None) -> str | None:
    """意圖比對：有 embedder 走語意、否則走關鍵字；embedding 失敗自動退回關鍵字。"""
    if embedder is None:
        return _match_keyword(text)
    try:
        _ensure_trigger_embeddings(embedder)
        q = embedder.embed_query(text)
    except Exception:
        return _match_keyword(text)   # embedding 服務有問題就降級

    best_name, best_sim = None, -1.0
    for wf in load_workflows():
        for trig in wf.triggers:
            emb = _trigger_emb.get(trig)
            if emb is None:
                continue
            sim = _cosine(q, emb)
            if sim > best_sim:
                best_sim, best_name = sim, wf.name
    return best_name if best_sim >= WF_MATCH_THRESHOLD else None


def classify_complexity(text: str) -> str:
    n = len(text)
    needs_tool = any(h.lower() in text.lower() for h in _TOOL_HINTS)
    if needs_tool or n > 200:
        return "cloud"
    if n > 60:
        return "mid"
    return "cheap"


def route(text: str, embedder=None) -> dict:
    """自動路由決策（不含手動覆寫；覆寫在 chat 處理，但敏感守則仍最優先）。"""
    if detect_sensitive(text):
        return {"mode": "generate", "profile": "local", "reason": "含敏感資料，限本地模型"}
    wf = match_workflow(text, embedder)
    if wf:
        return {"mode": "workflow", "workflow": wf, "reason": "命中既有意圖"}
    profile = classify_complexity(text)
    return {"mode": "generate", "profile": profile, "reason": f"自動分級 → {profile}"}
