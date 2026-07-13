"""共用相依：登入使用者、workspace/對話權限檢查。"""
from fastapi import Header, HTTPException

from app.module import db_client as db
from app.services import auth


def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少登入憑證")
    user = auth.user_from_token(authorization.split(" ", 1)[1])
    if not user:
        raise HTTPException(401, "登入憑證無效")
    return user


def require_member(workspace_id, user):
    if not db.is_member(workspace_id, user["id"]):
        raise HTTPException(403, "你不是這個 workspace 的成員")


def require_conversation(cid, user):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "找不到對話")
    require_member(conv["workspace_id"], user)
    return conv
