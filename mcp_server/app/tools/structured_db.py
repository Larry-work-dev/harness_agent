"""Tool：結構化資料庫查詢（MSSQL）。

跟 knowledge_search（RAG，查文件片段）並列——這個工具查的是有明確 schema 的
結構化資料表，問題如果對得上已知的表/欄位，答案會比語意檢索準確很多。
LLM 自己依 tool description 決定要用哪一個，不用額外的前門路由規則。

兩種模式：
  1. 固定查詢：每一種已知情境各自一個 tool（下面 register() 裡註解掉的
     _query_example_by_id 是範本），SQL 是開發者寫死的，模型只能填參數、
     用 bindparams 綁定，不能自己改 SQL 語句——比較安全，優先建議這條路。
  2. db_query()：text-to-SQL 的保險，固定查詢都不適用時模型才會選到。
     依 _SCHEMA_DESC 這份手動維護的 schema 描述，請模型自己寫一句 SELECT，
     執行前一定會經過 structured_db.assert_readonly() 檢查。
"""
import os

import httpx

from app.module import structured_db as db
from app.module.logs import get as get_logger

log = get_logger("structured_db_tool")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_VERIFY_SSL = os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"

# 手動維護的 schema 描述：只描述你想讓 text-to-SQL 看得到、可以查的 table，
# 不要整個資料庫都列出來——這是白名單，不是自動內省，模型看不到的 table
# 它就不會（也不該）寫出查那張表的 SQL。
#
# 這份資料庫（STRUCTURED_DB_NAME=MCP）是專門開給這個工具查的整理庫，跟公司
# 正式 Portal 系統的原始表（Portal_2026061001.CAR_D1 等）不是同一份、欄位也
# 不一樣，不要混著猜。CARID 是所有子表共用的關聯鍵，格式固定「CAR3-YYYYMM-
# 六位數流水號」，例如 CAR3-202310-000261。
_SCHEMA_DESC = """\
table: CAR3_CAR（CAR 案件主表，一個 CARID 一筆）
  - CARID (nvarchar): 單號，主鍵，格式 CAR3-YYYYMM-NNNNNN
  - CARType (nvarchar): 案件類型，例如 OQC / IQC / Manufacturing / CustomerComplaint / DQE
  - Company, BUCode (nvarchar): 公司別、產品事業處代碼
  - ProductNo (nvarchar): 料號
  - ComplaintCategory (nvarchar): 客訴分類代碼
  - StatusCode (nvarchar): 目前流程狀態，例如 RELEASED（已結案）、制定D1-D3（處理中）
  - CreateBy/CreateByName, CreateStamp (datetime): 建立人、建立時間
  - UpdateBy/UpdateByName, UpdateStamp (datetime): 最後更新人、更新時間
  - RootTaskID, MaterialGroupID/MaterialGroupName: 關聯任務 ID、物料群組
  用途：查某個 CAR 案號的基本資訊、狀態、建立/結案時間、負責人

table: CAR3_D2（8D 手法 D2：客戶/供應商聯絡窗口，一個 CARID 可多筆）
  - D2ID (nvarchar) / CARID (nvarchar, FK -> CAR3_CAR.CARID)
  - Role, CustomerNo, CustomerMemName, CustomerDep
  - SupplierCode, SupplierMemName, SupplierDep, SupplierContactEmail
  - CreateStamp, UpdateStamp (datetime)

table: CAR3_D3（8D 手法 D3：圍堵措施/庫存處置，一個 CARID 可多筆）
  - D3ID (nvarchar) / CARID (nvarchar, FK)
  - InventoryCategory, InventorySubCategory, PartNo, InventoryQTY
  - Action, ActionDesc, ActorMemID
  - EstimateFinishDate, TrackFinishDate (nvarchar，注意是文字格式的日期，不是 datetime)

table: CAR3_D4（8D 手法 D4：原因分析，一個 CARID 可多筆）
  - D4ID (nvarchar) / CARID (nvarchar, FK)
  - CauseCategory, CauseSubCategory, PartNo
  - ResponsibilityRatio, CauseDesc（原因描述全文）
  - ResponsibilityMemID/ResponsibilityMemDep, SupplierCode

table: CAR3_D5（8D 手法 D5：矯正措施，一個 CARID 可多筆）
  - D5ID (nvarchar) / CARID (nvarchar, FK)
  - ActionDesc（矯正措施內容）, ActorMemID/ActorMemDep
  - EstimateFinishDate, RealFinishDate, TrackFinishDate（nvarchar 文字日期）
  - TrackImproveOpinion（改善追蹤意見）, ConfirmResult

table: CAR3_D6（8D 手法 D6：預防再發措施，欄位跟 D5 同構，一個 CARID 可多筆）
  - D6ID (nvarchar) / CARID (nvarchar, FK)
  - ActionDesc, ActorMemID/ActorMemDep
  - EstimateFinishDate, RealFinishDate, TrackFinishDate
  - TrackReHappenOpinion（再發追蹤意見）

table: CAR3_D7（8D 手法 D7：效果確認/結案，一個 CARID 可多筆）
  - D7ID (nvarchar) / CARID (nvarchar, FK)
  - ActionDesc, ConfirmResult, RealFinishDate
  - ConfirmOpinion（確認意見）, TrackingNo

table: CAR3_DateBarCode / CAR3_Manufacturing（不良品批號，欄位相同，一個 CARID 可多筆）
  - DateBarCodeID (nvarchar) / CARID (nvarchar, FK)
  - DefectDateBarCode (nvarchar): 不良品批號/日期碼

table: CAR_VIEW_combined（整合摘要視圖，一個 CARID 一筆，欄位是把上面 D1-D8 攤平＋
  各階段處理天數，適合「這段期間/這個部門的 CAR 案件整體狀況」這類彙總問題；
  ⚠️ 這是抽樣/週期性更新的視圖，不保證包含所有 CARID——查特定單號的細節優先用
  CAR3_CAR + CAR3_D2~D7，這張視圖查不到不代表案件不存在）
  - [CAR NO.(單號)], [CAR Type(CAR 類型)], [Corp.(公司別)], [BU(產品事業處)]
  - [Dep.(權責部門)], [P/N(料號)], [Customer Code(客戶代碼)], [Customer Name(客戶名稱)]
  - [Customer Complaint Type(客訴類別)], 目前流程狀態
  - [CAR申請-工作負責人], [CAR申請-處理歷時(天)]（其餘各階段負責人/處理歷時欄位同一種命名規則，
    階段包含：申請人確認、制定D1-D3、裁決立案、審核D1-D3、匯總D4-D8、審核8D內容、追蹤結案）
  - [Created Date], [Released/Closed Date] (datetime)
  - [完成8D所用時間(天)], [CAR累計處理歷時(天)] (int)

table: CAR_Report_Data（各 BU/客訴類別的處理天數分布統計表，欄位名稱本身是天數區間
  的 bucket，例如「7」「8」...「30」「>30」都是 int 型別的筆數，用途很窄，一般問題
  不會用到這張，只有明確問「處理天數分布/統計報表」才考慮）
  - [BU(產品事業處)], [Customer Complaint Type(客訴類別)], [CAR申請-工作負責人]
  - 7, 8, 9, ..., 30, >30 (int): 落在該天數（或以上）區間的案件數
"""


