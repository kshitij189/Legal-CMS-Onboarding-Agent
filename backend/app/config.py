import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Matches docker-compose.yml defaults; local development credentials only.
_DEFAULT_DATABASE_URL = "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/onboarding"


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Origins a CMS connector may point at; anything else is rejected (blueprint §14).
    allowed_cms_origins: frozenset[str]
    artifacts_dir: Path
    cms_timeout_seconds: float


@lru_cache
def get_settings() -> Settings:
    origins = os.environ.get("ONBOARDING_ALLOWED_CMS_ORIGINS", "http://localhost:8100")
    return Settings(
        database_url=os.environ.get("ONBOARDING_DATABASE_URL", _DEFAULT_DATABASE_URL),
        allowed_cms_origins=frozenset(o.strip().rstrip("/") for o in origins.split(",") if o),
        artifacts_dir=Path(os.environ.get("ONBOARDING_ARTIFACTS_DIR", REPO_ROOT / "artifacts")),
        cms_timeout_seconds=float(os.environ.get("ONBOARDING_CMS_TIMEOUT_SECONDS", "10")),
    )
