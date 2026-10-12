"""Static vocabulary of the mock CMS: entities, data types, transformations and built-in fields.

Every new firm workspace receives a copy of BUILTIN_FIELDS; the copy is then configurable per firm.
"""

from dataclasses import dataclass
from enum import StrEnum


class Entity(StrEnum):
    CONTACT = "contact"
    MATTER = "matter"


class DataType(StrEnum):
    STRING = "string"
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    ENUM = "enum"
    DATE = "date"
    BOOLEAN = "boolean"
    NUMBER = "number"


class Transformation(StrEnum):
    NONE = "none"
    TRIM = "trim"
    LOWERCASE = "lowercase"
    NORMALIZE_PHONE = "normalize_phone"


TRANSFORMATION_TARGET_TYPES: dict[Transformation, frozenset[DataType]] = {
    Transformation.NONE: frozenset(DataType),
    Transformation.TRIM: frozenset(
        {DataType.STRING, DataType.TEXT, DataType.EMAIL, DataType.PHONE, DataType.ENUM}
    ),
    Transformation.LOWERCASE: frozenset(
        {DataType.STRING, DataType.TEXT, DataType.EMAIL, DataType.ENUM}
    ),
    Transformation.NORMALIZE_PHONE: frozenset({DataType.PHONE}),
}


@dataclass(frozen=True)
class BuiltinField:
    key: str
    label: str
    description: str
    data_type: DataType
    required: bool = False
    options: tuple[str, ...] | None = None

    @property
    def entity(self) -> Entity:
        return Entity(self.key.split(".", 1)[0])


BUILTIN_FIELDS: tuple[BuiltinField, ...] = (
    BuiltinField(
        "contact.full_name",
        "Full name",
        "Prospective client's full legal name.",
        DataType.STRING,
        required=True,
    ),
    BuiltinField("contact.email", "Email", "Primary email address.", DataType.EMAIL),
    BuiltinField(
        "contact.phone_number",
        "Phone number",
        "Primary phone number, digits with optional leading +.",
        DataType.PHONE,
    ),
    BuiltinField(
        "contact.preferred_contact_method",
        "Preferred contact method",
        "How the client prefers to be contacted.",
        DataType.ENUM,
        options=("email", "phone", "sms"),
    ),
    BuiltinField(
        "contact.callback_preference",
        "Callback preference",
        "Preferred time window for a callback.",
        DataType.STRING,
    ),
    BuiltinField(
        "matter.practice_area",
        "Practice area",
        "Category of the legal matter.",
        DataType.ENUM,
        required=True,
        options=(
            "family_law",
            "personal_injury",
            "employment",
            "immigration",
            "criminal_defense",
            "estate_planning",
            "real_estate",
            "business",
        ),
    ),
    BuiltinField(
        "matter.jurisdiction",
        "Jurisdiction",
        "Court or region where the matter arises.",
        DataType.STRING,
    ),
    BuiltinField(
        "matter.opposing_party",
        "Opposing party",
        "Name of the opposing party, if any.",
        DataType.STRING,
    ),
    BuiltinField("matter.summary", "Matter summary", "Free-text description.", DataType.TEXT),
    BuiltinField(
        "matter.intake_status",
        "Intake status",
        "Workflow status of the intake.",
        DataType.ENUM,
        options=("new", "under_review", "accepted", "declined"),
    ),
)
