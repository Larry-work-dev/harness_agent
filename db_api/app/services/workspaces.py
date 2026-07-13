"""Workspace 存取邏輯。"""
from sqlalchemy import select
from app.module.models import Workspace, WorkspaceMember


def create_workspace(db, name, owner_id) -> dict:
    w = Workspace(name=name, owner_id=owner_id)
    db.add(w); db.flush()
    db.add(WorkspaceMember(workspace_id=w.id, user_id=owner_id, role="owner"))
    db.commit()
    return {"id": w.id, "name": w.name}

def delete_workspace(db, workspace_id) -> None:
    # 1. 刪除 WorkspaceMember 中的所有成員
    db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == workspace_id).delete()
    
    # 2. 刪除 Workspace
    db.query(Workspace).filter(Workspace.id == workspace_id).delete()
    
    # 3. 提交變更
    db.commit()

def list_workspaces(db, user_id) -> list:
    rows = db.execute(
        select(Workspace.id, Workspace.name, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id).order_by(Workspace.id)
    ).all()
    return [{"id": r.id, "name": r.name, "role": r.role} for r in rows]


def add_member(db, workspace_id, user_id, role="member"):
    m = db.get(WorkspaceMember, {"workspace_id": workspace_id, "user_id": user_id})
    if m:
        m.role = role
    else:
        db.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
    db.commit()


def is_member(db, workspace_id, user_id) -> bool:
    return db.get(WorkspaceMember, {"workspace_id": workspace_id, "user_id": user_id}) is not None
