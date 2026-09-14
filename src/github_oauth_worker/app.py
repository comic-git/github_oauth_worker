"""FastAPI application composition for the GitHub OAuth worker."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from github_oauth_worker.config import WorkerSettings, load_settings
from github_oauth_worker.errors import WorkerError


def create_app(settings: WorkerSettings | None = None) -> FastAPI:
    """Create the bootstrap-only application before OAuth configuration is available."""
    worker_settings = settings or load_settings()
    app = FastAPI(
        title="github_oauth_worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = worker_settings

    @app.exception_handler(WorkerError)
    async def handle_worker_error(_: Request, error: WorkerError) -> JSONResponse:
        """Return only the deliberate public error text for expected worker failures."""
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.public_message},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report readiness without exposing configuration or secret state."""
        return {"status": worker_settings.service_mode.value}

    return app


app = create_app()
