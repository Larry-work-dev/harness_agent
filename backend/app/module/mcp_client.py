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
from pydantic import create_model

from app.module.logs import get as get_logger

log = get_logger("mcp_client")

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp_server:9200/mcp")

_JSON_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}

# 這幾個 tool 的 structured_content 帶了「參考資料」（sources），要用
# content_and_artifact 模式，harness 才抽得出 sources 給前端顯示；
# 其他 tool（calculator/text_stats/get_weather）只有純文字結果。
_ARTIFACT_TOOLS = {"knowledge_search"}


async def _list_tools_async() -> list[dict]:
    async with streamable_http_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"name": t.name, "description": t.description or "",
                     "input_schema": t.input_schema} for t in result.tools]


async def _call_tool_async(name: str, args: dict, emp_id: str | None):
    headers = {"X-Emp-Id": emp_id} if emp_id else {}
    http_client = httpx.AsyncClient(headers=headers)
    async with streamable_http_client(MCP_SERVER_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
            return text, (result.structured_content or {})


@lru_cache(maxsize=1)
def _tool_specs() -> tuple[dict, ...]:
    """工具的 name/description/schema 是靜態的，只跟 mcp_server 要一次、之後重複用。"""
    try:
        return tuple(asyncio.run(_list_tools_async()))
    except Exception as e:  # noqa: BLE001
        log.error("連線 mcp_server（%s）取得 tool 清單失敗(%s)，這輪對話將沒有任何 tool 可用",
                  MCP_SERVER_URL, e)
        return ()


def list_tools_metadata() -> list[dict]:
    """給前端 /skills 用的精簡清單。"""
    return [{"name": s["name"], "description": s["description"],
             "parameters": s["input_schema"].get("properties", {})} for s in _tool_specs()]


def _schema_to_pydantic(name: str, schema: dict):
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields = {}
    for pname, pschema in props.items():
        pytype = _JSON_TYPE_MAP.get(pschema.get("type"), str)
        fields[pname] = (pytype, ... if pname in required else None)
    return create_model(f"{name}_Args", **fields)


def call_knowledge_search(query: str, emp_id: str | None = None) -> tuple[str, list]:
    """給不經過 tool-calling 的安全網路徑用（harness.use_tools=False，gateway 不支援
    tool calling 時，orchestrator.retrieve() 直接呼叫這裡，不透過 LLM/tool schema）。"""
    try:
        text, structured = asyncio.run(_call_tool_async("knowledge_search", {"query": query}, emp_id))
    except Exception as e:  # noqa: BLE001
        log.warning("直接呼叫 mcp knowledge_search 失敗(%s)", e)
        return f"知識庫檢索失敗：{e}", []
    return text, structured.get("sources") or []


def build_tools(emp_id: str | None) -> list[StructuredTool]:
    """把 mcp_server 的 tool 轉成 LangChain tool，供 model.bind_tools() 用。

    每個 tool 實際呼叫時都走 mcp_server（Streamable HTTP），emp_id 綁進 header，
    不是函式參數，所以不會出現在任何 tool 的 schema 裡，模型看不到、也改不動。
    """
    def _make_run(name: str):
        """獨立的 factory：每個 tool 的 closure 各自綁自己的 name，
        不用迴圈變數延遲繫結那種常見的 closure-in-loop 陷阱。"""
        def _run(**kwargs):
            try:
                text, structured = asyncio.run(_call_tool_async(name, kwargs, emp_id))
            except Exception as e:  # noqa: BLE001
                log.warning("呼叫 mcp tool %s 失敗(%s)", name, e)
                text, structured = f"工具呼叫失敗：{e}", {}
            if name in _ARTIFACT_TOOLS:
                return text, structured.get("sources") or []
            return text
        return _run

    tools = []
    for spec in _tool_specs():
        name = spec["name"]
        tools.append(StructuredTool.from_function(
            func=_make_run(name),
            name=name,
            description=spec["description"],
            args_schema=_schema_to_pydantic(name, spec["input_schema"]),
            response_format="content_and_artifact" if name in _ARTIFACT_TOOLS else "content",
        ))
    return tools
