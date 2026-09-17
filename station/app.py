"""Local studio API with explicit trusted origins for a private HTTPS proxy."""
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core import ROOT, Station, StationError
from .media import MediaError
from .music_controls import MAX_SONG_SECONDS, NATURAL_LYRIC_SECONDS, MusicControls
from . import providers


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SongInput(Input):
    description: str = Field(min_length=1, max_length=4000)
    genre: str = Field(default="Country pop", min_length=1, max_length=100)
    vocal: str = Field(default="Warm female vocals", min_length=1, max_length=200)
    duration: int = Field(default=NATURAL_LYRIC_SECONDS, ge=15, le=MAX_SONG_SECONDS)
    seed: int | None = Field(default=None, ge=0, lt=2**64, strict=True)
    title: str | None = Field(default=None, max_length=160)
    lyrics: str | None = Field(default=None, max_length=16000)
    style: str | None = Field(default=None, min_length=1, max_length=4000)
    music: MusicControls = Field(default_factory=MusicControls)
    use_cover: bool = True
    auto_queue: bool = True

    @field_validator("seed", mode="before")
    @classmethod
    def parse_seed(cls, value):
        return MusicControls.parse_decimal_seed(value)


class RegenerateInput(Input):
    music: MusicControls | None = None
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    genre: str | None = Field(default=None, min_length=1, max_length=100)
    vocal: str | None = Field(default=None, min_length=1, max_length=200)
    style: str | None = Field(default=None, min_length=1, max_length=4000)
    lyrics: str | None = Field(default=None, min_length=1, max_length=16000)
    seed: int | None = Field(default=None, ge=0, lt=2**64, strict=True)
    duration: int | None = Field(default=None, ge=15, le=MAX_SONG_SECONDS)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    auto_queue: bool | None = None

    @field_validator("seed", mode="before")
    @classmethod
    def parse_seed(cls, value):
        return MusicControls.parse_decimal_seed(value)


class QueueInput(Input):
    song_id: str
    action: Literal["add", "remove", "up", "down"]


class DeleteSongInput(Input):
    confirmed: bool = Field(default=False, strict=True)


class PlaybackInput(Input):
    action: Literal["start", "pause", "stop", "skip", "restart", "play_song"]
    song_id: str | None = None


class SettingsInput(Input):
    api_key: str | None = Field(default=None, max_length=512, repr=False)
    save_key: bool = False
    image_model: str | None = Field(default=None, pattern=r"^gpt-image-[a-zA-Z0-9.\-]+$", max_length=100)
    image_quality: Literal["low", "medium", "high", "xhigh", "max", "auto"] | None = None
    cover_limit: int | None = Field(default=None, ge=0, le=1000)
    cover_budget: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)


class BroadcastInput(Input):
    server: str = Field(min_length=1, max_length=2048)
    stream_key: str = Field(min_length=1, max_length=1024, repr=False)
    orientation: Literal["landscape", "portrait"] = "landscape"
    confirmed: bool = False


def _external_origins(config_path):
    if not config_path.exists():
        return {}
    try:
        values = json.loads(config_path.read_text())["allowed_origins"]
        if not isinstance(values, list):
            raise ValueError
        allowed = {}
        for origin in values:
            if not isinstance(origin, str) or any(c.isspace() for c in origin):
                raise ValueError
            parsed = urlsplit(origin)
            if (parsed.scheme != "https" or not parsed.hostname or not parsed.netloc
                    or parsed.username or parsed.password or parsed.path or parsed.query
                    or parsed.fragment or parsed.port == 0 or "*" in parsed.netloc):
                raise ValueError
            allowed[parsed.netloc] = origin
        return allowed
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("data/network.json must contain an allowed_origins list of exact HTTPS origins without paths.") from None


