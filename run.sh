#!/usr/bin/env bash
# 一鍵啟動 Agent Harness：準備環境、裝相依、同時跑後端與前端。
# 用法：
#   bash run.sh                 # 啟動前後端
#   bash run.sh --offline-demo  # 不需金鑰，只跑離線 skill 執行示範
set -euo pipefail

cd "$(dirname "$0")"          # 切到腳本所在目錄，路徑才穩定

BACKEND_PORT=8000
FRONTEND_PORT=5500
PY="${PYTHON:-python3}"

# --- 準備虛擬環境 --------------------------------------------------------
# 若已經在某個 venv 裡（例如你手動 activate 過），就沿用它，不另外建。
if [ -n "${VIRTUAL_ENV:-}" ]; then
  echo "▸ 沿用現有虛擬環境：$VIRTUAL_ENV"
else
  if [ ! -d ".venv" ]; then
    echo "▸ 建立虛擬環境 (.venv)…"
    "$PY" -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "▸ 安裝相依套件…"
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# --- 離線示範模式（不需 API 金鑰） ----------------------------------------
if [ "${1:-}" = "--offline-demo" ]; then
  echo "▸ 執行離線示範（假模型）…"
  ( cd backend && python demo_offline.py )
  exit 0
fi

# --- 檢查模型設定 --------------------------------------------------------
# 設定放在 backend/.env（見 .env.example）；也接受直接用環境變數 LLM_MODEL。
if [ ! -f "backend/.env" ] && [ -z "${LLM_MODEL:-}" ]; then
  echo "✗ 找不到模型設定。請擇一："
  echo "    cp backend/.env.example backend/.env   然後填入你的模型設定"
  echo "  或 export LLM_MODEL=\"你的模型名稱\"（及 LLM_API_KEY 等）"
  echo "  或改用離線示範：bash run.sh --offline-demo"
  exit 1
fi

# --- 結束時一併關閉子行程 --------------------------------------------------
pids=()
cleanup() {
  echo ""
  echo "▸ 關閉服務…"
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

# --- 啟動後端（注意：一定要在 backend/ 裡跑，skills 才找得到）-------------
echo "▸ 後端啟動中 → http://localhost:$BACKEND_PORT"
( cd backend && uvicorn main:app --port "$BACKEND_PORT" --reload ) &
pids+=($!)

# --- 啟動前端（靜態伺服器）-----------------------------------------------
echo "▸ 前端啟動中 → http://localhost:$FRONTEND_PORT"
( cd frontend && python -m http.server "$FRONTEND_PORT" >/dev/null 2>&1 ) &
pids+=($!)

echo ""
echo "═══════════════════════════════════════════════"
echo "  開啟瀏覽器：http://localhost:$FRONTEND_PORT"
echo "  按 Ctrl+C 結束"
echo "═══════════════════════════════════════════════"
wait
