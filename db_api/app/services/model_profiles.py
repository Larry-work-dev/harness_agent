"""自訂模型 profile 存取邏輯。"""
from sqlalchemy import select
from app.module.models import ModelProfile


def create(db, user_id, name, base_url, model, api_key) -> dict:
    p = ModelProfile(user_id=user_id, name=name, base_url=base_url, model=model, api_key=api_key)
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "name": p.name, "base_url": p.base_url, "model": p.model}


def list_profiles(db, user_id) -> list:
    rows = db.scalars(select(ModelProfile).where(ModelProfile.user_id == user_id).order_by(ModelProfile.id)).all()
    return [{"id": p.id, "name": p.name, "base_url": p.base_url, "model": p.model} for p in rows]


def get(db, profile_id, user_id):
    p = db.get(ModelProfile, profile_id)
    if not p or p.user_id != user_id: return None
    return {"id": p.id, "name": p.name, "base_url": p.base_url, "model": p.model, "api_key": p.api_key}


def delete(db, profile_id, user_id):
    p = db.get(ModelProfile, profile_id)
    if p and p.user_id == user_id: db.delete(p); db.commit()
