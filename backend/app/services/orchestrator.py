"""Orchestrator —— 開放式任務的分類與（複合任務的）拆解執行。

流程（對應 mermaid 的「開放式」分支）：
  1. classify()：便宜 LLM 一次判斷 單一/複合 + task_type / subtasks。
  2. 單一任務：由 chat 直接查路由表取最佳模型生成。
  3. 複合任務：run_subtask() 逐一執行（後者可看前者結果），assemble() 組裝。

模型皆依 routing_table 的 task_type → 最佳模型；主模型失敗改用 fallback。
"""
from __future__ import annotations

import json
import re

from app.config import create_model
from app.module import agent_config as cfg
from app.module.skills.knowledge_search import _knowledge_search

# 分類器用的模型（預設本地 baseline；可用環境變數指定）
import os
_CLASSIFIER_MODEL = os.environ.get("AGENT_CLASSIFIER_MODEL") or cfg.local_default()

# 這些任務類型需要「查公司知識庫」——sub-agent 會先呼叫 RAG 再交給模型（不需 gateway tool-calling）
RETRIEVAL_TASK_TYPES = {t.strip() for t in
                        os.environ.get("RETRIEVAL_TASK_TYPES", "RAG切片").split(",") if t.strip()}


def retrieve(query: str) -> tuple[str, list]:
    """呼叫公司知識庫（RAG），回 (檢索內容, 來源清單)。失敗時回錯誤訊息與空來源。"""
    return _knowledge_search(query)


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rstrip("`").strip()
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0)) if m else {}


def classify(text: str) -> dict:
    """便宜 LLM 分類：回 {composite, task_type} 或 {composite, subtasks}。失敗→單一語意分析。"""
    types = cfg.valid_task_types()
    default_tt = "語意分析" if "語意分析" in types else types[0]
    sys = cfg.classifier_prompt().replace("{{TASK_TYPES}}", "、".join(types))
    try:
        model = create_model(spec=cfg.model_spec(_CLASSIFIER_MODEL or cfg.local_default()))
        out = model.invoke([{"role": "system", "content": sys},
                            {"role": "user", "content": text}]).content
        plan = _parse_json(out)
        if plan.get("composite"):
            subs = [s for s in plan.get("subtasks", [])
                    if isinstance(s, dict) and s.get("task_type") in types and s.get("desc")]
            if len(subs) >= 2:
                return {"composite": True, "subtasks": subs}
        tt = plan.get("task_type")
        if tt in types:
            return {"composite": False, "task_type": tt}
    except Exception:
        pass
    return {"composite": False, "task_type": default_tt}


def run_subtask(sub: dict, prior: str, claude: str) -> tuple[str, str, list]:
    """執行單一子任務。檢索型（RAG切片）先查公司知識庫再交模型；回 (模型, 產出, 來源)。"""
    tt, desc = sub["task_type"], sub["desc"]
    retrieved, sources = "", []
    if tt in RETRIEVAL_TASK_TYPES:
        q = desc if not prior else f"{desc}（脈絡：{prior[:400]}）"
        retrieved, sources = retrieve(q)

    primary, fallback = cfg.primary_model(tt)
    prompt = f"{claude}\n\n[子任務類型] {tt}\n[要完成的事] {desc}\n"
    if retrieved:
        prompt += (f"\n[公司知識庫檢索結果，請只根據這些內容，並用 [n] 標註來源]\n{retrieved}\n")
    if prior:
        prompt += f"\n[前面步驟的結果，供你參考]\n{prior}\n"
    prompt += "\n請只完成這個子任務，直接給出結果。"
    for mid in (primary, fallback):
        try:
            out = create_model(spec=cfg.model_spec(mid)).invoke(
                [{"role": "user", "content": prompt}]).content
            return mid, out, sources
        except Exception:
            continue
    return primary, "（此子任務執行失敗）", sources


def assemble(original: str, results: list[dict], claude: str) -> str:
    """組裝各子任務結果成最終回覆（用語意分析類型的最佳模型）。"""
    body = "\n\n".join(f"[{r['task_type']}]\n{r['output']}" for r in results)
    prompt = (f"{claude}\n\n{cfg.orchestrator_prompt()}\n\n"
              f"使用者原始需求：{original}\n\n各子任務結果：\n{body}")
    mid, fb = cfg.primary_model("語意分析")
    for m in (mid, fb):
        try:
            return create_model(spec=cfg.model_spec(m)).invoke(
                [{"role": "user", "content": prompt}]).content
        except Exception:
            continue
    # 全失敗就直接串接
    return "\n\n".join(r["output"] for r in results)
