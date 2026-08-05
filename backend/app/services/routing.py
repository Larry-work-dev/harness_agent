"""路由層 —— 前門決策：先規則式擋敏感資料，再用 LLM 意圖路由決定怎麼走。

判斷點（順序即優先級）：
  1. 敏感資料（規則式，最優先）→ 強制走 local profile，連手動覆寫都蓋不過，
     而且直接跳過意圖路由（絕不把疑似敏感內容送去做 LLM 分類）。
  2. 意圖路由（LLM，對齊公司現有入口的 7 類）→ orchestrator.classify_intent()：
       - leave / sign / contact / system_dev → 觸發對應 workflow（既定流程，不經生成）
       - translate → auto_route，強制 task_type「文件翻譯」
       - summary   → auto_route，強制 task_type「語意分析」
       - kbchat    → auto_route（開放式，走完整 Planner/RAG 管道；一般問答、公司規範
                     查詢、產品/工程問題、檔案問答、文件產生等都在這條）

（舊版用 embedding 對 workflow 意圖範例句算 cosine 的做法已被 LLM 意圖路由取代。）
"""
import re

from app.module.logs import get as get_logger
from app.services import orchestrator

log = get_logger("routing")

# 敏感資料規則（可自行擴充）
_SENSITIVE_PATTERNS = [
    r"\b[A-Z][12]\d{8}\b",                          # 台灣身分證
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # 信用卡卡號
    r"\b\d{3}-?\d{2}-?\d{4}\b",                      # SSN 形式
]
_SENSITIVE_WORDS = ["機密", "密件", "薪資", "salary", "confidential", "病歷", "身分證"]

# 意圖 → 觸發哪個 workflow（既定流程，不經模型生成）。
# key 是 classify_intent 回的意圖，value 是 workflows/ 底下的 WORKFLOW.name。
_WORKFLOW_INTENTS = {
    "leave": "leave_request",
    "sign": "sign",
    "contact": "contact",
    "system_dev": "system_dev",
}
# 意圖 → 強制的 task_type（走 auto_route，但跳過 Planner 重新分類）。
_FORCED_TASK_TYPE = {
    "translate": "文件翻譯",
    "summary": "語意分析",
}


def detect_sensitive(text: str) -> bool:
    if any(w.lower() in text.lower() for w in _SENSITIVE_WORDS):
        return True
    return any(re.search(p, text) for p in _SENSITIVE_PATTERNS)


def route(text: str) -> dict:
    """前門決策：敏感（規則）優先，其餘交給 LLM 意圖路由。

    回傳的 decision 一律帶 intent / keywords（keywords 供 workflow 或下游使用，
    例如 contact 意圖抓到的員工編號/姓名）。
    """
    if detect_sensitive(text):
        log.info("route → 敏感資料，限本地模型（跳過意圖路由）")
        return {"mode": "generate", "profile": "local", "reason": "含敏感資料，限本地模型",
                "intent": "sensitive", "keywords": []}

    result = orchestrator.classify_intent(text)
    intent, keywords = result["intent"], result["keywords"]

    if intent in _WORKFLOW_INTENTS:
        wf = _WORKFLOW_INTENTS[intent]
        log.info("route → workflow:%s（意圖 %s）", wf, intent)
        return {"mode": "workflow", "workflow": wf, "intent": intent, "keywords": keywords,
                "reason": f"意圖：{intent} → 既定流程 {wf}"}

    if intent in _FORCED_TASK_TYPE:
        tt = _FORCED_TASK_TYPE[intent]
        log.info("route → auto_route，強制 task_type=%s（意圖 %s）", tt, intent)
        return {"mode": "auto_route", "forced_task_type": tt, "intent": intent, "keywords": keywords,
                "reason": f"意圖：{intent} → {tt}"}

    log.info("route → auto_route（意圖 kbchat，開放式，交給 Planner）")
    return {"mode": "auto_route", "intent": "kbchat", "keywords": keywords,
            "reason": "意圖：kbchat（開放式，查路由表/RAG）"}
