"""兩層記憶。

短期（working memory，綁單一對話）：
  - 全部訊息存 DB；超過視窗的舊訊息壓成「滾動摘要」，只保留最近 N 輪原文。
  - build_context() 回傳 (要注入的摘要, 最近 N 輪訊息)。
  - maybe_summarize() 在每輪回覆後，把超出視窗的舊訊息折疊進摘要。

長期（綁 user，跨對話）：
  - recall()：把使用者問題算 embedding，用 pgvector 召回最相關的幾條記憶注入。
  - extract_and_store()：回覆後萃取穩定事實，去重後 embedding 存入。
"""
import json

from app.module import db_client as db

RECENT_TURNS = 8      # 短期記憶保留的最近訊息數（超過的折進摘要）
RECALL_K = 5          # 長期記憶每次召回的條數

# ---------- 短期記憶 ----------
_SUMMARY_PROMPT = (
    "你在維護一段對話的滾動摘要。請把「既有摘要」與「新增對話」濃縮成一段更新後的摘要，"
    "保留對後續對話有用的重點（決定、事實、待辦、使用者意圖），去除寒暄與重複。用繁體中文，精簡。\n\n"
    "既有摘要：\n{prev}\n\n新增對話：\n{new}"
)


def build_context(conversation_id) -> tuple[str, list[dict]]:
    conv = db.get_conversation(conversation_id)
    summary = conv["summary"] if conv else ""
    tail = db.list_messages_after(conversation_id, conv["summary_until_id"] if conv else 0)
    recent = [{"role": m["role"], "content": m["content"]} for m in tail]
    return summary, recent


def maybe_summarize(model, conversation_id) -> None:
    conv = db.get_conversation(conversation_id)
    if not conv:
        return
    tail = db.list_messages_after(conversation_id, conv["summary_until_id"])
    if len(tail) <= RECENT_TURNS:
        return
    to_fold = tail[: len(tail) - RECENT_TURNS]
    convo_text = "\n".join(f"{m['role']}：{m['content']}" for m in to_fold)
    try:
        resp = model.invoke(_SUMMARY_PROMPT.format(prev=conv["summary"] or "（無）", new=convo_text))
        new_summary = resp.content if hasattr(resp, "content") else str(resp)
        db.set_summary(conversation_id, new_summary.strip(), to_fold[-1]["id"])
    except Exception:
        pass  # 摘要失敗不影響對話；下輪再試


# ---------- 長期記憶 ----------
_EXTRACT_PROMPT = (
    "你是記憶萃取器。從以下這一輪對話找出「值得長期記住的使用者偏好或穩定事實」"
    "（稱呼、慣用語言、部門/角色、長期關注主題、明確偏好）。\n"
    "只擷取明確、通用、跨對話仍成立的資訊；一次性、臨時或只是助理提供的知識都不要。\n"
    "只輸出 JSON 陣列，每個元素是一句精簡中文事實；沒有則輸出 []。\n\n"
    "使用者說：{user}\n助理說：{assistant}"
)


def recall(embedder, user_id, query) -> str:
    """語意召回最相關的長期記憶，整理成注入文字。"""
    try:
        vec = embedder.embed_query(query)
        hits = db.search_memories(user_id, vec, RECALL_K)
    except Exception:
        return ""
    return "\n".join(f"- {h['content']}" for h in hits)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    return t.strip()


def extract_and_store(model, embedder, user_id, user_msg, assistant_msg) -> list[str]:
    existing = {m["content"] for m in db.list_memories(user_id)}
    try:
        resp = model.invoke(_EXTRACT_PROMPT.format(user=user_msg, assistant=assistant_msg))
        text = resp.content if hasattr(resp, "content") else str(resp)
        items = json.loads(_strip_fence(text))
        if not isinstance(items, list):
            return []
    except Exception:
        return []

    learned = []
    for item in items:
        fact = str(item).strip()
        if not fact or fact in existing:
            continue
        try:
            vec = embedder.embed_query(fact)
        except Exception:
            vec = None
        db.add_memory(user_id, fact, vec)
        existing.add(fact)
        learned.append(fact)
    return learned
