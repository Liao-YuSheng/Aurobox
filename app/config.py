"""
環境變數設定。
從 .env 檔案讀取 LINE 相關的機密資訊，絕對不要把這些值寫死在程式碼裡。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    LINE_CHANNEL_SECRET: str
    LINE_CHANNEL_ACCESS_TOKEN: str
    LIFF_ID: str
    LINE_LOGIN_CHANNEL_ID: str    
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/aurobox_line"
    APP_ENV: str = "development"


settings = Settings()
