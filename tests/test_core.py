import json
import os
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest

from station import core, providers
from station.app import create_app
from station.core import Station, StationError

HEADERS = {"X-Station-Request": "1"}
REQUEST = {"description": "A hopeful river song", "genre": "Country pop", "vocal": "Female",
           "duration": 60, "use_cover": False, "auto_queue": True}


@pytest.fixture
def station(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    value = Station(tmp_path / "data", project_root=tmp_path)
    yield value
    value.close()


@pytest.fixture
def client(station):
    with TestClient(create_app(station, start_worker=False), base_url="http://127.0.0.1:8765") as value:
        yield value


def ready(station, identifier="one", duration=10):
    directory = station.data_dir / "songs" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind in core.KINDS:
        path = directory / (kind + (".png" if kind == "cover" else ".mp4"))
        path.write_bytes((identifier + "-" + kind + "-original").encode())
        files[kind] = station._relative(path)
    song = {"id": identifier, "title": identifier.title(), "description": "A river song", "genre": "Country", "vocal": "Female",
            "duration": duration, "created_at": core.now(), "status": "ready", "cover_source": "station", "revision": 1, "files": files}
    with station.lock:
        station._save("songs", song)
    return song


def fake_media(monkeypatch):
    def cover(title, genre, output):
        Path(output).write_bytes(b"fallback image")
        return Path(output)

    def package(audio, cover, title, output):
        paths = {"duration": 60}
        for kind in ("landscape", "portrait"):
            path = Path(output) / (kind + ".mp4")
            path.write_bytes(Path(cover).read_bytes() + b" paired with " + Path(audio).read_bytes())
            paths[kind] = str(path)
        return paths

    monkeypatch.setattr(core.media, "make_station_cover", cover)
    monkeypatch.setattr(core.media, "package_song", package)


def fake_song_providers(monkeypatch):
    def compose(*args):
        return {"title": "River Light", "style": "Country pop", "lyrics": "[Verse]\nThe river shines",
                "cover_prompt": "Moonlit water"}

    def music(style, lyrics, seed, duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"generated song")
        kwargs["on_progress"]({"stage": "submitted", "prompt_id": "own-prompt"})
        return {"duration": 60, "audio_path": str(out_path), "prompt_id": "own-prompt",
                "completion": {"semantic_truncated": False, "abc_truncated": False, "abc": "X:1\nK:C\nC|]"},
                "music": core.resolve_music_controls(kwargs.get("music"), seed, duration),
                "graph": {"2": {"inputs": {"seed": str(seed)}}}}

    monkeypatch.setattr(core.providers, "compose_song", compose)
    monkeypatch.setattr(core.providers, "generate_music", music)


def test_api_origin_host_and_mutation_guards(client):
    assert client.post("/api/songs", json=REQUEST).status_code == 403
    assert client.post("/api/songs", headers={**HEADERS, "Origin": "https://other.example"}, json=REQUEST).status_code == 403
    assert client.get("/api/state", headers={"Host": "other.example"}).status_code == 403
    response = client.post("/api/songs", headers={**HEADERS, "Origin": "http://127.0.0.1:8765"}, json=REQUEST)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_private_https_proxy_origin_and_local_access(station):
    origin = "https://music.example.ts.net:8443"
    (station.data_dir / "network.json").write_text(json.dumps({"allowed_origins": [origin]}))
    app = create_app(station, start_worker=False)
    with TestClient(app, base_url=origin) as client:
        assert client.get("/api/state").status_code == 200
        # Reverse-proxied requests are HTTP on the backend; the configured
        # browser origin is HTTPS. Forwarded headers are not the allowlist.
        response = client.post("http://music.example.ts.net:8443/api/songs",
                               headers={**HEADERS, "Origin": origin}, json=REQUEST)
        assert response.status_code == 202
        assert client.post("/api/songs", headers={"Origin": origin}, json=REQUEST).status_code == 403
        for bad in ("null", "", "http://music.example.ts.net:8443", "https://music.example.ts.net",
                    "https://other.example", origin + "/", origin + "?x=1"):
            assert client.post("/api/songs", headers={**HEADERS, "Origin": bad}, json=REQUEST).status_code == 403
        for bad in ("music.example.ts.net", "music.example.ts.net:443", "music.example.ts.net.evil:8443",
                    "user@music.example.ts.net:8443", "127.0.0.1:bad"):
            assert client.get("/api/state", headers={"Host": bad, "X-Forwarded-Host": "music.example.ts.net:8443"}).status_code == 403
        assert client.get("http://127.0.0.1:8765/api/state").status_code == 200
        assert client.post("http://127.0.0.1:8765/api/songs", headers={**HEADERS, "Origin": "http://127.0.0.1:8765"}, json=REQUEST).status_code == 202


@pytest.mark.parametrize("value", [None, "https://music.example", ["http://music.example"],
    ["https://*.example"], ["https://user@music.example"], ["https://music.example/path"],
    ["https://music.example:bad"], ["https://music.example?x=1"], ["https://music.example#x"]])
def test_invalid_private_origins_fail_closed(station, value):
    (station.data_dir / "network.json").write_text(json.dumps({"allowed_origins": value}))
    with pytest.raises(ValueError, match="network.json"):
        create_app(station, start_worker=False)


@pytest.mark.parametrize("payload", [{"api_key": {"secret-value": "hidden"}}, {"api_key": "test-secret", "cover_limit": "test-secret"}, {"test-secret": "hidden"}])
def test_validation_never_reflects_secret_inputs(client, payload):
    response = client.post("/api/settings", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert "test-secret" not in response.text
    assert "secret-value" not in response.text
    assert "hidden" not in response.text


def test_settings_keep_key_in_memory_until_explicit_save(station, client):
    response = client.post("/api/settings", headers=HEADERS, json={"api_key": "test-secret", "save_key": False})
    assert response.status_code == 200
    assert response.json()["key_configured"]
    assert not response.json()["key_persisted"]
    assert not (station.project_root / ".env.local").exists()
    assert "test-secret" not in client.get("/api/state").text
    assert "test-secret" not in "\n".join(station.db.iterdump())
    response = client.post("/api/settings", headers=HEADERS, json={"save_key": True})
    assert response.json()["key_persisted"]
    path = station.project_root / ".env.local"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "test-secret" in path.read_text()
    response = client.post("/api/settings/forget-key", headers=HEADERS)
    assert not response.json()["key_configured"]
    assert "test-secret" not in path.read_text()


def test_key_save_refuses_symlink(station):
    destination = station.project_root / "outside"
    destination.write_text("original")
    (station.project_root / ".env.local").symlink_to(destination)
    with pytest.raises(StationError, match="symlink"):
        station.update_settings({"api_key": "test-secret", "save_key": True})
    assert destination.read_text() == "original"


def test_blank_seed_randomized_and_explicit_zero_preserved(station, monkeypatch):
    monkeypatch.setattr(core.secrets, "randbits", lambda bits: 987654)
    random_job = station.create_song(REQUEST)
    zero_job = station.create_song({**REQUEST, "seed": 0})
    assert station._get("jobs", random_job["id"])["request"]["seed"] == 987654
    assert station._get("jobs", zero_job["id"])["request"]["seed"] == 0


def test_song_job_packages_before_queue_and_hides_paths(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    job = station.create_song(REQUEST)
    assert station.snapshot()["queue"] == []
    station._run_job(job["id"])
    state = station.snapshot()
    assert state["jobs"][0]["status"] == "completed"
    assert state["songs"][0]["status"] == "ready"
    assert state["songs"][0]["title"] == "River Light"
    assert state["queue"] == [job["song_id"]]
    assert str(station.data_dir) not in json.dumps(state)
    assert all(state["songs"][0]["assets"].values())
    assert state["songs"][0]["generation_time"] >= 0


def test_ambiguous_cover_falls_back_and_retains_reservation(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    station.update_settings({"api_key": "test-secret"})
    calls = []

    def cover(*args, **kwargs):
        calls.append(1)
        assert station.settings()["usage"]["estimated_usd"] == 1
        raise providers.ProviderError("The artwork result is uncertain.", ambiguous=True)

    monkeypatch.setattr(core.providers, "generate_cover", cover)
    job = station.create_song({**REQUEST, "use_cover": True})
    station._run_job(job["id"])
    state = station.snapshot()
    assert state["songs"][0]["status"] == "ready"
    assert state["songs"][0]["cover_source"] == "station"
    assert state["settings"]["usage"]["uncertain_requests"] == 1
    assert state["settings"]["usage"]["estimated_usd"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("actual_duration,semantic,abc,status", [
    (60., False, False, "ready"), (42., True, False, "needs_review"), (59.8, False, True, "needs_review"),
])
def test_completion_report_not_duration_controls_queue(station, monkeypatch, actual_duration, semantic, abc, status):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)

    def music(style, lyrics, seed, duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"generated song")
        return {"duration": actual_duration, "audio_path": str(out_path), "prompt_id": "own-prompt",
                "completion": {"semantic_truncated": semantic, "abc_truncated": abc}}

    monkeypatch.setattr(core.providers, "generate_music", music)
    job = station.create_song(REQUEST)
    station._run_job(job["id"])
    song = station.snapshot()["songs"][0]
    assert song["status"] == status
    assert bool(station.snapshot()["queue"]) is (status == "ready")
    assert bool(song.get("warning")) is (status == "needs_review")
    completed_job = station.snapshot()["jobs"][0]
    assert completed_job["status"] == "completed"
    assert completed_job["stage"] == ("needs_review" if status == "needs_review" else "complete")
    assert completed_job.get("warning") == song.get("warning")


def test_missing_report_keeps_audio_for_review_without_queue(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    original_package = core.media.package_song

    def package(*args):
        return {**original_package(*args), "duration": 41.5}

    monkeypatch.setattr(core.media, "package_song", package)

    def music(style, lyrics, seed, duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"retained audio")
        error = providers.ProviderError("The completion report is unavailable.")
        error.audio_path, error.duration = str(out_path), 41.5
        error.music = core.resolve_music_controls(kwargs.get("music"), seed, duration)
        error.graph = {"2": {"inputs": {"seed": str(seed)}}}
        raise error

    monkeypatch.setattr(core.providers, "generate_music", music)
    job = station.create_song(REQUEST)
    station._run_job(job["id"])
    song = station.snapshot()["songs"][0]
    assert song["status"] == "needs_review"
    assert song["completion"]["status"] == "unknown"
    assert song["duration"] == 41.5
    assert station.asset_path(song["id"], "audio").read_bytes() == b"retained audio"
    assert song["assets"]["portrait"]
    assert station.snapshot()["queue"] == []
    with pytest.raises(StationError, match="ready"):
        station.edit_queue(song["id"], "add")


def test_intentional_clip_is_ready_with_explicit_warning(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    original_music = core.providers.generate_music

    def music(*args, **kwargs):
        result = original_music(*args, **kwargs)
        result["completion"]["semantic_truncated"] = True
        return result

    monkeypatch.setattr(core.providers, "generate_music", music)
    job = station.create_song({**REQUEST, "music": {"ending_mode": "clip"}})
    station._run_job(job["id"])
    song = station.snapshot()["songs"][0]
    assert song["status"] == "ready"
    assert song["completion"]["status"] == "intentional_clip"
    assert "intentional clip" in song["warning"]
    assert song["music"]["max_duration"] == REQUEST["duration"]
    assert station.snapshot()["queue"] == [song["id"]]


def test_new_take_preserves_source_and_reuses_saved_brief_and_cover(station, client, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    seed = 2**64 - 1
    response = client.post("/api/songs", headers=HEADERS, json={**REQUEST, "seed": str(seed), "style": "Exact steel guitar style",
        "lyrics": "[Verse]\nThe saved river shines", "music": {"temperature": .8, "decoder_seed": "42", "planning_seed": str(seed)}})
    original_job = response.json()
    assert response.status_code == 202
    station._run_job(original_job["id"])
    source_id = original_job["song_id"]
    before = station._get("songs", source_id)
    original_cover = station.asset_path(source_id, "cover").read_bytes()
    monkeypatch.setattr(core.providers, "compose_song", lambda *args, **kwargs: pytest.fail("A new take rewrote saved lyrics"))
    monkeypatch.setattr(core.providers, "generate_cover", lambda *args, **kwargs: pytest.fail("A new take made a paid cover request"))
    response = client.post(f"/api/songs/{source_id}/regenerate", headers=HEADERS,
                           json={"music": {"ending_mode": "natural", "safety_seconds": 240}})
    assert response.status_code == 202
    next_job = response.json()
    assert next_job["kind"] == "music"
    assert next_job["song_id"] != source_id
    stored_request = station._get("jobs", next_job["id"])["request"]
    assert stored_request["seed"] == seed
    assert stored_request["style"] == before["style"]
    assert stored_request["lyrics"] == before["lyrics"]
    assert stored_request["music"]["temperature"] == .8
    assert stored_request["music"]["decoder_seed"] == 42
    assert stored_request["music"]["safety_seconds"] == 240
    station._run_job(next_job["id"])
    sibling = station._get("songs", next_job["song_id"])
    assert station._get("songs", source_id) == before
    assert sibling["source_song_id"] == source_id
    assert sibling["status"] == "ready"
    assert sibling["title"] == before["title"]
    assert station.asset_path(sibling["id"], "cover").read_bytes() == original_cover
    assert station.asset_path(sibling["id"], "audio") != station.asset_path(source_id, "audio")
    public = client.get(f"/api/songs/{sibling['id']}/details").json()
    assert public["seed"] == str(seed)
    assert public["music"]["planning_seed"] == str(seed)
    assert public["music"]["decoder_seed"] == "42"
    assert public["generation_graph"]["2"]["inputs"]["seed"] == str(seed)
    assert public["abc"] and "abc" not in public["completion"]
    assert all("generation_graph" not in song for song in client.get("/api/state").json()["songs"])
    assert station.settings()["usage"]["requests"] == 0


def test_legacy_new_take_recovers_job_seed_and_fixed_decoder(station, client, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    source = ready(station)
    source.update(style="Original folk style", lyrics="[Verse]\nOriginal lyrics", status="needs_review")
    station._save("songs", source)
    station._save("jobs", {"id": "old", "song_id": source["id"], "kind": "song", "status": "completed", "stage": "complete",
        "request": {**REQUEST, "seed": 0}})
    response = client.post("/api/songs/one/regenerate", headers=HEADERS, json={"description": "Adjusted brief", "genre": "Pop",
        "vocal": "Male", "title": "New title", "style": "Edited model style", "lyrics": "[Verse]\nEdited lyrics",
        "duration": 240, "auto_queue": False, "music": {"safety_seconds": 240}})
    assert response.status_code == 202
    job = response.json()
    request = station._get("jobs", job["id"])["request"]
    assert request["seed"] == 0
    assert request["music"]["decoder_seed"] == 42
    assert request["music"]["planning_seed"] == 0
    assert request["duration"] == 180
    station._run_job(job["id"])
    sibling = station._get("songs", job["song_id"])
    assert sibling["title"] == "New title"
    assert sibling["style"] == "Edited model style"
    assert sibling["lyrics"] == "[Verse]\nEdited lyrics"
    assert sibling["description"] == "Adjusted brief"
    assert station.snapshot()["queue"] == []
    assert station._get("songs", "one") == source


@pytest.mark.parametrize("overrides", [{"duration": 241}, {"duration": 600}, {"music": {"safety_seconds": 241}},
                                       {"music": {"safety_seconds": 900}}])
def test_api_rejects_new_generation_over_four_minutes(client, overrides):
    assert client.post("/api/songs", headers=HEADERS, json={**REQUEST, **overrides}).status_code == 422
    assert client.post("/api/songs/old/regenerate", headers=HEADERS, json=overrides).status_code == 422


def test_new_natural_request_uses_automatic_lyric_target(station, client):
    for desired in (15, 60, 240):
        response = client.post("/api/songs", headers=HEADERS, json={**REQUEST, "duration": desired})
        assert response.status_code == 202
        request = station._get("jobs", response.json()["id"])["request"]
        assert request["duration"] == 180
        assert request["music"]["safety_seconds"] == 240
    response = client.post("/api/songs", headers=HEADERS, json={**REQUEST, "duration": 240, "music": {"ending_mode": "clip"}})
    request = station._get("jobs", response.json()["id"])["request"]
    assert request["duration"] == 240


def test_historical_long_take_stays_readable_and_queued_while_new_take_clamps(station, client, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    source = ready(station, "old-long-song", 480)
    source.update(style="Saved country style", lyrics="[Verse]\nSaved complete lyrics", seed="7", target_duration=600,
                  completion={"status": "complete", "semantic_truncated": False, "abc_truncated": False},
                  music={"ending_mode": "natural", "safety_seconds": 900, "decoder_seed": "42", "temperature": .8})
    station._save("songs", source)
    station.edit_queue(source["id"], "add")
    response = client.get(f"/api/songs/{source['id']}/details")
    assert response.status_code == 200
    assert response.json()["duration"] == 480
    assert response.json()["music"]["safety_seconds"] == 900
    response = client.post(f"/api/songs/{source['id']}/regenerate", headers=HEADERS, json={"auto_queue": False})
    assert response.status_code == 202
    job = response.json()
    request = station._get("jobs", job["id"])["request"]
    assert request["duration"] == 180
    assert request["music"]["safety_seconds"] == 240
    assert request["music"]["decoder_seed"] == 42
    assert request["music"]["temperature"] == .8
    station._run_job(job["id"])
    assert station._get("songs", source["id"]) == source
    assert station.snapshot()["queue"] == [source["id"]]


def test_queued_legacy_request_normalizes_before_composer_and_provider(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    original_compose = core.providers.compose_song
    original_music = core.providers.generate_music
    seen = []

    def compose(description, genre, vocal, duration):
        seen.append(("composer", duration))
        return original_compose(description, genre, vocal, duration)

    def music(style, lyrics, seed, duration, out_path, **kwargs):
        seen.append(("music", duration, kwargs["music"]["safety_seconds"]))
        result = original_music(style, lyrics, seed, duration, out_path, **kwargs)
        result["completion"]["semantic_truncated"] = True
        return result

    monkeypatch.setattr(core.providers, "compose_song", compose)
    monkeypatch.setattr(core.providers, "generate_music", music)
    job = station.create_song(REQUEST)
    legacy = station._get("jobs", job["id"])
    legacy["request"]["duration"] = 600
    legacy["request"]["music"]["safety_seconds"] = 900
    station._save("jobs", legacy)
    station._run_job(job["id"])
    assert seen == [("composer", 180), ("music", 180, 240)]
    song = station._get("songs", job["song_id"])
    assert song["status"] == "needs_review"
    assert song["music"]["max_duration"] == 240
    assert song["completion"]["status"] == "truncated"
    assert station.snapshot()["queue"] == []


def test_startup_reconciles_known_legacy_clips_preserving_files(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    first = Station(tmp_path / "data", project_root=tmp_path)
    clipped = ready(first, "clipped", 60)
    clipped.update(duration_warning=core.DURATION_WARNING, warning=core.DURATION_WARNING)
    first._save("songs", clipped)
    short = ready(first, "short", 49)
    first.edit_queue("clipped", "add")
    first.edit_queue("short", "add")
    first.close()
    second = Station(tmp_path / "data", project_root=tmp_path)
    try:
        assert second._get("songs", "clipped")["status"] == "needs_review"
        assert second._get("songs", "clipped")["completion"]["status"] == "legacy_truncated"
        assert second._get("songs", "short")["status"] == "ready"
        assert second._get("songs", "short")["completion"]["status"] == "legacy"
        assert second._get("songs", "short")["completion"]["semantic_truncated"] is None
        assert second.snapshot()["queue"] == ["short"]
        assert second._get("songs", "clipped")["files"] == clipped["files"]
        assert second.asset_path("clipped", "audio").read_bytes() == b"clipped-audio-original"
    finally:
        second.close()


def test_cover_fail_and_regeneration_preserve_duration_warning(station, monkeypatch):
    fake_media(monkeypatch)
    fake_song_providers(monkeypatch)
    original_music = core.providers.generate_music

    def truncated_music(*args, **kwargs):
        result = original_music(*args, **kwargs)
        result["completion"]["semantic_truncated"] = True
        return result

    monkeypatch.setattr(core.providers, "generate_music", truncated_music)
    station.update_settings({"api_key": "test-secret"})

    def fail_cover(*args, **kwargs):
        raise providers.ProviderError("Artwork generation was unavailable.")

    monkeypatch.setattr(core.providers, "generate_cover", fail_cover)
    job = station.create_song({**REQUEST, "use_cover": True})
    station._run_job(job["id"])
    song = station.snapshot()["songs"][0]
    assert "before its end token" in song["warning"]
    assert "Artwork generation was unavailable." in song["warning"]

    def good_cover(prompt, key, model, quality, destination, **kwargs):
        Path(destination).write_bytes(b"new artwork")
        return {"estimated_cost_usd": .1}

    monkeypatch.setattr(core.providers, "generate_cover", good_cover)
    cover_job = station.create_cover(song["id"])
    station._run_job(cover_job["id"])
    updated = station.snapshot()["songs"][0]
    assert updated["revision"] == 2
    assert updated["warning"] == station._get("songs", song["id"])["duration_warning"]
    assert updated["status"] == "needs_review"


def test_cover_failure_preserves_original_pair(station, monkeypatch):
    song = ready(station)
    before = station._public_song(song)
    station.update_settings({"api_key": "test-secret"})
    monkeypatch.setattr(core.providers, "generate_cover", lambda *args, **kwargs: (_ for _ in ()).throw(providers.ProviderError("Rejected.")))
    job = station.create_cover(song["id"])
    station._run_job(job["id"])
    assert station.snapshot()["songs"][0] == before
    assert station.snapshot()["jobs"][0]["status"] == "failed"
    assert station.settings()["usage"]["estimated_usd"] == 0


def test_regenerated_cover_keeps_old_revision_urls_immutable(station, client, monkeypatch):
    song = ready(station)
    old_url = station._public_song(song)["assets"]["cover"]
    before = client.get(old_url).content
    fake_media(monkeypatch)
    station.update_settings({"api_key": "test-secret"})

    def cover(prompt, key, model, quality, destination, **kwargs):
        Path(destination).write_bytes(b"new artwork")
        return {"estimated_cost_usd": .15}

    monkeypatch.setattr(core.providers, "generate_cover", cover)
    job = station.create_cover(song["id"])
    station._run_job(job["id"])
    current = station.snapshot()["songs"][0]
    assert current["revision"] == 2
    assert client.get(old_url).content == before
    assert client.get(current["assets"]["cover"]).content == b"new artwork"
    assert client.get("/media/one/cover?v=99").status_code == 404
    assert client.get("/media/one/cover?v=invalid").status_code == 422


def test_unknown_image_usage_remains_uncertain(station, monkeypatch):
    station.update_settings({"api_key": "test-secret"})
    monkeypatch.setattr(core.providers, "generate_cover", lambda *args, **kwargs: {"estimated_cost_usd": None})
    station._cover("river", station.data_dir / "cover.png", "job")
    assert station.settings()["usage"]["uncertain_requests"] == 1
    assert station.settings()["usage"]["estimated_usd"] == 1


def test_limit_blocks_parallel_second_paid_request(station, monkeypatch):
    station.update_settings({"api_key": "test-secret", "cover_limit": 1})
    entered, release = threading.Event(), threading.Event()
    calls = []

    def cover(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"estimated_cost_usd": .2}

    monkeypatch.setattr(core.providers, "generate_cover", cover)
    thread = threading.Thread(target=lambda: station._cover("first", station.data_dir / "a.png", "first"))
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(StationError, match="limit"):
            station._cover("second", station.data_dir / "b.png", "second")
    finally:
        release.set()
        thread.join(5)
    assert len(calls) == 1
    assert station.settings()["usage"]["estimated_usd"] == .2


def test_paid_cancel_settles_cost_before_job_cancelled(station, monkeypatch):
    ready(station)
    station.update_settings({"api_key": "test-secret"})
    job = station.create_cover("one")

    def cover(prompt, key, model, quality, destination, *, cancel_event):
        cancel_event.set()
        Path(destination).write_bytes(b"paid result")
        return {"estimated_cost_usd": .25}

    monkeypatch.setattr(core.providers, "generate_cover", cover)
    station._run_job(job["id"])
    assert station.snapshot()["jobs"][0]["status"] == "cancelled"
    assert station.snapshot()["songs"][0]["revision"] == 1
    assert station.settings()["usage"]["estimated_usd"] == .25


def test_queued_cancel_never_generates(station, monkeypatch):
    monkeypatch.setattr(core.providers, "compose_song", lambda *args: pytest.fail("Cancelled job called provider"))
    job = station.create_song(REQUEST)
    station.cancel_job(job["id"])
    station._run_job(job["id"])
    assert station.snapshot()["jobs"][0]["status"] == "cancelled"


def test_restart_interrupts_running_and_keeps_ambiguous_spend(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    first = Station(tmp_path / "data", project_root=tmp_path)
    job = first.create_song(REQUEST)
    first._stage(job["id"], "cover", status="running")
    first.db.execute("INSERT INTO image_ledger VALUES (?,?,?,?,?,?)", ("reserved", core.today(), job["id"], "reserved", 1., None))
    first.db.commit()
    first.close()
    second = Station(tmp_path / "data", project_root=tmp_path)
    try:
        assert second.snapshot()["jobs"][0]["status"] == "interrupted"
        assert second.pending.empty()
        assert second.settings()["usage"]["uncertain_requests"] == 1
        assert not second.playback()["playing"]
        assert not second.broadcast.status()["running"]
    finally:
        second.close()


def test_second_station_cannot_mutate_live_data_before_port_bind(station):
    song = ready(station)
    station.edit_queue(song["id"], "add")
    station.control_playback("start")
    job = station.create_song(REQUEST)
    station._stage(job["id"], "generating", status="running")
    with pytest.raises(StationError, match="already in use"):
        Station(station.data_dir, project_root=station.project_root)
    assert station.playback()["playing"]
    assert station._get("jobs", job["id"])["status"] == "running"


def test_playback_uses_one_clock_loops_and_pauses(station, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    ready(station, "one", 10)
    ready(station, "two", 20)
    station.edit_queue("one", "add")
    station.edit_queue("two", "add")
    assert station.control_playback("start")["song"]["id"] == "one"
    clock[0] += 15
    state = station.playback()
    assert state["song"]["id"] == "two"
    assert state["position"] == 5
    assert station.playback()["position"] == 5
    station.control_playback("pause")
    clock[0] += 100
    assert station.playback()["position"] == 5
    station.control_playback("start")
    clock[0] += 16
    assert station.playback()["song"]["id"] == "one"
    assert station.playback()["position"] == 1


def test_media_range_and_path_escape(client, station, tmp_path):
    song = ready(station)
    response = client.get("/media/one/audio?v=1", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206
    assert response.content == b"one-"
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    song["files"]["audio"] = "../outside"
    station._save("songs", song)
    assert client.get("/media/one/audio").status_code == 404


def test_broadcast_requires_confirmation_and_freezes_paths(station, monkeypatch):
    ready(station)
    ready(station, "two")
    station.edit_queue("one", "add")
    calls = []
    monkeypatch.setattr(station.broadcast, "start", lambda paths, url, work: calls.append(list(paths)) or {"running": True})
    with pytest.raises(StationError, match="Confirm"):
        station.start_broadcast("rtmps://example.com/live", "private-key", "landscape", False)
    assert not calls
    station.start_broadcast("rtmps://example.com/live", "private-key", "landscape", True)
    station.edit_queue("two", "add")
    assert len(calls[0]) == 1
    assert "private-key" not in json.dumps(station.snapshot())
