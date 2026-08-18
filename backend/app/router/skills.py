"""使用者自建技能：CRUD + 測試執行。

kind="http"/"code" 的技能實際執行時是 mcp_server 動態掛出來的 mcp tool
（名稱 skill_<id>），呼叫路徑跟內建工具一樣走 mcp_client；新增/刪除後要推播
mcp_client.notify_skill_changed/deleted 讓 mcp_server 立刻掛上/拔掉，並清掉
backend 這邊 build_tools() 用的工具目錄快取。kind="prompt" 不進 mcp_server，
直接由 app/services/user_skills.py 執行（見 chat.py 的 user_skill_prompt 分支）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.module import db_client as db
from app.module import mcp_client
from app.module.deps import current_user
from app.services.user_skills import run_prompt_skill

router = APIRouter()

_KINDS = ("http", "prompt", "code")


class SkillIn(BaseModel):
    kind: str
    name: str
    description: str
    spec: dict


class TestRunIn(BaseModel):
    args: dict = {}
    message: str | None = None


def _validate_spec(kind: str, spec: dict) -> None:
    if kind == "http":
        if not spec.get("url") or not spec.get("method"):
            raise HTTPException(400, "http 技能的 spec 需要 url 與 method")
    elif kind == "prompt":
        if not spec.get("system_prompt_template"):
            raise HTTPException(400, "prompt 技能的 spec 需要 system_prompt_template")
        if not spec.get("trigger_keywords"):
            raise HTTPException(400, "prompt 技能的 spec 需要至少一個 trigger_keywords")
    elif kind == "code":
        if not spec.get("source") or spec.get("language") not in ("python", "javascript"):
            raise HTTPException(400, "code 技能的 spec 需要 source 與 language（python/javascript）")


@router.post("/skills")
def create_skill(body: SkillIn, user=Depends(current_user)):
    if body.kind not in _KINDS:
        raise HTTPException(400, f"kind 必須是 {_KINDS} 其中之一")
    _validate_spec(body.kind, body.spec)
    skill = db.create_skill(user["id"], body.kind, body.name, body.description, body.spec)
    if body.kind in ("http", "code"):
        mcp_client.notify_skill_changed(skill["id"])
    return skill


@router.get("/skills")
def list_skills(kind: str | None = None, user=Depends(current_user)):
    return db.list_skills(user["id"], kind)


@router.delete("/skills/{sid}")
def delete_skill(sid: int, user=Depends(current_user)):
    skill = db.get_skill(sid, user["id"])
    if not skill:
        raise HTTPException(404, "找不到技能")
    db.delete_skill(sid, user["id"])
    if skill["kind"] in ("http", "code"):
        mcp_client.notify_skill_deleted(sid)
    return {"ok": True}


@router.post("/skills/{sid}/test")
def test_skill(sid: int, body: TestRunIn, user=Depends(current_user)):
    """手動測試執行一次，不經過 LLM/對話——http/code 直接呼叫 mcp tool，
    prompt 直接跑一次 system prompt。方便使用者建完技能後先驗證能不能動。"""
    skill = db.get_skill(sid, user["id"])
    if not skill:
        raise HTTPException(404, "找不到技能")
    if skill["kind"] == "prompt":
        result = run_prompt_skill(skill, body.message or "（測試訊息）")
        return {"result": result}
    try:
        text, structured = mcp_client.call_tool_directly(
            f"skill_{sid}", body.args, user.get("emp_id"), user["id"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"執行失敗：{e}")
    return {"result": text, "structured": structured}
