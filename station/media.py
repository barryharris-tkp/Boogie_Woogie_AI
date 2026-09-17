"""Local song packaging and explicit, operator-started RTMP broadcasting.

The video contains its song cover, so artwork and audio share one timeline.
Broadcasting freezes the selected playlist; queue edits take effect on restart.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont


class MediaError(ValueError):
    """An invalid media input or unsuccessful local media operation."""


def _local_file(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_file() or result.stat().st_size == 0:
        raise MediaError(f"Media file is missing or empty: {result.name}")
    if any(char in str(result) for char in "\r\n\x00"):
        raise MediaError("Media filenames cannot contain control characters.")
    return result


def _probe(path: str | Path) -> dict:
    local = _local_file(path)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-show_format", "-show_streams", "-of", "json", str(local)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError("ffprobe is unavailable or timed out.") from exc
    if result.returncode:
        raise MediaError(f"Cannot decode media file: {local.name}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned invalid media information.") from exc


def probe_audio(path: str | Path) -> dict:
    """Return a finite duration and the first audio stream's basic properties."""
    data = _probe(path)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise MediaError("The file does not contain an audio stream.")
    try:
        duration = float(audio.get("duration", data.get("format", {}).get("duration", 0)))
    except (TypeError, ValueError) as exc:
        raise MediaError("The audio duration is invalid.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("The audio must have a finite, positive duration.")
    return {
        "duration": duration,
        "codec": audio.get("codec_name"),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "format": data.get("format", {}).get("format_name"),
    }


def _font(size: int, *, bold: bool = False):
    suffix = "-Bold" if bold else ""
    for root in ("/usr/share/fonts/TTF", "/usr/share/fonts/truetype/dejavu"):
        candidate = Path(root) / f"DejaVuSans{suffix}.ttf"
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def _safe_text(value: str, limit: int = 160) -> str:
    return " ".join(str(value).replace("\x00", "").split())[:limit]


def _wrap_text(text: str, font, width: int, max_lines: int = 3) -> str:
    """Wrap even unusually long unbroken viewer-supplied titles."""
    lines: list[str] = []
    current = ""
    for word in _safe_text(text).split():
        while font.getlength(word) > width:
            if current:
                lines.append(current)
                current = ""
            split = max(1, len(word) - 1)
            while split > 1 and font.getlength(word[:split]) > width:
                split -= 1
            lines.append(word[:split])
            word = word[split:]
        if font.getlength((current + " " + word).strip()) > width and current:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return "\n".join(lines)


def make_station_cover(title: str, genre: str, output_path: str | Path) -> Path:
    """Create an atomic, local typographic fallback without calling an image API."""
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1024, 1024), "#111720")
    draw = ImageDraw.Draw(image)
    for radius in range(450, 30, -28):
        draw.ellipse((740-radius, 265-radius, 740+radius, 265+radius), outline="#243445", width=2)
    draw.ellipse((590, 115, 890, 415), fill="#e5a859")
    draw.ellipse((699, 224, 781, 306), fill="#111720")
    draw.text((76, 75), "BOOGIE WOOGIE", font=_font(30, bold=True), fill="#e5a859")
    draw.text((76, 119), "AI RADIO", font=_font(20), fill="#c5cdd6")
    title_text = _wrap_text(title or "Untitled song", _font(68, bold=True), 872, 4)
    draw.multiline_text((76, 550), title_text, font=_font(68, bold=True), fill="#f7f2e9", spacing=12)
    draw.line((76, 906, 948, 906), fill="#3c4a59", width=2)
    draw.text((76, 938), _safe_text(genre or "Original music", 64), font=_font(22), fill="#c5cdd6")
    fd, temporary = tempfile.mkstemp(prefix=".cover-", suffix=".png", dir=output.parent)
    os.close(fd)
    try:
        image.save(temporary, format="PNG")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def package_song(
    audio_path: str | Path, cover_path: str | Path, title: str, output_dir: str | Path,
) -> dict:
    """Package matching landscape/portrait MP4s and publish them atomically.

    Originals remain untouched. Both outputs have H.264 video at 24 fps and AAC
    stereo at 48 kHz. A private staging directory is renamed only after both
    videos pass probing, so callers never enqueue a partly rendered package.
    """
    audio = _local_file(audio_path)
    cover = _local_file(cover_path)
    duration = probe_audio(audio)["duration"]
    try:
        with Image.open(cover) as image:
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise MediaError("The song cover is not a valid supported image.") from exc
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".package-", dir=output))
    final = output / f"package-{uuid4().hex}"
    try:
        for layout, width, height in (("landscape", 1280, 720), ("portrait", 720, 1280)):
            font_size = 34 if layout == "landscape" else 32
            title_lines = _wrap_text(title or "Untitled song", _font(font_size), width - 100, 2)
            (staging / "title.txt").write_text(title_lines, encoding="utf-8")
            filter_graph = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111720,setsar=1,"
                f"drawbox=x=0:y=ih-125:w=iw:h=125:color=black@0.68:t=fill,"
                f"drawtext=textfile=title.txt:expansion=none:fontcolor=white:fontsize={font_size}:"
                "line_spacing=8:x=(w-text_w)/2:y=h-100,format=yuv420p"
            )
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-filter_threads", "2", "-loop", "1", "-framerate", "24",
                "-protocol_whitelist", "file,pipe", "-i", str(cover),
                "-protocol_whitelist", "file,pipe", "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-vf", filter_graph,
                "-c:v", "libx264", "-threads", "2", "-preset", "fast", "-tune", "stillimage",
                "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "24",
                "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-af", "apad", "-t", f"{duration:.6f}", "-movflags", "+faststart",
                "-map_metadata", "-1", str(staging / f"{layout}.mp4"),
            ]
            try:
                result = subprocess.run(
                    command, cwd=staging, capture_output=True, text=True,
                    timeout=min(max(duration * 6, 120), 21600), check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MediaError("Song video rendering failed or timed out.") from exc
            if result.returncode:
                detail = result.stderr.strip()[-2000:]
                raise MediaError(f"Song video rendering failed: {detail}")
            rendered = _probe(staging / f"{layout}.mp4")
            streams = rendered.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            actual_duration = probe_audio(staging / f"{layout}.mp4")["duration"]
            if (video.get("width"), video.get("height")) != (width, height) or abs(actual_duration-duration) > .15:
                raise MediaError("The rendered video did not match its song duration or dimensions.")
        (staging / "title.txt").unlink()
        os.replace(staging, final)
        return {
            "duration": duration,
            "landscape": str(final / "landscape.mp4"),
            "portrait": str(final / "portrait.mp4"),
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _validate_destination(url: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() or ord(c) < 32 for c in url):
        raise MediaError("Enter a valid public RTMP or RTMPS broadcast destination.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "rtmps" else 1935)
    except ValueError as exc:
        raise MediaError("The broadcast destination is invalid.") from exc
    if (parsed.scheme not in {"rtmp", "rtmps"} or not host or parsed.username is not None
            or parsed.password is not None or parsed.fragment or not parsed.path.strip("/")):
        raise MediaError("Use a public RTMP or RTMPS URL with a stream path and no username or password.")
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise MediaError("The broadcast destination hostname cannot be resolved.") from exc
    if not addresses:
        raise MediaError("The broadcast destination hostname cannot be resolved.")
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global or address.is_multicast:
            raise MediaError("The broadcast destination must resolve only to public IP addresses.")
    tokens = {url, unquote(url)}
    tokens.update(part for part in parsed.path.split("/") if part)
    tokens.update(unquote(part) for part in parsed.path.split("/") if part)
    tokens.update(value for _, value in parse_qsl(parsed.query) if value)
    return host, tuple(sorted(tokens, key=len, reverse=True))


def _write_concat_playlist(paths: list[Path], work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="playlist-", suffix=".ffconcat", dir=work_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write("ffconcat version 1.0\n")
        for path in paths:
            quoted = str(path.resolve()).replace("'", "'\\''")
            file.write(f"file '{quoted}'\n")
    return Path(name)


def _broadcast_command(playlist: Path, destination: str) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-stream_loop", "-1", "-re", "-f", "concat", "-safe", "0",
        "-protocol_whitelist", "file,pipe", "-i", str(playlist),
        "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
        "-flvflags", "no_duration_filesize", "-f", "flv", destination,
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BroadcastManager:
    """Maintain one authorized broadcast session until an explicit stop.

    Reconnection restarts the frozen playlist at its beginning. This watches
    process exits, not remote platform ingestion health, and does not restore
    sessions after an application restart. The destination stays in memory.
    """

    _INITIAL_RETRY_SECONDS = 2.0
    _MAX_RETRY_SECONDS = 60.0
    _HEALTHY_RESET_SECONDS = 300.0

    def __init__(self):
        self._lock = threading.RLock()
        # Serialize complete start/stop lifecycles while allowing status and
        # stderr draining during a child shutdown or supervisor join.
        self._lifecycle_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._desired = False
        self._reconnecting = False
        self._retry_count = 0
        self._next_retry_at: str | None = None
        self._playlist: Path | None = None
        self._destination_host: str | None = None
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._count = 0
        self._logs: list[str] = []

    def start(self, song_video_paths: list[str | Path], rtmp_url: str, work_dir: str | Path) -> dict:
        if not isinstance(song_video_paths, list) or not song_video_paths:
            raise MediaError("Add at least one ready song video before starting a broadcast.")
        if len(song_video_paths) > 10000:
            raise MediaError("A broadcast playlist may contain at most 10,000 videos.")
        paths = [_local_file(path) for path in song_video_paths]
        signature = None
        for path in paths:
            data = _probe(path)
            video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
            if video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
                raise MediaError("Broadcast videos must contain H.264 video and AAC audio; package the songs first.")
            current = (
                video.get("width"), video.get("height"), video.get("pix_fmt"),
                video.get("r_frame_rate"), video.get("time_base"),
                audio.get("sample_rate"), audio.get("channels"), audio.get("time_base"),
            )
            if signature is not None and signature != current:
                raise MediaError("All broadcast videos must use the same layout, frame rate, and audio format.")
            signature = current
            probe_audio(path)
        hostname, secret_tokens = _validate_destination(rtmp_url)
        with self._lifecycle_lock, self._lock:
            if self._desired:
                raise MediaError("A broadcast is already running. Stop it before changing the playlist.")
            if self._playlist:
                self._playlist.unlink(missing_ok=True)
            playlist = _write_concat_playlist(paths, Path(work_dir).expanduser().resolve())
            try:
                process = subprocess.Popen(
                    _broadcast_command(playlist, rtmp_url), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
                )
            except OSError as exc:
                playlist.unlink(missing_ok=True)
                raise MediaError("The broadcast process could not start. Check that FFmpeg is installed.") from exc
            self._process = process
            self._playlist = playlist
            self._destination_host = hostname
            self._count = len(paths)
            self._started_at = _utc_now()
            self._stopped_at = None
            self._logs = []
            self._desired = True
            self._reconnecting = False
            self._retry_count = 0
            self._next_retry_at = None
            self._stop_event = threading.Event()
            self._reader = self._start_reader(process, secret_tokens)
            self._supervisor = threading.Thread(
                target=self._supervise,
                args=(process, self._reader, playlist, rtmp_url, secret_tokens, self._stop_event),
                daemon=True, name="broadcast-supervisor",
            )
            self._supervisor.start()
            return self.status()

    def _start_reader(self, process, secret_tokens):
        reader = threading.Thread(
            target=self._collect_output, args=(process, secret_tokens),
            daemon=True, name="broadcast-log-reader",
        )
        reader.start()
        return reader

    def _supervise(self, process, reader, playlist, destination, secret_tokens, stop_event):
        delay = self._INITIAL_RETRY_SECONDS
        started = time.monotonic()
        while not stop_event.is_set():
            process.wait()
            reader.join(timeout=1)
            if time.monotonic() - started >= self._HEALTHY_RESET_SECONDS:
                delay = self._INITIAL_RETRY_SECONDS
            while not stop_event.is_set():
                with self._lock:
                    if not self._desired or stop_event.is_set():
                        return
                    self._reconnecting = True
                    self._retry_count += 1
                    self._next_retry_at = datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()
                    self._append_log(process, f"Broadcast process ended; reconnecting in {delay:g} seconds.", secret_tokens)
                # Event.wait makes Stop immediate even during the 60-second cap.
                if stop_event.wait(delay):
                    return
                with self._lock:
                    if not self._desired or stop_event.is_set():
                        return
                    try:
                        replacement = subprocess.Popen(
                            _broadcast_command(playlist, destination), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
                        )
                    except OSError:
                        self._append_log(process, "The broadcast process could not restart; another attempt will follow.", secret_tokens)
                        delay = min(delay * 2, self._MAX_RETRY_SECONDS)
                        continue
                    process = replacement
                    self._process = process
                    self._reader = reader = self._start_reader(process, secret_tokens)
                    self._reconnecting = False
                    self._next_retry_at = None
                    started = time.monotonic()
                    delay = min(delay * 2, self._MAX_RETRY_SECONDS)
                    break

    def _append_log(self, process, line: str, secret_tokens: tuple[str, ...]):
        line = re.sub(r"(?i)\brtmps?://[^\s'\"<>]+", "[broadcast destination redacted]", line)
        for token in secret_tokens:
            line = line.replace(token, "[redacted]")
        # Strip terminal control codes before returning any external program output.
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
        line = "".join(c for c in line if c == "\t" or ord(c) >= 32)[:2048]
        with self._lock:
            if process is not self._process:
                return
            self._logs.append(line)
            while len(self._logs) > 80 or sum(map(len, self._logs)) > 16000:
                self._logs.pop(0)

    def _collect_output(self, process, secret_tokens: tuple[str, ...]):
        pending = b""
        oversized = False
        try:
            while process.stderr is not None:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                for fragment in chunk.splitlines(keepends=True):
                    if not oversized:
                        pending += fragment
                        if len(pending) > 8192:
                            pending = b""
                            oversized = True
                    if fragment.endswith((b"\n", b"\r")):
                        self._append_log(process, "[oversized log line omitted]" if oversized else pending.decode("utf-8", "replace"), secret_tokens)
                        pending = b""
                        oversized = False
            if pending and not oversized:
                self._append_log(process, pending.decode("utf-8", "replace"), secret_tokens)
        finally:
            if process.stderr is not None:
                process.stderr.close()

    def stop(self) -> dict:
        with self._lifecycle_lock:
            with self._lock:
                self._desired = False
                self._stop_event.set()
                self._reconnecting = False
                self._next_retry_at = None
                process = self._process
                supervisor = self._supervisor
                reader = self._reader
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            # No lock is held while the log reader drains or supervisor exits.
            # A new start cannot enter until cleanup completes.
            if supervisor is not None:
                supervisor.join(timeout=3)
            if reader is not None:
                reader.join(timeout=2)
            with self._lock:
                if process is not None:
                    self._stopped_at = self._stopped_at or _utc_now()
                if self._playlist:
                    self._playlist.unlink(missing_ok=True)
                    self._playlist = None
                self._supervisor = None
                self._reader = None
                return self.status()

    def status(self) -> dict:
        with self._lock:
            exit_code = self._process.poll() if self._process else None
            process_running = self._process is not None and exit_code is None
            return {
                # A desired session remains stoppable throughout reconnection.
                "running": self._desired,
                "process_running": process_running,
                "reconnecting": self._desired and (self._reconnecting or not process_running),
                "retry_count": self._retry_count,
                "next_retry_at": self._next_retry_at,
                "pid": self._process.pid if process_running else None,
                "exit_code": exit_code,
                "destination_host": self._destination_host,
                "playlist_count": self._count,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "playlist_mode": "frozen",
                "queue_changes_apply": "on broadcast restart",
                "logs": list(self._logs),
            }
