"""Tool：結構化資料庫查詢（MSSQL）。

跟 knowledge_search（RAG，查文件片段）並列——這個工具查的是有明確 schema 的
結構化資料表，問題如果對得上已知的表/欄位，答案會比語意檢索準確很多。
LLM 自己依 tool description 決定要用哪一個，不用額外的前門路由規則。

兩種模式：
  1. 固定查詢：每一種已知情境各自一個 tool（見 register() 裡的 query_pdp_project /
     query_pdp_related_docs / query_employee），SQL 是開發者寫死的，模型只能填參數、
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

table: PDP_PDPConsolidatedSummary_Report（PDP 產品開發專案彙總表，一個 ProjectCode 一筆，
  約 4.7 萬筆。地位類似 CAR 那邊的 CAR_VIEW_combined，但這張每個專案都有、不是抽樣視圖）
  - ProjectCode (nvarchar): 專案代碼，唯一鍵。主要格式是 PDPn-YYYYMM-NNNN
    （例如 PDP5-202409-0007，n 見過 2/3/5/T），部分帶 -NN 尾碼（PDP3-202112-0016-01，
    約 1,952 筆）。⚠️ 另外還有一批舊格式：PDP-NNNNNNNN（約 4,910 筆）、TEMP-*、
    以及 14B-*／18C-*／15B-*／18D-* 這類早期編碼。查詢時把使用者給的代碼原樣比對，
    不要假設它一定符合 PDPn-YYYYMM-NNNN，也不要「修正」它的格式
  - ProjectName, ODMProjectName, ProductName (nvarchar): 專案名稱、ODM 專案名、產品名稱
  - ProductPartNo (nvarchar): 產品料號；OriginalPartNo 是沿用機種的原始料號
  - ProjectDivision, ProductionMU, ProjectPriority (nvarchar): 事業處、生產單位、優先度
  - StatusCode / StatusCodeName (nvarchar): 流程狀態，StatusCodeName 是中文，例如 已作廢、已放棄
  - CurrentTaskProName, CurrentTaskExeUserName (nvarchar): 目前關卡名稱、目前關卡執行人
  - PM, PE, SALES, RD (nvarchar): 各角色負責人「姓名」（不是工號）；對應的
    PM_DepCode / PE_DepCode / SALESDepCode / RDDepCode 是部門代碼
  - RDPMManager, DesignRDManager, PEManager (nvarchar): 各角色主管
  - ProjectCategoryName, ProductCategoryName, ProductSubCategoryName, ProjectTypeName,
    ProductionTypeName, DesignCaseName, ProjectDifficultName: 各種分類名稱（都是中文）
  - CustLevel, POCustomerDisplayName, EndCustomerDisplayName, DecisionCustomer: 客戶分級與名稱
  - TargetPrice_2, TotalCost_2, GrossMargin_P, ExpectedCost, TotalProfit,
    CustomerTotalPriceNTD, ActualTotalPriceNTD (numeric): 價格、成本、毛利
  - EstimatedTotalDemand, EstimatedMonthlyDemand, CustomerTotalDemand (numeric): 需求量預估
  - CreateStamp, DR1EndDate, DR2EndDate, DR3EndDate, EndProjectDate,
    RequestedProductionDate (datetime): 建立時間與各 Design Review 關卡結束時間
  - TotalProcessTime (numeric), CurrentVOIDStatusTime (int): 累計處理時間、目前狀態停留時間
  - VOIDStamp (datetime), VOIDReason (nvarchar): 作廢時間與原因
  - [Cumulative Shipments > 1,000 pcs] (varchar): 累計出貨是否破千
    ⚠️ 欄位名稱本身含空白、逗號和大於號，一定要用中括號包起來，否則語法錯誤
  用途：查某個 PDP 專案的狀態、負責人、客戶、DR 關卡進度、成本毛利

table: PDP_RelatedDoc_Info（PDP 專案的「對應附件」清單，一個專案可對應多個附件，
  約 3.9 萬筆。⚠️ 這張表就是回答「某個 PDP 有沒有附件／有哪些附件」的地方）
  - SourceDocID (nvarchar): 來源單號，對應 PDP_PDPConsolidatedSummary_Report.ProjectCode。
    ⚠️ 這張表也存了非 PDP 的來源單號（約 2,354 筆是純數字的單號，例如 202409070794），
    要限定在真的 PDP 專案上就 join 主表，或加 SourceDocID LIKE 'PDP%'
  - DocValue (varchar): ⚠️ 判斷有無附件就看這個欄位，只有「需要」/「不需要」兩種值。
    DocValue = '需要' 表示該項目「有」對應附件；'不需要' 表示沒有。
    所以「這個 PDP 有沒有附件」= 該 ProjectCode 有沒有 DocValue = '需要' 的列，
    一定要加這個條件——不能只看有沒有列存在，全表約七成的列都是「不需要」。
    全庫 47,605 個專案裡只有 2,778 個（約 5.8%）有附件，「查無附件」是常見答案，
    不是查詢失敗。
  - CategoryDescription (nvarchar): 該附件項目的名稱，例如 PFMEA、DFMEA、
    QualityControlPlan、是否已有可用安規、設計/生產文件一致性確認
  - GridType (varchar): 該附件屬於哪個驗證階段，只有 EVT / DVT / PVT 三種值（EVT 約七成）
  - CategoryManagementID (nvarchar): 附件項目本身的代碼，格式 CTM-YYYYMM-NNNNNN
  用途：查某個 PDP 專案有沒有附件、有哪些附件（單一專案最多見過 11 個）

table: AI_Library_KBMetadata（知識庫的「來源單」主檔，約 4.2 萬筆。表單被灌進知識庫時
  每一筆對應一個 MetadataID，這張表就是「表單號碼 → MetadataID」的對照表；附件的
  實體檔案在 AI_Library_KBFile，chunk 全文要用 get_kb_chunks 工具撈）
  - MetadataID (nvarchar): 這一筆在知識庫的 ID，32 碼小寫 hex，FK 給 AI_Library_KBFile
  - SourceID (nvarchar): ⚠️ 來源表單號碼，接得回前面 CAR／PDP 兩套單號，但有兩種形態：
    · CAR：就是 CARID 原樣（一個 CARID 剛好一筆），例如 CAR3-202608-000293
    · PDP：專案本身一筆（ProjectCode，例如 PDP5-202302-0057），另外「每個附件項目」
      各一筆複合單號 = ProjectCode + '_' + GridType + '_' + CategoryManagementID，
      例如 PDP5-202302-0057_EVT_CTM-202209-051001（後兩段對得回 PDP_RelatedDoc_Info）。
      所以查一個 PDP 專案的檔案要同時抓 SourceID = 專案代碼 和
      SourceID LIKE 專案代碼 + '_%'，只用等號會漏掉附件那幾筆（附件都在複合單號底下）
  - SourceName (nvarchar): 來源系統，CAR（2.5 萬筆）/ PDM（= PDP，8,236 筆）/
    ISODoc / LessonLearn / PERSONAL（個人上傳）
  - SourceType (nvarchar): QUEUE / IMPORT / WEB / APP
  - DocType (nvarchar): CAR / PDM / KM / DCC_AVCVN / QIM / USERUPLOAD
  - Title (nvarchar): 這一筆的摘要標題，把表單重點串成一行
    （例如「CARID：CAR3-202608-000293｜料號：…｜CAR類型：OQC｜…」）
  - ReferenceURL (nvarchar): 回原系統看那張表單的連結
  - TotalCount (int): 這一筆底下有幾個檔案
  - StatusCode (nvarchar): RELEASE（正常）/ VOID（作廢）/ FAILED（處理失敗）/ PROCESSING
  - CompCode, DepCode, EmpID (nvarchar): 這份文件的可看範圍（公司別／部門代碼清單／
    員工代碼清單），'ALL' = 不限。CAR 全部是 ALL；PERSONAL 一律綁特定 EmpID
  - AllowDirect, AllowDownload (nvarchar): 'Y'／'N'
  - CreateStamp, UpdateStamp (datetime)
  用途：把表單號碼換成 MetadataID、查這張單有沒有被灌進知識庫、狀態如何

table: AI_Library_KBFile（知識庫的實體檔案清單，約 5.2 萬筆，一個 MetadataID 可多筆）
  - FileID (nvarchar): 檔案 ID，32 碼小寫 hex；撈 chunk 全文要的就是這個
  - MetadataID (nvarchar, FK -> AI_Library_KBMetadata.MetadataID)
  - OriginalFileName (nvarchar): 原始檔名
  - FileType (nvarchar): DATA（系統把表單內容轉出來的 .json，不是使用者上傳的檔）/
    FILE（真正的附件，pdf/docx/xlsx）/ TEXT / URL
  - StatusCode (nvarchar): ⚠️ 只有 INDEX_OK（41,124 筆）才真的進了搜尋索引、撈得到
    chunk；VOID / CHUNK_NG / CHUNK_UNSUPPORTED / UPLOAD_NG 這些撈了都是空的
  - FileSize (bigint): 位元組；FileKey (nvarchar) 是儲存路徑；ProjectFileID、ETag
  - CreateStamp, UpdateStamp (datetime)
  用途：MetadataID → 有哪些檔案、每個檔案的 FileID（再拿去 get_kb_chunks 撈全文）

table: HCM_EmployeeData（員工主檔，約 25 萬筆）
  ⚠️ 這張表原本有 135 個欄位，大部分是個資。下面列出的是「唯一查得到」的欄位——
  沒列到的（身分證、銀行帳號、護照、住址、手機、緊急聯絡人、密碼、推播 token 等）
  會被 structured_db 模組的欄位允許清單擋下來，不要嘗試去查；也不能用 SELECT *，
  一定要列出明確欄位（COUNT(*) 例外，可以用）。
  - WID (nvarchar): 工號，多數是七碼數字（例如 2647120），也有 H 開頭的（例如
    H26000850），所以是字串不是數字，比較時不要當成 int；GroupWID 是集團工號
  - MemID (nvarchar): 內部會員代碼，格式 MEM 開頭一長串數字；EmployeeDataID 是本表主鍵
    （格式 EMD-YYYYMM-NNNNNN）
    ⚠️ 同一個人可能有多筆歷史紀錄（同 WID／MemID 出現多列），要查「某人現在的資料」
    必須 ORDER BY UpdateStamp DESC 取最新一筆，不然會拿到舊版本
  - UserName, UserEnglishName (nvarchar): 中文姓名、英文姓名
  - DepCode, DepName (nvarchar): 部門代碼、部門名稱；另有 DivisionDepCode、GroupDepCode、
    VirtDepCode／VirtDepCodeName（虛擬部門）
  - TitleCodeName, PositionCodeName, StandardJobTitle, JobCode, NewJobLevel: 職稱、職級
  - Resign (nvarchar): 是否離職，⚠️ 值是字串 'True' / 'False'，不是 bit，比較時要用字串
  - ResignDate, ReinstatementDate (datetime): 離職日、復職日
  - OnBoardDate, GroupOnboardDate (datetime): 到職日、集團到職日
  - CompanySeniority, GroupSeniority (decimal): 公司年資、集團年資
  - EmpEmail, Email (nvarchar), OfficePhone (nvarchar): 公司信箱、公務分機
  - StatusCode (nvarchar): 表單流程狀態，RELEASE 表示該筆已生效
  用途：查某位員工的部門、職稱、到職／離職狀態、年資、公司聯絡方式
"""


def _format_rows(columns: list[str], rows: list[dict]) -> str:
    if not rows:
        return "查無資料。"
    lines = [" | ".join(columns)]
    for r in rows:
        lines.append(" | ".join(str(r.get(c, "")) for c in columns))
    return "\n".join(lines)


def _format_records(columns: list[str], rows: list[dict]) -> str:
    """欄位很多、筆數很少時用直式（欄位: 值）輸出。

    _format_rows 那種 pipe 表格欄位一多就排成又長又難對齊的一行；PDP 專案彙總
    一次就 20 幾欄，直式讀起來清楚得多。空值直接略過不印，省 token。
    """
    if not rows:
        return "查無資料。"
    blocks = []
    for i, r in enumerate(rows, 1):
        lines = [f"── 第 {i} 筆 ──"] if len(rows) > 1 else []
        lines += [f"{c}: {r[c]}" for c in columns if r.get(c) not in (None, "")]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _human_size(size) -> str:
    """位元組轉人看得懂的大小。FileSize 允許 NULL，所以要吃得下 None。"""
    if size in (None, ""):
        return "大小未知"
    try:
        n = float(size)
    except (TypeError, ValueError):
        return str(size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _like_prefix(doc_no: str) -> str:
    """把單號轉成「底下的複合單號」用的 LIKE 樣板（配 SQL 裡的 ESCAPE '\\'）。

    PDP 的附件掛在 ProjectCode + '_' + GridType + '_' + CategoryManagementID 這種
    複合 SourceID 底下，所以要用前綴比對。⚠️ T-SQL 的 LIKE 裡 _ 本身是「任一個
    字元」的萬用字元，直接寫 code + '_%' 會讓 PDP3-202112-0016 也撈到
    PDP3-202112-0016-01（_ 剛好比對到那個 -），所以使用者給的值裡的 \\ % _ [
    一律轉義，只有最後接上去的那個底線是真的分隔符。
    """
    escaped = (doc_no.replace("\\", "\\\\").replace("%", "\\%")
               .replace("_", "\\_").replace("[", "\\["))
    return escaped + "\\_%"


# AI_Library_KBFile.StatusCode 代表「已建索引、撈得到 chunk」的值。
# 同一個值在 tools/kb_chunks.py 也用到（INDEXED_STATUS）。
_INDEXED_STATUS = "INDEX_OK"


def _format_kb_attachments(doc_no: str, rows: list[dict]) -> str:
    """把 metadata × file 的 join 結果排成「一張來源單一個區塊」。

    刻意不用 _format_rows 那種 pipe 表格：一列同時有 Title、ReferenceURL 跟兩個
    32 碼 ID，排成表格會又寬又難讀，而且模型要從裡面挑出 MetadataID／FileID 帶去
    get_kb_chunks——ID 直式列出來比較不容易抄錯。
    """
    if not rows:
        return (f"{doc_no}：知識庫裡查不到這張單"
                f"（AI_Library_KBMetadata 沒有 SourceID = {doc_no} 或 {doc_no}_… 的紀錄）。"
                "可能是還沒被灌進知識庫，或單號打錯。")

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("SourceID") or "", []).append(r)
    files = [r for r in rows if r.get("FileID")]
    fetchable = [r for r in files if r.get("FileStatus") == _INDEXED_STATUS]

    head = [f"{doc_no}：知識庫裡有 {len(groups)} 筆來源單（MetadataID）、{len(files)} 個檔案，"
            f"其中 {len(fetchable)} 個可以撈 chunk 全文（StatusCode = {_INDEXED_STATUS}）。"]
    if fetchable:
        head.append("要看某個檔案裡實際寫了什麼，把下面的 MetadataID 與 FileID "
                    "原樣帶進 get_kb_chunks。")

    blocks = []
    for source_id, rs in groups.items():
        m = rs[0]
        line = (f"── {source_id}（{m.get('SourceName')} / {m.get('MetaStatus')}）"
                f"｜MetadataID: {m.get('MetadataID')}")
        # 可看範圍不是 ALL 的文件，get_kb_chunks 預設會擋（見該檔案的權限說明），
        # 這裡先標出來，免得模型撈到一半才發現拿不到內容。
        scoped = [f"{c}={_clip(m.get(c) or '', 40)}" for c in ("CompCode", "DepCode", "EmpID")
                  if (m.get(c) or "").strip().upper() != "ALL"]
        if scoped:
            line += f"｜⚠️ 可看範圍受限（{'、'.join(scoped)}）"
        lines = [line]
        title = _clip(m.get("Title") or "", 140)
        if title:
            lines.append(f"   摘要：{title}")
        url = (m.get("ReferenceURL") or "").strip()
        if url:
            lines.append(f"   表單連結：{url}")
        for r in rs:
            if not r.get("FileID"):
                lines.append("   （這筆底下沒有任何檔案）")
                continue
            ok = r.get("FileStatus") == _INDEXED_STATUS
            lines.append(
                f"   {'[可撈]' if ok else '[不可撈]'} {r.get('OriginalFileName')}"
                f"（{r.get('FileType')}, {_human_size(r.get('FileSize'))}, {r.get('FileStatus')}）"
                f"｜FileID: {r.get('FileID')}")
        blocks.append("\n".join(lines))
    return "\n".join(head) + "\n\n" + "\n".join(blocks)


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

    # ── 固定查詢 ──
    # SQL 由開發者寫死，模型只能填參數，而且參數是 bindparams 綁定值、不是字串
    # 插進 SQL 裡，天生不怕 injection。下面三支對應三種高頻情境；問題只要對得上
    # 就會直接選到它們，不會落到 db_query 那條 text-to-SQL 的路徑上。

    @server.tool(
        name="query_pdp_project",
        description=(
            "依專案代碼查一個 PDP 產品開發專案的彙總資料：流程狀態、目前卡在哪一關、"
            "PM/PE/SALES/RD 負責人、客戶、DR1~DR3 關卡日期、成本與毛利。"
            "專案代碼主要是 PDPn-YYYYMM-NNNN（例如 PDP5-202409-0007），也有帶 -NN 尾碼"
            "（PDP3-202112-0016-01）和 PDP-NNNNNNNN、TEMP-*、14B-* 等舊格式——"
            "問題裡出現 PDP 專案代碼時直接用這支，不用先查 knowledge_search，"
            "代碼原樣傳進來、不要修正格式。"
        ),
    )
    def query_pdp_project(project_code: str) -> str:
        """PDP 專案代碼，原樣照抄使用者給的值（例如 PDP5-202409-0007、PDP3-202112-0016-01）"""
        sql = (
            "SELECT ProjectCode, ProjectName, ODMProjectName, ProductPartNo, ProductName, "
            "ProjectDivision, ProjectPriority, StatusCodeName, CurrentTaskProName, "
            "CurrentTaskExeUserName, PM, PE, SALES, RD, ProjectCategoryName, "
            "ProductCategoryName, ProjectTypeName, CustLevel, POCustomerDisplayName, "
            "EndCustomerDisplayName, CreateStamp, DR1EndDate, DR2EndDate, DR3EndDate, "
            "RequestedProductionDate, EndProjectDate, TotalProcessTime, VOIDReason "
            "FROM PDP_PDPConsolidatedSummary_Report WHERE ProjectCode = :project_code"
        )
        columns, rows = db.run_readonly(sql, params={"project_code": project_code})
        return _format_records(columns, rows)

    @server.tool(
        name="query_pdp_related_docs",
        description=(
            "查一個 PDP 專案「有沒有對應附件、有哪些附件」（PFMEA、DFMEA、"
            "QualityControlPlan、安規申請、設計/生產文件一致性確認等）。"
            "一個專案可以對應多個附件。問「這個 PDP 有沒有附件」、「附件有哪些」"
            "就用這支——回傳的第一行就是有幾個附件的結論，不用自己判斷。"
            "專案代碼把使用者給的值原樣傳進來，不要修正格式。"
        ),
    )
    def query_pdp_related_docs(project_code: str) -> str:
        """PDP 專案代碼，原樣照抄使用者給的值（例如 PDP5-202409-0007、PDP3-202112-0016-01）"""
        # 「有沒有附件」看的是 DocValue = '需要'，不是「這個專案在表裡有沒有列」——
        # 全表約七成的列是「不需要」，只看列數會把「明確標記為不需要」誤判成有附件。
        # 不需要的列一樣回傳但排在後面，這樣「有哪些附件」跟「哪一項被標為不需要」
        # 兩種問題都答得出來。
        # ORDER BY 寫成 CASE 而不是直接靠 DocValue 排序：中文字的排序結果取決於
        # collation，不直觀也不保證，明確寫「需要優先」才穩。
        sql = (
            "SELECT DocValue, GridType, CategoryDescription, CategoryManagementID "
            "FROM PDP_RelatedDoc_Info WHERE SourceDocID = :project_code "
            "ORDER BY CASE WHEN DocValue = N'需要' THEN 0 ELSE 1 END, "
            "GridType, CategoryDescription"
        )
        columns, rows = db.run_readonly(sql, params={"project_code": project_code})
        if not rows:
            return f"{project_code}：沒有對應附件（這個專案在附件清單裡完全沒有資料）。"
        attached = [r for r in rows if r.get("DocValue") == "需要"]
        skipped = len(rows) - len(attached)
        if not attached:
            head = f"{project_code}：沒有對應附件（{skipped} 個項目全部標記為「不需要」）。"
        else:
            head = (f"{project_code}：有 {len(attached)} 個對應附件"
                    f"（另有 {skipped} 個項目標記為「不需要」）。")
        return f"{head}\n\n{_format_rows(columns, rows)}"

    @server.tool(
        name="query_kb_attachments",
        description=(
            "依表單號碼查它在知識庫裡有哪些檔案（附件），並回傳撈全文需要的 "
            "MetadataID 與 FileID。吃 CAR 案號（CAR3-YYYYMM-NNNNNN）與 PDP 專案代碼"
            "（PDP5-202409-0007 等），PDP 會一併帶出各附件項目（EVT/DVT/PVT 各階段的 "
            "PFMEA、DFMEA、安規申請、一致性確認…）底下的檔案。"
            "何時使用：使用者想知道「這張單有沒有附件、附件是什麼檔」，"
            "或接下來要用 get_kb_chunks 看附件內容時——先用這支拿到兩個 ID。"
            "注意：這支只回檔案清單，不回檔案內容；單號原樣傳進來，不要修正格式。"
        ),
    )
    def query_kb_attachments(doc_no: str) -> str:
        """表單號碼，原樣照抄使用者給的值（CAR 案號如 CAR3-202310-000261，
        PDP 專案代碼如 PDP5-202302-0057；也可以直接給 PDP 的複合單號
        PDP5-202302-0057_EVT_CTM-202209-051001）"""
        # 等號比對抓「這張單本身」那一筆，LIKE 前綴抓 PDP 附件用的複合單號；
        # 只有其中一種的話 CAR 查不到（沒有複合單號）或 PDP 漏掉全部附件。
        # LEFT JOIN 是因為 metadata 可能一個檔案都沒有（例如 StatusCode=FAILED
        # 的那 984 筆），那種情況也要看得到「這張單在知識庫裡但沒有檔案」。
        sql = (
            "SELECT m.SourceID, m.MetadataID, m.SourceName, m.DocType, "
            "m.StatusCode AS MetaStatus, m.TotalCount, m.Title, m.ReferenceURL, "
            "m.CompCode, m.DepCode, m.EmpID, f.FileID, f.OriginalFileName, "
            "f.FileType, f.FileSize, f.StatusCode AS FileStatus "
            "FROM AI_Library_KBMetadata m "
            "LEFT JOIN AI_Library_KBFile f ON f.MetadataID = m.MetadataID "
            "WHERE m.SourceID = :doc_no OR m.SourceID LIKE :doc_prefix ESCAPE '\\' "
            # 排序：來源單擺在一起（專案本身那筆的 SourceID 最短，會排在附件前面），
            # 每張單裡把「撈得到 chunk 的真附件」排在最上面，DATA 的 .json 跟
            # 作廢/失敗的檔案往後排。ORDER BY 明確寫死，應用層截斷（ROW_LIMIT）
            # 才會是「砍掉最不重要的那些」而不是隨機少幾筆。
            "ORDER BY m.SourceID, "
            "CASE WHEN f.StatusCode = 'INDEX_OK' THEN 0 ELSE 1 END, "
            "CASE WHEN f.FileType = 'FILE' THEN 0 ELSE 1 END, f.OriginalFileName"
        )
        columns, rows = db.run_readonly(
            sql, params={"doc_no": doc_no, "doc_prefix": _like_prefix(doc_no)})
        return _format_kb_attachments(doc_no, rows)

    @server.tool(
        name="query_employee",
        description=(
            "依工號或姓名查員工的部門、職稱、到職／離職狀態、年資、公司信箱與分機。"
            "工號多為七碼數字（例如 2647120），也有 H 開頭的；姓名可以只給前幾個字，會做前綴比對。"
            "注意：只回傳上述公務欄位，員工的身分證、銀行帳號、住址、個人手機、"
            "緊急聯絡人等個資一律不開放查詢。"
        ),
    )
    def query_employee(keyword: str) -> str:
        """員工工號（多為七碼數字，也有 H 開頭）或中文姓名，姓名可以只給前幾個字"""
        # 同一個人可能有多筆歷史紀錄，UpdateStamp DESC 讓最新一筆排在最前面；
        # TOP 20 是給「同姓多人」留的空間，不是一筆就夠。
        sql = (
            "SELECT TOP 20 WID, UserName, UserEnglishName, DepCode, DepName, "
            "TitleCodeName, PositionCodeName, OnBoardDate, CompanySeniority, "
            "Resign, ResignDate, EmpEmail, OfficePhone, UpdateStamp "
            "FROM HCM_EmployeeData "
            "WHERE WID = :kw OR MemID = :kw OR UserName LIKE :kw_like "
            "ORDER BY UpdateStamp DESC"
        )
        columns, rows = db.run_readonly(
            sql, params={"kw": keyword, "kw_like": f"{keyword}%"})
        return _format_rows(columns, rows)
