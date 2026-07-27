from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import users as svc

router = APIRouter()

class UserIn(BaseModel):
    username: str; password_hash: str; salt: str; emp_id: str | None = None
class SessionIn(BaseModel):
    token: str; user_id: int
class EmpIdIn(BaseModel):
    emp_id: str
class PasswordIn(BaseModel):
    password_hash: str; salt: str

@router.post("/users")
def create_user(u: UserIn, db: Session = Depends(get_db)):
    return {"id": svc.create_user(db, u.username, u.password_hash, u.salt, u.emp_id)}

@router.patch("/users/{user_id}/emp-id")
def set_emp_id(user_id: int, body: EmpIdIn, db: Session = Depends(get_db)):
    return svc.set_emp_id(db, user_id, body.emp_id)

@router.patch("/users/{user_id}/password")
def update_password(user_id: int, body: PasswordIn, db: Session = Depends(get_db)):
    return svc.update_password(db, user_id, body.password_hash, body.salt)

@router.get("/users/by-name")
def user_by_name(username: str, db: Session = Depends(get_db)):
    return svc.get_user_by_name(db, username)

@router.get("/users/by-emp-id")
def user_by_emp_id(emp_id: str, db: Session = Depends(get_db)):
    return svc.get_user_by_emp_id(db, emp_id)

@router.get("/users/by-token")
def user_by_token(token: str, db: Session = Depends(get_db)):
    return svc.get_user_by_token(db, token)

@router.post("/sessions")
def create_session(s: SessionIn, db: Session = Depends(get_db)):
    svc.create_session(db, s.token, s.user_id); return {"ok": True}
