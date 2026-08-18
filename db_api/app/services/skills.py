"""使用者自建技能存取邏輯。"""
from sqlalchemy import select
from app.module.models import Skill


def _out(s: Skill) -> dict:
    return {"id": s.id, "user_id": s.user_id, "kind": s.kind, "name": s.name,
            "description": s.description, "spec": s.spec, "enabled": s.enabled,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


def create(db, user_id, kind, name, description, spec) -> dict:
    s = Skill(user_id=user_id, kind=kind, name=name, description=description, spec=spec, enabled=True)
    db.add(s); db.commit(); db.refresh(s)
    return _out(s)


def list_for_user(db, user_id, kind=None) -> list:
    q = select(Skill).where(Skill.user_id == user_id)
    if kind:
        q = q.where(Skill.kind == kind)
    rows = db.scalars(q.order_by(Skill.id)).all()
    return [_out(s) for s in rows]


def list_all(db, kind=None, enabled_only=True) -> list:
    """不分使用者，撈全部技能——只給 mcp_server 用來把 http/code 技能掛進工具目錄。"""
    q = select(Skill)
    if kind:
        q = q.where(Skill.kind == kind)
    if enabled_only:
        q = q.where(Skill.enabled.is_(True))
    rows = db.scalars(q.order_by(Skill.id)).all()
    return [_out(s) for s in rows]


def get(db, skill_id, user_id=None):
    s = db.get(Skill, skill_id)
    if not s or (user_id is not None and s.user_id != user_id):
        return None
    return _out(s)


def delete(db, skill_id, user_id) -> bool:
    s = db.get(Skill, skill_id)
    if not s or s.user_id != user_id:
        return False
    db.delete(s); db.commit()
    return True
