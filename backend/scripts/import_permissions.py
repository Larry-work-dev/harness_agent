"""一次性 CLI：把 tmp/permission/*_filterCriteria.json 匯入 DB。

每個檔案 <姓名>_filterCriteria.json 的結構（小奇小助手匯出的權限快照）：
  - empId          → users.emp_id（供登入後查 RAG 權限用，users 表比對用這個 key）
  - filterCriteria → user_permissions.filter_criteria，原樣存 JSONB；
                     knowledge_search 依目前登入者的 emp_id 查出來後，原樣當
                     RAG /api/v1/query 的 filter 送出，取代寫死的 RAG_FILTER。

新使用者一律用預設密碼（DEFAULT_PASSWORD，預設 123456；可用環境變數
IMPORT_DEFAULT_PASSWORD 覆寫），請登入後自行改密碼（POST /auth/change-password）。

使用者若已存在（username 與檔名相同）預設只更新 emp_id，不動密碼；
加 --reset-password 才會連同已存在的使用者一起把密碼重設回預設值。

用法：
  cd backend && python -m scripts.import_permissions [--reset-password] [目錄，預設 ../tmp/permission]
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.module import db_client as db  # noqa: E402
from app.services import auth  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "tmp" / "permission"
SUFFIX = "_filterCriteria"
DEFAULT_PASSWORD = os.environ.get("IMPORT_DEFAULT_PASSWORD", "123456")


def import_one(path: Path, reset_password: bool) -> None:
    name = path.stem[: -len(SUFFIX)] if path.stem.endswith(SUFFIX) else path.stem
    data = json.loads(path.read_text(encoding="utf-8"))
    emp_id = data.get("empId")
    filter_criteria = data.get("filterCriteria") or []
    if not emp_id:
        print(f"跳過 {path.name}：找不到 empId")
        return

    existing = db.get_user_by_name(name)
    if existing:
        db.set_user_emp_id(existing["id"], emp_id)
        if reset_password:
            pw_hash, salt = auth.hash_password(DEFAULT_PASSWORD)
            db.update_password(existing["id"], pw_hash, salt)
            print(f"更新使用者 {name}（emp_id={emp_id}），密碼已重設為預設值 {DEFAULT_PASSWORD}")
        else:
            print(f"更新使用者 {name}（emp_id={emp_id}）")
    else:
        pw_hash, salt = auth.hash_password(DEFAULT_PASSWORD)
        user_id = db.create_user(name, pw_hash, salt, emp_id=emp_id)
        db.create_workspace(f"{name} 的空間", user_id)
        print(f"新增使用者 {name}（emp_id={emp_id}），初始密碼：{DEFAULT_PASSWORD}")

    db.upsert_permission(emp_id, filter_criteria)
    print(f"  → 權限規則 {len(filter_criteria)} 條已寫入 user_permissions")


def main():
    args = sys.argv[1:]
    reset_password = "--reset-password" in args
    positional = [a for a in args if not a.startswith("--")]
    target = Path(positional[0]) if positional else DEFAULT_DIR
    files = sorted(target.glob(f"*{SUFFIX}.json"))
    if not files:
        print(f"{target} 底下沒有 *{SUFFIX}.json")
        return
    for f in files:
        try:
            import_one(f, reset_password)
        except Exception as e:  # noqa: BLE001
            print(f"匯入 {f.name} 失敗：{e}")


if __name__ == "__main__":
    main()
