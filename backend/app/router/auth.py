from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import auth

router = APIRouter()


class Credentials(BaseModel):
    username: str
    password: str


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
