"""模型設定 —— 唯一「掛 model」的地方。

想換模型、換供應商、改參數，只動這個檔案或 backend/.env，
main.py / harness.py 都不用碰。

用環境變數 LLM_PROVIDER 切換來源：
    openai     任何 OpenAI 相容端點（公司內部端點屬於這類，預設）
    anthropic  Anthropic 官方或自架 gateway
    ollama     本機 Ollama

其餘設定（模型名稱、金鑰、base_url、溫度）都從環境變數 / .env 讀。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 讀取 backend/.env（不論從哪個目錄啟動都找得到）
load_dotenv(Path(__file__).parent / ".env")


def create_model():
    """依 LLM_PROVIDER 建立並回傳一個聊天模型。"""
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0"))

    if provider == "openai":
        import httpx
        from langchain_openai import ChatOpenAI

        # 處理內部端點的自簽 / 私有 CA 憑證：
        #   LLM_CA_BUNDLE=/path/ca.pem  → 用公司 CA 驗證（正確做法）
        #   LLM_VERIFY_SSL=false        → 跳過驗證（僅限內網測試，不安全）
        #   兩者都沒設                   → 照系統預設正常驗證
        ca = os.environ.get("LLM_CA_BUNDLE")
        verify_ssl = os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"
        if ca:
            http_client = httpx.Client(verify=ca)
        elif not verify_ssl:
            http_client = httpx.Client(verify=False)
        else:
            http_client = None

        return ChatOpenAI(
            model=_require("LLM_MODEL"),
            base_url=os.environ.get("LLM_BASE_URL", "https://devops.avc.co:28003/LLM_api/v1"),
            api_key=os.environ.get("LLM_API_KEY", "sk-noauth"),  # 端點不需金鑰時留預設
            temperature=temperature,
            http_client=http_client,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=_require("LLM_MODEL"),
            api_key=_require("LLM_API_KEY"),
            temperature=temperature,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=_require("LLM_MODEL"),
            base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434"),
            temperature=temperature,
        )

    raise ValueError(
        f"未知的 LLM_PROVIDER：'{provider}'（可用：openai / anthropic / ollama）"
    )


def _openai_http_client():
    """依環境變數回傳處理過憑證的 httpx client（供 LLM 與 embedding 共用）。"""
    import httpx

    ca = os.environ.get("LLM_CA_BUNDLE")
    verify_ssl = os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"
    if ca:
        return httpx.Client(verify=ca)
    if not verify_ssl:
        return httpx.Client(verify=False)
    return None


def create_embedder():
    """長期記憶用的 embedding 模型（OpenAI 相容 /v1/embeddings）。"""
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=os.environ.get("EMBED_MODEL", "Qwen3-embedding"),
        base_url=os.environ.get("EMBED_BASE_URL",
                                os.environ.get("LLM_BASE_URL", "https://devops.avc.co:28003/LLM_api/v1")),
        api_key=os.environ.get("LLM_API_KEY", "sk-noauth"),
        http_client=_openai_http_client(),
        check_embedding_ctx_length=False,  # 非 OpenAI 端點不套 tiktoken 分段假設
    )


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"缺少環境變數 {key}。請在 backend/.env 設定"
            f"（可先 cp .env.example .env 再填）。"
        )
    return val
