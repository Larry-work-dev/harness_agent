"""Tool：呼叫公司 RAG 服務，檢索知識庫中相關的文件片段。

對接的是內部 RAG Local Service 的 POST /api/v1/query，
它會做「檢索 → 重排序」並回傳一組節點（text / score / metadata）。
這個 tool 只負責取回相關片段；答案由 backend 那邊的模型根據片段生成。

emp_id 從 HTTP header（X-Emp-Id）取得，不是 tool 的參數——backend 呼叫這個
tool 時會依「目前登入者」把 emp_id 放進 header；這個 header 不會出現在
LLM 看到的 tool schema 裡（見 Context.headers 的用法），確保「用誰的權限
查」這件事只能由後端依登入者身分決定，模型看不到、也改不動。

── local / azure 來源切換（KB_BACKEND）──
KB_BACKEND=local（預設）：走上面說的 RAG Local Service，回傳原始節點清單，
由 _format() 組成帶 [FileID] 引用指示的文字。
KB_BACKEND=azure：改打公司 KBApi/AskQuestionStream（SSE），那支本身就是
「檢索＋生成答案」一次做完，回傳的已經是完整答案，不是原始節點——所以這條
路徑不會再套用 _format() 的引用範本，直接把它吐出來的文字當作 content。
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

KB_BACKEND = os.environ.get("KB_BACKEND", "local").strip().lower()

RAG_BASE_URL = os.environ.get("RAG_BASE_URL", "http://172.16.174.116:8000")
RAG_TOPK = int(os.environ.get("RAG_TOPK", "5"))
RAG_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "30"))
RAG_VERIFY_SSL = os.environ.get("RAG_VERIFY_SSL", "true").lower() != "false"

AZURE_KB_URL = os.environ.get(
    "AZURE_KB_URL",
    "https://deveip3.avc.co/DesktopModules/AiLibrary/API/KBApi/AskQuestionStream",
)
AZURE_KB_TIMEOUT = float(os.environ.get("AZURE_KB_TIMEOUT", "30"))
AZURE_KB_VERIFY_SSL = os.environ.get("AZURE_KB_VERIFY_SSL", "true").lower() != "false"

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


def _query_rag(query: str, topk: int, filter_list: list):
    payload = {
        "search": query,
        "filter": filter_list,
        "topk": topk,
        # RAG API 現在分兩階段：r_topk 是檢索階段的候選數（預設 30），
        # topk 是 rerank 後篩到的輸出數。若 topk 超過 r_topk 預設值，
        # 結果會被攔腰砍掉，所以兩個要帶一樣的值，維持「topk 就是總筆數」的語意。
        "r_topk": topk,
    }
    resp = httpx.post(
        f"{RAG_BASE_URL}/api/v1/query",
        json=payload,
        timeout=RAG_TIMEOUT,
        verify=RAG_VERIFY_SSL,
    )
    resp.raise_for_status()
    # 回應現在包成 {"results": [...], "timing": {...}}，不再是裸陣列。
    return resp.json().get("results", [])


def _query_azure(query: str, filter_list: list) -> tuple[str, list]:
    """呼叫 KBApi/AskQuestionStream，回傳（完整答案文字, 來源清單）。

    實測過的真實 SSE 格式（deveip3.avc.co，2026-08-19 手動打過確認，不是猜的）：
      data: {"type":"text_stream","data":"<逐段文字片段>"}
        → 累積成答案本文；文字裡會內嵌 [1][2] 這種數字引用索引。
      data: {"type":"reference_files","data":[{"fileid":...,"filename":...,
             "sasurl":...,"actionType":...,"citationIndex":...}, ...]}
        → 來源清單，citationIndex 對應本文裡的 [1][2] 數字。
      data: {"type":"end","data":"[DONE]"}
        → 串流結束。
    """
    payload = {
        "messages": [{"role": "user", "content": query}],
        "RewrittenQuery": query,
        "stats": {"RetryCount": 0},
        "filterCriteria": filter_list,
    }
    parts: list[str] = []
    sources: list[dict] = []
    with httpx.stream(
        "POST",
        AZURE_KB_URL,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        timeout=AZURE_KB_TIMEOUT,
        verify=AZURE_KB_VERIFY_SSL,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = chunk.get("type")
            if msg_type == "text_stream":
                text = chunk.get("data")
                if text:
                    parts.append(text)
            elif msg_type == "reference_files":
                # 這裡的欄位名稱直接照抄 Azure 原始回應，不要改名——前端 Vue 樣板的
                # refDisplayName/refIcon/refActionType/handleReferenceFile 這幾個
                # 既有方法認的就是 fileid/filename/sasurl/actionType/citationIndex
                # 這幾個名字，改了名字前端就要多一層轉換，沒有必要。
                for ref in chunk.get("data") or []:
                    sources.append({
                        "fileid": ref.get("fileid") or "",
                        "filename": ref.get("filename") or "",
                        "sasurl": ref.get("sasurl") or "",
                        "actionType": ref.get("actionType") or "",
                        "citationIndex": ref.get("citationIndex"),
                    })
            elif msg_type == "end":
                break
    return "".join(parts), sources


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


def _search(query: str, filter_list: list):
    """命中料號格式或 CAR/LL/MRB/報廢 等關鍵字時，直接用擴大過的 topk 查詢；
    若這類查詢結果剛好撈滿 topk（代表可能還有更多筆被截斷），且尚未到 RAG_TOPK_MAX，
    會自動再用 RAG_TOPK_MAX 查一次以擴大搜尋範圍。

    一般語意問題不吃「撈滿就升級」這條：rerank 幾乎必然把 topk 填滿，撈滿不代表
    被截斷，只是正常結果——直接升到 MAX 只會把不相關的段落也塞進 context。

    KB_BACKEND=azure 時完全走另一條路：AskQuestionStream 自己做完檢索＋生成，
    答案本文裡已經內嵌 [1][2] 這種數字引用索引，不套用下面 local 這條路的
    [FileID] 引用範本。來源清單附兩份：一份是給人看的「[索引] 檔名」文字，
    接在答案後面；另一份是包在 HTML 註解裡的 KB_FILES_JSON 結構化資料，給
    kiki-chat-openclaw/backend 從 session history 撈出來直接轉發給前端
    （見 KB_SOURCE_PASSTHROUGH_ENABLED），不是給模型讀的——HTML 註解在
    markdown 轉譯後本來就不會顯示，模型抄不抄都不影響 backend 撈得到資料。
    """
    if KB_BACKEND == "azure":
        try:
            answer, sources = _query_azure(query, filter_list)
        except Exception as e:  # noqa: BLE001
            return f"知識庫檢索失敗：{e}", []
        if not answer:
            return "知識庫中查無相關資料。", []
        if sources:
            # 這裡故意不加一段給人看的「來源：[1] 檔名...」文字——模型會把它原樣
            # 抄進自己最終的回覆，跟真正走 kb_files passthrough 出來的結構化
            # 清單同時出現在畫面上，看起來像重複兩份來源清單。答案本文（Azure
            # 自己的 text_stream）已經內嵌 [1][2] 這種引用，模型不需要這段提示
            # 也知道怎麼標註；HTML 註解才是唯一給 backend 撈的來源，不給模型看。
            files_json = json.dumps(sources, ensure_ascii=False)
            answer = f"{answer}\n\n<!--KB_FILES_JSON:{files_json}-->"
        return answer, sources

    expanded = _needs_expanded_search(query)
    topk = RAG_TOPK_EXPANDED if expanded else RAG_TOPK
    try:
        nodes = _query_rag(query, topk, filter_list)
        if expanded and len(nodes) >= topk and topk < RAG_TOPK_MAX:
            nodes = _query_rag(query, RAG_TOPK_MAX, filter_list)
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
        content, sources = _search(query, _resolve_filter(emp_id))
        # content 給模型讀；sources 走 structured_content，給 backend 組「參考資料」用
        # （對齊原本 in-process 版本的 content_and_artifact 設計）。
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content={"sources": sources},
        )

    @server.tool(
        name="knowledge_search_plain",
        description="在公司內部知識庫中檢索與問題相關的文件內容。"
                    "何時使用：使用者的問題涉及公司文件、內部規範、產品或流程等需要查資料才能回答時。"
                    "filter_criteria 必填：由前端依目前登入者權限組好的過濾條件陣列，原樣照抄，不要自己編造或省略。",
    )
    def knowledge_search_plain(query: str, filter_criteria: list[dict], ctx: Context) -> CallToolResult:
        """query: 要檢索的問題或關鍵字（用自然語言即可）。
        filter_criteria: 前端（DNN 權限系統）已經解析好的權限過濾條件陣列，每筆
        物件帶 docType/compCode/depCode/empID/allowDirect/metadataIds。這個
        client（openclaw）不支援用 HTTP header 傳遞呼叫端身分（每個 MCP 連線
        的 header 是設定檔裡的固定值，沒辦法依每次呼叫動態帶入），所以權限範圍
        改成明確的 tool 參數直接傳整包 filter_criteria，不再由這支 tool 自己
        依 emp_id 查表——換來的代價是模型看得到、也可能填錯或亂填，不像原本
        knowledge_search 的 header 版本那麼硬；filter_criteria 若沒填或給空
        陣列一律視為未提供權限範圍，退回環境變數 RAG_FILTER。
        """
        # 給 openclaw 這類不支援 content_and_artifact 分工的 MCP client 用：
        # openclaw 只要偵測到 structured_content 存在，就會整個蓋掉 content，
        # 模型永遠看不到帶全文的那份——所以這裡完全不回 structured_content，
        # 逼它退回去用 content（sources 已經內嵌在文字裡的 [FileID] 標註了）。
        content, _sources = _search(query, filter_criteria or _env_filter())
        return CallToolResult(content=[TextContent(type="text", text=content)])
