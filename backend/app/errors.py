from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error rendered as {"error": {"code", "message", "details"}}."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class Unauthenticated(AppError):
    status_code, code = 401, "unauthenticated"


class Forbidden(AppError):
    status_code, code = 403, "forbidden"


class NotFound(AppError):
    status_code, code = 404, "not_found"


class Conflict(AppError):
    status_code, code = 409, "conflict"


class ValidationFailed(AppError):
    status_code, code = 422, "validation_error"


class SchemaParseError(ValidationFailed):
    code = "schema_parse_error"


class ConnectorError(AppError):
    """A target-CMS call failed. `category` drives retry/stop decisions (CLAUDE.md §11)."""

    status_code, code = 502, "connector_error"

    def __init__(
        self, category: str, message: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message, details)
        self.category = category  # authorization | transient | contract | configuration


class LLMUnavailable(AppError):
    """No configured LLM provider produced a valid proposal."""

    status_code, code = 502, "llm_error"


def error_body(code: str, message: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, AppError):
            raise exc
        details = exc.details
        if isinstance(exc, ConnectorError):
            details = [{"category": exc.category}, *details]
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthenticated) else None
        return JSONResponse(
            jsonable_encoder(error_body(exc.code, exc.message, details)),
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

    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation)
