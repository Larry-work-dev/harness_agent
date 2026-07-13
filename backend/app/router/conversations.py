from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.module import db_client as db
from app.module.deps import current_user, require_conversation

router = APIRouter()


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
    db.delete_conversation(cid)
    return {"ok": True}

@router.post("/conversations/{cid}/mode")
def set_conversation_mode(cid: str, body: ModeIn, user=Depends(current_user)):
    require_conversation(cid, user)
    db.set_conversation_mode(cid, body.mode, body.model)
    return {"ok": True}
