from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.module import attachments as att
from app.module import db_client as db
from app.module.deps import current_user, require_member
from app.module.logs import get as get_logger

router = APIRouter()
log = get_logger("workspaces")


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

@router.delete("/workspaces/{wid}")
def delete_workspace(wid: int, user=Depends(current_user)):
    # 1. 驗證使用者是否有該 workspace 的權限
    require_member(wid, user)

    # 2. 刪前先記下底下所有對話 id：DB 那邊 conversations/messages/doc_chunks 都是
    #    FK ondelete=CASCADE 會自動清掉，但磁盤上的附件檔案不歸 DB 管，要自己清
    cids = [c["id"] for c in db.list_conversations(wid)]

    # 3. 呼叫資料庫操作刪除 workspace
    db.delete_workspace(wid)

    for cid in cids:
        try:
            att.delete_conversation_files(cid)
        except Exception as e:  # noqa: BLE001
            log.warning("刪 workspace %s 時清對話 %s 附件檔案失敗(%s)", wid, cid, e)

    # 4. 回傳成功訊息
    return {"ok": True}