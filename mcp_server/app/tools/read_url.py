"""Tool：抓取網頁並抽出純文字（read_url）。

搭配 web_search 用：web_search 給網址清單，模型挑一個想細看的丟進來，這裡把 HTML
抓下來、去掉 script/style/nav 等雜訊，回傳可讀的純文字（截斷到上限，避免灌爆
context）。只處理 http/https 的一般網頁，不下載檔案。
"""
import hashlib
import os

import httpx
from bs4 import BeautifulSoup
from mcp_types import CallToolResult, TextContent

from app.module.logs import get as get_logger

log = get_logger("read_url")

READ_URL_TIMEOUT = float(os.environ.get("READ_URL_TIMEOUT", "20"))
READ_URL_MAX_CHARS = int(os.environ.get("READ_URL_MAX_CHARS", "8000"))
# 假裝成一般瀏覽器，不少網站會擋沒有 UA 的請求。
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _extract_text(html: str) -> tuple[str, str]:
    """回 (標題, 內文純文字)。去掉 script/style/nav/footer 等非內容區塊。"""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    # get_text 用換行分隔區塊，再壓掉多餘空白行。
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return title, text


def _cite_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _read(url: str):
    """回 (給模型的文字, source dict 或 None)。source 讓這個網址能進來源清單被引用。"""
    if not url.lower().startswith(("http://", "https://")):
        return "只支援 http/https 的網頁網址。", None
    try:
        resp = httpx.get(url, timeout=READ_URL_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("read_url 抓取失敗(%s): %s", url, e)
        return f"抓取網頁失敗：{e}", None

    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return f"這個網址不是網頁（content-type: {ctype or '未知'}），無法抽取文字。", None

    title, text = _extract_text(resp.text)
    if not text:
        return "抓到網頁但抽不出可讀文字（可能是需要 JavaScript 才會渲染的頁面）。", None

    truncated = len(text) > READ_URL_MAX_CHARS
    body = text[:READ_URL_MAX_CHARS] + ("\n\n…（內容過長，已截斷）" if truncated else "")
    cid = _cite_id(url)
    # 開頭放引用代號，跟 web_search / knowledge_search 同一套：模型引用這頁內容時
    # 用 [代號] 標註，就能進畫面的來源清單（可點回原網址）。
    header = (f"[{cid}] 標題：{title}\n網址：{url}\n"
              f"（引用這頁內容時，請在句尾用 [{cid}] 標註來源）\n\n")
    return header + body, {"n": cid, "name": title or url, "url": url}


def register(server) -> None:
    @server.tool(
        name="read_url",
        description="抓取指定網址的網頁，回傳去掉雜訊後的純文字內容。"
                    "何時使用：web_search 找到某個網址後，想進一步讀它的完整內容時。",
    )
    def read_url(url: str) -> CallToolResult:
        """url: 要讀取的網頁網址（http/https）"""
        content, source = _read(url)
        return CallToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content={"sources": [source] if source else []},
        )
