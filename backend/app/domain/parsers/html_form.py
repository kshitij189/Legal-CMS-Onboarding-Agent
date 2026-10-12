"""Sample HTML intake form -> NormalizedSchema.

Uses the standard-library HTML parser: markup is only read as data, nothing is executed or fetched.
Labels come from <label for>, a wrapping <label>, a <fieldset> <legend> (radio groups), aria-label,
or placeholder, in that order. Hidden inputs and buttons are not intake fields; they are listed in
the schema's extensions so nothing silently disappears.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from app.domain.parsers.common import humanize
from app.errors import SchemaParseError
from app.schemas.normalized import (
    DataType,
    NormalizedField,
    NormalizedSchema,
    SchemaFormat,
    SchemaKind,
)

_INPUT_TYPES = {
    "text": DataType.STRING,
    "search": DataType.STRING,
    "email": DataType.EMAIL,
    "tel": DataType.PHONE,
    "date": DataType.DATE,
    "number": DataType.NUMBER,
    "checkbox": DataType.BOOLEAN,
    "radio": DataType.ENUM,
    "url": DataType.STRING,
    "file": DataType.UNSUPPORTED,
}
_NON_FIELD_TYPES = {"hidden", "submit", "button", "reset", "image"}
_WHITESPACE = re.compile(r"\s+")


@dataclass
class _Control:
    name: str
    tag: str
    input_type: str
    attrs: dict[str, str]
    legend: str | None
    wrapping_label: str | None = None
    options: list[str] = field(default_factory=list)
    # Visible text of select options whose value is a code (e.g. DIV -> Divorce & Custody).
    option_labels: dict[str, str] = field(default_factory=dict)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.controls: list[_Control] = []
        self.ignored: list[dict[str, str]] = []
        self.labels_for: dict[str, str] = {}
        self._label_stack: list[dict[str, Any]] = []
        self._legend: str | None = None
        self._in_legend = False
        self._legend_text: list[str] = []
        self._in_title = False
        self._select: _Control | None = None
        self._option_value: str | None = None
        self._option_text: list[str] = []
        self._in_option = False

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: v or "" for k, v in attrs_list}
        match tag:
            case "title":
                self._in_title = True
            case "label":
                self._label_stack.append({"for": attrs.get("for"), "text": [], "controls": []})
            case "fieldset":
                self._legend = None
            case "legend":
                self._in_legend, self._legend_text = True, []
            case "input":
                self._add_control(tag, attrs.get("type", "text").lower(), attrs)
            case "textarea":
                self._add_control(tag, "textarea", attrs)
            case "select":
                self._select = self._add_control(tag, "select", attrs)
            case "option" if self._select is not None:
                self._in_option, self._option_text = True, []
                self._option_value = attrs.get("value")

    def handle_endtag(self, tag: str) -> None:
        match tag:
            case "title":
                self._in_title = False
            case "label" if self._label_stack:
                label = self._label_stack.pop()
                text = _clean("".join(label["text"]))
                if label["for"]:
                    self.labels_for[label["for"]] = text
                for control in label["controls"]:
                    control.wrapping_label = text
            case "legend":
                self._in_legend = False
                self._legend = _clean("".join(self._legend_text))
            case "fieldset":
                self._legend = None
            case "option" if self._select is not None and self._in_option:
                self._in_option = False
                text = _clean("".join(self._option_text))
                value = text if self._option_value is None else self._option_value
                if value:  # empty value = placeholder ("Choose…")
                    self._select.options.append(value)
                    if text and text != value:
                        self._select.option_labels[value] = text
            case "select":
                self._select = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = _clean((self.title or "") + data)
        if self._in_legend:
            self._legend_text.append(data)
        if self._in_option:
            self._option_text.append(data)
        for label in self._label_stack:
            label["text"].append(data)

    def _add_control(self, tag: str, input_type: str, attrs: dict[str, str]) -> _Control | None:
        name = attrs.get("name", "")
        if input_type in _NON_FIELD_TYPES or not name:
            self.ignored.append(
                {
                    "tag": tag,
                    "type": input_type,
                    "name": name,
                    "reason": (
                        "not an intake field"
                        if input_type in _NON_FIELD_TYPES
                        else "control has no name"
                    ),
                }
            )
            return None
        if input_type == "radio":
            existing = next((c for c in self.controls if c.name == name), None)
            if existing is not None and existing.input_type == "radio":
                existing.options.append(attrs.get("value", ""))
                if "required" in attrs:
                    existing.attrs["required"] = ""
                return existing
        control = _Control(
            name=name, tag=tag, input_type=input_type, attrs=attrs, legend=self._legend
        )
        if input_type == "radio":
            control.options.append(attrs.get("value", ""))
        self.controls.append(control)
        if self._label_stack:
            self._label_stack[-1]["controls"].append(control)
        return control


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _data_type(control: _Control) -> DataType:
    if control.tag == "textarea":
        return DataType.TEXT
    if control.tag == "select":
        return DataType.UNSUPPORTED if "multiple" in control.attrs else DataType.ENUM
    return _INPUT_TYPES.get(control.input_type, DataType.STRING)


def _label(control: _Control, labels_for: dict[str, str]) -> str:
    # A radio group is described by its legend; a wrapping label names only one option.
    candidates = [
        control.legend if control.input_type == "radio" else None,
        labels_for.get(control.attrs.get("id", "")),
        control.wrapping_label,
        control.attrs.get("aria-label"),
        control.attrs.get("placeholder"),
    ]
    return next((c for c in candidates if c), humanize(control.name))


def parse_html_form(content: str) -> NormalizedSchema:
    parser = _FormParser()
    parser.feed(content)
    parser.close()
    if not parser.controls:
        raise SchemaParseError(
            "HTML contains no named form controls.", [{"code": "no_form_fields"}]
        )
    names = [c.name for c in parser.controls]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise SchemaParseError(
            "HTML form has several controls with the same name.",
            [{"code": "duplicate_field", "field": n} for n in duplicates],
        )

    fields = []
    for control in parser.controls:
        data_type = _data_type(control)
        extensions: dict[str, Any] = {
            k: v for k, v in control.attrs.items() if k.startswith("data-")
        }
        if control.option_labels:
            extensions["option_labels"] = control.option_labels
        if data_type is DataType.UNSUPPORTED:
            extensions["html_control"] = f"{control.tag}[{control.input_type}]"
        max_length = control.attrs.get("maxlength", "")
        fields.append(
            NormalizedField(
                key=control.name,
                label=_label(control, parser.labels_for),
                description=control.attrs.get("title", ""),
                data_type=data_type,
                required="required" in control.attrs,
                options=(control.options or None) if data_type is DataType.ENUM else None,
                max_length=int(max_length) if max_length.isdigit() else None,
                extensions=extensions,
            )
        )
    return NormalizedSchema(
        kind=SchemaKind.SOURCE,
        format=SchemaFormat.HTML_FORM,
        title=parser.title,
        fields=fields,
        extensions={"ignored_controls": parser.ignored} if parser.ignored else {},
    )
