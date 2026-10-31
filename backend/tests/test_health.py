from fastapi.testclient import TestClient

from backend.main import app


def test_health_returns_ok():
    # Send an HTTP request through the app rather than calling the route function directly
    # The context manager also runs startup and shutdown hooks if we add them later
    with TestClient(app) as client:
        response = client.get("/health")

    # Verify the route, response format and body that a caller will actually receive
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}



def test_workspace_is_served_without_exposing_repository_files():
    # The mount exposes only the interface folder while API routes remain separate
    with TestClient(app) as client:
        page = client.get("/workspace/")
        script = client.get("/workspace/workspace.js")
        private = client.get("/workspace/context/background_information.txt")
    assert page.status_code == script.status_code == 200
    assert "Before you begin" in page.text
    assert "Simulated demo" not in page.text
    assert private.status_code == 404


def test_root_redirect_and_cache_headers():
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        assert response.headers["location"] == "/workspace/"
        page = client.get("/workspace/")
        assert page.headers["cache-control"] == "no-cache"
        assert page.headers["x-content-type-options"] == "nosniff"
