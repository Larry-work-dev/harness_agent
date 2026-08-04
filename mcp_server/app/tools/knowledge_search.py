"""Tool：呼叫公司 RAG 服務，檢索知識庫中相關的文件片段。

對接的是內部 RAG Local Service 的 POST /api/v1/query，
它會做「檢索 → 重排序」並回傳一組節點（text / score / metadata）。
這個 tool 只負責取回相關片段；答案由 backend 那邊的模型根據片段生成。

emp_id 從 HTTP header（X-Emp-Id）取得，不是 tool 的參數——backend 呼叫這個
tool 時會依「目前登入者」把 emp_id 放進 header；這個 header 不會出現在
LLM 看到的 tool schema 裡（見 Context.headers 的用法），確保「用誰的權限
查」這件事只能由後端依登入者身分決定，模型看不到、也改不動。
"""
import json
import os
import re

import httpx
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent

from app.module import db_client as db
from app.module.logs import get as get_logger

log = get_logger("knowledge_search")

RAG_BASE_URL = os.environ.get("RAG_BASE_URL", "http://172.16.174.116:8001")
RAG_TOPK = int(os.environ.get("RAG_TOPK", "5"))
RAG_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "30"))
RAG_VERIFY_SSL = os.environ.get("RAG_VERIFY_SSL", "true").lower() != "false"

# ── 擴大搜尋範圍 ──
# 料號查詢常常對應到數十甚至數百筆資料，預設 topk 撈不完；
# 命中「料號格式」或下列關鍵字時，改用較大的 RAG_TOPK_EXPANDED。
RAG_TOPK_EXPANDED = int(os.environ.get("RAG_TOPK_EXPANDED", "50"))
# 保險：若第一次查詢剛好撈滿 topk 筆（代表可能還有更多、被截斷），
# 且目前用的 topk 還沒到這個上限，就自動用 RAG_TOPK_MAX 再查一次。
RAG_TOPK_MAX = int(os.environ.get("RAG_TOPK_MAX", "100"))
RAG_EXPAND_KEYWORDS = {kw.strip() for kw in
                       os.environ.get("RAG_EXPAND_KEYWORDS", "CAR,LL,MRB,報廢").split(",") if kw.strip()}
# 料號格式：英數混合、6 碼以上，可能帶 -數字 後綴（例如 DFPK0456B2GY0P3-1）
_PART_NUMBER_RE = re.compile(
    r"\b(?=[A-Za-z0-9-]{6,}\b)(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*[0-9])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?\b"
)


def _needs_expanded_search(query: str) -> bool:
    if _PART_NUMBER_RE.search(query):
        return True
    return any(kw.lower() in query.lower() for kw in RAG_EXPAND_KEYWORDS)


# 權限過濾。RAG 服務的 filter 欄位為必填。
# 優先順序：呼叫端帶的 emp_id（依 emp_id 查 db_api 的 user_permissions 表）
# → 沒有 emp_id 或查詢失敗 → 退回環境變數 RAG_FILTER（沒設就是空陣列，不加限制）。
# Fail-open：DB 查詢失敗絕不能讓檢索整個掛掉，退回環境變數繼續查。
def _env_filter() -> list:
    raw = os.environ.get("RAG_FILTER")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _resolve_filter(emp_id: str | None) -> list:
    if not emp_id:
        return _env_filter()
    try:
        perm = db.get_permission_by_emp_id(emp_id)
    except Exception as e:  # noqa: BLE001
        log.warning("查詢使用者權限失敗(emp_id=%s): %s，退回環境變數 RAG_FILTER", emp_id, e)
        return _env_filter()
    if perm and perm.get("filter_criteria"):
        return perm["filter_criteria"]
    log.info("emp_id=%s 沒有 DB 權限紀錄，退回環境變數 RAG_FILTER", emp_id)
    return _env_filter()


def _query_rag(query: str, topk: int, emp_id: str | None):
    payload = {"search": query, "filter": _resolve_filter(emp_id), "topk": topk}
    resp = httpx.post(
        f"{RAG_BASE_URL}/api/v1/query",
        json=payload,
        timeout=RAG_TIMEOUT,
        verify=RAG_VERIFY_SSL,
    )
    resp.raise_for_status()
    return resp.json()


def _format(nodes: list):
    """把節點轉成給模型看的文字＋來源清單。

    引用用 RAG 服務 metadata 裡的 FileID（該檔案的唯一 UUID/hex ID）標註，
    不是自己編號的 [1][2][3]——避免筆數一多（甚至上百筆）時，模型引用的序號
    跟實際來源對不上；FileID 直接對應回真正的檔案，來源絕對正確。
    """
    parts = []
    sources = []
    for i, node in enumerate(nodes, 1):
        text = (node.get("text") or "").strip()
        meta = node.get("metadata") or {}
        file_id = meta.get("FileID") or meta.get("file_id") or f"src-{i}"
        name = meta.get("FileName") or meta.get("file_name") or file_id
        url = meta.get("ReferenceURL") or meta.get("reference_url") or ""
        if url.strip().upper() in ("", "N/A"):  # RAG 服務沒有連結時填 "N/A"，不是有效網址
            url = ""
        parts.append(f"[{file_id}] 來源：{name}\n{text}")
        sources.append({"n": file_id, "name": name, "url": url})

    content = (
        "以下是從公司知識庫檢索到的資料。回答時請只根據這些內容，"
        "並在每個句子後面、句號之前，原樣照抄該段落開頭方括號內的 FileID"
        "（例如 [5d73300befa4450ea08808ece5a49d38]）標註它依據的來源；"
        "不要自己編號、不要省略字元、不要修改 FileID 內容：\n\n" + "\n\n".join(parts)
    )
    return content, sources


def _search(query: str, emp_id: str | None):
    """命中料號格式或 CAR/LL/MRB/報廢 等關鍵字時，直接用擴大過的 topk 查詢；
    若查詢結果剛好撈滿 topk（代表可能還有更多筆被截斷），且尚未到 RAG_TOPK_MAX，
    會自動再用 RAG_TOPK_MAX 查一次以擴大搜尋範圍。
    """
    topk = RAG_TOPK_EXPANDED if _needs_expanded_search(query) else RAG_TOPK
    try:
        nodes = _query_rag(query, topk, emp_id)
        if len(nodes) >= topk and topk < RAG_TOPK_MAX:
            nodes = _query_rag(query, RAG_TOPK_MAX, emp_id)
    except Exception as e:  # noqa: BLE001
        return f"知識庫檢索失敗：{e}", []

    if not nodes:
        return "知識庫中查無相關資料。", []

    return _format(nodes)


def register(server) -> None:
    @server.tool(
        name="knowledge_search",
        description="在公司內部知識庫中檢索與問題相關的文件內容。"
                    "何時使用：使用者的問題涉及公司文件、內部規範、產品或流程等需要查資料才能回答時。",
    )
    def knowledge_search(query: str, ctx: Context) -> CallToolResult:
        """要檢索的問題或關鍵字（用自然語言即可）"""
        emp_id = (ctx.headers or {}).get("x-emp-id")
        content, sources = _search(query, emp_id)
        # content 給模型讀；sources 走 structured_content，給 backend 組「參考資料」用
        # （對齊原本 in-process 版本的 content_and_artifact 設計）。
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content={"sources": sources},
        )
