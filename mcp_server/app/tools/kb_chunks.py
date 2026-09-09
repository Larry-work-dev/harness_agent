"""Tool：把知識庫索引裡某個檔案的實際 chunk 全文叫出來（get_kb_chunks）。

跟 knowledge_search 的分工：knowledge_search 是「拿問題去語意檢索、由服務決定
撈哪幾段」；這支是「已經知道要看哪一份文件了，把它切出來的內容原封不動撈回來」。
典型用法是兩段：先用 query_kb_attachments 把表單號碼（CAR3-*/PDP*）換成
MetadataID／FileID，再用這支撈那份檔案的全文。

打的是 Azure AI Search 的 POST /indexes/{index}/docs/search：search=* 不做語意
檢索，純粹用 filter 精確比對 MetadataId／FileId，所以回來的就是那份文件的所有
chunk（不是「最相關的幾段」）。索引裡的欄位名稱是 MetadataId／FileId（小寫 d），
資料庫欄位是 MetadataID／FileID——OData filter 的欄位名稱大小寫要照索引那邊寫，
不要照資料庫的抄。

一份大 pdf 動輒上百個 chunk、全文上萬字，整包塞回去會直接灌爆 context，所以
「打 API 要幾筆」跟「回給模型幾筆」是兩個不同的上限：前者是 AZURE_SEARCH_TOP
（照原本 REST 範例的 1000），後者是 KB_CHUNK_LIMIT／KB_CHUNK_MAX_CHARS。總筆數
用 API 的 @odata.count 回報，所以就算只顯示前幾筆，也知道實際有多少。

── 權限（重要）──
⚠️ 這支工具拿不到「呼叫者是誰」。openclaw 這類 client 的 MCP header 是設定檔裡
的固定值，沒辦法依每次呼叫動態帶入登入者身分（原因見 knowledge_search.py 裡
knowledge_search_plain 的說明），所以這裡沒有任何辦法判斷「這個人能不能看這份
文件」——只要 MetadataID／FileID 拿得到，內容就撈得出來。

AI_Library_KBMetadata 的 CompCode／DepCode／EmpID 就是那份文件的可看範圍。
KB_CHUNK_SCOPE_GUARD（預設開）因此只放行三個欄位都是 'ALL'（= 全公司可見）的
文件：CAR 全部是 ALL、PDP/PDM 有 7,975/8,236 是 ALL，這兩種本來就是這支工具的
主要用途；被擋下來的是個人上傳（SourceName=PERSONAL，170 筆全部綁特定 EmpID）
跟部門限定的 ISO 文件（2,905 筆）。要開放那些文件的正確做法是先讓呼叫端能帶
進登入者身分，不是把這個開關關掉。
"""
import os
import re
import time

import httpx

from app.module import kb_log
from app.module import structured_db as db
from app.module.logs import get as get_logger

log = get_logger("kb_chunks")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "").strip().rstrip("/")
AZURE_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "").strip()
AZURE_SEARCH_API_VERSION = os.environ.get("AZURE_SEARCH_API_VERSION", "2024-07-01").strip()
AZURE_SEARCH_KEY = os.environ.get("AZURE_SEARCH_KEY", "").strip()
AZURE_SEARCH_TIMEOUT = float(os.environ.get("AZURE_SEARCH_TIMEOUT", "30"))
AZURE_SEARCH_VERIFY_SSL = os.environ.get("AZURE_SEARCH_VERIFY_SSL", "true").lower() != "false"

# 打 API 時要幾筆（原本 REST 範例是 1000）。這是「一次撈完整份文件」用的上限，
# 跟下面 KB_CHUNK_LIMIT（回給模型幾筆）是兩件事：全部撈回來才知道總共幾個
# chunk、也才能依 ChunkId 排成文件順序後取前面幾個，而不是拿到索引隨便給的幾個。
AZURE_SEARCH_TOP = int(os.environ.get("AZURE_SEARCH_TOP", "1000"))
# 索引的欄位名稱；select 只挑要用的欄位，Content 是大宗。FileId 是實際打過確認
# 可以取回的（2026-09-09 對 vector-index-1 實測），有它才分得出「只給
# metadata_id」那種多檔案模式下每個 chunk 屬於哪個檔案。
# 走 env 是因為「索引有哪些欄位可取回」是那邊的設定，不是這支工具的邏輯：
# 欄位在索引裡沒設成 retrievable 時整個請求會 400，改 env 就能拿掉。
AZURE_SEARCH_SELECT = os.environ.get(
    "AZURE_SEARCH_SELECT", "ChunkId, FileName, MetadataId, FileId, Content").strip()

