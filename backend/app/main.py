from fastapi import FastAPI

from app.api import auth, firms, onboarding, schemas
from app.db.session import get_engine
from app.errors import register_error_handlers
from app.observability.logging import configure_logging, install_request_logging
from app.observability.tracing import configure_tracing


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Legal CMS Onboarding Agent",
        version="0.1.0",
        description="Onboards law-firm intake schemas into a target CMS with human approval.",
    )
    register_error_handlers(app)
    install_request_logging(app)
    app.include_router(auth.router)
    app.include_router(firms.router)
    app.include_router(onboarding.router)
    app.include_router(schemas.router)
    configure_tracing(app, get_engine())

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
