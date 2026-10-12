import logging

from fastapi import FastAPI

from app import api, ui
from app.errors import register_api_error_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mock Legal CMS",
        version="0.1.0",
        description="Simulated legal case management system (synthetic data only).",
    )
    register_api_error_handlers(app)
    ui.register_ui_error_handlers(app)
    app.include_router(api.router)
    app.include_router(ui.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
