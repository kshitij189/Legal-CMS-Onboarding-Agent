"""Run the evaluation suite with one command (blueprint §13, Phase 6).

    backend\\.venv\\Scripts\\python scripts\\run_evaluations.py            # replay recorded LLM answers
    backend\\.venv\\Scripts\\python scripts\\run_evaluations.py --llm live # call the LLM and re-record

Needs PostgreSQL (docker compose up -d postgres) with the `onboarding_eval` database, which is
rebuilt from migrations on every run. Uses only simulated CMSs, so no other service is needed.
Writes docs/evidence/evaluation-report.{json,md}; exits 1 if a hard safety criterion fails.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Isolated settings, fixed before the application reads its configuration.
os.environ["ONBOARDING_DATABASE_URL"] = os.environ.get(
    "ONBOARDING_EVAL_DATABASE_URL",
    "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/onboarding_eval",
)
os.environ["ONBOARDING_ORCHESTRATOR"] = "inline"
os.environ["ONBOARDING_ARTIFACTS_DIR"] = tempfile.mkdtemp(prefix="onboarding-eval-")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")

from app.evaluation.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
