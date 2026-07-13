from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import model_profiles as svc

router = APIRouter()

class ModelProfileIn(BaseModel):
    user_id: int; name: str; base_url: str; model: str; api_key: str | None = None

@router.post("/model-profiles")
def create(p: ModelProfileIn, db: Session = Depends(get_db)):
    return svc.create(db, p.user_id, p.name, p.base_url, p.model, p.api_key)

@router.get("/model-profiles")
def list_profiles(user_id: int, db: Session = Depends(get_db)):
    return svc.list_profiles(db, user_id)

@router.get("/model-profiles/{pid}")
def get_profile(pid: int, user_id: int, db: Session = Depends(get_db)):
    return svc.get(db, pid, user_id)

@router.delete("/model-profiles/{pid}")
def delete_profile(pid: int, user_id: int, db: Session = Depends(get_db)):
    svc.delete(db, pid, user_id); return {"ok": True}
