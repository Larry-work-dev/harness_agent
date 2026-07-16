"""模型設定 —— 掛 model 的地方（OpenAI 相容端點）。

支援：
  - 分級 profile（local / cloud / mid / cheap / default），各讀自己那組環境變數，
    未設則回退到 LLM_*。供路由層依任務挑選。
  - 依明確 spec（base_url/model/api_key）建立模型：給「gateway 上選的模型」或
    「使用者自訂 profile」使用。
  - 列出 gateway /v1/models 可用模型。
  - embedding 模型（長期記憶語意召回）。

環境變數（以 CLOUD 為例）：CLOUD_BASE_URL / CLOUD_MODEL / CLOUD_API_KEY，
未設則用 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DEFAULT_BASE = "https://devops.avc.co:28003/LLM_api/v1"

# 路由層會用到的分級 profile 名稱
PROFILES = ["local", "cloud", "mid", "cheap"]


def _openai_http_client():
    """依環境變數回傳處理過憑證的 httpx client（LLM / embedding / 列模型共用）。"""
    import httpx

    ca = os.environ.get("LLM_CA_BUNDLE")
    verify_ssl = os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"
    if ca:
        return httpx.Client(verify=ca)
    if not verify_ssl:
        return httpx.Client(verify=False)
    return None


def profile_spec(profile: str | None) -> dict:
    """把 profile 名稱轉成 {base_url, model, api_key}；未設則回退 LLM_*。"""
    pre = (profile or "").upper()
    def g(suffix, fallback):
        return os.environ.get(f"{pre}_{suffix}" if pre else "__none__", fallback)
    return {
        "base_url": g("BASE_URL", os.environ.get("LLM_BASE_URL", DEFAULT_BASE)),
        "model":    g("MODEL",    os.environ.get("LLM_MODEL", "")),
        "api_key":  g("API_KEY",  os.environ.get("LLM_API_KEY", "sk-noauth")),
    }


def build_chat_model(base_url, model, api_key, temperature=0.0):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model, base_url=base_url, api_key=api_key,
        temperature=temperature, http_client=_openai_http_client(),
    )


def create_model(profile: str | None = None, spec: dict | None = None, temperature: float = 0.0):
    """建立聊天模型：給 spec 就用 spec，否則依 profile（None → LLM_* 預設）。"""
    from app.module.logs import get as get_logger
    if spec is None:
        spec = profile_spec(profile)
    verify = os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"
    get_logger("model").info("create_model: model=%s base=%s verify_ssl=%s",
                             spec["model"], spec["base_url"], verify)
    return build_chat_model(spec["base_url"], spec["model"], spec.get("api_key"), temperature)


def list_gateway_models() -> dict:
    """列出 gateway /v1/models。回 {models: [...], error: None|str}，讓前端能分辨『沒模型』與『連不到』。"""
    import httpx

    base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE).rstrip("/")
    key = os.environ.get("LLM_API_KEY", "sk-noauth")
    client = _openai_http_client() or httpx.Client()
    try:
        r = client.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        r.raise_for_status()
        return {"models": [m["id"] for m in r.json().get("data", [])], "error": None}
    except Exception as e:
        return {"models": [], "error": str(e)}
    finally:
        try: client.close()
        except Exception: pass


def create_embedder():
    """長期記憶用的 embedding 模型（OpenAI 相容 /v1/embeddings）。"""
    from langchain_openai import OpenAIEmbeddings
    from app.module.logs import get as get_logger
    m = os.environ.get("EMBED_MODEL", "Qwen3-embedding")
    base = os.environ.get("EMBED_BASE_URL", os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    get_logger("model").debug("create_embedder: model=%s base=%s", m, base)
    return OpenAIEmbeddings(
        model=m, base_url=base,
        api_key=os.environ.get("LLM_API_KEY"),
        http_client=_openai_http_client(),
        check_embedding_ctx_length=False,
    )
