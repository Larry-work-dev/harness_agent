"""db_client —— 以 HTTP 呼叫 db_api（只需要查 emp_id 對應的 RAG 權限這一個功能）。"""
import os

import httpx

DB_API_URL = os.environ.get("DB_API_URL", "http://localhost:9000")
_client = httpx.Client(base_url=DB_API_URL, timeout=30)


def get_permission_by_emp_id(emp_id: str):
    resp = _client.get("/permissions/by-emp-id", params={"emp_id": emp_id})
    resp.raise_for_status()
    return resp.json()
