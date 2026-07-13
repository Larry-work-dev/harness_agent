# Agent Harness

前後分離、多使用者、有長短期記憶的對話式 agent。服務拆成四塊：

```
frontend (Vite+Vue) ── 對話式深色 UI（登入 + workspace + 對話列表 + 記憶面板）
      │  同源反代
backend           ── 主邏輯：認證流程、harness、兩層記憶編排
      │  HTTP
db_api            ── 唯一連 DB 的服務，只做 CRUD / 向量查詢
      │
db (pgvector) + pgadmin   ── 獨立一包，可單獨啟停 / 備份
```

- **db_api** 是唯一碰 Postgres 的服務；backend 完全不裝 DB driver。
- **記憶兩層**：短期＝單一對話的滾動摘要 + 最近幾輪；長期＝綁 user 的事實，用 pgvector 語意召回。
- **workspace** 為租戶邊界，可多人共用；長期記憶綁 user。

## 啟動（Docker）

先起 DB 這包（會建立共用網路 harness-net）：
```bash
docker compose -f db/docker-compose.yml up -d
```
填好 backend 設定後起應用這包：
```bash
cp backend/.env.example backend/.env    # 填入 LLM_MODEL / LLM_API_KEY 等
docker compose up -d --build
```
- 前端： http://localhost:8080
- pgAdmin： http://localhost:5050 （admin@harness.local / admin；連線 host=db、port=5432、db/user/pw=harness）

停止 / 清資料：
```bash
docker compose down                         # 停應用
docker compose -f db/docker-compose.yml down        # 停 DB（保留資料）
docker compose -f db/docker-compose.yml down -v     # 連資料一起清
```

## 服務埠

| 服務 | 埠 | 說明 |
|---|---|---|
| frontend | 8080 | nginx，serve 前端 + 反代 API |
| backend | 8000 | 主邏輯 API |
| db_api | (內部) | CRUD，僅在 harness-net 內 |
| db | 5432 | Postgres + pgvector |
| pgadmin | 5050 | DB 管理介面 |

## 記憶如何運作

- 每輪對話：短期記憶提供「摘要 + 最近幾輪」當上下文；長期記憶用當前問題做向量召回，注入最相關的幾條。
- 回覆後：把超出視窗的舊訊息折疊進滾動摘要；並萃取新的長期事實（embedding 後存入，去重）。

## 新增 skill

在 `backend/skills/` 放一個 `.py`、匯出 `SKILL` 物件即可，harness 會自動載入。

## 前端（Vite + Vue）

開發：`cd frontend && npm install && npm run dev`（vite proxy 會把 API 代到 backend:8000）。
正式：由 `frontend/Dockerfile` 建置後用 nginx serve，`docker compose up` 會自動 build。
