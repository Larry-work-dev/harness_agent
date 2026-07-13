from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import conversations as svc

router = APIRouter()

class ConversationIn(BaseModel):
    workspace_id: int; title: str = "新對話"
class RenameIn(BaseModel):
    title: str
class SummaryIn(BaseModel):
    summary: str; summary_until_id: int
class ModeIn(BaseModel):
    mode: str; model: str = "auto"

@router.post("/conversations")
def create_conversation(c: ConversationIn, db: Session = Depends(get_db)):
    return svc.create_conversation(db, c.workspace_id, c.title)

@router.get("/conversations")
def list_conversations(workspace_id: int, db: Session = Depends(get_db)):
    return svc.list_conversations(db, workspace_id)

@router.get("/conversations/{cid}")
def get_conversation(cid: str, db: Session = Depends(get_db)):
    return svc.get_conversation(db, cid)

@router.post("/conversations/{cid}/rename")
def rename(cid: str, body: RenameIn, db: Session = Depends(get_db)):
    svc.rename_conversation(db, cid, body.title); return {"ok": True}

@router.post("/conversations/{cid}/summary")
def set_summary(cid: str, body: SummaryIn, db: Session = Depends(get_db)):
    svc.set_summary(db, cid, body.summary, body.summary_until_id); return {"ok": True}

@router.post("/conversations/{cid}/mode")
def set_mode(cid: str, body: ModeIn, db: Session = Depends(get_db)):
    svc.set_mode(db, cid, body.mode, body.model); return {"ok": True}

@router.delete("/conversations/{cid}")
def delete(cid: str, db: Session = Depends(get_db)):
    svc.delete_conversation(db, cid); return {"ok": True}
