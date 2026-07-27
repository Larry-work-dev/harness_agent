from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.module.deps import current_user
from app.services import auth

router = APIRouter()


class Credentials(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/register")
def register(c: Credentials):
    try:
        return {"token": auth.register(c.username, c.password)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/auth/login")
def login(c: Credentials):
    try:
        return {"token": auth.login(c.username, c.password)}
    except ValueError as e:
        raise HTTPException(401, str(e))


@router.post("/auth/change-password")
def change_password(body: ChangePassword, user=Depends(current_user)):
    try:
        auth.change_password(user, body.old_password, body.new_password)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))
