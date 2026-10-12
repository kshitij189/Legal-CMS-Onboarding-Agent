import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Matches docker-compose.yml defaults; local development credentials only.
_DEFAULT_DATABASE_URL = "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/onboarding"


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a git-ignored .env file for local runs.

    Variables already present in the environment always win, so containers, CI and tests stay in
    control. Values are never logged.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Origins a CMS connector may point at; anything else is rejected (blueprint §14).
    allowed_cms_origins: frozenset[str]
    artifacts_dir: Path
    cms_timeout_seconds: float
    token_ttl_minutes: int
    # Self-service firm sign-up (creates a firm and its first firm admin). Disable outside demos.
    allow_signup: bool


@lru_cache
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    origins = os.environ.get("ONBOARDING_ALLOWED_CMS_ORIGINS", "http://localhost:8100")
    return Settings(
        database_url=os.environ.get("ONBOARDING_DATABASE_URL", _DEFAULT_DATABASE_URL),
        allowed_cms_origins=frozenset(o.strip().rstrip("/") for o in origins.split(",") if o),
        artifacts_dir=Path(os.environ.get("ONBOARDING_ARTIFACTS_DIR", REPO_ROOT / "artifacts")),
        cms_timeout_seconds=float(os.environ.get("ONBOARDING_CMS_TIMEOUT_SECONDS", "10")),
        token_ttl_minutes=int(os.environ.get("ONBOARDING_TOKEN_TTL_MINUTES", "480")),
        allow_signup=os.environ.get("ONBOARDING_ALLOW_SIGNUP", "true").lower() == "true",
    )
