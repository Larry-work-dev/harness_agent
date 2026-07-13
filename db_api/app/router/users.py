from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import users as svc

router = APIRouter()

class UserIn(BaseModel):
    username: str; password_hash: str; salt: str
class SessionIn(BaseModel):
    token: str; user_id: int

@router.post("/users")
def create_user(u: UserIn, db: Session = Depends(get_db)):
    return {"id": svc.create_user(db, u.username, u.password_hash, u.salt)}

@router.get("/users/by-name")
def user_by_name(username: str, db: Session = Depends(get_db)):
    return svc.get_user_by_name(db, username)

@router.get("/users/by-token")
def user_by_token(token: str, db: Session = Depends(get_db)):
    return svc.get_user_by_token(db, token)

@router.post("/sessions")
def create_session(s: SessionIn, db: Session = Depends(get_db)):
    svc.create_session(db, s.token, s.user_id); return {"ok": True}
