import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from station import core
from station.app import create_app
from station.core import Station, StationError

ID = 'a' * 32
OTHER = 'b' * 32
HEADERS = {'X-Station-Request': '1'}


@pytest.fixture
def station(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    value = Station(tmp_path / 'data', project_root=tmp_path)
    yield value
    value.close()


def add_song(station, identifier=ID):
    directory = station.data_dir / 'songs' / identifier
    directory.mkdir(parents=True)
    files = {}
    for kind in core.KINDS:
        path = directory / (kind + '.test')
        path.write_bytes((identifier + kind).encode())
        files[kind] = station._relative(path)
    older = directory / 'v0' / 'audio.test'
    older.parent.mkdir()
    older.write_bytes(b'older take')
    song = dict(id=identifier, title='Test song', description='Synthetic fixture', genre='Pop', vocal='Male',
                duration=30, status='ready', revision=2, cover_source='station', files=files,
                revisions={'1': {'audio': station._relative(older)}}, created_at=core.now())
    station._save('songs', song)
    return song


def test_confirmed_api_delete_clears_own_assets_jobs_queue_and_playback_only(station):
    song = add_song(station)
    other = add_song(station, OTHER)
    station.edit_queue(ID, 'add')
    station.edit_queue(OTHER, 'add')
    station.control_playback('play_song', ID)
    station._save('jobs', dict(id='finished', song_id=ID, kind='song', status='completed', stage='complete'))
    station.db.execute('INSERT INTO image_ledger VALUES (?,?,?,?,?,?)', ('ledger', core.today(), 'finished', 'completed', .1, 'request'))
    station.db.commit()
    with TestClient(create_app(station, start_worker=False), base_url='http://127.0.0.1:8765') as client:
        route = f'/api/songs/{ID}/delete'
        assert client.post(route, json={'confirmed': True}).status_code == 403
        assert client.post(route, headers={**HEADERS, 'Origin': 'https://foreign.example'}, json={'confirmed': True}).status_code == 403
        assert client.post(route, headers=HEADERS, json={}).status_code == 400
        assert client.post(route, headers=HEADERS, json={'confirmed': 'yes'}).status_code == 422
        assert station.asset_path(ID, 'audio').exists()
        response = client.post(route, headers=HEADERS, json={'confirmed': True})
        assert response.status_code == 200
        assert response.json() == {'deleted': ID, 'queue': [OTHER]}
        assert client.get(f'/media/{ID}/audio?v=1').status_code == 404
        assert client.get(f'/media/{ID}/audio').status_code == 404
        assert client.post(route, headers=HEADERS, json={'confirmed': True}).status_code == 400
    assert station._get('songs', ID) is None
    assert station._get('jobs', 'finished') is None
    assert station._get('songs', OTHER) == other
    assert not (station.data_dir / 'songs' / ID).exists()
    assert not (station.data_dir / 'songs' / ('.deleting-' + ID)).exists()
    assert station.asset_path(OTHER, 'audio').read_bytes() == (OTHER + 'audio').encode()
    assert station.playback()['song'] is None
    assert station.playback()['playing'] is False
    assert station.settings()['usage']['requests'] == 1


@pytest.mark.parametrize('kind', ['own', 'dependent', 'broadcast', 'shared'])
def test_in_use_song_is_not_removed(station, monkeypatch, kind):
    song = add_song(station)
    if kind in {'own', 'dependent'}:
        station._save('jobs', dict(id='active', song_id=ID if kind == 'own' else OTHER, kind='music',
                                  status='running', request={} if kind == 'own' else {'source_song_id': ID, 'reuse_cover': song['files']['cover']}))
    elif kind == 'broadcast':
        monkeypatch.setattr(station.broadcast, 'status', lambda: {'running': True, 'reconnecting': True})
    else:
        other = add_song(station, OTHER)
        other['revisions']['1']['cover'] = song['files']['cover']
        station._save('songs', other)
    with pytest.raises(StationError):
        station.delete_song(ID, True)
    assert station._get('songs', ID) == song
    assert station.asset_path(ID, 'audio').exists()


def test_deletion_rolls_back_database_and_files_together(station):
    song = add_song(station)
    station.edit_queue(ID, 'add')
    station.db.execute("CREATE TRIGGER block_delete BEFORE DELETE ON songs BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(StationError, match='retained'):
        station.delete_song(ID, True)
    assert station._get('songs', ID) == song
    assert station._state('queue') == [ID]
    assert station.asset_path(ID, 'audio').exists()


def test_cleanup_retry_and_precommit_recovery(station, monkeypatch):
    add_song(station)
    add_song(station, OTHER)
    root = station.data_dir / 'songs'
    (root / OTHER).rename(root / ('.deleting-' + OTHER))
    original_rmtree = core.shutil.rmtree
    def failed_cleanup(path):
        raise OSError('synthetic permission failure')
    monkeypatch.setattr(core.shutil, 'rmtree', failed_cleanup)
    result = station.delete_song(ID, True)
    assert result['warning']
    assert station._get('songs', ID) is None
    assert (root / ('.deleting-' + ID)).exists()
    monkeypatch.setattr(core.shutil, 'rmtree', original_rmtree)
    station._recover_song_deletions()
    assert not (root / ('.deleting-' + ID)).exists()
    assert station.asset_path(OTHER, 'audio').exists()


def test_missing_files_and_unrelated_playback(station):
    add_song(station)
    add_song(station, OTHER)
    station.control_playback('play_song', OTHER)
    core.shutil.rmtree(station.data_dir / 'songs' / ID)
    station.delete_song(ID, True)
    assert station.playback()['playing'] is True
    assert station.playback()['song']['id'] == OTHER


def test_symlink_and_invalid_id_cannot_remove_external_files(station, tmp_path):
    add_song(station)
    directory = station.data_dir / 'songs' / ID
    target = tmp_path / 'external'
    directory.rename(target)
    directory.symlink_to(target, target_is_directory=True)
    with pytest.raises(StationError, match='storage'):
        station.delete_song(ID, True)
    with pytest.raises(StationError, match='identifier'):
        station.delete_song('../external', True)
    assert (target / 'audio.test').exists()
    assert station._get('songs', ID)
