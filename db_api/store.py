"""Postgres 資料層（psycopg3 + pgvector）。

租戶模型：workspace 為最上層邊界，可多人共用。
  users ─┬─ workspace_members ─ workspaces
         └─ memories（長期記憶，綁 user）
  workspaces ─ conversations ─ messages

長期記憶用 pgvector 做語意召回；memory 資料量小，不建索引直接精確搜尋即可。
連線用環境變數 DATABASE_URL。每次操作開一條連線，簡單清楚；要更高吞吐再換連線池。
"""
import json
import os
import uuid

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///harness")


def _conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _vec(embedding) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"


def init_db() -> None:
    with _conn() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS workspace_members (
                workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role         TEXT NOT NULL DEFAULT 'member',
                PRIMARY KEY (workspace_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id               TEXT PRIMARY KEY,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                title            TEXT NOT NULL,
                summary          TEXT NOT NULL DEFAULT '',
                summary_until_id BIGINT NOT NULL DEFAULT 0,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS messages (
                id              BIGSERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                sources         JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS memories (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content    TEXT NOT NULL,
                embedding  vector,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )


# ---- 使用者 ----------------------------------------------------------
def create_user(username, password_hash, salt) -> int:
    with _conn() as c:
        row = c.execute(
            "INSERT INTO users(username,password_hash,salt) VALUES (%s,%s,%s) RETURNING id",
            (username, password_hash, salt),
        ).fetchone()
        return row["id"]


def get_user_by_name(username):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE username=%s", (username,)).fetchone()


def create_session(token, user_id):
    with _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id) VALUES (%s,%s)", (token, user_id))


def get_user_by_token(token):
    with _conn() as c:
        return c.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=%s",
            (token,),
        ).fetchone()


# ---- Workspace -------------------------------------------------------
def create_workspace(name, owner_id) -> dict:
    with _conn() as c:
        ws = c.execute(
            "INSERT INTO workspaces(name,owner_id) VALUES (%s,%s) RETURNING id,name",
            (name, owner_id),
        ).fetchone()
        c.execute(
            "INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (%s,%s,'owner')",
            (ws["id"], owner_id),
        )
        return ws


def list_workspaces(user_id) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT w.id, w.name, m.role
               FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id
               WHERE m.user_id=%s ORDER BY w.id""",
            (user_id,),
        ).fetchall()
        return rows


def add_member(workspace_id, user_id, role="member"):
    with _conn() as c:
        c.execute(
            """INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (%s,%s,%s)
               ON CONFLICT (workspace_id,user_id) DO UPDATE SET role=EXCLUDED.role""",
            (workspace_id, user_id, role),
        )


def is_member(workspace_id, user_id) -> bool:
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
            (workspace_id, user_id),
        ).fetchone() is not None


# ---- 對話 ------------------------------------------------------------
def create_conversation(workspace_id, title="新對話") -> dict:
    cid = uuid.uuid4().hex
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations(id,workspace_id,title) VALUES (%s,%s,%s)",
            (cid, workspace_id, title),
        )
    return {"id": cid, "title": title, "workspace_id": workspace_id}


def list_conversations(workspace_id) -> list[dict]:
    with _conn() as c:
        return c.execute(
            "SELECT id,title,updated_at FROM conversations WHERE workspace_id=%s ORDER BY updated_at DESC",
            (workspace_id,),
        ).fetchall()


def get_conversation(conversation_id):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM conversations WHERE id=%s", (conversation_id,)
        ).fetchone()


def rename_conversation(conversation_id, title):
    with _conn() as c:
        c.execute("UPDATE conversations SET title=%s WHERE id=%s", (title, conversation_id))


def set_summary(conversation_id, summary, summary_until_id):
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET summary=%s, summary_until_id=%s WHERE id=%s",
            (summary, summary_until_id, conversation_id),
        )


def delete_conversation(conversation_id):
    with _conn() as c:
        c.execute("DELETE FROM conversations WHERE id=%s", (conversation_id,))


# ---- 訊息 ------------------------------------------------------------
def add_message(conversation_id, role, content, sources=None):
    with _conn() as c:
        c.execute(
            "INSERT INTO messages(conversation_id,role,content,sources) VALUES (%s,%s,%s,%s)",
            (conversation_id, role, content, json.dumps(sources, ensure_ascii=False) if sources else None),
        )
        c.execute("UPDATE conversations SET updated_at=now() WHERE id=%s", (conversation_id,))


def list_messages(conversation_id) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,role,content,sources FROM messages WHERE conversation_id=%s ORDER BY id",
            (conversation_id,),
        ).fetchall()
    for r in rows:
        r["sources"] = r["sources"] if r["sources"] else None
    return rows


def list_messages_after(conversation_id, after_id) -> list[dict]:
    with _conn() as c:
        return c.execute(
            "SELECT id,role,content FROM messages WHERE conversation_id=%s AND id>%s ORDER BY id",
            (conversation_id, after_id),
        ).fetchall()


# ---- 長期記憶（綁 user）----------------------------------------------
def add_memory(user_id, content, embedding=None):
    with _conn() as c:
        c.execute(
            "INSERT INTO memories(user_id,content,embedding) VALUES (%s,%s,%s::vector)",
            (user_id, content, _vec(embedding)),
        )


def list_memories(user_id) -> list[dict]:
    with _conn() as c:
        return c.execute(
            "SELECT id,content,created_at FROM memories WHERE user_id=%s ORDER BY id",
            (user_id,),
        ).fetchall()


def search_memories(user_id, embedding, k=5) -> list[dict]:
    """用向量相似度召回最相關的記憶。"""
    with _conn() as c:
        return c.execute(
            """SELECT content, 1 - (embedding <=> %s::vector) AS similarity
               FROM memories
               WHERE user_id=%s AND embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (_vec(embedding), user_id, _vec(embedding), k),
        ).fetchall()


def delete_memory(memory_id, user_id):
    with _conn() as c:
        c.execute("DELETE FROM memories WHERE id=%s AND user_id=%s", (memory_id, user_id))
