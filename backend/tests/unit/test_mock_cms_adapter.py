"""Mock CMS adapter against a fake HTTP transport: normalization and error classification."""

from typing import Any

import httpx
import pytest

from app.errors import ConnectorError
from app.integrations.mock_cms import MAX_ATTEMPTS, MockCMSAdapter
from app.integrations.secrets import Credentials, env_prefix, resolve_credentials
from app.schemas.normalized import DataType, SchemaKind

BASE = "http://localhost:8100"
ALLOWED = frozenset({BASE})
CREDS = Credentials("a.integration", "secret")

SCHEMA_BODY: dict[str, Any] = {
    "tenant": {"slug": "firm-a", "name": "Firm A"},
    "config_revision": 7,
    "entities": ["contact", "matter"],
    "data_types": ["string"],
    "transformations": [
        {"name": "none", "applicable_types": ["string", "enum"]},
        {"name": "normalize_phone", "applicable_types": ["phone"]},
    ],
    "fields": [
        {
            "key": "contact.full_name",
            "entity": "contact",
            "label": "Full name",
            "description": "",
            "data_type": "string",
            "required": True,
            "enabled": True,
            "is_custom": False,
            "options": None,
        },
        {
            "key": "matter.custom.review_status",
            "entity": "matter",
            "label": "Review status",
            "description": "",
            "data_type": "enum",
            "required": True,
            "enabled": True,
            "is_custom": True,
            "options": ["pending", "reviewed"],
        },
        {
            "key": "matter.custom.signature",
            "entity": "matter",
            "label": "Signature",
            "description": "",
            "data_type": "signature",
            "required": False,
            "enabled": True,
            "is_custom": True,
            "options": None,
        },
    ],
}


class FakeCMS:
    """Scripted responses for /schema; records every request."""

    def __init__(self, schema_responses: list[Any]) -> None:
        self.schema_responses = schema_responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        outcome = self.schema_responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body)


def _adapter(fake: FakeCMS) -> MockCMSAdapter:
    return MockCMSAdapter(
        BASE, CREDS, ALLOWED, transport=httpx.MockTransport(fake), sleep=lambda _: None
    )


def test_inspection_normalizes_vendor_schema() -> None:
    fake = FakeCMS([(200, SCHEMA_BODY)])
    inspection = _adapter(fake).inspect_target()
    schema = inspection.normalized
    assert inspection.config_revision == 7
    assert schema.kind is SchemaKind.TARGET
    full_name = schema.field("contact.full_name")
    assert full_name is not None and full_name.canonical_ref == "client.full_name"
    custom = schema.field("matter.custom.review_status")
    assert custom is not None
    assert custom.canonical_ref is None
    assert custom.extensions == {"mock-cms": {"is_custom": True}}
    # Unknown vendor types are preserved, not guessed.
    signature = schema.field("matter.custom.signature")
    assert signature is not None and signature.data_type is DataType.UNSUPPORTED
    assert signature.extensions["mock-cms"]["data_type"] == "signature"
    assert schema.supported_transformations == {
        "none": [DataType.ENUM, DataType.STRING],
        "normalize_phone": [DataType.PHONE],
    }
    assert fake.requests[1].headers["Authorization"] == "Bearer tok"


def test_authorization_failure_is_not_retried() -> None:
    fake = FakeCMS([(403, {"error": {}})])
    with pytest.raises(ConnectorError) as exc:
        _adapter(fake).inspect_target()
    assert exc.value.category == "authorization"
    assert len(fake.requests) == 2  # login + one schema call


def test_transient_failures_are_retried_then_succeed() -> None:
    fake = FakeCMS([(503, {}), httpx.ConnectTimeout("slow"), (200, SCHEMA_BODY)])
    assert _adapter(fake).inspect_target().config_revision == 7


def test_transient_failures_are_bounded() -> None:
    fake = FakeCMS([httpx.ReadTimeout("slow")] * MAX_ATTEMPTS)
    with pytest.raises(ConnectorError) as exc:
        _adapter(fake).inspect_target()
    assert exc.value.category == "transient"
    assert exc.value.details[0]["attempts"] == MAX_ATTEMPTS


def test_contract_violation_is_reported() -> None:
    fake = FakeCMS([(200, {"fields": "not-a-list"})])
    with pytest.raises(ConnectorError) as exc:
        _adapter(fake).inspect_target()
    assert exc.value.category == "contract"


def test_disallowed_origin_is_rejected_before_any_request() -> None:
    with pytest.raises(ConnectorError) as exc:
        MockCMSAdapter("http://evil.example", CREDS, ALLOWED)
    assert exc.value.category == "configuration"


def test_credentials_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = env_prefix("firm-z-cms")
    assert prefix == "ONBOARDING_SECRET_FIRM_Z_CMS"
    with pytest.raises(ConnectorError) as exc:
        resolve_credentials("firm-z-cms")
    assert exc.value.category == "configuration"
    monkeypatch.setenv(f"{prefix}_USERNAME", "z.integration")
    monkeypatch.setenv(f"{prefix}_PASSWORD", "pw")
    creds = resolve_credentials("firm-z-cms")
    assert creds.username == "z.integration"
    assert "pw" not in repr(creds)
