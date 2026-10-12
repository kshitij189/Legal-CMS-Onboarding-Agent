"""The common representation that every source and target schema is normalized into.

Source parsers and target adapters both emit NormalizedSchema, so later phases (mapping proposals,
validation, verification) never deal with vendor- or format-specific structures. Anything a parser
recognises but the common model does not cover is kept in `extensions`, never discarded.
"""

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataType(StrEnum):
    STRING = "string"
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    ENUM = "enum"
    DATE = "date"
    BOOLEAN = "boolean"
    NUMBER = "number"
    # Recognised but not representable as a scalar intake value (arrays, objects, files, ...).
    UNSUPPORTED = "unsupported"


class SchemaKind(StrEnum):
    SOURCE = "source"
    TARGET = "target"


class SchemaFormat(StrEnum):
    JSON_SCHEMA = "json_schema"
    FIELD_EXPORT_CSV = "field_export_csv"
    HTML_FORM = "html_form"
    MOCK_CMS_API = "mock_cms_api"


SOURCE_FORMATS = frozenset(
    {SchemaFormat.JSON_SCHEMA, SchemaFormat.FIELD_EXPORT_CSV, SchemaFormat.HTML_FORM}
)


class NormalizedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    label: str
    description: str = ""
    data_type: DataType
    required: bool = False
    options: list[str] | None = None
    max_length: int | None = None
    # Target fields only: owning CMS entity and the canonical attribute the adapter maps it to.
    # canonical_ref None on a target field means "vendor-specific", described in extensions.
    entity: str | None = None
    canonical_ref: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class NormalizedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SchemaKind
    format: SchemaFormat
    title: str | None = None
    fields: list[NormalizedField]
    # Target schemas only: transformation name -> target data types it can produce.
    supported_transformations: dict[str, list[DataType]] | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        """Content hash used for versioning: identical content => identical fingerprint."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def field(self, key: str) -> NormalizedField | None:
        return next((f for f in self.fields if f.key == key), None)
