"""結構化資料庫（MSSQL）唯讀連線 + 受限查詢執行。

structured_db tool 的兩種模式（固定參數化查詢／text-to-SQL）都經過這裡唯一的
run_readonly()，逾時、列數上限、欄位檢查只寫一份，不用兩邊各自維護。

安全分層：
  1. DB 帳號本身的權限——「這句 SQL 會不會寫到資料」唯一的防線。
     STRUCTURED_DB_USER 必須是只有 SELECT 的唯讀帳號（db_datareader），不要用 sa。
     MSSQL 沒有 Postgres 那種 session 層級可設的唯讀交易，帳號權限就是那道牆。
  2. assert_readonly()：只擋 DB 權限管不到的兩件事——空查詢、用 ; 疊多條語句。
     這裡以前還有「只允許 SELECT/WITH 開頭」跟危險關鍵字黑名單，那兩層是在應用層
     模擬 DB 權限；改用唯讀帳號後它們擋的東西 DB 已經擋掉了，是重複的 deny-list
     （而且 deny-list 本來就不是完整證明），所以移除。
  3. fetchmany(列數上限) + 連線逾時：不管 SQL 有沒有自己加 TOP，應用層還是
     會截斷回傳筆數；跑太久的查詢會被逾時砍斷。
  4. HCM_EmployeeData 的欄位允許清單：員工主檔 135 個欄位裡大部分是個資
     （身分證、銀行帳號、護照、住址、緊急聯絡人、密碼、推播 token），只有
     STRUCTURED_DB_HCM_ALLOWED_COLUMNS（env）列出的那些查得到，見
     _assert_hcm_columns()；沒設定就整張表擋掉。
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


class UnsafeQueryError(Exception):
    pass


# ── HCM_EmployeeData 欄位允許清單 ──
# 員工主檔是這個庫裡唯一一張「大部分欄位都不該給模型看」的表，所以用允許清單
# 而不是列黑名單：下面沒列到的欄位一律擋掉。這個方向的好處是預設安全——以後
# HR 那邊在表上加欄位，新欄位預設是被擋的，不會因為沒人記得更新黑名單就漏出去。
#
# 這層是「模型不知道有這些欄位」（_SCHEMA_DESC 裡本來就只寫允許的欄位）之外的
# 第二道：模型若從別處猜到欄位名、或使用者直接把欄位名餵進問題裡，擋在這裡。
HCM_TABLE = "HCM_EmployeeData"

# 允許清單從環境變數讀（逗號分隔）。「哪些欄位可以查」是會隨 HR/法遵政策變動的
# 設定，不是程式邏輯——放 env 才能改完重啟就生效，不用動程式碼重 build image。
#
# 沒設或設成空字串時，整張表一律擋掉（fail closed），不是退回全開：漏設定的後果
# 是「查不到員工資料」，不是「個資全開」。
HCM_ALLOWED_COLUMNS = {
    c.strip() for c in
    os.environ.get("STRUCTURED_DB_HCM_ALLOWED_COLUMNS", "").split(",")
    if c.strip()
}

_HCM_REF = re.compile(r"\b" + HCM_TABLE + r"\b", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COUNT_STAR = re.compile(r"COUNT\s*\(\s*\*\s*\)", re.IGNORECASE)

_hcm_blocked: set[str] | None = None


def _hcm_blocked_columns() -> set[str]:
    """HCM_EmployeeData 上「不在允許清單裡」的欄位名，第一次用到時跟
    INFORMATION_SCHEMA 問一次再 cache 起來。

    用問的而不是在程式碼裡寫死一份黑名單，是為了讓「預設是擋的」這件事成立：
    表上新增欄位時它自動落進黑名單，不需要有人記得回來改這個檔案。
    問不到的時候（DB 連不上、權限不足）直接 raise UnsafeQueryError 把整張表
    擋掉，不 cache——寧可查不到，不要因為內省失敗就變成全開。
    """
    global _hcm_blocked
    if _hcm_blocked is None:
        with _get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t"
            ).bindparams(t=HCM_TABLE)).fetchall()
        all_cols = {r[0] for r in rows}
        if not all_cols:
            raise UnsafeQueryError(
                f"無法確認 {HCM_TABLE} 的欄位清單，為安全起見拒絕查詢這張表")
        _hcm_blocked = {c for c in all_cols
                        if c.lower() not in {a.lower() for a in HCM_ALLOWED_COLUMNS}}
        log.info("%s 欄位允許 %d 個、阻擋 %d 個",
                 HCM_TABLE, len(all_cols) - len(_hcm_blocked), len(_hcm_blocked))
    return _hcm_blocked


def _assert_hcm_columns(sql: str) -> None:
    """查詢有碰到員工主檔時，檢查它只用了允許清單上的欄位。"""
    if not _HCM_REF.search(sql):
        return
    if not HCM_ALLOWED_COLUMNS:
        raise UnsafeQueryError(
            f"未設定 STRUCTURED_DB_HCM_ALLOWED_COLUMNS，{HCM_TABLE} 一律不開放查詢")
    # SELECT * 會整個繞過欄位檢查（星號裡看不到欄位名），所以只要碰到這張表就
    # 一律要求列出明確欄位。COUNT(*) 是例外——它不會吐出任何欄位內容。
    if "*" in _COUNT_STAR.sub("", sql):
        raise UnsafeQueryError(
            f"查詢 {HCM_TABLE} 時不允許使用 *，請列出要查的欄位")
    blocked = _hcm_blocked_columns()
    lowered = {b.lower() for b in blocked}
    hit = sorted({m.group(0) for m in _IDENTIFIER.finditer(sql)
                  if m.group(0).lower() in lowered})
    if hit:
        raise UnsafeQueryError(
            f"{HCM_TABLE} 的下列欄位不開放查詢：{', '.join(hit)}")


def assert_readonly(sql: str) -> None:
    """執行前的應用層檢查。

    注意這裡「不」判斷這句 SQL 是不是唯讀——那件事交給 DB 帳號權限（見模組
    docstring 第 1 層）。這裡只做 DB 權限管不到的三件事：擋空查詢、擋用 ; 疊
    多條語句，以及 HCM_EmployeeData 的欄位允許清單（唯讀帳號擋得住寫入，
    擋不住「SELECT 別人的身分證」）。
    """
    s = sql.strip()
    if not s:
        raise UnsafeQueryError("空查詢")
    if ";" in s.rstrip(";"):
        raise UnsafeQueryError("不允許多條語句（含 ; 疊查詢）")
    _assert_hcm_columns(s)


def run_readonly(sql: str, params: dict | None = None) -> tuple[list[str], list[dict]]:
    """執行一段唯讀查詢，回傳 (欄位名稱, 列資料)。

    params 有值時走 bindparams——固定查詢的參數是真的綁定值，不是字串拼接，
    天生不怕 injection；text-to-SQL 那條路徑整句 SQL 都是模型產生的，
    寫入層面的安全性完全靠 DB 帳號的唯讀權限。
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
