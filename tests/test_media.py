"""Media integration tests; every broadcast target here is a local temporary file."""

import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from station import media


class MediaTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        self.fail("Timed out waiting for a local broadcast state change")

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="boogie media 'test-")
        cls.root = Path(cls.directory.name)
        cls.audio = cls.root / "original song.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-ar", "44100", "-ac", "1", str(cls.audio)],
            check=True, capture_output=True, timeout=20,
        )
        cls.cover = media.make_station_cover("Test %{literal}: song's title", "Country / pop", cls.root / "cover.png")
        cls.original_audio = cls.audio.read_bytes()
        cls.original_cover = cls.cover.read_bytes()
        cls.package = media.package_song(cls.audio, cls.cover, "Test %{literal}: song's title", cls.root / "rendered")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_probe_rejects_invalid_media_and_reads_audio(self):
        data = media.probe_audio(self.audio)
        self.assertAlmostEqual(data["duration"], 2, places=2)
        self.assertEqual(data["sample_rate"], 44100)
        self.assertEqual(data["channels"], 1)
        bad = self.root / "bad.wav"
        bad.write_text("This is not audio")
        with self.assertRaises(media.MediaError):
            media.probe_audio(bad)
        with self.assertRaises(media.MediaError):
            media.probe_audio(self.cover)

    def test_packages_match_audio_and_preserve_originals(self):
        self.assertEqual(self.audio.read_bytes(), self.original_audio)
        self.assertEqual(self.cover.read_bytes(), self.original_cover)
        self.assertAlmostEqual(self.package["duration"], 2, places=2)
        for layout, size in (("landscape", (1280, 720)), ("portrait", (720, 1280))):
            path = Path(self.package[layout])
            streams = media._probe(path)["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            audio = next(s for s in streams if s["codec_type"] == "audio")
            self.assertEqual((video["width"], video["height"]), size)
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual(video["r_frame_rate"], "24/1")
            self.assertEqual(audio["codec_name"], "aac")
            self.assertEqual(audio["sample_rate"], "48000")
            self.assertEqual(audio["channels"], 2)
            self.assertAlmostEqual(media.probe_audio(path)["duration"], 2, delta=.1)
        self.assertFalse(list((self.root / "rendered").glob(".package-*")))
        self.assertFalse(list((self.root / "rendered").rglob("title.txt")))

    def test_fallback_is_valid_png(self):
        with Image.open(self.cover) as image:
            self.assertEqual(image.size, (1024, 1024))
            self.assertEqual(image.format, "PNG")

    def test_failed_package_does_not_publish_partial_output(self):
        original_run = subprocess.run
        render_count = 0

        def fail_second_render(command, **kwargs):
            nonlocal render_count
            if command[0] == "ffmpeg":
                render_count += 1
                if render_count == 2:
                    return subprocess.CompletedProcess(command, 1, "", "simulated second render failure")
            return original_run(command, **kwargs)

        output = self.root / "failed-render"
        with patch("station.media.subprocess.run", side_effect=fail_second_render):
            with self.assertRaisesRegex(media.MediaError, "simulated second render failure"):
                media.package_song(self.audio, self.cover, "Fail atomically", output)
        self.assertEqual(list(output.iterdir()), [])

    def test_destination_validation_rejects_nonpublic_urls(self):
        for address in ("http://example.com/live/key", "file:///tmp/key", "rtmp://user:secret@example.com/live/key", "rtmp://example.com/live/key\n"):
            with self.subTest(address=address), self.assertRaises(media.MediaError):
                media._validate_destination(address)
        for address in ("127.0.0.1", "192.168.1.2", "::1", "169.254.169.254", "224.0.0.1"):
            with self.subTest(address=address), patch("station.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 1935))]):
                with self.assertRaises(media.MediaError):
                    media._validate_destination("rtmp://example.com/live/key")
        with patch("station.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            host, tokens = media._validate_destination("rtmps://example.com/live/SECRET?token=QUERYSECRET")
        self.assertEqual(host, "example.com")
        self.assertIn("SECRET", tokens)
        self.assertIn("QUERYSECRET", tokens)

    def test_manager_loop_to_local_file_stop_and_redaction(self):
        manager = media.BroadcastManager()
        output = self.root / "loop.flv"
        original_command = media._broadcast_command

        def local_command(playlist, destination):
            command = original_command(playlist, destination)
            command[-1] = str(output)
            command.insert(1, "-y")
            return command

        with patch("station.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            with patch("station.media._broadcast_command", side_effect=local_command):
                try:
                    status = manager.start([self.package["landscape"]], "rtmps://example.com/live/SECRET?token=QUERYSECRET", self.root / "broadcast")
                    self.assertTrue(status["running"])
                    self.assertEqual(status["destination_host"], "example.com")
                    self.assertEqual(status["playlist_mode"], "frozen")
                    with self.assertRaisesRegex(media.MediaError, "already running"):
                        manager.start([self.package["landscape"]], "rtmps://example.com/live/SECRET", self.root / "broadcast")
                    manager._append_log(manager._process, "Failure rtmps://example.com/live/SECRET?token=QUERYSECRET with SECRET and QUERYSECRET", ("QUERYSECRET", "SECRET"))
                    for _ in range(100):
                        manager._append_log(manager._process, "bounded log message " * 100, ())
                    manager._append_log(manager._process, "Failure rtmps://example.com/live/SECRET?token=QUERYSECRET with SECRET and QUERYSECRET", ("QUERYSECRET", "SECRET"))
                    self.assertNotIn("SECRET", json.dumps(manager.status()))
                    self.assertLessEqual(sum(map(len, manager.status()["logs"])), 16000)
                    time.sleep(4.8)
                    self.assertTrue(manager.status()["running"])
                finally:
                    status = manager.stop()
        self.assertFalse(status["running"])
        self.assertIsNone(status["pid"])
        self.assertNotIn("SECRET", json.dumps(status))
        self.assertFalse(list((self.root / "broadcast").glob("*.ffconcat")))
        duration = media.probe_audio(output)["duration"]
        self.assertGreater(duration, 4, "The two-second source should have looped at least twice.")
        self.assertFalse(manager.stop()["running"])

    def test_manager_rejects_empty_or_mixed_layout_playlist(self):
        manager = media.BroadcastManager()
        with self.assertRaisesRegex(media.MediaError, "at least one"):
            manager.start([], "rtmp://example.com/live/key", self.root)
        with self.assertRaisesRegex(media.MediaError, "same layout"):
            manager.start([self.package["landscape"], self.package["portrait"]], "rtmp://example.com/live/key", self.root)

    def test_manager_reconnects_failed_child_to_same_frozen_local_playlist(self):
        manager = media.BroadcastManager()
        output = self.root / "reconnected.flv"
        original_command = media._broadcast_command
        calls = []

        def fail_then_local(playlist, destination):
            calls.append((str(playlist), destination, playlist.read_text()))
            if len(calls) == 1:
                return [sys.executable, "-c", "import sys; sys.stderr.write(sys.argv[1] + '\\n'); sys.exit(23)", destination]
            command = original_command(playlist, destination)
            command[-1] = str(output)
            command.insert(1, "-y")
            return command

        destination = "rtmps://example.com/live/SUPERSECRET?token=QUERYSECRET"
        with patch("station.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), \
                patch("station.media._broadcast_command", side_effect=fail_then_local):
            try:
                manager.start([self.package["landscape"]], destination, self.root / "reconnect")
                self.wait_for(lambda: manager.status()["next_retry_at"])
                status = manager.status()
                self.assertTrue(status["running"], "The desired session must remain stoppable in backoff")
                self.assertTrue(status["reconnecting"])
                self.assertFalse(status["process_running"])
                self.assertEqual(status["retry_count"], 1)
                remaining = (datetime.fromisoformat(status["next_retry_at"]) - datetime.now(timezone.utc)).total_seconds()
                self.assertGreater(remaining, 1.5)
                self.assertLessEqual(remaining, 2)
                self.assertNotIn("SECRET", json.dumps(status))
                self.wait_for(lambda: len(calls) == 2 and manager.status()["process_running"])
                self.assertFalse(manager.status()["reconnecting"])
                self.assertIsNone(manager.status()["next_retry_at"])
                self.assertEqual(calls[0], calls[1], "Retry must reuse the same frozen playlist and destination")
                time.sleep(.8)
            finally:
                status = manager.stop()
        self.assertFalse(status["running"])
        self.assertFalse(status["process_running"])
        self.assertNotIn("SECRET", json.dumps(status))
        self.assertGreater(media.probe_audio(output)["duration"], .2)
        self.assertFalse(list((self.root / "reconnect").glob("*.ffconcat")))

    def test_stop_interrupts_exponential_backoff_and_prevents_new_child(self):
        manager = media.BroadcastManager()
        calls = []

        def fail_locally(playlist, destination):
            calls.append(str(playlist))
            return [sys.executable, "-c", "import sys; sys.stderr.write(sys.argv[1] + '\\n'); sys.exit(2)", destination]

        with patch("station.media.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), \
                patch("station.media._broadcast_command", side_effect=fail_locally):
            try:
                manager.start([self.package["landscape"]], "rtmps://example.com/live/SECRET", self.root / "backoff")
                self.wait_for(lambda: manager.status()["retry_count"] == 2)
                status = manager.status()
                remaining = (datetime.fromisoformat(status["next_retry_at"]) - datetime.now(timezone.utc)).total_seconds()
                self.assertGreater(remaining, 3.5)
                self.assertLessEqual(remaining, 4)
                with self.assertRaisesRegex(media.MediaError, "already running"):
                    manager.start([self.package["landscape"]], "rtmps://example.com/live/SECRET", self.root / "backoff")
                supervisor = manager._supervisor
                started = time.monotonic()
                status = manager.stop()
                self.assertLess(time.monotonic() - started, 1)
                self.assertFalse(supervisor.is_alive())
                self.assertFalse(status["running"])
                self.assertFalse(status["reconnecting"])
                self.assertIsNone(status["next_retry_at"])
                time.sleep(.1)
                self.assertEqual(len(calls), 2)
                self.assertNotIn("SECRET", json.dumps(status))
            finally:
                manager.stop()
        self.assertFalse(list((self.root / "backoff").glob("*.ffconcat")))


if __name__ == "__main__":
    unittest.main()
