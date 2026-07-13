"""使用者 / session 存取邏輯。"""
import uuid
from sqlalchemy import select
from app.module.models import User, Session


def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "password_hash": u.password_hash,
            "salt": u.salt, "created_at": u.created_at}


def create_user(db, username, password_hash, salt) -> int:
    u = User(username=username, password_hash=password_hash, salt=salt)
    db.add(u); db.commit(); db.refresh(u)
    return u.id


def get_user_by_name(db, username):
    u = db.scalar(select(User).where(User.username == username))
    return _user_dict(u) if u else None


def create_session(db, token, user_id):
    db.add(Session(token=token, user_id=user_id)); db.commit()


def get_user_by_token(db, token):
    u = db.scalar(select(User).join(Session, Session.user_id == User.id).where(Session.token == token))
    return _user_dict(u) if u else None
