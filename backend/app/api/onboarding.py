from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.serializers import project_out
from app.db.session import get_db
from app.domain import project_service
from app.schemas.api import ProjectCreate, ProjectOut

router = APIRouter(prefix="/api/v1/onboarding-projects", tags=["onboarding"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    return project_out(db, project_service.create_project(db, body))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectOut:
    return project_out(db, project_service.get_project(db, project_id))
