"""路由層 —— 依決策樹決定「用哪個模型 profile」或「觸發哪個 workflow」。

安全原則：敏感資料判定必須是本地/規則式，絕不把疑似敏感內容送到雲端模型分類。
判斷點：
  1. 敏感資料（規則）→ 強制走 local profile（最優先，連手動覆寫都蓋不過）。
  2. 意圖命中（關鍵字）→ 觸發對應 workflow，跳過生成。
  3. 複雜度（啟發式）→ 選 cloud / mid / cheap。
之後可把 2、3 換成 embedding 語意比對 / 便宜模型分類，介面不變。
"""
import re

from workflows import load_workflows

# 敏感資料規則（可自行擴充）
_SENSITIVE_PATTERNS = [
    r"\b[A-Z][12]\d{8}\b",                 # 台灣身分證
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # 信用卡卡號
    r"\b\d{3}-?\d{2}-?\d{4}\b",            # SSN 形式
]
_SENSITIVE_WORDS = ["機密", "密件", "薪資", "salary", "confidential", "病歷", "身分證"]

# 複雜度啟發式：出現這些字視為需要工具 / 較複雜
_TOOL_HINTS = ["查", "檢索", "搜尋", "寄", "email", "計算", "分析", "整理", "報告", "程式", "code"]


def detect_sensitive(text: str) -> bool:
    if any(w.lower() in text.lower() for w in _SENSITIVE_WORDS):
        return True
    return any(re.search(p, text) for p in _SENSITIVE_PATTERNS)


def match_workflow(text: str) -> str | None:
    for wf in load_workflows():
        if any(trigger in text for trigger in wf.triggers):
            return wf.name
    return None


def classify_complexity(text: str) -> str:
    n = len(text)
    needs_tool = any(h.lower() in text.lower() for h in _TOOL_HINTS)
    if needs_tool or n > 200:
        return "cloud"
    if n > 60:
        return "mid"
    return "cheap"


def route(text: str) -> dict:
    """自動路由決策（不含手動覆寫；覆寫在 main 處理，但敏感守則仍最優先）。"""
    if detect_sensitive(text):
        return {"mode": "generate", "profile": "local", "reason": "含敏感資料，限本地模型"}
    wf = match_workflow(text)
    if wf:
        return {"mode": "workflow", "workflow": wf, "reason": "命中既有意圖"}
    profile = classify_complexity(text)
    return {"mode": "generate", "profile": profile, "reason": f"自動分級 → {profile}"}
