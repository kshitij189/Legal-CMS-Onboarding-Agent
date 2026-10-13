import os
from dataclasses import dataclass
from functools import lru_cache

# Matches docker-compose.yml defaults; local development credentials only.
_DEFAULT_DATABASE_URL = "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/mock_cms"


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_ttl_minutes: int
    cookie_secure: bool
    # "v1" or "v2": v2 renames some form controls, simulating a vendor UI release (drift tests).
    ui_variant: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("MOCK_CMS_DATABASE_URL", _DEFAULT_DATABASE_URL),
        session_ttl_minutes=int(os.environ.get("MOCK_CMS_SESSION_TTL_MINUTES", "480")),
        cookie_secure=os.environ.get("MOCK_CMS_COOKIE_SECURE", "false").lower() == "true",
        ui_variant=os.environ.get("MOCK_CMS_UI_VARIANT", "v1"),
    )
