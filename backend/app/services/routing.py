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

# 複合任務的判斷與 task_type 分類已移到 orchestrator（便宜 LLM 分類）+ routing_table。
# routing 只保留規則式的「敏感」與語意式的「意圖」。

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


def _phrases(wf) -> list[str]:
    """語意比對用的句子：優先用意圖範例句，沒有才退回關鍵字。"""
    return wf.examples or wf.triggers


def _ensure_trigger_embeddings(embedder) -> None:
    """把所有 workflow 的意圖範例句批次 embed 進快取（只算沒算過的）。"""
    missing = [p for wf in load_workflows() for p in _phrases(wf) if p not in _trigger_emb]
    if not missing:
        return
    embs = embedder.embed_documents(missing)
    for p, e in zip(missing, embs):
        _trigger_emb[p] = e


def match_workflow(text: str, embedder=None) -> str | None:
    """意圖比對：有 embedder 走語意（比對意圖範例句）、否則關鍵字；embedding 失敗自動退回關鍵字。"""
    if embedder is None:
        return _match_keyword(text)
    try:
        _ensure_trigger_embeddings(embedder)
        q = embedder.embed_query(text)
    except Exception:
        return _match_keyword(text)   # embedding 服務有問題就降級

    best_name, best_sim = None, -1.0
    for wf in load_workflows():
        for phrase in _phrases(wf):
            emb = _trigger_emb.get(phrase)
            if emb is None:
                continue
            sim = _cosine(q, emb)
            if sim > best_sim:
                best_sim, best_name = sim, wf.name
    return best_name if best_sim >= WF_MATCH_THRESHOLD else None


def classify_complexity(text: str) -> str:  # 保留相容，已不用於路由
    return "cheap"


def route(text: str, embedder=None) -> dict:
    """規則+意圖層決策。複雜度分級已移除；開放式任務改由 orchestrator 查路由表。"""
    if detect_sensitive(text):
        return {"mode": "generate", "profile": "local", "reason": "含敏感資料，限本地模型"}
    wf = match_workflow(text, embedder)
    if wf:
        return {"mode": "workflow", "workflow": wf, "reason": "命中既有意圖"}
    return {"mode": "auto_route", "reason": "開放式任務，查路由表"}
