from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import doc_chunks as svc

router = APIRouter()


class ChunkIn(BaseModel):
    content: str
    embedding: list | None = None
class AddIn(BaseModel):
    user_id: int
    conversation_id: str
    source_name: str
    chunks: list[ChunkIn]
class SearchIn(BaseModel):
    conversation_id: str
    embedding: list
    k: int = 4


@router.post("/doc-chunks")
def add_chunks(body: AddIn, db: Session = Depends(get_db)):
    return svc.add_chunks(db, body.user_id, body.conversation_id, body.source_name,
                          [c.model_dump() for c in body.chunks])

@router.post("/doc-chunks/search")
def search_chunks(body: SearchIn, db: Session = Depends(get_db)):
    return svc.search_chunks(db, body.conversation_id, body.embedding, body.k)

@router.get("/doc-chunks/sources")
def list_sources(conversation_id: str, db: Session = Depends(get_db)):
    return svc.list_sources(db, conversation_id)
