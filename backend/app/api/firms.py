from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain import project_service
from app.schemas.api import ConnectorCreate, ConnectorOut, FirmCreate, FirmOut

router = APIRouter(prefix="/api/v1/firms", tags=["firms"])


@router.post("", response_model=FirmOut, status_code=201)
def create_firm(body: FirmCreate, db: Session = Depends(get_db)) -> FirmOut:
    return FirmOut.model_validate(project_service.create_firm(db, body))


@router.get("", response_model=list[FirmOut])
def list_firms(db: Session = Depends(get_db)) -> list[FirmOut]:
    return [FirmOut.model_validate(f) for f in project_service.list_firms(db)]


@router.post("/{firm_id}/connectors", response_model=ConnectorOut, status_code=201)
def create_connector(
    firm_id: int, body: ConnectorCreate, db: Session = Depends(get_db)
) -> ConnectorOut:
    return ConnectorOut.model_validate(project_service.create_connector(db, firm_id, body))


@router.get("/{firm_id}/connectors", response_model=list[ConnectorOut])
def list_connectors(firm_id: int, db: Session = Depends(get_db)) -> list[ConnectorOut]:
    return [ConnectorOut.model_validate(c) for c in project_service.list_connectors(db, firm_id)]
