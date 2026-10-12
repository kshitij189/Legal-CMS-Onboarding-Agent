"""Common interface every target-CMS adapter implements.

Phase 2 needs only read-only schema inspection; write and read-back operations are added with the
execution phase, against approved plans only.
"""

from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.errors import ConnectorError
from app.schemas.normalized import NormalizedSchema


class TargetInspection(BaseModel):
    normalized: NormalizedSchema
    # The CMS's configuration revision at inspection time, when the vendor exposes one.
    config_revision: int | None


class CMSAdapter(Protocol):
    def inspect_target(self) -> TargetInspection: ...


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
