from fastapi import FastAPI

from app.api import firms, onboarding, schemas
from app.errors import register_error_handlers
from app.observability.logging import configure_logging, install_request_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Legal CMS Onboarding Agent",
        version="0.1.0",
        description="Onboards law-firm intake schemas into a target CMS with human approval.",
    )
    register_error_handlers(app)
    install_request_logging(app)
    app.include_router(firms.router)
    app.include_router(onboarding.router)
    app.include_router(schemas.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
