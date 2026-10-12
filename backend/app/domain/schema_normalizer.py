"""Entry point for turning an uploaded source document into a NormalizedSchema."""

from collections.abc import Callable

from app.domain.parsers.field_export import parse_field_export
from app.domain.parsers.html_form import parse_html_form
from app.domain.parsers.json_schema import parse_json_schema
from app.errors import SchemaParseError
from app.schemas.normalized import NormalizedSchema, SchemaFormat

MAX_SOURCE_BYTES = 1_000_000

_PARSERS: dict[SchemaFormat, Callable[[str], NormalizedSchema]] = {
    SchemaFormat.JSON_SCHEMA: parse_json_schema,
    SchemaFormat.FIELD_EXPORT_CSV: parse_field_export,
    SchemaFormat.HTML_FORM: parse_html_form,
}


def normalize_source(source_format: SchemaFormat, content: str) -> NormalizedSchema:
    """Parse untrusted source content. The content is treated strictly as data."""
    parser = _PARSERS.get(source_format)
    if parser is None:
        raise SchemaParseError(
            f"'{source_format}' is not a source schema format.",
            [{"code": "unsupported_format", "format": source_format.value}],
        )
    if len(content.encode()) > MAX_SOURCE_BYTES:
        raise SchemaParseError(
            "Source document is too large.",
            [{"code": "too_large", "max_bytes": MAX_SOURCE_BYTES}],
        )
    return parser(content)
