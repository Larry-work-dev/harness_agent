"""MCP client —— 呼叫獨立的 mcp_server（Streamable HTTP），取代原本 in-process 的
app/module/skills/*。harness.py 用這裡的 build_tools() 取得 LangChain tool。

emp_id 用 HTTP header（X-Emp-Id）帶給 mcp_server，不是 tool 參數——確保「用誰
的權限查」這件事只能由後端依登入者身分決定，模型看不到、也改不動（見
mcp_server/app/tools/knowledge_search.py 的說明）。每個 tool 呼叫都會帶這個
header，沒用到 emp_id 的 tool（calculator 等）單純忽略。
"""
from __future__ import annotations

import asyncio
import os
from functools import lru_cache

import httpx
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from app.module.logs import get as get_logger

log = get_logger("mcp_client")

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp_server:9200/mcp")
# custom_route 掛的管理端點，走一般 HTTP（不是 MCP 的 streamable-http 協議），base 拿掉 /mcp。
MCP_SERVER_BASE_URL = os.environ.get("MCP_SERVER_BASE_URL", MCP_SERVER_URL.rsplit("/mcp", 1)[0])

# 這些 tool 的 structured_content 帶了「參考資料」（sources 陣列），要用
# content_and_artifact 模式，harness 才抽得出 sources 給前端顯示。
# web_search / read_url 也會回 sources（每筆帶短 hex 代號 + url），走同一套引用/來源渲染。
_SOURCES_TOOLS = {"knowledge_search", "web_search", "read_url"}
# 這些 tool 的 structured_content 帶了「產生的檔案」（filename/mime/data_base64），
# 一樣要用 content_and_artifact，chat.py 會把它存成附件、讓使用者下載。
_FILE_TOOLS = {"create_excel", "create_word", "create_ppt"}
_ARTIFACT_TOOLS = _SOURCES_TOOLS | _FILE_TOOLS


async def _list_tools_async() -> list[dict]:
    async with streamable_http_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"name": t.name, "description": t.description or "",
                     "input_schema": t.input_schema, "meta": t.meta or {}} for t in result.tools]


