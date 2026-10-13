"""Shared fixtures. Integration tests use real PostgreSQL (ONBOARDING_TEST_DATABASE_URL)."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "ONBOARDING_TEST_DATABASE_URL",
    "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/onboarding_test",
)
ARTIFACTS_DIR = Path(tempfile.mkdtemp(prefix="onboarding-artifacts-"))
# Must be set before app settings are first read.
os.environ["ONBOARDING_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ONBOARDING_ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
# Tests execute runs inline unless they exercise Temporal explicitly (tests/e2e durable tests).
os.environ.setdefault("ONBOARDING_ORCHESTRATOR", "inline")
# 8199/8198: mock CMS processes started by the e2e browser tests (UI v1 / v2).
os.environ.setdefault(
    "ONBOARDING_ALLOWED_CMS_ORIGINS",
    "http://localhost:8100,http://127.0.0.1:8199,http://127.0.0.1:8198",
)

import pytest  # noqa: E402

from app.config import REPO_ROOT  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures"
SOURCE_FILES = {
    "firm-a": ("json_schema", FIXTURES / "source-schemas" / "firm-a.schema.json"),
    "firm-b": ("field_export_csv", FIXTURES / "source-schemas" / "firm-b.fields.csv"),
    "firm-c": ("html_form", FIXTURES / "source-schemas" / "firm-c.form.html"),
}


def read_fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def artifacts_dir() -> Iterator[Path]:
    yield ARTIFACTS_DIR
