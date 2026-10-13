"""Mapping proposals -> human review -> approved, immutable mapping version (blueprint §4–§6).

Nothing here talks to a CMS. A mapping version is only a plan; executing it is a later phase and
will require the version id.
"""

import hashlib
import json
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.llm_client import LLMClient, LLMError
from app.agent.mapping_agent import find_untrusted_fields, propose_mappings
from app.agent.prompts import PROMPT_VERSION
from app.domain import audit
from app.domain.mapping_validator import validate_proposals
from app.domain.project_service import latest_schema
from app.errors import Conflict, LLMUnavailable, NotFound, ValidationFailed
from app.models import (
    MappingProposal,
    MappingVersion,
    OnboardingProject,
    ProposalGeneration,
    SchemaDefinition,
    User,
    utcnow,
)
from app.schemas.mapping import (
    Decision,
    MappingValidation,
    ProposedMapping,
    ValidationIssue,
    ValidationStatus,
)
from app.schemas.normalized import NormalizedSchema, SchemaKind

ReviewAction = Literal["approve", "reject", "request_clarification", "edit"]
EXECUTABLE_DECISIONS = {Decision.MAP, Decision.REQUIRES_CUSTOM_FIELD}


def _current_schemas(
    db: Session, project: OnboardingProject
) -> tuple[SchemaDefinition, SchemaDefinition]:
    source = latest_schema(db, project, SchemaKind.SOURCE)
    target = latest_schema(db, project, SchemaKind.TARGET)
    if source is None or target is None:
        raise Conflict(
            "Register a source schema and inspect the target before proposing mappings.",
            [{"code": "schemas_missing"}],
        )
    return source, target


def _proposal_row(
    generation: ProposalGeneration,
    mapping: ProposedMapping,
    result: MappingValidation,
    origin: str,
    supersedes_id: int | None = None,
) -> MappingProposal:
    return MappingProposal(
        firm_id=generation.firm_id,
        project_id=generation.project_id,
        generation_id=generation.id,
        origin=origin,
        supersedes_id=supersedes_id,
        source_field=mapping.source_field,
        decision=mapping.decision.value,
        content=mapping.model_dump(mode="json"),
        validation_status=result.status.value,
        validation_issues=[i.model_dump(mode="json") for i in result.issues],
        review_status="pending",
    )


# --- generation ---------------------------------------------------------------------------------