async def _call_tool_async(name: str, args: dict, emp_id: str | None, user_id: int | None = None):
    headers = {}
    if emp_id:
        headers["X-Emp-Id"] = emp_id
    if user_id is not None:
        # 使用者自建技能（skill_<id>）用這個 header 核對呼叫者是不是技能擁有者——
        # 正常情況下 build_tools() 早就把別人的技能濾掉了，這裡是多一層防線。
        headers["X-User-Id"] = str(user_id)
    # streamable_http_client 內部走 httpx2.AsyncClient（有 .sse()），不能傳舊版 httpx 的
    # AsyncClient 進去，型別不合會在 GET stream 階段炸 AttributeError。
    # 用 SDK 自己的 create_mcp_http_client() 建（30s connect/write/pool、300s read，
    # 對長連線的 SSE 友善），不要手動 httpx2.AsyncClient(headers=headers)——那樣沒指定
    # timeout 的話預設只有 5 秒，使用者自建的 code 技能（可以跑到 SKILL_RUNNER_MAX_TIMEOUT_S
    # 那麼久）或任何較慢的工具都會被攔腰砍斷，回一個看起來像連線問題的 TaskGroup 例外，
    # 不是我們自己設計好、看得懂的逾時訊息。
    http_client = create_mcp_http_client(headers=headers)
    async with streamable_http_client(MCP_SERVER_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
            return text, (result.structured_content or {})


@lru_cache(maxsize=1)
def _tool_specs() -> tuple[dict, ...]:
    """整個工具目錄（內建工具 + 所有使用者的自建技能）只跟 mcp_server 要一次、之後重複用。
    使用者自建技能的可見範圍在 build_tools() 依 meta.owner_user_id 過濾，這裡不分使用者。
    技能新增/刪除後，backend/app/router/skills.py 會呼叫 invalidate_tool_cache() 清掉這份快取。"""
    try:
        return tuple(asyncio.run(_list_tools_async()))
    except Exception as e:  # noqa: BLE001
        log.error("連線 mcp_server（%s）取得 tool 清單失敗(%s)，這輪對話將沒有任何 tool 可用",
                  MCP_SERVER_URL, e)
        return ()


def invalidate_tool_cache() -> None:
    """使用者新增/刪除自建技能後呼叫，讓下一輪對話立刻看到最新的工具目錄。"""
    _tool_specs.cache_clear()


def notify_skill_changed(skill_id: int) -> None:
    """技能新增/更新後推播給 mcp_server，讓它立刻重新掛上這個 http/code 工具。
    best-effort：推播失敗不擋 API 回應——mcp_server 自己的 reconcile_loop 每分鐘
    會補跑一次，這裡只是為了「新增後馬上能用」的低延遲路徑。"""
    try:
        httpx.post(f"{MCP_SERVER_BASE_URL}/admin/skills/{skill_id}/sync", timeout=5)
    except Exception as e:  # noqa: BLE001
        log.warning("通知 mcp_server 同步技能(%s)失敗(%s)，等下一輪 reconcile", skill_id, e)
    invalidate_tool_cache()


def notify_skill_deleted(skill_id: int) -> None:
    try:
        httpx.delete(f"{MCP_SERVER_BASE_URL}/admin/skills/{skill_id}", timeout=5)
    except Exception as e:  # noqa: BLE001
        log.warning("通知 mcp_server 移除技能(%s)失敗(%s)，等下一輪 reconcile", skill_id, e)
    invalidate_tool_cache()


def call_tool_directly(name: str, args: dict, emp_id: str | None, user_id: int | None) -> tuple[str, dict]:
    """繞過 LLM/tool-calling，直接呼叫一個 mcp tool——技能的「測試執行」用這個。"""
    return asyncio.run(_call_tool_async(name, args, emp_id, user_id))


def call_knowledge_search(query: str, emp_id: str | None = None) -> tuple[str, list]:
    """給不經過 tool-calling 的安全網路徑用（harness.use_tools=False，gateway 不支援
    tool calling 時，orchestrator.retrieve() 直接呼叫這裡，不透過 LLM/tool schema）。"""
    try:
        text, structured = asyncio.run(_call_tool_async("knowledge_search", {"query": query}, emp_id))
    except Exception as e:  # noqa: BLE001
        log.warning("直接呼叫 mcp knowledge_search 失敗(%s)", e)
        return f"知識庫檢索失敗：{e}", []
    return text, structured.get("sources") or []


def build_tools(emp_id: str | None, user_id: int | None = None) -> list[StructuredTool]:
    """把 mcp_server 的 tool 轉成 LangChain tool，供 model.bind_tools() 用。

    每個 tool 實際呼叫時都走 mcp_server（Streamable HTTP），emp_id 綁進 header，
    不是函式參數，所以不會出現在任何 tool 的 schema 裡，模型看不到、也改不動。

    _tool_specs() 回來的是「內建工具 + 所有使用者的自建技能」整份目錄，這裡依
    meta.owner_user_id 過濾：沒有 owner_user_id 的是內建工具（誰都能用），
    有的話只留自己的——別人的自建技能連 bind_tools() 都不會給模型看到，
    LangGraph 的 ToolNode 只認識這裡實際傳進去的工具，模型也就不可能呼叫得到。
    """
    def _make_run(name: str):
        """獨立的 factory：每個 tool 的 closure 各自綁自己的 name，
        不用迴圈變數延遲繫結那種常見的 closure-in-loop 陷阱。"""
        def _run(**kwargs):
            try:
                text, structured = asyncio.run(_call_tool_async(name, kwargs, emp_id, user_id))
            except Exception as e:  # noqa: BLE001
                log.warning("呼叫 mcp tool %s 失敗(%s)", name, e)
                text, structured = f"工具呼叫失敗：{e}", {}
            if name in _SOURCES_TOOLS:
                return text, structured.get("sources") or []
            if name in _FILE_TOOLS:
                file_info = structured.get("data_base64") and {
                    "filename": structured.get("filename"),
                    "mime": structured.get("mime"),
                    "data_base64": structured.get("data_base64"),
                }
                return text, file_info or None
            return text
        return _run

    tools = []
    for spec in _tool_specs():
        owner = (spec.get("meta") or {}).get("owner_user_id")
        if owner is not None and owner != user_id:
            continue
        name = spec["name"]
        # args_schema 直接用 mcp_server 回傳的原始 JSON schema（含巢狀 $defs），
        # 不要自己再轉一份 pydantic model——LangChain 的 StructuredTool 本來就
        # 接受 dict 當 args_schema，且 convert_to_openai_tool() 會把 $ref 完整
        # 展開進最終送給模型的 schema。曾經自己動手轉型別（陣列/物件都轉成
        # 泛用的 list/dict），結果巢狀欄位的名稱/說明全部消失，模型看不到
        # sheets/sections/slides 裡面該填哪些欄位，一直猜錯導致 LangGraph
        # 撞 recursion limit——直接沿用原始 schema 才能保留完整欄位資訊。
        tools.append(StructuredTool.from_function(
            func=_make_run(name),
            name=name,
            description=spec["description"],
            args_schema=spec["input_schema"],
            response_format="content_and_artifact" if name in _ARTIFACT_TOOLS else "content",
        ))
    return tools
