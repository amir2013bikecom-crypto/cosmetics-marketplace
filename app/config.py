from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    BOT_TOKEN: str
    MINI_APP_URL: str
    API_URL: str = ""
    SELLER_API_KEY: str
    DATABASE_URL: str
    CORS_ORIGINS: str = "*"
    DEBUG: bool = False
    WEBHOOK_SECRET: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
