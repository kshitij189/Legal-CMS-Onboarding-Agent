"""Adapter for the local mock legal CMS (`mock-cms/`), over its documented JSON API only.

Translates the vendor schema into NormalizedSchema and maps vendor fields onto canonical attributes.
Vendor fields without a canonical home (custom fields, callback preference) keep canonical_ref None
and carry their vendor details in extensions.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import ConnectorError
from app.integrations.base import TargetInspection, ensure_allowed_origin
from app.integrations.secrets import Credentials
from app.schemas.execution import (
    CMSConfiguration,
    CMSField,
    CustomFieldSpec,
    MappingSpec,
    WriteOutcome,
)
from app.schemas.normalized import (
    DataType,
    NormalizedField,
    NormalizedSchema,
    SchemaFormat,
    SchemaKind,
)

VENDOR = "mock-cms"
VENDOR_TO_CANONICAL: dict[str, str] = {
    "contact.full_name": "client.full_name",
    "contact.email": "client.email",
    "contact.phone_number": "client.phone_number",
    "contact.preferred_contact_method": "client.preferred_contact_method",
    "matter.practice_area": "matter.category",
    "matter.jurisdiction": "matter.jurisdiction",
    "matter.opposing_party": "matter.opposing_party",
    "matter.summary": "matter.summary",
    "matter.intake_status": "matter.intake_status",
}
MAX_ATTEMPTS = 3


# --- vendor response contract (anything else is a contract violation, not something to guess) ---


class _VendorTenant(BaseModel):
    slug: str
    name: str


class _VendorField(BaseModel):
    key: str
    entity: str
    label: str
    description: str
    data_type: str
    required: bool
    enabled: bool
    is_custom: bool
    options: list[str] | None


class _VendorTransformation(BaseModel):
    name: str
    applicable_types: list[str]


class _VendorSchema(BaseModel):
    tenant: _VendorTenant
    config_revision: int
    fields: list[_VendorField]
    transformations: list[_VendorTransformation]


class MockCMSAdapter:
    def __init__(
        self,
        base_url: str,
        credentials: Credentials,
        allowed_origins: frozenset[str],
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        ensure_allowed_origin(base_url, allowed_origins)
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleep

    def inspect_target(self) -> TargetInspection:
        with httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            token = self._login(client)
            response = self._request(
                client, "GET", "/api/v1/schema", headers={"Authorization": f"Bearer {token}"}
            )
        try:
            vendor = _VendorSchema.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ConnectorError(
                "contract",
                "Mock CMS schema response did not match the expected contract.",
                [{"code": "unexpected_response", "message": str(exc)[:500]}],
            ) from None
        return TargetInspection(
            normalized=_normalize(vendor), config_revision=vendor.config_revision
        )

    # --- configuration read-back and writes (Phase 4) -------------------------------------------

    channel = "api"

    def __enter__(self) -> "MockCMSAdapter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        )

    def read_configuration(self) -> CMSConfiguration:
        with self._client() as client:
            token = self._login(client)
            response = self._request(
                client, "GET", "/api/v1/config", headers={"Authorization": f"Bearer {token}"}
            )
        try:
            body = response.json()
            return CMSConfiguration(
                config_revision=body["config_revision"],
                fields=[CMSField.model_validate(f) for f in body["fields"]],
                mappings=[
                    MappingSpec(
                        source_key=m["source_key"],
                        target_field=m["target_field"],
                        transformation=m["transformation"],
                        value_map=m["value_map"] or None,
                    )
                    for m in body["intake_mappings"]
                ],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ConnectorError(
                "contract",
                "Mock CMS config response did not match the expected contract.",
                [{"code": "unexpected_response", "message": str(exc)[:300]}],
            ) from None

    def create_custom_field(self, spec: CustomFieldSpec) -> WriteOutcome:
        body = {
            "entity": spec.entity,
            "name": spec.name,
            "label": spec.label,
            "data_type": spec.data_type,
            "required": spec.required,
            "options": spec.options,
        }
        response = self._write("POST", "/api/v1/config/custom-fields", body)
        return WriteOutcome(
            changed=True,
            evidence={
                "channel": "api",
                "status": response.status_code,
                "config_revision": response.json().get("config_revision"),
            },
        )

    def upsert_mapping(self, spec: MappingSpec) -> WriteOutcome:
        body = {
            "target_field": spec.target_field,
            "transformation": spec.transformation,
            "value_map": spec.value_map,
        }
        response = self._write("PUT", f"/api/v1/config/intake-mappings/{spec.source_key}", body)
        payload = response.json()
        return WriteOutcome(
            changed=payload.get("changed"),
            evidence={
                "channel": "api",
                "status": response.status_code,
                "config_revision": payload.get("config_revision"),
            },
        )

    def _write(self, method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        """Single attempt. A lost response is 'uncertain': the caller must read state before any
        retry, because the write may have been applied."""
        with self._client() as client:
            token = self._login(client)
            try:
                response = client.request(
                    method, path, json=body, headers={"Authorization": f"Bearer {token}"}
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ConnectorError(
                    "uncertain",
                    f"No response to the write ({type(exc).__name__}).",
                    [{"code": "no_response"}],
                ) from None
        if response.status_code < 400:
            return response
        error = _error_details(response)
        if response.status_code in (401, 403):
            raise ConnectorError("authorization", "The CMS refused the write.", error)
        if response.status_code == 409:
            raise ConnectorError("conflict", "The CMS reported a conflict.", error)
        if response.status_code == 422:
            raise ConnectorError("validation", "The CMS rejected the values.", error)
        if response.status_code >= 500:
            # The server may have applied the change before failing: treat as uncertain.
            raise ConnectorError("uncertain", "The CMS failed while writing.", error)
        raise ConnectorError("contract", "Unexpected response to a write.", error)

    def _login(self, client: httpx.Client) -> str:
        response = self._request(
            client,
            "POST",
            "/api/v1/auth/token",
            json={"username": self._credentials.username, "password": self._credentials.password},
        )
        token = response.json().get("access_token")
        if not isinstance(token, str):
            raise ConnectorError("contract", "Token response had no access_token.")
        return token

    def _request(
        self, client: httpx.Client, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """Read-only calls: bounded retry on transient failures, classify everything else."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                failure = ConnectorError(
                    "transient",
                    f"Could not reach the mock CMS: {type(exc).__name__}.",
                    [{"code": "unreachable", "attempts": attempt}],
                )
            else:
                if response.status_code < 400:
                    return response
                failure = _classify(response, attempt)
                if failure.category != "transient":
                    raise failure
            if attempt < MAX_ATTEMPTS:
                self._sleep(0.2 * 2 ** (attempt - 1))
        raise failure


