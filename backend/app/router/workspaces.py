from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.module import db_client as db
from app.module.deps import current_user, require_member

router = APIRouter()


class WorkspaceIn(BaseModel):
    name: str
class MemberIn(BaseModel):
    username: str
    role: str = "member"
class ConversationIn(BaseModel):
    title: str | None = None


@router.get("/workspaces")
def get_workspaces(user=Depends(current_user)):
    return db.list_workspaces(user["id"])

@router.post("/workspaces")
def new_workspace(body: WorkspaceIn, user=Depends(current_user)):
    return db.create_workspace(body.name, user["id"])

@router.post("/workspaces/{wid}/members")
def add_member(wid: int, body: MemberIn, user=Depends(current_user)):
    require_member(wid, user)
    target = db.get_user_by_name(body.username)
    if not target:
        raise HTTPException(404, "找不到該使用者")
    db.add_member(wid, target["id"], body.role)
    return {"ok": True}

@router.get("/workspaces/{wid}/conversations")
def get_conversations(wid: int, user=Depends(current_user)):
    require_member(wid, user)
    return db.list_conversations(wid)

@router.post("/workspaces/{wid}/conversations")
def new_conversation(wid: int, body: ConversationIn, user=Depends(current_user)):
    require_member(wid, user)
    return db.create_conversation(wid, body.title or "新對話")
