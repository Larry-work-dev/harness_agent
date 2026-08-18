"""使用者自建 prompt 技能的執行邏輯——單次 LLM 呼叫，換掉 system prompt，
沒有 tool loop、沒有串流（跟 workflows/ 的既定流程一樣，run() 直接回完整文字）。"""
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import create_model
from app.module import agent_config as cfg


def run_prompt_skill(skill: dict, message: str) -> str:
    system_prompt = (skill.get("spec") or {}).get("system_prompt_template") or ""
    model = create_model(spec=cfg.model_spec(cfg.local_default()), temperature=0.0)
    result = model.invoke([SystemMessage(content=system_prompt), HumanMessage(content=message)])
    return result.content
