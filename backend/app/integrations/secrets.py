"""Resolve a connector's credential_ref to credentials, kept outside the database and model prompts.

A ref like "firm-a-mock-cms" reads ONBOARDING_SECRET_FIRM_A_MOCK_CMS_USERNAME / _PASSWORD.
"""

import os
import re
from dataclasses import dataclass, field

from app.errors import ConnectorError

CREDENTIAL_REF_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


def env_prefix(credential_ref: str) -> str:
    return "ONBOARDING_SECRET_" + _NON_ALNUM.sub("_", credential_ref.upper())


def resolve_credentials(credential_ref: str) -> Credentials:
    prefix = env_prefix(credential_ref)
    username = os.environ.get(f"{prefix}_USERNAME")
    password = os.environ.get(f"{prefix}_PASSWORD")
    if not username or not password:
        raise ConnectorError(
            "configuration",
            f"Credentials for '{credential_ref}' are not configured.",
            [
                {
                    "code": "missing_credentials",
                    "expected_env": [f"{prefix}_USERNAME", f"{prefix}_PASSWORD"],
                }
            ],
        )
    return Credentials(username=username, password=password)
