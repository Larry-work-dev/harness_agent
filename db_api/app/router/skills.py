from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import skills as svc

router = APIRouter()


class SkillIn(BaseModel):
    user_id: int
    kind: str
    name: str
    description: str
    spec: dict


@router.post("/skills")
def create(s: SkillIn, db: Session = Depends(get_db)):
    if s.kind not in ("http", "prompt", "code"):
        raise HTTPException(400, "kind 必須是 http / prompt / code")
    return svc.create(db, s.user_id, s.kind, s.name, s.description, s.spec)


@router.get("/skills")
def list_for_user(user_id: int, kind: str | None = None, db: Session = Depends(get_db)):
    return svc.list_for_user(db, user_id, kind)


@router.get("/skills/executable")
def list_all(kind: str | None = None, db: Session = Depends(get_db)):
    """不分使用者，只給 mcp_server 拉取要掛進工具目錄的 http/code 技能用。"""
    return svc.list_all(db, kind, enabled_only=True)


@router.get("/skills/{sid}")
def get(sid: int, user_id: int, db: Session = Depends(get_db)):
    s = svc.get(db, sid, user_id)
    if not s:
        raise HTTPException(404, "找不到技能")
    return s


@router.delete("/skills/{sid}")
def delete(sid: int, user_id: int, db: Session = Depends(get_db)):
    if not svc.delete(db, sid, user_id):
        raise HTTPException(404, "找不到技能")
    return {"ok": True}