def _error_details(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return [{"code": f"http_{response.status_code}"}]
    return [
        {
            "code": f"http_{response.status_code}",
            "cms_code": error.get("code"),
            "cms_message": error.get("message"),
            "cms_details": error.get("details", []),
        }
    ]


def _classify(response: httpx.Response, attempt: int) -> ConnectorError:
    detail = {"code": f"http_{response.status_code}", "attempts": attempt}
    if response.status_code in (401, 403):
        return ConnectorError(
            "authorization",
            "The mock CMS rejected the connector's credentials or permissions.",
            [detail],
        )
    if response.status_code >= 500 or response.status_code == 429:
        return ConnectorError("transient", "The mock CMS is temporarily unavailable.", [detail])
    return ConnectorError("contract", "Unexpected response from the mock CMS.", [detail])


def _normalize(vendor: _VendorSchema) -> NormalizedSchema:
    known_types = {t.value for t in DataType}
    fields = []
    for f in vendor.fields:
        if not f.enabled:  # the vendor already hides these; skip defensively
            continue
        extensions: dict[str, Any] = {VENDOR: {"is_custom": f.is_custom}}
        if f.data_type not in known_types or f.data_type == DataType.UNSUPPORTED:
            extensions[VENDOR]["data_type"] = f.data_type
        fields.append(
            NormalizedField(
                key=f.key,
                label=f.label,
                description=f.description,
                data_type=DataType(f.data_type)
                if f.data_type in known_types
                else DataType.UNSUPPORTED,
                required=f.required,
                options=f.options,
                entity=f.entity,
                canonical_ref=VENDOR_TO_CANONICAL.get(f.key),
                extensions=extensions,
            )
        )
    return NormalizedSchema(
        kind=SchemaKind.TARGET,
        format=SchemaFormat.MOCK_CMS_API,
        title=f"Mock Legal CMS — {vendor.tenant.name}",
        fields=fields,
        supported_transformations={
            t.name: sorted(DataType(x) for x in t.applicable_types if x in known_types)
            for t in vendor.transformations
        },
        extensions={"vendor": VENDOR, "tenant": vendor.tenant.slug},
    )