def _format_rows(columns: list[str], rows: list[dict]) -> str:
    if not rows:
        return "查無資料。"
    lines = [" | ".join(columns)]
    for r in rows:
        lines.append(" | ".join(str(r.get(c, "")) for c in columns))
    return "\n".join(lines)


def _generate_sql(question: str) -> str:
    """把自然語言問題轉成一句 SELECT。這裡直接打 HTTP 呼叫 gateway，不走
    LangChain/agent 框架——跟 knowledge_search 直接呼叫 RAG 服務是同一種輕量
    風格，不為了一次性的文字轉換多拉一整套框架進 mcp_server。用便宜/快的模型
    就好（跟 backend 的 query_rewrite/classify_intent 一樣是機械式轉換子任務，
    不需要主力模型）。"""
    prompt = (
        "你是 SQL 產生器。根據下面的資料表結構，把使用者問題轉成「一句」T-SQL "
        "SELECT 查詢（MSSQL 語法）。只准輸出 SQL 本身，不要任何說明文字、不要用 "
        "```包起來、不要加分號、只能是單一 SELECT 或 WITH...SELECT，不准 INSERT/"
        "UPDATE/DELETE/DROP 等任何非查詢語句。\n\n"
        f"資料表結構：\n{_SCHEMA_DESC}\n\n使用者問題：{question}"
    )
    resp = httpx.post(
        f"{LLM_BASE_URL}/chat/completions",
        json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        verify=LLM_VERIFY_SSL, timeout=20,
    )
    resp.raise_for_status()
    out = resp.json()["choices"][0]["message"]["content"].strip()
    if out.startswith("```"):  # 防呆：模型偶爾還是會用 ```sql ... ``` 包起來
        out = out.strip("`").removeprefix("sql").strip()
    return out


