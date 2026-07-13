"""設定：連線字串從 .env 讀。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://harness:harness@localhost:5432/harness"


settings = Settings()
