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
    source_name: str
    chunks: list[ChunkIn]
class SearchIn(BaseModel):
    user_id: int
    embedding: list
    k: int = 4


@router.post("/doc-chunks")
def add_chunks(body: AddIn, db: Session = Depends(get_db)):
    return svc.add_chunks(db, body.user_id, body.source_name,
                          [c.model_dump() for c in body.chunks])

@router.post("/doc-chunks/search")
def search_chunks(body: SearchIn, db: Session = Depends(get_db)):
    return svc.search_chunks(db, body.user_id, body.embedding, body.k)

@router.get("/doc-chunks/sources")
def list_sources(user_id: int, db: Session = Depends(get_db)):
    return svc.list_sources(db, user_id)
