"""RAG 檢索權限存取邏輯——依 emp_id upsert / 查詢 filter_criteria。"""
from sqlalchemy import select

from app.module.models import UserPermission


def _dict(p: UserPermission) -> dict:
    return {"id": p.id, "emp_id": p.emp_id, "filter_criteria": p.filter_criteria}


def upsert_permission(db, emp_id: str, filter_criteria: list) -> dict:
    p = db.scalar(select(UserPermission).where(UserPermission.emp_id == emp_id))
    if p:
        p.filter_criteria = filter_criteria
    else:
        p = UserPermission(emp_id=emp_id, filter_criteria=filter_criteria)
        db.add(p)
    db.commit(); db.refresh(p)
    return _dict(p)


def get_permission(db, emp_id: str):
    p = db.scalar(select(UserPermission).where(UserPermission.emp_id == emp_id))
    return _dict(p) if p else None
