import pytest

from app import app


@pytest.fixture()
def client():
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app.test_client() as test_client:
        yield test_client


def test_monitoring_api_requires_login(client):
    response = client.get("/api/traffic/now")
    assert response.status_code == 401
    assert response.get_json()["error"] == "authentication_required"


def test_admin_api_rejects_normal_user(client):
    with client.session_transaction() as session:
        session["user_id"] = 7
        session["role_id"] = 2

    response = client.get("/api/devices/list")
    assert response.status_code == 403
    assert response.get_json()["error"] == "administrator_required"


def test_dashboard_redirects_when_logged_out(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/show/infer")
