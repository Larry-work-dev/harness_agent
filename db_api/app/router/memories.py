from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import memories as svc

router = APIRouter()

class MemoryIn(BaseModel):
    user_id: int; content: str; embedding: list[float] | None = None
class MemorySearchIn(BaseModel):
    user_id: int; embedding: list[float]; k: int = 5

@router.post("/memories")
def add_memory(m: MemoryIn, db: Session = Depends(get_db)):
    svc.add_memory(db, m.user_id, m.content, m.embedding); return {"ok": True}

@router.get("/memories")
def list_memories(user_id: int, db: Session = Depends(get_db)):
    return svc.list_memories(db, user_id)

@router.post("/memories/search")
def search_memories(q: MemorySearchIn, db: Session = Depends(get_db)):
    return svc.search_memories(db, q.user_id, q.embedding, q.k)

@router.delete("/memories/{mid}")
def delete_memory(mid: int, user_id: int, db: Session = Depends(get_db)):
    svc.delete_memory(db, mid, user_id); return {"ok": True}
