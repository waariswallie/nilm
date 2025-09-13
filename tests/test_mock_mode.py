import os
from fastapi.testclient import TestClient

def test_scan_with_mock(monkeypatch):
    # Force mock mode
    monkeypatch.setenv("MOCK_DB", "1")
    # Re-import settings & app to pick up flag
    from importlib import reload
    from app import config
    reload(config)  # updates settings.mock_db
    from app.main import app

    assert config.settings.mock_db is True
    client = TestClient(app)
    r = client.get("/scan?last_days=2")
    assert r.status_code == 200
    data = r.json()
    assert data["n_points"] > 0
    # events may be zero if threshold high; just assert baseload present
    assert "baseload" in data