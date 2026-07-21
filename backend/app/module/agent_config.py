"""Agent 設定載入層 —— 把「路由表」與「各 agent 的 prompt」外置成檔案。

設定目錄（AGENT_CONFIG_DIR，預設 backend/agent/）結構：
    CLAUDE.md              最高指導原則（每次生成注入 system prompt）
    routing_table.json     任務類型 → 最佳模型（離線評測產出）
    agents/planner.md      Planner：便宜 LLM 分類/拆解 prompt
    agents/worker.md       Worker：子任務執行者 persona（併入 Harness 的 extra_system）
    agents/critic.md       Critic：審核 Worker 產出的 prompt
    agents/orchestrator.md 多 subtask 時的組裝（assemble）階段 prompt
    agents/query_rewrite.md 查詢改寫器：RAG 檢索前把指代不明的原句改寫成獨立查詢

改這些 md/json 即可調整行為，不必動程式。docker 可把此目錄掛成 volume 以便即時編輯。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

AGENT_CONFIG_DIR = Path(
    os.environ.get("AGENT_CONFIG_DIR", str(Path(__file__).resolve().parents[2] / "agent"))
)

DEFAULT_CLAUDE = "你是一個繁體中文助理，請清楚、準確、簡潔地回答。"
DEFAULT_CLASSIFIER = (
    "你是任務分類器。可用類型：{{TASK_TYPES}}。"
    '單一任務回 {"composite": false, "task_type": "<類型>"}；'
    '複合任務回 {"composite": true, "subtasks": [{"desc": "...", "task_type": "<類型>"}]}。'
    "不明確用「語意分析」。只輸出 JSON。"
)
DEFAULT_ORCH = "整合各子任務結果成一份連貫的繁體中文回覆，只輸出最終回覆。"
DEFAULT_WORKER = "你是子任務執行者，只需完成交給你的這一個子任務，直接給出結果。"
DEFAULT_CRITIC = (
    '你是審核者。判斷輸出是否完成子任務、且（若有提供檢索來源）是否有根據來源而非憑空捏造。'
    '只輸出 JSON：通過 {"pass": true, "reason": "..."}；'
    '不通過 {"pass": false, "reason": "...", "feedback": "給 Worker 的具體修正指示"}。'
)
DEFAULT_QUERY_REWRITE = (
    "根據最近對話（若有）跟使用者原句，改寫成一句獨立、明確、適合語意檢索的查詢；"
    "已經明確獨立就原樣輸出。只輸出改寫後的查詢本身。"
)


def _read(rel: str, default: str) -> str:
    try:
        return (AGENT_CONFIG_DIR / rel).read_text(encoding="utf-8")
    except Exception:
        return default


# ---- 路由表 ----
@lru_cache(maxsize=1)
def routing_table() -> dict:
    try:
        return json.loads((AGENT_CONFIG_DIR / "routing_table.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def valid_task_types() -> list[str]:
    return list(routing_table().get("scores", {}).keys()) or ["語意分析"]


def _row(task_type: str) -> dict:
    for r in routing_table().get("routing_table", []):
        if r.get("task_type") == task_type:
            return r
    return {}


def model_hosting(model_id: str) -> str:
    for m in routing_table().get("models", []):
        if m.get("id") == model_id:
            return m.get("hosting", "cloud")
    return "cloud"


def local_default() -> str:
    """敏感資料時用的本地模型：優先 baseline（若為 local），否則第一個 local。"""
    b = routing_table().get("baseline", {}).get("model")
    if b and model_hosting(b) == "local":
        return b
    for m in routing_table().get("models", []):
        if m.get("hosting") == "local":
            return m["id"]
    return b or ""


def primary_model(task_type: str, local_only: bool = False) -> tuple[str, str]:
    """回傳 (primary, fallback) 模型 id。local_only 時只挑 hosting=local。"""
    row = _row(task_type)
    ranked = row.get("candidates_ranked") or []
    if local_only:
        locs = [c["model"] for c in ranked if model_hosting(c["model"]) == "local"]
        if locs:
            return locs[0], (locs[1] if len(locs) > 1 else locs[0])
        ld = local_default()
        return ld, ld
    p = row.get("primary")
    f = row.get("fallback") or p
    if not p:
        ld = local_default()
        return ld, ld
    return p, f


def vision_models() -> set[str]:
    """支援 vision（能吃圖）的模型白名單。

    優先用環境變數 VISION_MODELS（逗號分隔）；未設時從路由表推導：
    baseline（Qwen3-VL）＋ OCR/圖面理解 這兩個影像任務的 primary。
    ⚠ 白名單內的模型必須在你 gateway 上真的支援 vision，否則呼叫會失敗（會退 fallback）。
    """
    raw = os.environ.get("VISION_MODELS")
    if raw:
        return {m.strip() for m in raw.split(",") if m.strip()}
    vm = {local_default()}
    for tt in ("OCR", "圖面理解"):
        p = _row(tt).get("primary")
        if p:
            vm.add(p)
    return {m for m in vm if m}


def primary_vision_model(task_type: str, local_only: bool = False) -> tuple[str, str]:
    """在『視覺白名單』內，挑該任務類型分數最高的 (primary, fallback)。

    local_only=True 時再收斂成只挑白名單裡 hosting=local 的（敏感資料用）。
    白名單內沒有可用者 → 退回 local_default（Qwen3-VL）。
    """
    vm = vision_models()
    ranked = [c["model"] for c in (_row(task_type).get("candidates_ranked") or [])
              if c["model"] in vm and (not local_only or model_hosting(c["model"]) == "local")]
    if ranked:
        return ranked[0], (ranked[1] if len(ranked) > 1 else ranked[0])
    ld = local_default()
    return ld, ld


def model_spec(model_id: str) -> dict:
    """把路由表的模型 id 轉成呼叫 gateway 用的 spec（gateway 依 model 名分流）。"""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "model": model_id,
        "api_key": os.environ.get("LLM_API_KEY", "sk-noauth"),
    }


# ---- prompts ----
def claude_md() -> str:
    return _read("CLAUDE.md", DEFAULT_CLAUDE)


def planner_prompt() -> str:
    return _read("agents/planner.md", DEFAULT_CLASSIFIER)


def orchestrator_prompt() -> str:
    return _read("agents/orchestrator.md", DEFAULT_ORCH)


def worker_prompt() -> str:
    return _read("agents/worker.md", DEFAULT_WORKER)


def critic_prompt() -> str:
    return _read("agents/critic.md", DEFAULT_CRITIC)


def query_rewrite_prompt() -> str:
    return _read("agents/query_rewrite.md", DEFAULT_QUERY_REWRITE)
