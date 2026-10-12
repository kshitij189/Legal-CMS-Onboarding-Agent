from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class CMSError(Exception):
    """Domain error rendered as {"error": {"code", "message", "details"}}."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class Unauthenticated(CMSError):
    status_code, code = 401, "unauthenticated"


class Forbidden(CMSError):
    status_code, code = 403, "forbidden"


class NotFound(CMSError):
    status_code, code = 404, "not_found"


class Conflict(CMSError):
    status_code, code = 409, "conflict"


class ValidationFailed(CMSError):
    status_code, code = 422, "validation_error"


def error_body(code: str, message: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_api_error_handlers(app: FastAPI) -> None:
    async def handle_cms_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, CMSError):
            raise exc
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthenticated) else None
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=headers,
        )

    async def handle_request_validation(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):
            raise exc
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "code": e["type"], "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            jsonable_encoder(error_body("validation_error", "Request validation failed.", details)),
            status_code=422,
        )

    app.add_exception_handler(CMSError, handle_cms_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation)
