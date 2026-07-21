"""Orchestrator —— 開放式任務的 Planner / Worker prompt 組裝 / Critic 三代理支援層。

流程：
  1. plan()：Planner（便宜 LLM）判斷 單一/複合，一律回傳 >=1 筆 subtasks。
  2. chat.py 的 run_plan() 逐一用 Harness 執行每個 subtask（Worker），
     本模組只負責組 Worker 的 extra_system prompt（build_worker_prompt），
     不碰 Harness/SSE，那是 chat.py 的職責。
  3. review()：Critic 審核 Worker 產出，不通過時 chat.py 會帶著 feedback 重跑一次。
  4. assemble()：多個 subtask 時，組裝成一份連貫回覆。

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

# 分類器/審核/查詢改寫用的模型（預設本地 baseline；可用環境變數指定）
import os
_CLASSIFIER_MODEL = os.environ.get("AGENT_CLASSIFIER_MODEL") or cfg.local_default()
_CRITIC_MODEL = os.environ.get("AGENT_CRITIC_MODEL") or cfg.local_default()
_QUERY_REWRITE_MODEL = os.environ.get("AGENT_QUERY_REWRITE_MODEL") or _CLASSIFIER_MODEL
QUERY_REWRITE_ENABLED = os.environ.get("QUERY_REWRITE_ENABLED", "true").lower() == "true"

# 這些任務類型需要「查公司知識庫」——sub-agent 會先呼叫 RAG 再交給模型（不需 gateway tool-calling）
RETRIEVAL_TASK_TYPES = {t.strip() for t in
                        os.environ.get("RETRIEVAL_TASK_TYPES", "RAG切片").split(",") if t.strip()}


def retrieve(query: str) -> tuple[str, list]:
    """呼叫公司知識庫（RAG），回 (檢索內容, 來源清單)。失敗時回錯誤訊息與空來源。"""
    return _knowledge_search(query)


def rewrite_query(query: str, history_text: str = "") -> str:
    """RAG 檢索前把使用者原句（可能指代不明，如「那個」「這樣的話」）依對話上下文
    改寫成一句獨立、明確、適合語意檢索的查詢。Fail-open：關掉/失敗/改寫成空白都直接用原句，
    絕不能讓查詢改寫壞掉卡住檢索。"""
    if not QUERY_REWRITE_ENABLED or not query.strip():
        return query
    sys = cfg.query_rewrite_prompt()
    user = f"最近對話：\n{history_text}\n\n使用者原句：{query}" if history_text else f"使用者原句：{query}"
    try:
        out = create_model(spec=cfg.model_spec(_QUERY_REWRITE_MODEL)).invoke(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}]).content
        rewritten = (out or "").strip()
        if rewritten:
            log.info("rewrite_query: %r → %r", query, rewritten)
            return rewritten
    except Exception as e:  # noqa: BLE001
        log.warning("rewrite_query 失敗(%s)，用原句", e)
    return query


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
    sys = cfg.planner_prompt().replace("{{TASK_TYPES}}", "、".join(types))
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


def plan(text: str) -> list[dict]:
    """Planner：一律回傳 >=1 個 {'task_type':..., 'desc':...}。

    單一任務包成 1 筆清單，下游（chat.py 的 run_plan）不用再分兩條路；
    複合任務則原樣回傳 classify() 拆解出的 subtasks。
    """
    result = classify(text)
    if result["composite"]:
        return result["subtasks"]
    return [{"task_type": result["task_type"], "desc": text}]


def build_worker_prompt(sub: dict, prior: str, ctx: str, retrieved: str = "",
                         retry_feedback: str | None = None) -> str:
    """組 Worker 的 extra_system 字串（不做任何檢索呼叫，retrieval 由呼叫端做一次並傳入，
    避免 Critic 要求 retry 時重新檢索、對出不一致的來源）。"""
    tt, desc = sub["task_type"], sub["desc"]
    parts = [ctx, cfg.worker_prompt(), f"[子任務類型] {tt}\n[要完成的事] {desc}"]
    if retrieved:
        parts.append(f"[公司知識庫檢索結果，請只根據這些內容，並用 [n] 標註來源]\n{retrieved}")
    if prior:
        parts.append(f"[前面步驟的結果，供你參考]\n{prior}")
    if retry_feedback:
        parts.append(f"[上一輪的審核意見，請針對此修正]\n{retry_feedback}")
    return "\n\n".join(parts)


def review(sub: dict, output: str, extra_system: str, tool_sources: list, full_request: str) -> dict:
    """Critic：審核 Worker 產出。回 {'pass': bool, 'reason': str, 'feedback': str}。

    extra_system 是 Worker 當時實際看到的完整上下文（build_worker_prompt() 組出來的，
    已經包含 ctx 裡的附件/先前上傳文件片段、以及 RETRIEVAL_TASK_TYPES 的安全網檢索內容）——
    一定要把這個給 Critic 看，否則 Critic 只看得到窄窄一份 tool_sources 清單，
    會把「根據 ctx 裡文件內容回答」誤判成憑空捏造來源（曾實際發生：使用者上傳文件問問題，
    Worker 依附件內容回答並標註來源，Critic 卻因為看不到那份附件內容而判定捏造）。
    tool_sources 是額外透過工具（knowledge_search 等）查到、帶編號的來源，用來核對 [n] 標註是否對得上。

    Fail-open：任何例外（timeout/JSON 解析失敗/模型錯誤）一律回 pass=True，
    絕不讓 Critic 壞掉卡住整輪對話。
    """
    tt, desc = sub["task_type"], sub["desc"]
    src_text = ("\n".join(f"[{s.get('n')}] {s.get('name')}" for s in tool_sources)
                if tool_sources else "（這次沒有另外透過工具查詢帶編號的來源）")
    prompt = (f"{cfg.critic_prompt()}\n\n"
              f"使用者原始需求：{full_request}\n"
              f"子任務類型：{tt}\n要完成的事：{desc}\n"
              f"Worker 執行時已經拿到的上下文（可能包含系統指示、附件/先前上傳文件片段、"
              f"公司知識庫檢索結果等，這些內容都算合法依據，不算憑空捏造）：\n{extra_system}\n\n"
              f"另外透過工具查詢、帶編號的來源：\n{src_text}\n\nWorker 的回覆：\n{output}")
    try:
        out = create_model(spec=cfg.model_spec(_CRITIC_MODEL)).invoke(
            [{"role": "user", "content": prompt}]).content
        verdict = _parse_json(out)
        passed = bool(verdict.get("pass", True))
        log.info("review[%s] → pass=%s (%s)", tt, passed, verdict.get("reason"))
        return {"pass": passed, "reason": verdict.get("reason", ""), "feedback": verdict.get("feedback", "")}
    except Exception as e:  # noqa: BLE001
        log.warning("review[%s] Critic 呼叫失敗(%s)，fail-open 放行", tt, e)
        return {"pass": True, "reason": f"Critic 呼叫失敗，放行（{e}）", "feedback": ""}


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
