"""Canonical legal data model (blueprint §7).

These are integration concepts, not a full case-management schema. Vendor adapters translate their
fields to and from these attributes; anything without a canonical home goes into `extensions`.
"""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.normalized import DataType


class _Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extensions: dict[str, Any] = Field(default_factory=dict)


class ContactMethod(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    SMS = "sms"


class Client(_Entity):
    client_id: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    preferred_contact_method: ContactMethod | None = None


class Matter(_Entity):
    matter_id: str | None = None
    category: str | None = None
    jurisdiction: str | None = None
    opposing_party: str | None = None
    summary: str | None = None
    intake_status: str | None = None


class Intake(_Entity):
    intake_id: str | None = None
    source_form: str | None = None
    collected_values: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    validation_status: str | None = None
    review_status: str | None = None


class Task(_Entity):
    task_id: str | None = None
    task_type: str | None = None
    assigned_to: str | None = None
    due_date: date | None = None
    status: str | None = None


class DocumentReference(_Entity):
    document_id: str | None = None
    document_type: str | None = None
    storage_reference: str | None = None
    intake_id: str | None = None
    matter_id: str | None = None


# Mappable canonical attributes and the data type each one carries. Identifiers and structural
# attributes (ids, collected_values, missing_fields, ...) are system-managed and not mappable.
CANONICAL_ATTRIBUTES: dict[str, DataType] = {
    "client.full_name": DataType.STRING,
    "client.email": DataType.EMAIL,
    "client.phone_number": DataType.PHONE,
    "client.preferred_contact_method": DataType.ENUM,
    "matter.category": DataType.ENUM,
    "matter.jurisdiction": DataType.STRING,
    "matter.opposing_party": DataType.STRING,
    "matter.summary": DataType.TEXT,
    "matter.intake_status": DataType.ENUM,
    "intake.review_status": DataType.ENUM,
    "task.task_type": DataType.STRING,
    "task.due_date": DataType.DATE,
    "document.document_type": DataType.STRING,
}
