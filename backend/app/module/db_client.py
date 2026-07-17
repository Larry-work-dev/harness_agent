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

def delete_workspace(workspace_id) -> None:
    return _json(_client.delete(f"/workspaces/{workspace_id}"))

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

def add_message(conversation_id, role, content, sources=None, attachments=None):
    _json(_client.post("/messages", json={
        "conversation_id": conversation_id, "role": role, "content": content,
        "sources": sources, "attachments": attachments}))

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


# ---- 上傳文件切片（app 端 RAG，綁 conversation_id：檢索範圍限定這個對話）------
def add_doc_chunks(user_id, conversation_id, source_name, chunks) -> dict:
    return _json(_client.post("/doc-chunks", json={
        "user_id": user_id, "conversation_id": conversation_id,
        "source_name": source_name, "chunks": chunks}))

def search_doc_chunks(conversation_id, embedding, k=4) -> list:
    return _json(_client.post("/doc-chunks/search", json={
        "conversation_id": conversation_id, "embedding": embedding, "k": k}))

def list_doc_sources(conversation_id) -> list:
    return _json(_client.get("/doc-chunks/sources", params={"conversation_id": conversation_id}))


# ---- 自訂模型 profile ------------------------------------------------
def create_model_profile(user_id, name, base_url, model, api_key=None) -> dict:
    return _json(_client.post("/model-profiles", json={
        "user_id": user_id, "name": name, "base_url": base_url, "model": model, "api_key": api_key}))

def list_model_profiles(user_id) -> list:
    return _json(_client.get("/model-profiles", params={"user_id": user_id}))

def get_model_profile(profile_id, user_id):
    return _json(_client.get(f"/model-profiles/{profile_id}", params={"user_id": user_id}))

def delete_model_profile(profile_id, user_id):
    _json(_client.delete(f"/model-profiles/{profile_id}", params={"user_id": user_id}))


def set_conversation_mode(conversation_id, mode, model="auto"):
    _json(_client.post(f"/conversations/{conversation_id}/mode", json={"mode": mode, "model": model}))
