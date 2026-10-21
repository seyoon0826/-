from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://stock:stock@localhost:5432/stocksim"
    redis_url: str = "redis://localhost:6379/0"
    anthropic_api_key: str = ""
    kis_paper: bool = True


settings = Settings()
