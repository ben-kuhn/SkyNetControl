from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    debug: bool = False

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
