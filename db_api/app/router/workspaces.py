from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.module.database import get_db
from app.services import workspaces as svc

router = APIRouter()

class WorkspaceIn(BaseModel):
    name: str; owner_id: int
class MemberIn(BaseModel):
    workspace_id: int; user_id: int; role: str = "member"

@router.post("/workspaces")
def create_workspace(w: WorkspaceIn, db: Session = Depends(get_db)):
    return svc.create_workspace(db, w.name, w.owner_id)

@router.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: int, db: Session = Depends(get_db)):
    return svc.delete_workspace(db, workspace_id)

@router.get("/workspaces")
def list_workspaces(user_id: int, db: Session = Depends(get_db)):
    return svc.list_workspaces(db, user_id)

@router.post("/workspaces/members")
def add_member(m: MemberIn, db: Session = Depends(get_db)):
    svc.add_member(db, m.workspace_id, m.user_id, m.role); return {"ok": True}

@router.get("/workspaces/is-member")
def is_member(workspace_id: int, user_id: int, db: Session = Depends(get_db)):
    return {"member": svc.is_member(db, workspace_id, user_id)}
