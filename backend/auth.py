"""認證：註冊、登入、以 token 換取使用者。

密碼用 pbkdf2-hmac-sha256 加鹽雜湊（Python 標準庫，不需額外套件）。
登入後發一個隨機 token 存進 sessions 表；請求帶 Authorization: Bearer <token>。
"""
import hashlib
import secrets

import db_client as db


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return dk.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, expected_hash)


def register(username: str, password: str) -> str:
    if db.get_user_by_name(username):
        raise ValueError("使用者名稱已被使用")
    if len(password) < 6:
        raise ValueError("密碼至少 6 個字元")
    pw_hash, salt = hash_password(password)
    user_id = db.create_user(username, pw_hash, salt)
    db.create_workspace(f"{username} 的空間", user_id)  # 註冊即配一個個人 workspace
    return _issue_token(user_id)


def login(username: str, password: str) -> str:
    user = db.get_user_by_name(username)
    if not user or not verify_password(password, user["salt"], user["password_hash"]):
        raise ValueError("帳號或密碼錯誤")
    return _issue_token(user["id"])


def _issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(token, user_id)
    return token


def user_from_token(token: str):
    return db.get_user_by_token(token)