def generate_proposals(
    db: Session,
    user: User,
    project: OnboardingProject,
    llm: LLMClient,
    idempotency_key: str | None,
) -> tuple[ProposalGeneration, bool]:
    if idempotency_key:
        existing = db.scalar(
            select(ProposalGeneration).where(
                ProposalGeneration.project_id == project.id,
                ProposalGeneration.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False

    source_def, target_def = _current_schemas(db, project)
    source, target = source_def.to_normalized(), target_def.to_normalized()
    generation = ProposalGeneration(
        firm_id=project.firm_id,
        project_id=project.id,
        source_schema_id=source_def.id,
        target_schema_id=target_def.id,
        prompt_version=PROMPT_VERSION,
        idempotency_key=idempotency_key,
        created_by_id=user.id,
        untrusted_source_fields=sorted(find_untrusted_fields(source)),
    )
    try:
        proposal = propose_mappings(llm, source, target)
    except LLMError as exc:
        generation.status = "failed"
        generation.provider = exc.provider
        generation.error_category = exc.category
        generation.error_message = exc.message
        db.add(generation)
        db.flush()
        audit.record(
            db,
            firm_id=project.firm_id,
            project_id=project.id,
            actor=user,
            action="proposal.generation_failed",
            entity_type="proposal_generation",
            entity_id=generation.id,
            details={"provider": exc.provider, "category": exc.category},
        )
        db.commit()
        raise LLMUnavailable(
            "The LLM did not produce a usable proposal; nothing was stored as a proposal.",
            [{"category": exc.category, "provider": exc.provider, "generation_id": generation.id}],
        ) from None

    call = proposal.call
    generation.status = "succeeded"
    generation.provider = call.provider
    generation.model = call.model
    generation.input_tokens = call.input_tokens
    generation.output_tokens = call.output_tokens
    generation.latency_ms = call.latency_ms
    generation.fallbacks = [list(f) for f in call.fallbacks]
    mappings = list(proposal.args.mappings)
    report = validate_proposals(mappings, source, target, set(proposal.untrusted_fields))
    generation.coverage_gaps = [g.model_dump(mode="json") for g in report.coverage_gaps]
    generation.unproposed_source_fields = report.unproposed_source_fields
    db.add(generation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("A concurrent request used the same idempotency key.") from None

    for mapping, result in zip(mappings, report.mappings, strict=True):
        db.add(_proposal_row(generation, mapping, result, "llm"))
    # A skipped field still needs a human decision, so it gets an explicit placeholder.
    for key in report.unproposed_source_fields:
        placeholder = ProposedMapping(
            source_field=key,
            decision=Decision.NO_TARGET,
            confidence=0,
            rationale="The model returned no proposal for this field.",
        )
        result = MappingValidation(
            source_field=key,
            status=ValidationStatus.NEEDS_REVIEW,
            issues=[
                ValidationIssue(
                    code="not_proposed",
                    message="No proposal from the model; decide manually.",
                    severity=ValidationStatus.NEEDS_REVIEW,
                )
            ],
        )
        db.add(_proposal_row(generation, placeholder, result, "system"))

    counts = {s.value: sum(r.status is s for r in report.mappings) for s in ValidationStatus}
    audit.record(
        db,
        firm_id=project.firm_id,
        project_id=project.id,
        actor=user,
        action="proposal.generated",
        entity_type="proposal_generation",
        entity_id=generation.id,
        details={
            "provider": call.provider,
            "model": call.model,
            "prompt_version": PROMPT_VERSION,
            "validation_counts": counts,
            "coverage_gaps": len(report.coverage_gaps),
            "unproposed": len(report.unproposed_source_fields),
            "fallbacks": [list(f) for f in call.fallbacks],
        },
    )
    db.commit()
    db.refresh(generation)
    return generation, True


def get_generation(
    db: Session, project: OnboardingProject, generation_id: int | None
) -> ProposalGeneration:
    query = select(ProposalGeneration).where(ProposalGeneration.project_id == project.id)
    if generation_id is not None:
        query = query.where(ProposalGeneration.id == generation_id)
    else:
        query = query.where(ProposalGeneration.status == "succeeded")
    generation = db.scalar(query.order_by(ProposalGeneration.id.desc()).limit(1))
    if generation is None:
        raise NotFound("No mapping proposals found for this project.")
    return generation


# --- review -------------------------------------------------------------------------------------


def _generation_schemas(
    db: Session, generation: ProposalGeneration
) -> tuple[NormalizedSchema, NormalizedSchema]:
    source = db.get(SchemaDefinition, generation.source_schema_id)
    target = db.get(SchemaDefinition, generation.target_schema_id)
    assert source is not None and target is not None  # noqa: S101 - FK guarantees
    return source.to_normalized(), target.to_normalized()


def review_proposal(
    db: Session,
    user: User,
    project: OnboardingProject,
    proposal_id: int,
    action: ReviewAction,
    comment: str | None,
    edited: ProposedMapping | None,
) -> MappingProposal:
    proposal = db.scalar(
        select(MappingProposal).where(
            MappingProposal.id == proposal_id, MappingProposal.project_id == project.id
        )
    )
    if proposal is None:
        raise NotFound(f"Mapping proposal {proposal_id} not found.")
    if proposal.review_status == "edited":
        raise Conflict(
            "This proposal was replaced by an edit; review the replacement instead.",
            [{"code": "superseded"}],
        )
    previous = proposal.review_status
    result_row = proposal

    if action == "approve":
        if proposal.validation_status == ValidationStatus.INVALID:
            raise ValidationFailed(
                "An invalid proposal cannot be approved; edit or reject it.",
                [{"code": "cannot_approve_invalid", "issues": proposal.validation_issues}],
            )
        if proposal.decision == Decision.AMBIGUOUS:
            raise ValidationFailed(
                "Resolve an ambiguous proposal by editing it into a concrete decision.",
                [{"code": "ambiguous_requires_edit"}],
            )
        if proposal.validation_status == ValidationStatus.NEEDS_REVIEW and not comment:
            raise ValidationFailed(
                "Approving a proposal flagged for review requires a comment.",
                [{"code": "comment_required"}],
            )
        proposal.review_status = "approved"
    elif action == "reject":
        proposal.review_status = "rejected"
    elif action == "request_clarification":
        if not comment:
            raise ValidationFailed("Say what needs clarifying.", [{"code": "comment_required"}])
        proposal.review_status = "needs_clarification"
    else:  # edit
        if edited is None:
            raise ValidationFailed(
                "An edit needs the replacement mapping.", [{"code": "mapping_required"}]
            )
        if edited.source_field != proposal.source_field:
            raise ValidationFailed(
                "An edit must keep the same source field.", [{"code": "source_field_changed"}]
            )
        generation = db.get(ProposalGeneration, proposal.generation_id)
        assert generation is not None  # noqa: S101
        source, target = _generation_schemas(db, generation)
        report = validate_proposals(
            [edited], source, target, set(generation.untrusted_source_fields)
        )
        proposal.review_status = "edited"
        result_row = _proposal_row(
            generation, edited, report.mappings[0], "reviewer", supersedes_id=proposal.id
        )
        db.add(result_row)

    proposal.review_comment = comment
    proposal.reviewed_by_id = user.id
    proposal.reviewed_at = utcnow()
    db.flush()
    audit.record(
        db,
        firm_id=project.firm_id,
        project_id=project.id,
        actor=user,
        action="mapping.reviewed",
        entity_type="mapping_proposal",
        entity_id=proposal.id,
        details={
            "action": action,
            "previous_status": previous,
            "new_status": proposal.review_status,
            "source_field": proposal.source_field,
            "replacement_id": (result_row.id if result_row is not proposal else None),
        },
    )
    db.commit()
    return result_row


# --- approval -----------------------------------------------------------------------------------


def approve_mappings(
    db: Session, user: User, project: OnboardingProject, generation_id: int
) -> tuple[MappingVersion, bool]:
    generation = db.scalar(
        select(ProposalGeneration).where(
            ProposalGeneration.id == generation_id, ProposalGeneration.project_id == project.id
        )
    )
    if generation is None or generation.status != "succeeded":
        raise NotFound(f"Successful proposal generation {generation_id} not found.")

    source_def, target_def = _current_schemas(db, project)
    if (source_def.id, target_def.id) != (generation.source_schema_id, generation.target_schema_id):
        raise Conflict(
            "The source or target schema changed after these proposals were generated; "
            "generate and review new proposals.",
            [
                {
                    "code": "schema_changed",
                    "proposals_for": [generation.source_schema_id, generation.target_schema_id],
                    "current": [source_def.id, target_def.id],
                }
            ],
        )

    current = [p for p in generation.proposals if p.review_status != "edited"]
    open_items = [
        {"proposal_id": p.id, "source_field": p.source_field, "review_status": p.review_status}
        for p in current
        if p.review_status in ("pending", "needs_clarification")
    ]
    if open_items:
        raise Conflict(
            "Every proposal must be approved or rejected before approval.",
            [{"code": "unreviewed_proposals", "proposals": open_items}],
        )
    approved = [p for p in current if p.review_status == "approved"]
    if not any(Decision(p.decision) in EXECUTABLE_DECISIONS for p in approved):
        raise ValidationFailed(
            "Nothing to approve: no approved mapping changes the CMS.", [{"code": "empty_plan"}]
        )

    # Re-validate the final plan as a whole (e.g. two approved fields onto one target).
    source, target = source_def.to_normalized(), target_def.to_normalized()
    mappings = [ProposedMapping.model_validate(p.content) for p in approved]
    report = validate_proposals(mappings, source, target, set(generation.untrusted_source_fields))
    blocking = [
        {"source_field": r.source_field, "issues": [i.code for i in r.issues]}
        for r in report.mappings
        if r.status is ValidationStatus.INVALID
        or any(i.code == "duplicate_target" for i in r.issues)
    ]
    if blocking:
        raise ValidationFailed(
            "The approved set is not a valid plan.", [{"code": "invalid_plan", "items": blocking}]
        )

    items = [
        {"proposal_id": p.id, "mapping": p.content}
        for p in approved
        if Decision(p.decision) in EXECUTABLE_DECISIONS
    ]
    excluded = [
        {
            "proposal_id": p.id,
            "source_field": p.source_field,
            "decision": p.decision,
            "review_status": p.review_status,
        }
        for p in current
        if p.review_status == "rejected" or Decision(p.decision) not in EXECUTABLE_DECISIONS
    ]
    gaps = [g.model_dump(mode="json") for g in report.coverage_gaps]
    fingerprint = hashlib.sha256(
        json.dumps(
            {"source": source_def.id, "target": target_def.id, "items": items},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    existing = db.scalar(
        select(MappingVersion).where(
            MappingVersion.generation_id == generation.id,
            MappingVersion.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return existing, False

    next_version = (
        db.scalar(
            select(func.max(MappingVersion.version)).where(MappingVersion.project_id == project.id)
        )
        or 0
    ) + 1
    db.execute(
        update(MappingVersion)
        .where(MappingVersion.project_id == project.id, MappingVersion.status == "approved")
        .values(status="superseded")
    )
    version = MappingVersion(
        firm_id=project.firm_id,
        project_id=project.id,
        version=next_version,
        generation_id=generation.id,
        source_schema_id=source_def.id,
        target_schema_id=target_def.id,
        items=items,
        excluded=excluded,
        coverage_gaps=gaps,
        complete=not gaps,
        fingerprint=fingerprint,
        status="approved",
        approved_by_id=user.id,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("A concurrent approval created this version first.") from None
    audit.record(
        db,
        firm_id=project.firm_id,
        project_id=project.id,
        actor=user,
        action="mapping_version.approved",
        entity_type="mapping_version",
        entity_id=version.id,
        details={
            "version": next_version,
            "generation_id": generation.id,
            "fingerprint": fingerprint,
            "items": len(items),
            "complete": not gaps,
        },
    )
    db.commit()
    return version, True


def list_versions(db: Session, project: OnboardingProject) -> list[MappingVersion]:
    return list(
        db.scalars(
            select(MappingVersion)
            .where(MappingVersion.project_id == project.id)
            .order_by(MappingVersion.version)
        )
    )


def get_version(db: Session, project: OnboardingProject, version_id: int) -> MappingVersion:
    version = db.scalar(
        select(MappingVersion).where(
            MappingVersion.id == version_id, MappingVersion.project_id == project.id
        )
    )
    if version is None:
        raise NotFound(f"Mapping version {version_id} not found.")
    return version
