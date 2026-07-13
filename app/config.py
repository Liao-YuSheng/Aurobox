"""
環境變數設定。
從 .env 檔案讀取 LINE 相關的機密資訊，絕對不要把這些值寫死在程式碼裡。
"""
from functools import lru_cache
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LIFF_ID: str = ""
    LINE_LOGIN_CHANNEL_ID: str = ""
    ROBOT_API_BASE_URL: str = ""
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/aurobox_line"
    APP_ENV: str = "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
