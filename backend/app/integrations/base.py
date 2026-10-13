"""Common interface every target-CMS adapter implements.

Reads (schema inspection, configuration read-back) always use the vendor's documented API. Writes
go through a ConfigurationWriter for the run's channel (API or browser) and only ever receive
actions derived from an approved mapping version.
"""

from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.errors import ConnectorError
from app.schemas.execution import CMSConfiguration, CustomFieldSpec, MappingSpec, WriteOutcome
from app.schemas.normalized import NormalizedSchema


class TargetInspection(BaseModel):
    normalized: NormalizedSchema
    # The CMS's configuration revision at inspection time, when the vendor exposes one.
    config_revision: int | None


class CMSAdapter(Protocol):
    def inspect_target(self) -> TargetInspection: ...


class ConfigurationReader(Protocol):
    """Reads the CMS's stored configuration (always through the documented API)."""

    def read_configuration(self) -> CMSConfiguration: ...


class ConfigurationWriter(Protocol):
    """Applies typed configuration actions. Used as a context manager (browser sessions)."""

    def __enter__(self) -> "ConfigurationWriter": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

    def create_custom_field(self, spec: CustomFieldSpec) -> WriteOutcome: ...

    def upsert_mapping(self, spec: MappingSpec) -> WriteOutcome: ...


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def ensure_allowed_origin(base_url: str, allowed: frozenset[str]) -> None:
    if origin_of(base_url) not in {o.lower() for o in allowed}:
        raise ConnectorError(
            "configuration",
            "Connector base URL is not on the allowed CMS origin list.",
            [{"code": "origin_not_allowed", "origin": origin_of(base_url)}],
        )
