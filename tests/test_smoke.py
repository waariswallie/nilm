from app.config import settings
from fastapi.testclient import TestClient
from app.main import app


def test_settings_load():
    assert settings.event_watt_threshold > 0


def test_root_endpoint():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "nilm-pipeline"
    assert "/scan" in data["endpoints"]
