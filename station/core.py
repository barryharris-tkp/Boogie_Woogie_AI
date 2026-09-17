"""Persistent local station state, a single generation worker, and shared playback."""
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
import httpx
from . import media, providers
from .music_controls import MAX_SONG_SECONDS, MusicControls, normalize_generation_request, resolve_music_controls

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = {"image_model": providers.DEFAULT_IMAGE_MODEL, "image_quality": "high", "cover_limit": 20, "cover_budget": 5.0}
KINDS = {"audio", "cover", "landscape", "portrait"}
DURATION_WARNING = ("The song reached its maximum duration, so its ending may be cut. "
                    "Review the ending, use a longer maximum, or use fewer lyrics.")

class StationError(ValueError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return datetime.now(timezone.utc).date().isoformat()

def _refresh_warning(song):
    warnings = [song[key] for key in ("duration_warning", "cover_warning", "other_warning") if song.get(key)]
    if warnings:
        song["warning"] = " ".join(warnings)
    else:
        song.pop("warning", None)

def _browser_safe(value, key=""):
    if key == "seed" or key.endswith("_seed"):
        return str(value) if value is not None else None
    if isinstance(value, dict):
        return {k: _browser_safe(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_browser_safe(v) for v in value]
    return value

def _music_values(value):
    return {key: val for key, val in (value or {}).items() if key in MusicControls.model_fields}

def _apply_completion(song, result, request):
    """Only native completion reports can prove a newly generated take ended."""
    report = result.get("completion")
    valid = (isinstance(report, dict) and type(report.get("semantic_truncated")) is bool
             and type(report.get("abc_truncated")) is bool)
    song["music"] = result.get("music") or resolve_music_controls(request.get("music"), request["seed"], request["duration"])
    song["generation_graph"] = result.get("graph") or {}
    song["seed"] = str(request["seed"])
    song["target_duration"] = request["duration"]
    song["completion"] = dict(report) if isinstance(report, dict) else {}
    song["abc"] = song["completion"].pop("abc", "")
    song.pop("duration_warning", None)
    if not valid:
        song["completion"]["status"] = "unknown"
        song["status"] = "needs_review"
        song["duration_warning"] = "The model's ending report is unavailable. This take needs review and was not added to the queue."
    elif song["music"]["ending_mode"] == "clip":
        song["completion"]["status"] = "intentional_clip"
        song["status"] = "ready"
        song["duration_warning"] = "This take was made in intentional clip mode; the selected limit may cut its musical ending."
    elif report["semantic_truncated"] or report["abc_truncated"]:
        song["completion"]["status"] = "truncated"
        song["status"] = "needs_review"
        song["duration_warning"] = "The model reached a music or arrangement limit before its end token. This take needs review and was not added to the queue."
    else:
        song["completion"]["status"] = "complete"
        song["status"] = "ready"
    _refresh_warning(song)

class Station:
    def __init__(self, data_dir=None, project_root=ROOT):
        self.project_root = Path(project_root).resolve()
        self.data_dir = Path(data_dir or self.project_root / "data").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Acquire before recovery writes: Uvicorn starts lifespan before binding
        # its port, so the occupied port does not protect an already live worker.
        try:
            lock_fd = os.open(self.data_dir / ".station.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            self._process_lock = os.fdopen(lock_fd, "r+")
            fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if hasattr(self, "_process_lock"):
                self._process_lock.close()
            raise StationError("This station data directory is already in use or cannot be locked. Stop its existing station before starting another.") from None
        self._closed = False
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.data_dir / "station.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS songs (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS image_ledger (id TEXT PRIMARY KEY, day TEXT NOT NULL, job_id TEXT NOT NULL,
                status TEXT NOT NULL, estimate REAL NOT NULL, request_id TEXT);
        """)
        self.pending = queue.Queue()
        self.events = {}
        self.stopping = threading.Event()
        self.worker = None
        self.broadcast = media.BroadcastManager()
        self._health_lock = threading.Lock()
        self._health_cache = None
        self._health_at = 0
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self._key_source = "environment" if self.api_key else "none"
        self._persisted_key = ""
        envfile = self.project_root / ".env.local"
        if envfile.is_file() and not envfile.is_symlink():
            local_values = dotenv_values(envfile)
            if "OPENAI_API_KEY" in local_values:
                # An explicit blank keeps a deleted key from returning via the environment.
                self._persisted_key = (local_values["OPENAI_API_KEY"] or "").strip()
                self.api_key = self._persisted_key
                self._key_source = "saved_file" if self.api_key else "none"
        with self.lock:
            if self._state("settings") is None:
                self._set_state("settings", DEFAULT_SETTINGS.copy())
            if self._state("queue") is None:
                self._set_state("queue", [])
            self._set_state("playback", {"playing": False, "song_id": None, "position": 0., "started_at": time.time()})
            for job in self._all("jobs"):
                if job["status"] == "running":
                    job.update(status="interrupted", stage="failed", error="The station restarted during this job. It was not retried.")
                    self._save("jobs", job)
                    if job["kind"] in {"song", "music"}:
                        song = self._get("songs", job["song_id"])
                        if song and song["status"] != "ready":
                            song["status"] = "interrupted"
                            self._save("songs", song)
                elif job["status"] == "queued":
                    self.events[job["id"]] = threading.Event()
                    self.pending.put(job["id"])
            self.db.execute("UPDATE image_ledger SET status='uncertain' WHERE status='reserved'")
            self.db.commit()
            self._recover_song_deletions()
            self._reconcile_legacy_songs()

    def _recover_song_deletions(self):
        """Recover the narrow file/SQLite boundary after an interrupted delete."""
        root = self.data_dir / "songs"
        if root.is_symlink() or not root.is_dir():
            return
        for staged in root.glob(".deleting-*"):
            identifier = staged.name.removeprefix(".deleting-")
            if not re.fullmatch(r"[a-f0-9]{32}", identifier) or staged.is_symlink() or not staged.is_dir():
                continue
            original = root / identifier
            try:
                if self._get("songs", identifier):
                    if not original.exists():
                        staged.rename(original)
                else:
                    shutil.rmtree(staged)
            except OSError:
                # Retain remaining files for the next startup cleanup attempt.
                pass

    def delete_song(self, song_id, confirmed=False):
        if confirmed is not True:
            raise StationError("Confirm permanent deletion of this song first.")
        if not re.fullmatch(r"[a-f0-9]{32}", song_id):
            raise StationError("Invalid song identifier.")
        with self.lock:
            song = self._get("songs", song_id)
            if not song:
                raise StationError("This song is no longer in the library.")
            if self.broadcast.status()["running"]:
                raise StationError("Stop the broadcast before deleting songs from its library.")
            jobs = self._all("jobs")
            prefix = f"songs/{song_id}/"
            if any(j["status"] in {"queued", "running"} and (
                    j["song_id"] == song_id or j.get("request", {}).get("source_song_id") == song_id
                    or str(j.get("request", {}).get("reuse_cover", "")).startswith(prefix)) for j in jobs):
                raise StationError("Wait for this song's active jobs or new takes to finish, or cancel them before deleting.")
            for other in self._all("songs"):
                if other["id"] == song_id:
                    continue
                versions = [other.get("files", {}), *other.get("revisions", {}).values()]
                if any(str(path).startswith(prefix) for files in versions for path in files.values()):
                    raise StationError("Another song still uses these files; this song cannot be deleted yet.")
            root = self.data_dir / "songs"
            directory = root / song_id
            staged = root / (".deleting-" + song_id)
            if root.is_symlink() or directory.is_symlink() or staged.exists() or staged.is_symlink():
                raise StationError("Song storage needs attention before this song can be deleted.")
            moved = False
            try:
                if directory.exists():
                    if not directory.is_dir():
                        raise StationError("Song storage is not a directory.")
                    directory.rename(staged)
                    moved = True
                identifiers = [value for value in self._state("queue") if value != song_id]
                playback = self._state("playback")
                if playback["song_id"] == song_id:
                    playback.update(playing=False, song_id=None, position=0., started_at=time.time())
                with self.db:
                    self.db.execute("DELETE FROM songs WHERE id=?", (song_id,))
                    for job in jobs:
                        if job["song_id"] == song_id:
                            self.db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
                    for key, value in (("queue", identifiers), ("playback", playback)):
                        self.db.execute("UPDATE state SET doc=? WHERE key=?", (json.dumps(value), key))
            except Exception as exc:
                if moved:
                    try:
                        staged.rename(directory)
                    except OSError:
                        pass  # Startup recovery can restore it from the retained record.
                if isinstance(exc, StationError):
                    raise
                raise StationError("The song could not be deleted. Its library record was retained; try again.") from None
            for job in jobs:
                if job["song_id"] == song_id:
                    self.events.pop(job["id"], None)
            result = {"deleted": song_id, "queue": identifiers}
            if moved:
                try:
                    shutil.rmtree(staged)
                except OSError:
                    result["warning"] = "Song removed. Some files could not be cleared; cleanup will retry when the studio restarts."
            return result

    def _reconcile_legacy_songs(self):
        blocked = set()
        jobs = {j["song_id"]: j for j in reversed(self._all("jobs")) if j["kind"] in {"song", "music"}}
        for song in self._all("songs"):
            if not song.get("completion"):
                warned = bool(song.get("duration_warning") or DURATION_WARNING in song.get("warning", ""))
                song["completion"] = {"status": "legacy_truncated" if warned else "legacy",
                                      "semantic_truncated": None, "abc_truncated": None}
                if warned and song["status"] == "ready":
                    song["status"] = "needs_review"
                request = jobs.get(song["id"], {}).get("request", {})
                if request.get("seed") is not None:
                    song.setdefault("seed", str(request["seed"]))
                if request.get("duration"):
                    song.setdefault("target_duration", request["duration"])
                self._save("songs", song)
            if song["status"] == "needs_review":
                blocked.add(song["id"])
        self._set_state("queue", [identifier for identifier in self._state("queue") if identifier not in blocked])

    def _state(self, key):
        row = self.db.execute("SELECT doc FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _set_state(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def _get(self, table, identifier):
        row = self.db.execute(f"SELECT doc FROM {table} WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def _all(self, table):
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT doc FROM {table} ORDER BY rowid DESC")]

    def _save(self, table, doc):
        self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)", (doc["id"], json.dumps(doc)))
        self.db.commit()

    def _relative(self, path):
        try:
            return str(Path(path).resolve().relative_to(self.data_dir))
        except ValueError:
            raise StationError("A media asset lies outside station storage.") from None

    def asset_path(self, song_id, kind, revision=None):
        if kind not in KINDS:
            raise StationError("Unknown media type.")
        with self.lock:
            song = self._get("songs", song_id)
            files = (song or {}).get("files", {})
            if revision is not None and song and revision != song["revision"]:
                files = song.get("revisions", {}).get(str(revision), {})
            relative = files.get(kind)
        if not relative:
            raise StationError("This song asset is not ready.")
        result = (self.data_dir / relative).resolve()
        if not result.is_relative_to(self.data_dir) or not result.is_file():
            raise StationError("This song asset is unavailable.")
        return result

    def _public_song(self, song):
        if song is None:
            return None
        public = {key: song[key] for key in ("id", "title", "description", "genre", "vocal", "status", "created_at",
                  "duration", "lyrics", "warning", "cover_source", "revision", "generation_time", "style", "seed",
                  "music", "completion", "abc", "target_duration", "source_song_id") if key in song}
        public["assets"] = {kind: (f"/media/{song['id']}/{kind}?v={song['revision']}" if song.get("files", {}).get(kind) else None) for kind in sorted(KINDS)}
        return _browser_safe(public)

    def song_details(self, song_id):
        with self.lock:
            song = self._get("songs", song_id)
            if not song:
                raise StationError("Unknown song.")
            return {**self._public_song(song), "generation_graph": _browser_safe(song.get("generation_graph", {}))}

    def _public_job(self, job):
        return {key: job[key] for key in ("id", "song_id", "kind", "status", "stage", "error", "warning", "created_at", "updated_at", "prompt_id") if key in job}

    def _usage(self):
        rows = self.db.execute("SELECT status,estimate FROM image_ledger WHERE day=?", (today(),)).fetchall()
        return {"day": today(), "requests": len(rows), "estimated_usd": round(sum(row[1] for row in rows), 6),
                "uncertain_requests": sum(row[0] in {"uncertain", "reserved"} for row in rows)}

    def settings(self):
        with self.lock:
            return {**DEFAULT_SETTINGS, **self._state("settings"), "key_configured": bool(self.api_key),
                    "key_persisted": bool(self.api_key and self.api_key == self._persisted_key),
                    "key_saved": bool(self._persisted_key), "key_source": self._key_source, "usage": self._usage()}

    def snapshot(self):
        with self.lock:
            return {"songs": [self._public_song(s) for s in self._all("songs")], "jobs": [self._public_job(j) for j in self._all("jobs")[:100]],
                    "queue": self._state("queue"), "playback": self.playback(), "settings": self.settings(), "broadcast": self.broadcast.status()}

    def _write_key(self, value):
        target = self.project_root / ".env.local"
        if target.is_symlink():
            raise StationError("The local key file is a symlink; it cannot be used for saving.")
        if (self.project_root / ".git").exists():
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", ".env.local"], cwd=self.project_root, capture_output=True)
            ignored = subprocess.run(["git", "check-ignore", "-q", ".env.local"], cwd=self.project_root, capture_output=True)
            if tracked.returncode == 0 or ignored.returncode != 0:
                raise StationError("The local key file must be untracked and ignored by Git before saving.")
        text = target.read_text() if target.exists() else ""
        kept = [line for line in text.splitlines() if not re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=", line)]
        kept.append("OPENAI_API_KEY=" + value)
        fd, temporary = tempfile.mkstemp(prefix=".env-local-", dir=self.project_root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write("\n".join(kept) + ("\n" if kept else ""))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._persisted_key = value

    def update_settings(self, values):
        with self.lock:
            settings = {**DEFAULT_SETTINGS, **self._state("settings")}
            settings.update({key: values[key] for key in DEFAULT_SETTINGS if key in values})
            key = values.get("api_key")
            if key is not None:
                key = key.strip()
                if not key or len(key) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                    raise StationError("The API key format is invalid. Paste only the key into local settings.")
                if values.get("save_key"):
                    self._write_key(key)
                self.api_key = key
                self._key_source = "saved_file" if values.get("save_key") else "session"
            elif values.get("save_key"):
                if not self.api_key:
                    raise StationError("Enter an API key before saving it.")
                self._write_key(self.api_key)
                self._key_source = "saved_file"
            self._set_state("settings", settings)
            return self.settings()

    def forget_key(self):
        with self.lock:
            self._write_key("")
            self.api_key = ""
            self._key_source = "none"
            return self.settings()

    def start_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.stopping.clear()
            self.worker = threading.Thread(target=self._worker_loop, name="station-generation", daemon=True)
            self.worker.start()

    def close(self):
        if self._closed:
            return
        self.stopping.set()
        with self.lock:
            for event in self.events.values():
                event.set()
        self.broadcast.stop()
        if self.worker:
            self.worker.join(timeout=2)
        if not self.worker or not self.worker.is_alive():
            with self.lock:
                self.db.close()
                fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_UN)
                self._process_lock.close()
                self._closed = True

    def _worker_loop(self):
        while not self.stopping.is_set():
            try:
                identifier = self.pending.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                self._run_job(identifier)
            finally:
                self.pending.task_done()

    def _stage(self, identifier, stage, **extra):
        with self.lock:
            job = self._get("jobs", identifier)
            job.update(stage=stage, updated_at=now(), **extra)
            self._save("jobs", job)

    def create_song(self, request):
        with self.lock:
            request = dict(request)
            if request.get("seed") is None:
                request["seed"] = secrets.randbits(63)
            try:
                request.update(normalize_generation_request(request.get("music"), request["duration"]))
            except (TypeError, ValueError):
                raise StationError("New songs must use valid music controls and a length of at most four minutes.") from None
            song_id, job_id = uuid4().hex, uuid4().hex
            song = {"id": song_id, "title": request.get("title") or "New song", "description": request["description"],
                    "genre": request["genre"], "vocal": request["vocal"], "duration": request["duration"],
                    "created_at": now(), "status": "queued", "revision": 1, "cover_source": "pending", "files": {},
                    "seed": str(request["seed"]), "target_duration": request["duration"]}
            job = {"id": job_id, "song_id": song_id, "kind": "song", "status": "queued", "stage": "queued", "request": request, "created_at": now()}
            self._save("songs", song)
            self._save("jobs", job)
            self.events[job_id] = threading.Event()
            self.pending.put(job_id)
            return self._public_job(job)

    def _source_recipe(self, source):
        old_job = next((j for j in self._all("jobs") if j["song_id"] == source["id"] and j["kind"] in {"song", "music"}), {})
        old_request = old_job.get("request", {})
        recipe = {"style": source.get("style") or old_request.get("style"), "lyrics": source.get("lyrics") or old_request.get("lyrics"),
                  "seed": source.get("seed", old_request.get("seed")),
                  "duration": source.get("target_duration", old_request.get("duration"))}
        graph = source.get("generation_graph") or {}
        if any(recipe[key] is None for key in recipe):
            if not graph:
                try:
                    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=prompt", "-of", "json",
                                             str(self.asset_path(source["id"], "audio"))], capture_output=True, text=True, timeout=15, check=True)
                    graph = json.loads(json.loads(result.stdout)["format"]["tags"]["prompt"])
                except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
                    graph = {}
            inputs = graph.get("2", {}).get("inputs", {})
            for key, graph_key in (("style", "style"), ("lyrics", "lyrics"), ("seed", "seed"), ("duration", "max_duration")):
                if recipe[key] is None:
                    recipe[key] = inputs.get(graph_key)
        if not recipe["style"] or not recipe["lyrics"] or recipe["seed"] is None:
            raise StationError("This song lacks its original style, lyrics, or seed. Supply a complete new song brief instead.")
        recipe["seed"] = int(recipe["seed"])
        recipe["duration"] = min(MAX_SONG_SECONDS, int(recipe["duration"] or max(15, source["duration"])))
        controls = _music_values(source.get("music") or old_request.get("music"))
        if not controls:
            # Earlier station workflows used fixed decoder seed 42.
            controls = {"decoder_seed": 42, "planning_seed": recipe["seed"]}
        recipe["music"] = normalize_generation_request(controls, recipe["duration"], legacy=True)["music"]
        return recipe

    def regenerate_song(self, song_id, overrides):
        with self.lock:
            source = self._get("songs", song_id)
            if not source or source["status"] not in {"ready", "needs_review"}:
                raise StationError("Choose an existing rendered song before making another take.")
            self.asset_path(song_id, "audio")
            cover = self.asset_path(song_id, "cover")
            recipe = self._source_recipe(source)
            for key in ("style", "lyrics", "seed", "duration"):
                if overrides.get(key) is not None:
                    recipe[key] = overrides[key]
            try:
                recipe.update(normalize_generation_request({**recipe["music"], **overrides.get("music", {})}, recipe["duration"]))
            except (TypeError, ValueError):
                raise StationError("The combined music controls are invalid. Use at most four minutes and clear manual ABC notation before turning arrangement planning off.") from None
            recipe.update(description=overrides.get("description", source["description"]), genre=overrides.get("genre", source["genre"]),
                          vocal=overrides.get("vocal", source["vocal"]), title=overrides.get("title") or source["title"],
                          use_cover=False, auto_queue=overrides.get("auto_queue", True),
                          source_song_id=source["id"], reuse_cover=self._relative(cover))
            song_id, job_id = uuid4().hex, uuid4().hex
            song = {"id": song_id, "title": recipe["title"], "description": recipe["description"], "genre": recipe["genre"],
                    "vocal": recipe["vocal"], "duration": recipe["duration"], "target_duration": recipe["duration"],
                    "seed": str(recipe["seed"]), "created_at": now(), "status": "queued", "revision": 1,
                    "source_song_id": source["id"], "cover_source": source["cover_source"], "files": {},
                    "cover_prompt": source.get("cover_prompt", source["description"]), "cover_metadata": source.get("cover_metadata", {})}
            job = {"id": job_id, "song_id": song_id, "kind": "music", "status": "queued", "stage": "queued", "request": recipe, "created_at": now()}
            self._save("songs", song)
            self._save("jobs", job)
            self.events[job_id] = threading.Event()
            self.pending.put(job_id)
            return self._public_job(job)

    def create_cover(self, song_id):
        with self.lock:
            song = self._get("songs", song_id)
            if not song or song["status"] not in {"ready", "needs_review"}:
                raise StationError("Choose a rendered song before generating a new cover.")
            if not self.api_key:
                raise StationError("Add your OpenAI API key in local settings first.")
            if any(j["song_id"] == song_id and j["status"] in {"queued", "running"} for j in self._all("jobs")):
                raise StationError("This song already has an active job.")
            identifier = uuid4().hex
            job = {"id": identifier, "song_id": song_id, "kind": "cover", "status": "queued", "stage": "queued", "created_at": now()}
            self._save("jobs", job)
            self.events[identifier] = threading.Event()
            self.pending.put(identifier)
            return self._public_job(job)

    def cancel_job(self, identifier):
        with self.lock:
            job = self._get("jobs", identifier)
            if not job:
                raise StationError("Unknown job.")
            if job["status"] in {"queued", "running"}:
                self.events.setdefault(identifier, threading.Event()).set()
                if job["status"] == "queued":
                    job.update(status="cancelled", stage="failed", updated_at=now())
                    self._save("jobs", job)
                    if job["kind"] in {"song", "music"}:
                        song = self._get("songs", job["song_id"])
                        song["status"] = "cancelled"
                        self._save("songs", song)
                else:
                    job["cancel_requested"] = True
                    self._save("jobs", job)
            return self._public_job(job)

    def _check_cancel(self, event):
        if event.is_set():
            raise providers.ProviderError("The job was cancelled.", cancelled=True)

    def _cover(self, prompt, destination, job_id):
        with self.lock:
            settings = self.settings()
            if not self.api_key:
                raise StationError("No OpenAI key is configured; a station cover is used.")
            if settings["usage"]["requests"] >= settings["cover_limit"]:
                raise StationError("The daily artwork request limit has been reached.")
            if settings["usage"]["estimated_usd"] + 1 > settings["cover_budget"]:
                raise StationError("The remaining artwork budget target cannot reserve another request.")
            reservation = uuid4().hex
            self.db.execute("INSERT INTO image_ledger VALUES (?,?,?,?,?,?)", (reservation, today(), job_id, "reserved", 1., None))
            self.db.commit()
            api_key = self.api_key
        try:
            result = providers.generate_cover(prompt, api_key, settings["image_model"], settings["image_quality"], destination,
                                               cancel_event=self.events.get(job_id))
        except providers.ProviderError as exc:
            with self.lock:
                self.db.execute("UPDATE image_ledger SET status=?,estimate=?,request_id=? WHERE id=?",
                                ("uncertain" if exc.ambiguous else "failed", 1. if exc.ambiguous else 0., exc.request_id, reservation))
                self.db.commit()
            raise
        except Exception:
            with self.lock:
                self.db.execute("UPDATE image_ledger SET status='uncertain' WHERE id=?", (reservation,))
                self.db.commit()
            raise StationError("The artwork result is uncertain; review account usage before retrying.") from None
        estimate = result.get("estimated_cost_usd")
        ledger_status = "completed"
        if not isinstance(estimate, (int, float)) or not math.isfinite(estimate) or estimate < 0:
            estimate = 1.
            ledger_status = "uncertain"
        with self.lock:
            self.db.execute("UPDATE image_ledger SET status=?,estimate=?,request_id=? WHERE id=?", (ledger_status, estimate, result.get("request_id"), reservation))
            self.db.commit()
        return result

    def _run_job(self, identifier):
        with self.lock:
            job = self._get("jobs", identifier)
            if not job or job["status"] != "queued":
                return
            event = self.events.setdefault(identifier, threading.Event())
            song = self._get("songs", job["song_id"])
            producing = job["kind"] in {"song", "music"}
            stage = "writing" if job["kind"] == "song" else "generating" if producing else "cover"
            job.update(status="running", stage=stage, updated_at=now())
            self._save("jobs", job)
            if producing:
                song["status"] = "generating"
                self._save("songs", song)
        try:
            self._check_cancel(event)
            revision = song["revision"] + (job["kind"] == "cover")
            work = self.data_dir / "songs" / song["id"] / f"v{revision}-{identifier}"
            work.mkdir(parents=True, exist_ok=True)
            cover_path = work / "cover.png"
            if producing:
                request = dict(job["request"])
                request.update(normalize_generation_request(request.get("music"), request["duration"], legacy=True))
                # Queued jobs saved by older versions also obey today's limit.
                # Completed songs and their original metadata remain untouched.
                job["request"] = request
                with self.lock:
                    self._save("jobs", job)
                if job["kind"] == "music" or request.get("lyrics"):
                    brief = {"title": request.get("title") or "Original song",
                             "style": request.get("style") or ", ".join((request["genre"], request["vocal"], request["description"])),
                             "lyrics": request["lyrics"], "cover_prompt": song.get("cover_prompt", request["description"])}
                else:
                    brief = providers.compose_song(request["description"], request["genre"], request["vocal"], request["duration"])
                if request.get("title"):
                    brief["title"] = request["title"]
                if request.get("style"):
                    brief["style"] = request["style"]
                song.update(title=brief["title"], lyrics=brief["lyrics"], style=brief["style"], cover_prompt=brief["cover_prompt"])
                with self.lock:
                    self._save("songs", song)
                self._check_cancel(event)
                self._stage(identifier, "generating")
                audio_path = work / "audio.flac"
                start = time.monotonic()
                try:
                    music = providers.generate_music(brief["style"], brief["lyrics"], request["seed"], request["duration"], audio_path,
                                                     music=request.get("music"), cancel_event=event,
                                                     on_progress=lambda p: self._stage(identifier, "generating", prompt_id=p.get("prompt_id")))
                except providers.ProviderError as exc:
                    # Missing finish metadata is not permission to discard a
                    # successfully downloaded take or to label it complete.
                    if (exc.cancelled or not getattr(exc, "audio_path", None) or not audio_path.is_file()
                            or Path(exc.audio_path).resolve() != audio_path.resolve()):
                        raise
                    music = {"duration": getattr(exc, "duration", None), "completion": None,
                             "music": getattr(exc, "music", None), "graph": getattr(exc, "graph", None)}
                    if not isinstance(music["duration"], (int, float)) or not math.isfinite(music["duration"]) or music["duration"] <= 0:
                        raise
                song.update(duration=music["duration"], generation_time=round(time.monotonic()-start, 2))
                _apply_completion(song, music, request)
                self._check_cancel(event)
                if job["kind"] == "music":
                    original_cover = (self.data_dir / request["reuse_cover"]).resolve()
                    if not original_cover.is_relative_to(self.data_dir) or not original_cover.is_file():
                        raise StationError("The original song cover is unavailable.")
                    shutil.copy2(original_cover, cover_path)
                else:
                    song["cover_source"] = "station"
                if job["kind"] == "song" and request.get("use_cover", True):
                    self._stage(identifier, "cover")
                    try:
                        cover_result = self._cover(brief["cover_prompt"], cover_path, identifier)
                        song["cover_metadata"] = {k: v for k, v in cover_result.items() if k != "path"}
                        song["cover_source"] = "openai"
                    except (providers.ProviderError, StationError) as exc:
                        if isinstance(exc, providers.ProviderError) and exc.cancelled:
                            raise
                        song["cover_warning"] = str(exc)
                        _refresh_warning(song)
                if not cover_path.exists():
                    media.make_station_cover(song["title"], song["genre"], cover_path)
            else:
                audio_path = self.asset_path(song["id"], "audio")
                self._stage(identifier, "cover")
                cover_result = self._cover(song.get("cover_prompt") or song["description"], cover_path, identifier)
                song["cover_metadata"] = {k: v for k, v in cover_result.items() if k != "path"}
                song["cover_source"] = "openai"
                # Preserve warnings from older records whose source is unknown.
                if song.get("warning") and not any(song.get(key) for key in ("duration_warning", "cover_warning", "other_warning")):
                    song["other_warning"] = song["warning"]
                song.pop("cover_warning", None)
                _refresh_warning(song)
            self._check_cancel(event)
            self._stage(identifier, "packaging")
            packaged = media.package_song(audio_path, cover_path, song["title"], work)
            self._check_cancel(event)
            if song.get("files"):
                song.setdefault("revisions", {})[str(song["revision"])] = song["files"].copy()
            song.update(revision=revision, duration=packaged["duration"], files={
                "audio": self._relative(audio_path), "cover": self._relative(cover_path),
                "landscape": self._relative(packaged["landscape"]), "portrait": self._relative(packaged["portrait"])})
            with self.lock:
                self._save("songs", song)
                self._stage(identifier, "needs_review" if song["status"] == "needs_review" else "complete",
                            status="completed", warning=song.get("warning"))
                if producing and song["status"] == "ready" and job["request"].get("auto_queue", True):
                    self.edit_queue(song["id"], "add")
        except Exception as exc:
            cancelled = isinstance(exc, providers.ProviderError) and exc.cancelled
            message = str(exc) if isinstance(exc, (providers.ProviderError, StationError)) else "The local generation or packaging job failed. Check service health and retry manually."
            with self.lock:
                self._stage(identifier, "failed", status="cancelled" if cancelled else "failed", error=message)
                if producing:
                    current = self._get("songs", song["id"])
                    current["status"] = "cancelled" if cancelled else "failed"
                    self._save("songs", current)
        finally:
            with self.lock:
                self.events.pop(identifier, None)

    def import_audio(self, path, title, genre, description):
        info = media.probe_audio(path)
        identifier = uuid4().hex
        work = self.data_dir / "songs" / identifier / "v1-import"
        work.mkdir(parents=True)
        audio = work / ("audio" + Path(path).suffix.lower())
        shutil.copy2(path, audio)
        cover = media.make_station_cover(title, genre, work / "cover.png")
        package = media.package_song(audio, cover, title, work)
        song = {"id": identifier, "title": title, "genre": genre, "description": description, "vocal": "Imported", "created_at": now(),
                "status": "ready", "duration": info["duration"], "cover_source": "station", "revision": 1,
                "files": {"audio": self._relative(audio), "cover": self._relative(cover), "landscape": self._relative(package["landscape"]), "portrait": self._relative(package["portrait"])}}
        with self.lock:
            self._save("songs", song)
        return self._public_song(song)

    def edit_queue(self, song_id, action):
        with self.lock:
            song = self._get("songs", song_id)
            if not song or song["status"] != "ready":
                raise StationError("Only ready songs can enter the playback queue.")
            identifiers = self._state("queue")
            if action == "add":
                if song_id not in identifiers:
                    identifiers.append(song_id)
            elif song_id in identifiers:
                index = identifiers.index(song_id)
                if action == "remove":
                    identifiers.remove(song_id)
                elif action == "up" and index > 0:
                    identifiers[index-1], identifiers[index] = identifiers[index], identifiers[index-1]
                elif action == "down" and index < len(identifiers)-1:
                    identifiers[index+1], identifiers[index] = identifiers[index], identifiers[index+1]
            self._set_state("queue", identifiers)
            return {"queue": identifiers}

    def _advance_playback(self, timestamp):
        playback = self._state("playback")
        song = self._get("songs", playback["song_id"]) if playback["song_id"] else None
        if not playback["playing"] or song is None:
            return playback, song
        position = playback["position"] + max(0., timestamp - playback["started_at"])
        if position < song["duration"]:
            return {**playback, "position": position}, song
        choices = [self._get("songs", identifier) for identifier in self._state("queue")]
        choices = [s for s in choices if s and s["status"] == "ready"]
        if not choices:
            playback.update(playing=False, position=0., song_id=None, started_at=timestamp)
            self._set_state("playback", playback)
            return playback, None
        position -= song["duration"]
        ids = [s["id"] for s in choices]
        start = (ids.index(song["id"]) + 1) % len(ids) if song["id"] in ids else 0
        ordered = choices[start:] + choices[:start]
        position %= sum(s["duration"] for s in ordered)
        for song in ordered:
            if position < song["duration"]:
                break
            position -= song["duration"]
        playback.update(song_id=song["id"], position=position, started_at=timestamp)
        self._set_state("playback", playback)
        return playback, song

    def playback(self):
        with self.lock:
            timestamp = time.time()
            playback, song = self._advance_playback(timestamp)
            return {"playing": playback["playing"], "song": self._public_song(song), "position": playback["position"], "server_time": timestamp}

    def control_playback(self, action, song_id=None):
        with self.lock:
            timestamp = time.time()
            playback, song = self._advance_playback(timestamp)
            playback["started_at"] = timestamp
            identifiers = self._state("queue")
            if action == "stop":
                playback.update(playing=False, song_id=None, position=0.)
            elif action == "pause":
                playback["playing"] = False
            elif action in {"start", "restart", "play_song", "skip"}:
                if action == "play_song":
                    song = self._get("songs", song_id)
                    playback["position"] = 0.
                elif action == "skip":
                    index = (identifiers.index(song["id"]) + 1) % len(identifiers) if song and song["id"] in identifiers else 0
                    song = self._get("songs", identifiers[index]) if identifiers else None
                    playback["position"] = 0.
                elif not song:
                    song = self._get("songs", identifiers[0]) if identifiers else None
                    playback["position"] = 0.
                if not song or song["status"] != "ready":
                    raise StationError("Add a ready song to the queue first.")
                if action == "restart":
                    playback["position"] = 0.
                playback.update(playing=True, song_id=song["id"])
            else:
                raise StationError("Unknown playback action.")
            self._set_state("playback", playback)
            return self.playback()

    def start_broadcast(self, server, stream_key, orientation, confirmed):
        if not confirmed:
            raise StationError("Confirm that you want to start a public broadcast.")
        if orientation not in {"landscape", "portrait"}:
            raise StationError("Choose landscape or portrait.")
        if not stream_key or any(c.isspace() or ord(c) < 32 for c in stream_key) or any(c in stream_key for c in "?#"):
            raise StationError("Enter a valid stream key.")
        try:
            parsed = urlsplit(server)
        except ValueError:
            raise StationError("Enter a valid broadcast server.") from None
        if parsed.query or parsed.fragment:
            raise StationError("Enter the broadcast server without a query or fragment.")
        with self.lock:
            paths = [self.asset_path(identifier, orientation) for identifier in self._state("queue")]
            return self.broadcast.start(paths, server.rstrip("/") + "/" + stream_key, self.data_dir / "broadcast")

    def health(self):
        with self._health_lock:
            if self._health_cache is not None and time.monotonic() - self._health_at < 10:
                return self._health_cache
            result = {"comfy": False, "ollama": False, "ollama_model": "qwen3.5:4b", "ollama_model_available": False,
                      "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "gpu": None}
            with httpx.Client(timeout=2, trust_env=False) as client:
                try:
                    response = client.get("http://127.0.0.1:8188/system_stats")
                    response.raise_for_status()
                    result["comfy"] = True
                    result["comfy_info"] = {"version": response.json().get("system", {}).get("comfyui_version")}
                except (httpx.HTTPError, ValueError, AttributeError):
                    pass
                try:
                    response = client.get("http://127.0.0.1:11434/api/tags")
                    response.raise_for_status()
                    models = response.json().get("models", [])
                    result["ollama_model_available"] = any(m.get("name") == "qwen3.5:4b" for m in models)
                    result["ollama"] = result["ollama_model_available"]
                except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                    pass
            try:
                result_gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, check=True)
                name, used, total, utilization = result_gpu.stdout.splitlines()[0].split(",")
                result["gpu"] = {"name": name.strip(), "memory_used_mb": float(used), "memory_total_mb": float(total), "utilization_percent": float(utilization)}
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass
            self._health_at = time.monotonic()
            self._health_cache = result
            return result
