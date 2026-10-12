from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import current_token
from app.db.session import get_db
from app.domain import auth_service
from app.errors import Unauthenticated
from app.models import AuthToken
from app.permissions import ROLE_PERMISSIONS, Role
from app.schemas.api import FirmOut, MeResponse, TokenRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(body: TokenRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = auth_service.authenticate(db, body.username, body.password)
    if user is None:
        # One message for unknown user, wrong password and deactivated account.
        raise Unauthenticated("Invalid username or password.")
    token, record = auth_service.issue_token(db, user)
    return TokenResponse(access_token=token, expires_at=record.expires_at)


@router.post("/logout", status_code=204)
def logout(token: AuthToken = Depends(current_token), db: Session = Depends(get_db)) -> Response:
    auth_service.revoke_token(db, token.id)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
def me(token: AuthToken = Depends(current_token)) -> MeResponse:
    user = token.user
    return MeResponse(
        user=UserOut.model_validate(user),
        firm=FirmOut.model_validate(user.firm),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )
