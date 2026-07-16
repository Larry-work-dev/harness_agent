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
from app.module.logs import get as get_logger
from app.module.skills.knowledge_search import _knowledge_search

log = get_logger("orchestrator")

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
                log.info("classify → 複合任務 %d 步: %s",
                         len(subs), [(s["task_type"], s["desc"]) for s in subs])
                return {"composite": True, "subtasks": subs}
        tt = plan.get("task_type")
        if tt in types:
            log.info("classify → 單一任務: %s", tt)
            return {"composite": False, "task_type": tt}
        log.warning("classify → 解析結果不在類型內(%r)，用預設 %s", plan, default_tt)
    except Exception as e:  # noqa: BLE001
        log.warning("classify → 分類器呼叫失敗(%s)，用預設 %s", e, default_tt)
    return {"composite": False, "task_type": default_tt}


def classify_image_task(text: str) -> str:
    """判斷附圖需求是 OCR（抽文字）還是 圖面理解（理解內容）。便宜 LLM；失敗預設圖面理解。"""
    types = cfg.valid_task_types()
    img_types = [t for t in ("OCR", "圖面理解") if t in types] or ["圖面理解"]
    prompt = (f"使用者上傳了圖片並說：「{text or '（沒有文字，只有圖）'}」。\n"
              "這個需求比較接近哪一種？只回一個詞：\n"
              "- OCR：主要是要抽取／辨識圖片中的文字\n"
              "- 圖面理解：要理解圖表、照片、示意圖的內容或含義\n"
              "只輸出 OCR 或 圖面理解，不要其他字。")
    try:
        out = create_model(spec=cfg.model_spec(_CLASSIFIER_MODEL or cfg.local_default())).invoke(
            [{"role": "user", "content": prompt}]).content.strip()
        for t in img_types:
            if t in out:
                log.info("classify_image_task → %s", t)
                return t
    except Exception as e:  # noqa: BLE001
        log.warning("classify_image_task 失敗(%s)，預設 圖面理解", e)
    return "圖面理解" if "圖面理解" in img_types else img_types[0]


def run_subtask(sub: dict, prior: str, claude: str) -> tuple[str, str, list]:
    """執行單一子任務。檢索型（RAG切片）先查公司知識庫再交模型；回 (模型, 產出, 來源)。"""
    tt, desc = sub["task_type"], sub["desc"]
    retrieved, sources = "", []
    if tt in RETRIEVAL_TASK_TYPES:
        q = desc if not prior else f"{desc}（脈絡：{prior[:400]}）"
        retrieved, sources = retrieve(q)
        log.info("subtask[%s] 檢索公司知識庫: 命中 %d 筆來源, 內容 %d 字",
                 tt, len(sources), len(retrieved))

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
            log.info("subtask[%s] 用模型 %s 完成（%d 字）", tt, mid, len(out or ""))
            return mid, out, sources
        except Exception as e:  # noqa: BLE001
            log.warning("subtask[%s] 模型 %s 失敗(%s)，嘗試 fallback", tt, mid, e)
    log.error("subtask[%s] 主/備模型都失敗", tt)
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
