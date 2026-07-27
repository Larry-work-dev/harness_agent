from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.module.database import get_db
from app.services import permissions as svc

router = APIRouter()


class PermissionIn(BaseModel):
    emp_id: str
    filter_criteria: list[dict]


@router.post("/permissions")
def upsert_permission(p: PermissionIn, db: Session = Depends(get_db)):
    return svc.upsert_permission(db, p.emp_id, p.filter_criteria)


@router.get("/permissions/by-emp-id")
def permission_by_emp_id(emp_id: str, db: Session = Depends(get_db)):
    return svc.get_permission(db, emp_id)
