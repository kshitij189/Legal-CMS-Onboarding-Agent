"""Field-definition export (CSV from a legacy intake system) -> NormalizedSchema.

Required columns: field_name, label, type, required.
Optional: description, options (pipe-separated).
Any other column is preserved per field in `extensions["export"]`.
"""

import csv
import io
from typing import Any

from app.errors import SchemaParseError
from app.schemas.normalized import (
    DataType,
    NormalizedField,
    NormalizedSchema,
    SchemaFormat,
    SchemaKind,
)

_REQUIRED_COLUMNS = ("field_name", "label", "type", "required")
_KNOWN_COLUMNS = {*_REQUIRED_COLUMNS, "description", "options"}
_TYPES = {
    "text": DataType.STRING,
    "short text": DataType.STRING,
    "long text": DataType.TEXT,
    "textarea": DataType.TEXT,
    "email": DataType.EMAIL,
    "phone": DataType.PHONE,
    "picklist": DataType.ENUM,
    "dropdown": DataType.ENUM,
    "date": DataType.DATE,
    "checkbox": DataType.BOOLEAN,
    "number": DataType.NUMBER,
    "currency": DataType.NUMBER,
}
_TRUE = {"y", "yes", "true", "1"}
_FALSE = {"n", "no", "false", "0", ""}


def parse_field_export(content: str) -> NormalizedSchema:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    columns = [c.strip().lower() for c in reader.fieldnames or []]
    missing = [c for c in _REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise SchemaParseError(
            "Field export is missing required columns.",
            [{"code": "missing_column", "column": c} for c in missing],
        )

    fields: list[NormalizedField] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_number, raw_row in enumerate(reader, start=2):  # header is row 1
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        name = row["field_name"]
        if not name:
            errors.append({"code": "missing_field_name", "row": row_number})
            continue
        if name in seen:
            errors.append({"code": "duplicate_field", "row": row_number, "field": name})
            continue
        seen.add(name)

        required = row["required"].lower()
        if required not in _TRUE | _FALSE:
            errors.append(
                {
                    "code": "invalid_required_flag",
                    "row": row_number,
                    "field": name,
                    "value": row["required"],
                }
            )
            continue
        source_type = row["type"]
        data_type = _TYPES.get(source_type.lower(), DataType.UNSUPPORTED)
        options = [o.strip() for o in row.get("options", "").split("|") if o.strip()] or None
        if data_type is DataType.ENUM and not options:
            errors.append({"code": "options_required", "row": row_number, "field": name})
            continue

        extensions: dict[str, Any] = {k: v for k, v in row.items() if k not in _KNOWN_COLUMNS and v}
        if data_type is DataType.UNSUPPORTED:
            extensions["source_type"] = source_type
        fields.append(
            NormalizedField(
                key=name,
                label=row["label"] or name,
                description=row.get("description", ""),
                data_type=data_type,
                required=required in _TRUE,
                options=options if data_type is DataType.ENUM else None,
                extensions={"export": extensions} if extensions else {},
            )
        )

    if errors:
        raise SchemaParseError("Field export contains invalid rows.", errors)
    if not fields:
        raise SchemaParseError("Field export contains no fields.", [{"code": "no_fields"}])
    return NormalizedSchema(
        kind=SchemaKind.SOURCE, format=SchemaFormat.FIELD_EXPORT_CSV, fields=fields
    )
