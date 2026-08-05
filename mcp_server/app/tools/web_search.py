"""Tool：網路搜尋（web_search）。

預設用 DuckDuckGo（ddgs 套件，免 API key）。刻意把「實際去哪搜」抽成
_provider_search()，之後要換成付費／較穩定的來源（Tavily、Brave、Serper…）
只要改這個函式或加一個依 WEB_SEARCH_PROVIDER 分流的分支，不必動 tool 介面。

引用機制跟 knowledge_search 一致：每筆結果配一個短 hex 代號（url 的 sha1 前 12 碼），
文字裡用 [代號] 標在段落開頭，並把 {n:代號, name, url} 放進 structured_content。
這樣模型引用 [代號] 後，就能跟公司知識庫來源走同一套「流水號＋來源清單」渲染
（見 backend mcp_client 的 _SOURCES_TOOLS、前端 api.js 的 CITATION_RE）——網路來源
也會變成畫面上可點的來源連結，而不是一串裸網址。
"""
import hashlib
import os

from mcp_types import CallToolResult, TextContent

from app.module.logs import get as get_logger

log = get_logger("web_search")

WEB_SEARCH_PROVIDER = os.environ.get("WEB_SEARCH_PROVIDER", "duckduckgo").lower()
WEB_SEARCH_MAX_RESULTS = int(os.environ.get("WEB_SEARCH_MAX_RESULTS", "5"))
# DuckDuckGo 的地區碼（zh-tw / us-en …），影響結果的語言／地區傾向。
WEB_SEARCH_REGION = os.environ.get("WEB_SEARCH_REGION", "wt-wt")


def _cite_id(url: str) -> str:
    """依網址算一個穩定的短 hex 代號，當引用標記用（跟 knowledge_search 的 FileID 同性質）。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _provider_search(query: str, max_results: int) -> list[dict]:
    """實際打搜尋來源，回傳統一格式 [{'title','url','snippet'}]。要換來源改這裡。"""
    if WEB_SEARCH_PROVIDER == "duckduckgo":
        from ddgs import DDGS
        with DDGS() as ddgs:
            rows = ddgs.text(query, region=WEB_SEARCH_REGION, max_results=max_results)
        return [{"title": r.get("title", ""), "url": r.get("href", ""),
                 "snippet": r.get("body", "")} for r in rows]
    raise ValueError(f"未支援的 WEB_SEARCH_PROVIDER：{WEB_SEARCH_PROVIDER}")


def _search(query: str, max_results: int):
    n = max(1, min(max_results or WEB_SEARCH_MAX_RESULTS, 10))
    try:
        results = [r for r in _provider_search(query, n) if r.get("url")]
    except Exception as e:  # noqa: BLE001
        log.warning("web_search 失敗(%s)", e)
        return f"網路搜尋失敗：{e}", []
    if not results:
        return "查無相關的網路搜尋結果。", []

    parts, sources = [], []
    for r in results:
        cid = _cite_id(r["url"])
        parts.append(f"[{cid}] {r['title']}\n{r['url']}\n{r['snippet']}")
        sources.append({"n": cid, "name": r["title"] or r["url"], "url": r["url"]})
    content = ("以下是網路搜尋結果（來自公開網路，非公司內部知識庫，請自行判斷可信度）。"
               "回答時若引用某一筆，請在該句結尾、句號之前，原樣照抄該筆開頭方括號內的代號"
               "（例如 [a1b2c3d4e5f6]）標註來源，不要改動或省略字元：\n\n" + "\n\n".join(parts))
    return content, sources


def register(server) -> None:
    @server.tool(
        name="web_search",
        description="在公開網路上搜尋（DuckDuckGo），回傳標題／網址／摘要。"
                    "何時使用：需要公司知識庫沒有的外部／即時資訊（新聞、天氣、股價、產品規格、"
                    "技術文件、時事等）時。查公司內部規範請改用 knowledge_search。",
    )
    def web_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> CallToolResult:
        """
        query: 搜尋關鍵字（自然語言即可）
        max_results: 回傳幾筆（1-10，預設 5）
        """
        content, sources = _search(query, max_results)
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content={"sources": sources},
        )
