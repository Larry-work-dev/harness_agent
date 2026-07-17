from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.module import attachments as att
from app.module import db_client as db
from app.module.deps import current_user, require_conversation
from app.module.logs import get as get_logger

router = APIRouter()
log = get_logger("conversations")


class ModeIn(BaseModel):
    mode: str
    model: str = "auto"


@router.get("/conversations/{cid}/messages")
def get_messages(cid: str, user=Depends(current_user)):
    require_conversation(cid, user)
    return db.list_messages(cid)

@router.delete("/conversations/{cid}")
def remove_conversation(cid: str, user=Depends(current_user)):
    require_conversation(cid, user)
    db.delete_conversation(cid)  # cascade 帶走 messages + doc_chunks（DB FK ondelete=CASCADE）
    try:
        att.delete_conversation_files(cid)  # 磁盤上的附件不歸 DB 管，這裡另外清
    except Exception as e:  # noqa: BLE001
        log.warning("刪對話 %s 的附件檔案失敗(%s)", cid, e)
    return {"ok": True}

@router.post("/conversations/{cid}/mode")
def set_conversation_mode(cid: str, body: ModeIn, user=Depends(current_user)):
    require_conversation(cid, user)
    db.set_conversation_mode(cid, body.mode, body.model)
    return {"ok": True}
