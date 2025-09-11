from app.config import settings


def test_settings_load():
    assert settings.event_watt_threshold > 0