# 回給模型的上限：幾個 chunk、每個 chunk 幾個字。
KB_CHUNK_LIMIT = int(os.environ.get("KB_CHUNK_LIMIT", "20"))
KB_CHUNK_MAX_CHARS = int(os.environ.get("KB_CHUNK_MAX_CHARS", "1200"))
# 可看範圍檢查（見模組開頭「權限」那段）。預設開，fail closed。
KB_CHUNK_SCOPE_GUARD = os.environ.get("KB_CHUNK_SCOPE_GUARD", "true").lower() != "false"

# 索引裡的欄位名稱（注意是 MetadataId／FileId，跟資料庫的 MetadataID／FileID 不同）
F_METADATA_ID = "MetadataId"
F_FILE_ID = "FileId"
F_CHUNK_ID = "ChunkId"
F_FILE_NAME = "FileName"
F_CONTENT = "Content"

# 這兩個 ID 實際上都是 32 碼小寫 hex，但不寫死成 hex——同一個索引以後放別的來源
# 時 ID 形態可能不一樣。這裡的目的只是「確定它不是一段 OData 語法」：組 filter
# 時單引號已經照 OData 規則加倍轉義過，這條是第二道，順便把明顯打錯的參數
# （例如模型把整句話塞進來）擋在打 API 之前。
_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")

# 檔案要進得了索引才撈得到 chunk；AI_Library_KBFile.StatusCode 的這個值代表
# 「已建索引」（其餘 VOID/CHUNK_NG/UPLOAD_NG 撈了都是空的）。
# 同一個值在 tools/structured_db.py 的 query_kb_attachments 也用到。
INDEXED_STATUS = "INDEX_OK"

_SCOPE_COLUMNS = ("CompCode", "DepCode", "EmpID")


def _odata_str(value: str) -> str:
    """包成 OData 字串常值。單引號要加倍（OData 的轉義規則）。"""
    return "'" + value.replace("'", "''") + "'"


def _build_filter(metadata_id: str, file_id: str) -> str:
    filt = f"{F_METADATA_ID} eq {_odata_str(metadata_id)}"
    if file_id:
        filt += f" and {F_FILE_ID} eq {_odata_str(file_id)}"
    return filt


_NUM_RE = re.compile(r"(\d+)")


def _chunk_sort_key(chunk_id: str):
    """把 ChunkId 排成「文件順序」用的 key：數字段落當數字比，其餘當字串比。

    ChunkId 的實際格式是 <FileID>_P<頁次>_S<段次>（例如
    ca72552036b840f794596597e941a874_P1_S001），而 API 回來的順序是亂的
    （實測同一份 pdf 的 _P7_S003 排在 _P1_S003 前面），所以一定要自己排；
    直接字串排序又會把 _P10 排到 _P2 前面。每個元素固定成 (型別, 數字, 字串) 三元組，
    這樣即使兩個 ID 形狀不同（同一次撈到多個檔案的 chunk）也不會比到 int vs str
    而炸 TypeError。
    """
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p)
                 for p in _NUM_RE.split(chunk_id or ""))


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _lookup_metadata(metadata_id: str) -> dict | None:
    """查 AI_Library_KBMetadata 這一筆（可看範圍＋來源單資訊）。查無回 None。"""
    sql = ("SELECT MetadataID, SourceID, SourceName, DocType, StatusCode, Title, "
           "CompCode, DepCode, EmpID FROM AI_Library_KBMetadata "
           "WHERE MetadataID = :metadata_id")
    _columns, rows = db.run_readonly(sql, params={"metadata_id": metadata_id})
    return rows[0] if rows else None


def _scope_refusal(meta: dict) -> str | None:
    """這筆不是「全公司可見」時，回一段拒絕說明；可以放行則回 None。"""
    scoped = []
    for col in _SCOPE_COLUMNS:
        value = (meta.get(col) or "").strip()
        if value.upper() != "ALL":
            shown, clipped = _clip(value, 60)
            scoped.append(f"{col}={shown}{'…' if clipped else ''}")
    if not scoped:
        return None
    return (f"這份文件有指定可看範圍（{'、'.join(scoped)}），不是全公司可見；"
            f"這支工具無法確認呼叫者是誰，因此不提供內容。"
            f"（來源單 {meta.get('SourceID')}／{meta.get('SourceName')}；"
            f"要開放這類文件需要先讓呼叫端帶入登入者身分，或由管理者關掉 "
            f"KB_CHUNK_SCOPE_GUARD）")


