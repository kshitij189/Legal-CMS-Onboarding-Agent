"""Mapping proposals, human review, approval into immutable versions, and the audit trail.

There is deliberately no execute endpoint here: applying a version to a CMS arrives in a later
phase and will accept only an approved mapping version id.
"""

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.agent.llm_client import LLMClient, build_llm_client
from app.api.deps import require
from app.db.session import get_db
from app.domain import audit, mapping_service, project_service
from app.models import MappingProposal, MappingVersion, ProposalGeneration, User
from app.permissions import Permission
from app.schemas.mapping import ProposedMapping
from app.schemas.mapping_api import (
    ApproveRequest,
    AuditEventOut,
    GenerationOut,
    MappingVersionOut,
    ProposalOut,
    ReviewRequest,
)

router = APIRouter(prefix="/api/v1/onboarding-projects/{project_id}", tags=["mappings"])


def get_llm_client() -> LLMClient:
    """Overridable in tests; production builds the Gemini -> Groq fallback chain."""
    return build_llm_client()


def _proposal_out(p: MappingProposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        origin=p.origin,
        supersedes_id=p.supersedes_id,
        source_field=p.source_field,
        decision=p.decision,
        mapping=ProposedMapping.model_validate(p.content),
        validation_status=p.validation_status,
        validation_issues=p.validation_issues,
        review_status=p.review_status,
        review_comment=p.review_comment,
        reviewed_by=p.reviewed_by.username if p.reviewed_by else None,
        reviewed_at=p.reviewed_at,
    )


def _generation_out(g: ProposalGeneration, created: bool) -> GenerationOut:
    return GenerationOut(
        id=g.id,
        status=g.status,
        created=created,
        provider=g.provider,
        model=g.model,
        prompt_version=g.prompt_version,
        input_tokens=g.input_tokens,
        output_tokens=g.output_tokens,
        latency_ms=g.latency_ms,
        fallbacks=g.fallbacks,
        source_schema_id=g.source_schema_id,
        target_schema_id=g.target_schema_id,
        coverage_gaps=g.coverage_gaps,
        unproposed_source_fields=g.unproposed_source_fields,
        untrusted_source_fields=g.untrusted_source_fields,
        created_by=g.created_by.username,
        created_at=g.created_at,
        proposals=[_proposal_out(p) for p in g.proposals],
    )


def _version_out(v: MappingVersion, created: bool) -> MappingVersionOut:
    return MappingVersionOut(
        id=v.id,
        version=v.version,
        status=v.status,
        created=created,
        generation_id=v.generation_id,
        source_schema_id=v.source_schema_id,
        target_schema_id=v.target_schema_id,
        fingerprint=v.fingerprint,
        complete=v.complete,
        items=v.items,
        excluded=v.excluded,
        coverage_gaps=v.coverage_gaps,
        approved_by=v.approved_by.username,
        approved_at=v.approved_at,
    )


@router.post("/propose-mappings", response_model=GenerationOut)
def propose_mappings(
    project_id: int,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=100),
    user: User = Depends(require(Permission.MAPPINGS_PROPOSE)),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
) -> GenerationOut:
    project = project_service.get_project(db, user, project_id)
    generation, created = mapping_service.generate_proposals(
        db, user, project, llm, idempotency_key
    )
    response.status_code = 201 if created else 200
    return _generation_out(generation, created)


@router.get("/mappings", response_model=GenerationOut)
def get_mappings(
    project_id: int,
    generation_id: int | None = None,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> GenerationOut:
    project = project_service.get_project(db, user, project_id)
    return _generation_out(mapping_service.get_generation(db, project, generation_id), False)


@router.post("/mappings/{proposal_id}/review", response_model=ProposalOut)
def review_mapping(
    project_id: int,
    proposal_id: int,
    body: ReviewRequest,
    user: User = Depends(require(Permission.MAPPINGS_REVIEW)),
    db: Session = Depends(get_db),
) -> ProposalOut:
    project = project_service.get_project(db, user, project_id)
    proposal = mapping_service.review_proposal(
        db, user, project, proposal_id, body.action, body.comment, body.mapping
    )
    return _proposal_out(proposal)


@router.post("/approve-mappings", response_model=MappingVersionOut)
def approve_mappings(
    project_id: int,
    body: ApproveRequest,
    response: Response,
    user: User = Depends(require(Permission.MAPPINGS_APPROVE)),
    db: Session = Depends(get_db),
) -> MappingVersionOut:
    project = project_service.get_project(db, user, project_id)
    version, created = mapping_service.approve_mappings(db, user, project, body.generation_id)
    response.status_code = 201 if created else 200
    return _version_out(version, created)


@router.get("/mapping-versions", response_model=list[MappingVersionOut])
def list_versions(
    project_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> list[MappingVersionOut]:
    project = project_service.get_project(db, user, project_id)
    return [_version_out(v, False) for v in mapping_service.list_versions(db, project)]


@router.get("/mapping-versions/{version_id}", response_model=MappingVersionOut)
def get_version(
    project_id: int,
    version_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> MappingVersionOut:
    project = project_service.get_project(db, user, project_id)
    return _version_out(mapping_service.get_version(db, project, version_id), False)


@router.get("/audit-events", response_model=list[AuditEventOut])
def audit_events(
    project_id: int,
    user: User = Depends(require(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
) -> list[AuditEventOut]:
    project = project_service.get_project(db, user, project_id)
    return [
        AuditEventOut(
            id=e.id,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            actor=e.actor.username if e.actor else None,
            details=e.details,
            created_at=e.created_at,
        )
        for e in audit.list_events(db, user.firm_id, project.id)
    ]
