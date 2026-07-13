"""db_client —— 以 HTTP 呼叫 db_api 取代直接連 DB。

函式名稱與回傳形狀刻意對齊原本的 db.py，所以 memory.py / auth.py / main.py
只要 `import db_client as db` 就能沿用，backend 本身完全不碰 Postgres driver。
"""
import os

import httpx

DB_API_URL = os.environ.get("DB_API_URL", "http://localhost:9000")
_client = httpx.Client(base_url=DB_API_URL, timeout=30)


def _json(resp: httpx.Response):
    resp.raise_for_status()
    return resp.json()


# ---- 使用者 / session ------------------------------------------------
def create_user(username, password_hash, salt) -> int:
    return _json(_client.post("/users", json={
        "username": username, "password_hash": password_hash, "salt": salt}))["id"]

def get_user_by_name(username):
    return _json(_client.get("/users/by-name", params={"username": username}))

def create_session(token, user_id):
    _json(_client.post("/sessions", json={"token": token, "user_id": user_id}))

def get_user_by_token(token):
    return _json(_client.get("/users/by-token", params={"token": token}))


# ---- workspace -------------------------------------------------------
def create_workspace(name, owner_id) -> dict:
    return _json(_client.post("/workspaces", json={"name": name, "owner_id": owner_id}))

def list_workspaces(user_id) -> list:
    return _json(_client.get("/workspaces", params={"user_id": user_id}))

def add_member(workspace_id, user_id, role="member"):
    _json(_client.post("/workspaces/members", json={
        "workspace_id": workspace_id, "user_id": user_id, "role": role}))

def is_member(workspace_id, user_id) -> bool:
    return _json(_client.get("/workspaces/is-member", params={
        "workspace_id": workspace_id, "user_id": user_id}))["member"]


# ---- 對話 / 訊息 -----------------------------------------------------
def create_conversation(workspace_id, title="新對話") -> dict:
    return _json(_client.post("/conversations", json={
        "workspace_id": workspace_id, "title": title}))

def list_conversations(workspace_id) -> list:
    return _json(_client.get("/conversations", params={"workspace_id": workspace_id}))

def get_conversation(conversation_id):
    return _json(_client.get(f"/conversations/{conversation_id}"))

def rename_conversation(conversation_id, title):
    _json(_client.post(f"/conversations/{conversation_id}/rename", json={"title": title}))

def set_summary(conversation_id, summary, summary_until_id):
    _json(_client.post(f"/conversations/{conversation_id}/summary", json={
        "summary": summary, "summary_until_id": summary_until_id}))

def delete_conversation(conversation_id):
    _json(_client.delete(f"/conversations/{conversation_id}"))

def add_message(conversation_id, role, content, sources=None):
    _json(_client.post("/messages", json={
        "conversation_id": conversation_id, "role": role, "content": content, "sources": sources}))

def list_messages(conversation_id) -> list:
    return _json(_client.get("/messages", params={"conversation_id": conversation_id}))

def list_messages_after(conversation_id, after_id) -> list:
    return _json(_client.get("/messages/after", params={
        "conversation_id": conversation_id, "after_id": after_id}))


# ---- 長期記憶 --------------------------------------------------------
def add_memory(user_id, content, embedding=None):
    _json(_client.post("/memories", json={
        "user_id": user_id, "content": content, "embedding": embedding}))

def list_memories(user_id) -> list:
    return _json(_client.get("/memories", params={"user_id": user_id}))

def search_memories(user_id, embedding, k=5) -> list:
    return _json(_client.post("/memories/search", json={
        "user_id": user_id, "embedding": embedding, "k": k}))

def delete_memory(memory_id, user_id):
    _json(_client.delete(f"/memories/{memory_id}", params={"user_id": user_id}))