def _db_query(question: str) -> str:
    try:
        sql = _generate_sql(question)
    except Exception as e:  # noqa: BLE001
        log.warning("text-to-SQL 產生失敗(%s)", e)
        return f"無法把問題轉成查詢：{e}"
    try:
        columns, rows = db.run_readonly(sql)
    except db.UnsafeQueryError as e:
        log.warning("text-to-SQL 產生了不允許的語句：%r（%s）", sql, e)
        return f"產生的查詢不符合安全規則，已拒絕執行（{e}）。實際產生的 SQL：{sql}"
    except Exception as e:  # noqa: BLE001
        log.warning("查詢執行失敗(%r)：%s", sql, e)
        return f"查詢執行失敗：{e}\n實際執行的 SQL：{sql}"
    return f"執行的查詢：{sql}\n\n結果：\n{_format_rows(columns, rows)}"


def register(server) -> None:
    @server.tool(
        name="db_query",
        description=(
            "當問題可以用一句 SQL 直接查到結構化資料庫時使用（比查文件片段的 "
            "knowledge_search 更精確）。何時使用：問題涉及明確的表格化資料"
            "（例如數量、狀態、對照表這類有固定欄位的資料），且沒有更適合的固定查詢工具可用時。"
            "包含查詢特定 CAR 案號（格式 CAR3-YYYYMM-NNNNNN，例如 CAR3-202310-000261）"
            "的狀態、負責人、處理進度、8D 各階段內容——這類問題直接用這支工具查，"
            "不用先查 knowledge_search，CAR 案件資料是結構化的，不是文件。"
        ),
    )
    def db_query(question: str) -> str:
        """要查詢的問題，用自然語言描述即可（例如「A123 這個料號的庫存還有多少」）"""
        return _db_query(question)

    # 範本：等你有真正的 table，照這個形狀複製一份、改成真的 SQL/參數/名稱/說明，
    # 再把 @server.tool 這行取消註解。SQL 是寫死的、模型只能填 record_id 這個值
    # （bindparams 綁定，不是字串插入），這就是為什麼「固定查詢」比 text-to-SQL 安全。
    #
    # @server.tool(name="query_example_by_id", description="依 ID 查一筆範例資料")
    # def query_example_by_id(record_id: str) -> str:
    #     """要查詢的 ID"""
    #     sql = "SELECT * FROM your_table WHERE id = :record_id"
    #     columns, rows = db.run_readonly(sql, params={"record_id": record_id})
    #     return _format_rows(columns, rows)
