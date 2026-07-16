from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import messages as svc

router = APIRouter()

class MessageIn(BaseModel):
    conversation_id: str; role: str; content: str; sources: list | None = None; attachments: list | None = None

@router.post("/messages")
def add_message(m: MessageIn, db: Session = Depends(get_db)):
    svc.add_message(db, m.conversation_id, m.role, m.content, m.sources, m.attachments); return {"ok": True}

@router.get("/messages")
def list_messages(conversation_id: str, db: Session = Depends(get_db)):
    return svc.list_messages(db, conversation_id)

@router.get("/messages/after")
def list_messages_after(conversation_id: str, after_id: int, db: Session = Depends(get_db)):
    return svc.list_messages_after(db, conversation_id, after_id)
