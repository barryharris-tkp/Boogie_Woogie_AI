import json
import pytest
from station.core import Station


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)


def test_replace_and_delete_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-environment-key')
    station = Station(tmp_path / 'data', project_root=tmp_path)
    assert station.settings()['key_source'] == 'environment'
    station.update_settings({'api_key': 'dummy-first-key', 'save_key': True})
    replacement = station.update_settings({'api_key': 'dummy-replacement-key', 'save_key': True})
    assert replacement['key_persisted'] and replacement['key_saved']
    assert replacement['key_source'] == 'saved_file'
    station.close()
    station = Station(tmp_path / 'data', project_root=tmp_path)
    try:
        assert station.api_key == 'dummy-replacement-key'
        assert station.settings()['key_persisted']
        assert not station.forget_key()['key_configured']
        assert 'dummy-' not in (tmp_path / '.env.local').read_text()
    finally:
        station.close()
    station = Station(tmp_path / 'data', project_root=tmp_path)
    try:
        assert not station.settings()['key_configured']
        assert not station.settings()['key_saved']
        assert station.settings()['key_source'] == 'none'
    finally:
        station.close()


def test_temporary_replacement_reports_saved_fallback_without_secrets(tmp_path):
    (tmp_path / '.env.local').write_text('OTHER_SETTING=preserved\n')
    station = Station(tmp_path / 'data', project_root=tmp_path)
    try:
        station.update_settings({'api_key': 'dummy-saved-key', 'save_key': True})
        status = station.update_settings({'api_key': 'dummy-session-key', 'save_key': False})
        assert status['key_configured'] and status['key_saved'] and not status['key_persisted']
        assert status['key_source'] == 'session'
        station.update_settings({'image_quality': 'medium'})
        assert station.api_key == 'dummy-session-key'
        assert 'dummy-' not in json.dumps(station.snapshot())
        assert 'dummy-' not in '\n'.join(station.db.iterdump())
        assert 'OTHER_SETTING=preserved' in (tmp_path / '.env.local').read_text()
    finally:
        station.close()
    station = Station(tmp_path / 'data', project_root=tmp_path)
    try:
        assert station.api_key == 'dummy-saved-key'
    finally:
        station.close()
