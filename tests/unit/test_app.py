"""Tests for the bootstrap application surface."""

from fastapi.testclient import TestClient

from github_oauth_worker.app import create_app
from github_oauth_worker.config import ServiceMode, WorkerSettings
from github_oauth_worker.errors import WorkerUnavailableError


def test_health_reports_bootstrap_without_configuration() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "bootstrap"}


def test_oauth_routes_are_unavailable_in_bootstrap_mode() -> None:
    response = TestClient(create_app()).get("/auth")

    assert response.status_code == 404


def test_health_reports_ready_without_configuration_values() -> None:
    response = TestClient(
        create_app(
            WorkerSettings(
                service_mode=ServiceMode.READY,
                gcp_project_id="example-project",
                public_base_url="https://worker.example.com",
                github_app_client_id="client-id",
                github_app_client_secret="client-secret",
                state_signing_secret="state-secret",
            )
        )
    ).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_expected_worker_errors_have_generic_browser_safe_responses() -> None:
    app = create_app()

    @app.get("/unavailable")
    def unavailable() -> None:
        raise WorkerUnavailableError("sensitive deployment detail")

    response = TestClient(app).get("/unavailable")

    assert response.status_code == 503
    assert response.json() == {"error": "The OAuth worker is not configured."}
    assert "sensitive deployment detail" not in response.text
