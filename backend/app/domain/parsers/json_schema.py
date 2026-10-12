"""JSON Schema (draft 2020-12 or compatible) intake definitions -> NormalizedSchema.

Nested object properties are flattened to dotted keys ("client.phone"). Keywords the common model
does not cover are kept in the field's `extensions`.
"""

import json
from typing import Any

from jsonschema import SchemaError
from jsonschema.validators import validator_for

from app.domain.parsers.common import humanize
from app.errors import SchemaParseError
from app.schemas.normalized import (
    DataType,
    NormalizedField,
    NormalizedSchema,
    SchemaFormat,
    SchemaKind,
)

# Keywords consumed into NormalizedField; everything else is preserved as an extension.
_CONSUMED = {
    "type",
    "format",
    "enum",
    "title",
    "description",
    "maxLength",
    "properties",
    "required",
}
_SCHEMA_CONSUMED = {"$schema", "type", "title", "properties", "required"}
# Text longer than this is "text" rather than "string" (mirrors common CMS short-text limits).
_SHORT_TEXT_MAX = 255
_FORMATS = {"email": DataType.EMAIL, "date": DataType.DATE, "phone": DataType.PHONE}


def parse_json_schema(content: str) -> NormalizedSchema:
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SchemaParseError(
            "Source is not valid JSON.",
            [{"code": "invalid_json", "line": exc.lineno, "message": exc.msg}],
        ) from None
    if not isinstance(document, dict):
        raise SchemaParseError("JSON Schema must be an object.", [{"code": "not_an_object"}])
    try:
        validator_for(document).check_schema(document)
    except SchemaError as exc:
        raise SchemaParseError(
            "Document is not a valid JSON Schema.",
            [
                {
                    "code": "invalid_json_schema",
                    "message": exc.message,
                    "path": "/".join(str(p) for p in exc.path),
                }
            ],
        ) from None
    if not isinstance(document.get("properties"), dict) or not document["properties"]:
        raise SchemaParseError(
            "JSON Schema has no properties to treat as intake fields.",
            [{"code": "no_properties"}],
        )

    fields: list[NormalizedField] = []
    _collect(document, prefix="", parent_required=True, out=fields)
    extensions = {k: v for k, v in document.items() if k not in _SCHEMA_CONSUMED}
    return NormalizedSchema(
        kind=SchemaKind.SOURCE,
        format=SchemaFormat.JSON_SCHEMA,
        title=document.get("title"),
        fields=fields,
        extensions={"json_schema": extensions} if extensions else {},
    )


def _collect(
    node: dict[str, Any], prefix: str, parent_required: bool, out: list[NormalizedField]
) -> None:
    required = set(node.get("required", []))
    for name, prop in node["properties"].items():
        key = f"{prefix}{name}"
        is_required = parent_required and name in required
        if not isinstance(prop, dict):  # boolean subschemas carry no field information
            out.append(_field(key, {}, DataType.UNSUPPORTED, is_required))
            continue
        if _types(prop) == {"object"} and isinstance(prop.get("properties"), dict):
            _collect(prop, prefix=f"{key}.", parent_required=is_required, out=out)
            continue
        out.append(_field(key, prop, _data_type(prop), is_required))


def _types(prop: dict[str, Any]) -> set[str]:
    declared = prop.get("type")
    types = set(declared) if isinstance(declared, list) else {declared} if declared else set()
    return types - {"null"}


def _data_type(prop: dict[str, Any]) -> DataType:
    types = _types(prop)
    if "enum" in prop and types <= {"string"}:
        return DataType.ENUM
    if len(types) != 1:
        return DataType.UNSUPPORTED
    match types.pop():
        case "string":
            if prop.get("format") in _FORMATS:
                return _FORMATS[prop["format"]]
            max_length = prop.get("maxLength")
            if isinstance(max_length, int) and max_length > _SHORT_TEXT_MAX:
                return DataType.TEXT
            return DataType.STRING
        case "integer" | "number":
            return DataType.NUMBER
        case "boolean":
            return DataType.BOOLEAN
        case _:
            return DataType.UNSUPPORTED


def _field(key: str, prop: dict[str, Any], data_type: DataType, required: bool) -> NormalizedField:
    extensions = {k: v for k, v in prop.items() if k not in _CONSUMED}
    if prop.get("format") and prop["format"] not in _FORMATS:
        extensions["format"] = prop["format"]
    if data_type is DataType.UNSUPPORTED and "type" in prop:
        extensions["type"] = prop["type"]
    max_length = prop.get("maxLength")
    return NormalizedField(
        key=key,
        label=str(prop.get("title") or humanize(key)),
        description=str(prop.get("description", "")),
        data_type=data_type,
        required=required,
        options=[str(v) for v in prop["enum"]] if data_type is DataType.ENUM else None,
        max_length=max_length if isinstance(max_length, int) else None,
        extensions=extensions,
    )
