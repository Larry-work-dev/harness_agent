"""Skill：呼叫公司 RAG 服務，檢索知識庫中相關的文件片段。

對接的是內部 RAG Local Service 的 POST /api/v1/query，
它會做「檢索 → 重排序」並回傳一組節點（text / score / metadata）。
這個 skill 只負責取回相關片段；答案由 harness 裡的模型根據片段生成。
"""
import json
import os

import httpx

from .base import Skill

RAG_BASE_URL = os.environ.get("RAG_BASE_URL", "http://172.16.174.116:8001")
RAG_TOPK = int(os.environ.get("RAG_TOPK", "5"))
RAG_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "30"))
RAG_VERIFY_SSL = os.environ.get("RAG_VERIFY_SSL", "true").lower() != "false"

# 權限過濾。RAG 服務的 filter 欄位為必填；預設不加限制（空陣列）。
# 若要套權限，設環境變數 RAG_FILTER 為 JSON 陣列，例如：
#   RAG_FILTER='[{"allowDirect":"Y","compCode":"AVC","depCode":"IT",
#                 "docType":"","empID":"","metadataIds":""}]'
def _load_filter() -> list:
    raw = os.environ.get("RAG_FILTER")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _knowledge_search(query: str):
    """在公司知識庫中檢索與問題相關的文件片段。回傳 (給模型的文字, 來源清單)。"""
    payload = {"search": query, "filter": _load_filter(), "topk": RAG_TOPK}
    try:
        resp = httpx.post(
            f"{RAG_BASE_URL}/api/v1/query",
            json=payload,
            timeout=RAG_TIMEOUT,
            verify=RAG_VERIFY_SSL,
        )
        resp.raise_for_status()
        nodes = resp.json()
    except Exception as e:  # noqa: BLE001
        return (f"知識庫檢索失敗：{e}", [])

    if not nodes:
        return ("知識庫中查無相關資料。", [])

    parts = []
    sources = []
    for i, node in enumerate(nodes, 1):
        text = (node.get("text") or "").strip()
        meta = node.get("metadata") or {}
        name = meta.get("FileName") or meta.get("file_name") or f"來源 {i}"
        url = meta.get("ReferenceURL") or meta.get("reference_url") or ""
        parts.append(f"[{i}] 來源：{name}\n{text}")
        sources.append({"n": i, "name": name, "url": url})

    content = (
        "以下是從公司知識庫檢索到的資料。回答時請只根據這些內容，"
        "並在每個句子後面用 [n] 標註它依據的來源編號：\n\n" + "\n\n".join(parts)
    )
    return (content, sources)


SKILL = Skill(
    name="knowledge_search",
    description="在公司內部知識庫中檢索與問題相關的文件內容。",
    when_to_use="使用者的問題涉及公司文件、內部規範、產品或流程等需要查資料才能回答時。",
    parameters={"query": "要檢索的問題或關鍵字（用自然語言即可）"},
    run=_knowledge_search,
    returns_artifact=True,
)
