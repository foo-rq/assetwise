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