def _check_scope(metadata_id: str) -> tuple[dict | None, str | None]:
    """回 (metadata 那一筆, 拒絕說明)。拒絕說明不是 None 時就不要打 API。

    查不到或查失敗時：檢查開著就一律拒絕（fail closed，跟 structured_db 的
    HCM 欄位允許清單同一個原則——寧可撈不到，不要因為確認不了就當作可以給）；
    檢查關掉時只留一行 log 就繼續。
    """
    try:
        meta = _lookup_metadata(metadata_id)
    except Exception as e:  # noqa: BLE001
        log.warning("查 AI_Library_KBMetadata 失敗(metadata_id=%s)：%s", metadata_id, e)
        if KB_CHUNK_SCOPE_GUARD:
            return None, f"無法確認這份文件的可看範圍（查詢資料庫失敗：{e}），為安全起見不提供內容。"
        return None, None
    if meta is None:
        if KB_CHUNK_SCOPE_GUARD:
            return None, (f"查無這個 MetadataID（{metadata_id}），無法確認它的可看範圍，"
                          f"因此不提供內容。請先用 query_kb_attachments 從表單號碼查出正確的 ID。")
        return None, None
    if KB_CHUNK_SCOPE_GUARD:
        refusal = _scope_refusal(meta)
        if refusal:
            return meta, refusal
    return meta, None


def _fetch(filt: str) -> tuple[int, list[dict]]:
    """打 Azure AI Search，回 (符合條件的 chunk 總數, chunk 清單)。"""
    url = (f"{AZURE_SEARCH_ENDPOINT}/indexes/{AZURE_SEARCH_INDEX}/docs/search"
           f"?api-version={AZURE_SEARCH_API_VERSION}")
    payload = {
        "search": "*",
        "filter": filt,
        "count": True,
        "top": AZURE_SEARCH_TOP,
        "select": AZURE_SEARCH_SELECT,
    }
    resp = httpx.post(
        url, json=payload,
        headers={"api-key": AZURE_SEARCH_KEY, "Content-Type": "application/json"},
        timeout=AZURE_SEARCH_TIMEOUT, verify=AZURE_SEARCH_VERIFY_SSL,
    )
    resp.raise_for_status()
    data = resp.json()
    docs = data.get("value") or []
    total = data.get("@odata.count")
    return (total if isinstance(total, int) else len(docs)), docs


def _format(metadata_id: str, file_id: str, meta: dict | None,
            total: int, docs: list[dict]) -> str:
    head = [f"MetadataID: {metadata_id}" + (f"｜FileID: {file_id}" if file_id
                                            else "｜（未指定 FileID，撈這筆底下所有檔案）")]
    if meta:
        head.append(f"來源單：{meta.get('SourceID')}（{meta.get('SourceName')}"
                    f" / {meta.get('StatusCode')}）")
        title, clipped = _clip(meta.get("Title") or "", 200)
        if title:
            head.append(f"摘要：{title}{'…' if clipped else ''}")
    if not docs:
        head.append("索引裡沒有這份文件的 chunk。可能是這個檔案還沒建索引（"
                    f"AI_Library_KBFile.StatusCode 不是 {INDEXED_STATUS}）、已作廢，"
                    "或 MetadataID／FileID 帶錯。")
        return "\n".join(head)

    docs = sorted(docs, key=lambda d: (d.get(F_FILE_NAME) or "",
                                       _chunk_sort_key(d.get(F_CHUNK_ID) or "")))
    # 沒指定 file_id 時一次會撈到多個檔案的 chunk，先把「有哪些檔案、各幾個
    # chunk」列出來：模型才知道下一步可以帶哪個 FileID 單獨撈完某一個檔案，
    # 不然它只看得到被 KB_CHUNK_LIMIT 截斷後的前幾段，不知道還有別的檔。
    by_file: dict[str, list[dict]] = {}
    for d in docs:
        by_file.setdefault(d.get(F_FILE_NAME) or "（無檔名）", []).append(d)
    if len(by_file) > 1:
        head.append(f"這筆底下有 {len(by_file)} 個檔案的 chunk：")
        for name, ds in by_file.items():
            fid = ds[0].get(F_FILE_ID) or ""
            head.append(f"  · {name}（{len(ds)} 個 chunk）"
                        + (f"｜FileID: {fid}" if fid else ""))

    shown = docs[:KB_CHUNK_LIMIT]
    head.append(f"索引裡共 {total} 個 chunk，以下依文件順序顯示 {len(shown)} 個"
                f"（每個最多 {KB_CHUNK_MAX_CHARS} 字）。")
    if total > len(shown):
        head.append(f"⚠️ 還有 {total - len(shown)} 個 chunk 沒顯示——回答時要說明"
                    "這只是這份文件的一部分，不要當成全文。")

    blocks = []
    for i, d in enumerate(shown, 1):
        body, clipped = _clip(d.get(F_CONTENT) or "", KB_CHUNK_MAX_CHARS)
        label = f"── [{i}/{total}] {d.get(F_FILE_NAME) or ''}｜ChunkId: {d.get(F_CHUNK_ID) or ''}"
        blocks.append(f"{label}\n{body}" + ("\n…（這個 chunk 內容過長，已截斷）" if clipped else ""))
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks)


