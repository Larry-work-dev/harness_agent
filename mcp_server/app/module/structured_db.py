"""結構化資料庫（MSSQL）唯讀連線 + 受限查詢執行。

structured_db tool 的兩種模式（固定參數化查詢／text-to-SQL）都經過這裡唯一的
run_readonly()，逾時、列數上限、SELECT-only 檢查只寫一份，不用兩邊各自維護。

安全分層（由強到弱，弱的那幾層是加值，不是唯一防線）：
  1. DB 帳號本身的權限（GRANT SELECT / db_datareader）——這是最主要的防線。
     MSSQL 沒有 Postgres 那種 session 層級可設的唯讀交易可以當防線，帳號權限
     範圍就是最後一道牆，務必用專門開的唯讀帳號，不要用 sa。
  2. assert_readonly()：關鍵字/語句檢查，只認得單一 SELECT/WITH 開頭、擋掉
     常見危險關鍵字、擋掉用 ; 疊查詢——deny-list，不是完整證明。
  3. fetchmany(列數上限) + 連線逾時：不管 SQL 有沒有自己加 TOP，應用層還是
     會截斷回傳筆數；跑太久的查詢會被逾時砍斷。
"""
from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.module.logs import get as get_logger

log = get_logger("structured_db")

TIMEOUT_S = int(os.environ.get("STRUCTURED_DB_TIMEOUT_S", "10"))
ROW_LIMIT = int(os.environ.get("STRUCTURED_DB_ROW_LIMIT", "200"))

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        url = URL.create(
            "mssql+pymssql",
            username=os.environ.get("STRUCTURED_DB_USER"),
            password=os.environ.get("STRUCTURED_DB_PASSWORD"),
            host=os.environ.get("STRUCTURED_DB_HOST"),
            port=int(os.environ.get("STRUCTURED_DB_PORT", "1433")),
            database=os.environ.get("STRUCTURED_DB_NAME", "MCP"),
        )
        # timeout 是 pymssql 的「查詢逾時」（秒），login_timeout 是連線逾時；
        # 兩個都設，避免連不上或查太久卡住呼叫端（thread pool 裡的那個 worker）。
        _engine = create_engine(
            url, pool_pre_ping=True,
            connect_args={"timeout": TIMEOUT_S, "login_timeout": TIMEOUT_S},
        )
    return _engine


# text-to-SQL 那條路徑：只准單一 SELECT/WITH 開頭、不准疊查詢、擋掉常見危險關鍵字。
# 固定查詢（開發者自己寫死的 SQL）也會經過這關——多一層保險，不是專門刁難它。
_DENY_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|CREATE|xp_cmdshell|sp_executesql)\b", re.IGNORECASE)


class UnsafeQueryError(Exception):
    pass


def assert_readonly(sql: str) -> None:
    s = sql.strip()
    if not s:
        raise UnsafeQueryError("空查詢")
    if ";" in s.rstrip(";"):
        raise UnsafeQueryError("不允許多條語句（含 ; 疊查詢）")
    if not re.match(r"^(SELECT|WITH)\b", s, re.IGNORECASE):
        raise UnsafeQueryError("只允許 SELECT（或 WITH ... SELECT）語句")
    if _DENY_KEYWORDS.search(s):
        raise UnsafeQueryError("查詢中含有不允許的關鍵字")


def run_readonly(sql: str, params: dict | None = None) -> tuple[list[str], list[dict]]:
    """執行一段唯讀查詢，回傳 (欄位名稱, 列資料)。

    params 有值時走 bindparams——固定查詢的參數是真的綁定值，不是字串拼接，
    天生不怕 injection；text-to-SQL 那條路徑整句 SQL 都是模型產生的，
    安全性主要靠 assert_readonly() 加上 DB 帳號本身的權限。
    """
    assert_readonly(sql)
    stmt = text(sql)
    if params:
        stmt = stmt.bindparams(**params)
    engine = _get_engine()
    with engine.connect() as conn:
        result = conn.execute(stmt)
        columns = list(result.keys())
        rows = result.fetchmany(ROW_LIMIT)
        return columns, [dict(zip(columns, row)) for row in rows]
