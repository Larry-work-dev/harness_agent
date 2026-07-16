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

# 分類器用的模型（預設本地 baseline；可用環境變數指定）
import os
_CLASSIFIER_MODEL = os.environ.get("AGENT_CLASSIFIER_MODEL") or cfg.local_default()


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

from app.module.skills import Skill
import logging
from typing import Optional
from langchain_core.messages import ToolMessage, HumanMessage, BaseMessage

logger = logging.getLogger(__name__)

# ==========================================
# 假設你的技能包已經透過某個註冊機制載入
# 這裡對應不同的 task_type 會拿到一組 Skill 物件清單
# ==========================================
AVAILABLE_SKILLS: dict[str, list[Skill]] = {
    # 範例： "資料查詢": [skill_db_search, skill_web_search],
}

def run_subtask(sub: dict, prior: str, system_prompt: str) -> tuple[Optional[str], str]:
    tt, desc = sub.get("task_type", "unknown"), sub.get("desc", "")
    primary, fallback = cfg.primary_model(tt)
    
    prompt_text = f"{system_prompt}\n\n[子任務類型] {tt}\n[要完成的事] {desc}\n"
    if prior:
        prompt_text += f"\n[前面步驟的結果，供你參考]\n{prior}\n"
    prompt_text += "\n請只完成這個子任務，直接給出結果。"
    
    messages: list[BaseMessage] = [HumanMessage(content=prompt_text)]
    
    # 1. 取得當前子任務專屬的 Skill 清單，並轉換為 LangChain Tool
    skills_for_task = AVAILABLE_SKILLS.get(tt, [])
    tool_objects = [skill.as_tool() for skill in skills_for_task]
    
    # 建立 Tool 的字典映射，方便後續執行時透過名字查找
    tool_map = {t.name: t for t in tool_objects}
    
    for mid in (primary, fallback):
        if not mid:
            continue
            
        try:
            llm = create_model(spec=cfg.model_spec(mid))
            
            # 2. 如果有工具，綁定到模型
            if tool_objects:
                llm = llm.bind_tools(tool_objects)
            
            response = llm.invoke(messages)
            
            # 3. 處理 Tool Call 迴圈
            if hasattr(response, "tool_calls") and response.tool_calls:
                logger.info(f"模型 {mid} 發起 Tool Call: {[t['name'] for t in response.tool_calls]}")
                messages.append(response)
                
                # 遍歷執行所有要求的工具
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    
                    if tool_name not in tool_map:
                        # 處理模型發出不存在的工具名稱 (幻覺)
                        error_msg = f"找不到工具 {tool_name}"
                        logger.warning(error_msg)
                        messages.append(ToolMessage(
                            content=error_msg, 
                            tool_call_id=tool_call["id"], 
                            name=tool_name
                        ))
                        continue
                        
                    # 4. 直接利用 LangChain StructuredTool 的 invoke 
                    # 它會自動處理參數、執行，並回傳格式化好的 ToolMessage (包含 artifact)
                    target_tool = tool_map[tool_name]
                    try:
                        tool_msg = target_tool.invoke(tool_call)
                        messages.append(tool_msg)
                        
                        # (選擇性) 可以在這裡攔截 artifact 傳遞給其他系統
                        # if hasattr(tool_msg, "artifact") and tool_msg.artifact is not None:
                        #     save_to_frontend_stream(tool_msg.artifact)
                        
                    except Exception as e:
                        # 工具層級崩潰保護
                        error_msg = f"工具 {tool_name} 執行失敗: {str(e)}"
                        logger.error(error_msg)
                        messages.append(ToolMessage(
                            content=error_msg, 
                            tool_call_id=tool_call["id"], 
                            name=tool_name
                        ))
                
                # 帶著執行結果的 ToolMessage，進行第二次總結呼叫
                response = llm.invoke(messages)

            # 5. 輸出轉型
            raw_content = response.content
            out = raw_content if isinstance(raw_content, str) else str(raw_content)
            
            logger.debug(f"子任務 '{tt}' 由模型 {mid} 執行成功。")
            return mid, out
            
        except Exception as e:
            logger.warning(f"模型 '{mid}' 執行子任務 '{tt}' 時發生錯誤: {str(e)}")
            continue
            
    logger.error(f"子任務 '{tt}' 皆執行失敗。")
    return None, "（此子任務執行失敗）"


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