def _get_chunks(metadata_id: str, file_id: str) -> str:
    metadata_id = (metadata_id or "").strip()
    file_id = (file_id or "").strip()

    def refuse(reason: str) -> str:
        """所有「沒去打 API 就回絕」的路徑都走這裡，才不會有哪一種擋法沒留紀錄
        （權限擋掉的有紀錄、參數擋掉的沒紀錄，事後對不起來）。"""
        kb_log.write({"tool": "get_kb_chunks", "metadata_id": metadata_id,
                      "file_id": file_id, "refused": reason})
        return reason

    if not (AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_INDEX and AZURE_SEARCH_KEY):
        return refuse("尚未設定 Azure AI Search 連線資訊（AZURE_SEARCH_ENDPOINT / "
                      "AZURE_SEARCH_INDEX / AZURE_SEARCH_KEY），無法撈 chunk。")
    if not metadata_id:
        return refuse("metadata_id 必填。請先用 query_kb_attachments 從表單號碼查出 MetadataID。")
    for label, value in (("metadata_id", metadata_id), ("file_id", file_id)):
        if value and not _ID_RE.match(value):
            return refuse(f"{label} 看起來不是合法的 ID"
                          f"（只接受英數與 . _ - : @，最長 128 字）：{value!r}")

    meta, refusal = _check_scope(metadata_id)
    if refusal:
        return refuse(refusal)

    filt = _build_filter(metadata_id, file_id)
    record = {"tool": "get_kb_chunks", "metadata_id": metadata_id, "file_id": file_id,
              "filter_used": filt, "index": AZURE_SEARCH_INDEX}
    started = time.perf_counter()
    try:
        total, docs = _fetch(filt)
    except httpx.HTTPStatusError as e:
        # Azure 的錯誤訊息（欄位名稱不存在、filter 語法錯、api-key 不對）都在
        # response body 裡，只回 status code 等於把唯一有用的線索丟掉。
        body, _clipped = _clip(e.response.text, 300)
        log.warning("Azure Search 回 %s：%s", e.response.status_code, body)
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        record["error"] = f"HTTP {e.response.status_code}: {body}"
        kb_log.write(record)
        return f"撈 chunk 失敗（Azure Search 回 HTTP {e.response.status_code}）：{body}"
    except Exception as e:  # noqa: BLE001
        log.warning("撈 chunk 失敗(metadata_id=%s, file_id=%s)：%s", metadata_id, file_id, e)
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        record["error"] = repr(e)
        kb_log.write(record)
        return f"撈 chunk 失敗：{e}"

    content = _format(metadata_id, file_id, meta, total, docs)
    record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    record["result"] = {"total": total, "fetched": len(docs),
                        "shown": min(len(docs), KB_CHUNK_LIMIT), "content": content}
    kb_log.write(record)
    return content


def register(server) -> None:
    @server.tool(
        name="get_kb_chunks",
        description=(
            "撈知識庫裡某個「已知檔案」的實際內容（該檔案被切出來的 chunk 全文）。"
            "何時使用：已經用 query_kb_attachments 查到 MetadataID／FileID，"
            "要看那份附件裡到底寫了什麼的時候。這支不是語意檢索"
            "（要用問題找資料請用 knowledge_search），而是把指定文件的內容原樣叫出來。"
            "metadata_id 必填、file_id 選填：只給 metadata_id 會撈那張來源單底下"
            "所有檔案的 chunk，兩個都給就只撈那一個檔案。ID 請原樣照抄，不要改大小寫。"
        ),
    )
    def get_kb_chunks(metadata_id: str, file_id: str = "") -> str:
        """metadata_id: AI_Library_KBMetadata 的 MetadataID（32 碼 hex），必填。
        file_id: AI_Library_KBFile 的 FileID（32 碼 hex），選填；不給就撈同一個
        MetadataID 底下所有檔案的 chunk。兩個值都從 query_kb_attachments 的結果
        原樣照抄。
        """
        return _get_chunks(metadata_id, file_id)