def create_app(station=None, start_worker=True):
    owned = station is None
    access_dir = station.data_dir if station is not None else ROOT / "data"
    external_origins = _external_origins(access_dir / "network.json")

    @asynccontextmanager
    async def lifespan(app):
        nonlocal station
        if station is None:
            station = Station()
            app.state.station = station
        if start_worker:
            station.start_worker()
        yield
        if owned or start_worker:
            station.close()

    app = FastAPI(title="Boogie Woogie AI", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.station = station

    @app.middleware("http")
    async def trusted_requests_only(request, call_next):
        host = request.headers.get("host", "")
        try:
            parsed = urlsplit("//" + host)
            valid_host = (parsed.netloc == host and not parsed.path and not parsed.query
                          and not parsed.fragment and not parsed.username and not parsed.password
                          and parsed.port != 0 and not any(c.isspace() for c in host))
            local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            expected_origin = f"http://{host}" if local else external_origins.get(host)
            allowed = valid_host and expected_origin is not None
        except ValueError:
            allowed = False
        if not allowed:
            return JSONResponse({"error": "This address is not enabled for the station."}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin != expected_origin:
            return JSONResponse({"error": "This request came from a different site."}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-station-request") != "1":
            return JSONResponse({"error": "The station request header is required."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/index.html", "/app.js", "/style.css"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Default validation JSON includes the rejected input, possibly a key.
        return JSONResponse({"error": "Invalid request. Check the entered fields."}, status_code=422)

    @app.exception_handler(StationError)
    async def station_error(request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(MediaError)
    async def media_error(request, error):
        return JSONResponse({"error": "The media operation failed. Verify the ready playlist and broadcast settings."}, status_code=400)

    @app.get("/api/state")
    def state():
        return station.snapshot()

    @app.get("/api/health")
    def health():
        return station.health()

    @app.get("/api/music/options")
    def music_options():
        try:
            return providers.get_music_options()
        except providers.ProviderError:
            return JSONResponse({"error": "Music controls are unavailable while the model service is offline."}, status_code=503)

    @app.get("/api/songs/{song_id}/details")
    def song_details(song_id: str):
        return station.song_details(song_id)

    @app.post("/api/songs", status_code=202)
    def song(body: SongInput):
        return station.create_song(body.model_dump(exclude_none=True))

    @app.post("/api/songs/{song_id}/cover", status_code=202)
    def cover(song_id: str):
        return station.create_cover(song_id)

    @app.post("/api/songs/{song_id}/delete")
    def delete_song(song_id: str, body: DeleteSongInput):
        return station.delete_song(song_id, body.confirmed)

    @app.post("/api/songs/{song_id}/regenerate", status_code=202)
    def regenerate(song_id: str, body: RegenerateInput):
        changes = {key: value for key, value in body.model_dump(exclude_unset=True).items() if value is not None}
        return station.regenerate_song(song_id, changes)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return station.cancel_job(job_id)

    @app.post("/api/queue")
    def queue(body: QueueInput):
        return station.edit_queue(body.song_id, body.action)

    @app.get("/api/playback")
    def playback():
        return station.playback()

    @app.post("/api/playback")
    def control(body: PlaybackInput):
        return station.control_playback(body.action, body.song_id)

    @app.post("/api/settings")
    def settings(body: SettingsInput):
        return station.update_settings(body.model_dump(exclude_none=True))

    @app.post("/api/settings/forget-key")
    def forget():
        return station.forget_key()

    @app.post("/api/broadcast/start")
    def broadcast_start(body: BroadcastInput):
        return station.start_broadcast(body.server, body.stream_key, body.orientation, body.confirmed)

    @app.post("/api/broadcast/stop")
    def broadcast_stop():
        return station.broadcast.stop()

    @app.get("/media/{song_id}/{kind}")
    def media_file(song_id: str, kind: str, download: bool = False, v: int | None = Query(default=None, ge=1)):
        try:
            path = station.asset_path(song_id, kind, revision=v)
        except StationError:
            return JSONResponse({"error": "This media asset is unavailable."}, status_code=404)
        return FileResponse(path, filename=f"{song_id}-{kind}{path.suffix}" if download else None,
                            content_disposition_type="attachment" if download else "inline")

    web = Path(ROOT) / "web" / "dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="studio")
    return app


app = create_app()
