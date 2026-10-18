"""Audio playback engine: stream URL -> ffmpeg -> speakers (PortAudio).

The engine plays one track at a time with pause / seek / live volume, and
notifies the UI through a callback:

    on_event("state")           playback state changed (loading/playing/paused)
    on_event("end")             track finished naturally
    on_event("failed", msg)     track could not be resolved or was interrupted

Callbacks fire on a worker thread; the UI layer marshals them to its own
thread (see TermifyApp._call_ui).
"""

from __future__ import annotations

import subprocess
import threading
import time

import imageio_ffmpeg
import numpy as np
import sounddevice as sd

from .eq import Equalizer

SAMPLE_RATE = 44100
CHANNELS = 2
FRAME_BYTES = CHANNELS * 2  # int16
CHUNK_FRAMES = 2048
LEVEL_BANDS = 16  # spectrum bars exposed to the UI
_HTTP_RE = ("http://", "https://")


class Player:
    def __init__(self, get_stream, on_event=None):
        """*get_stream*: callable(Track) -> yt.StreamInfo (may block)."""
        self._get_stream = get_stream
        self._on_event = on_event or (lambda *a: None)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._stream: sd.OutputStream | None = None
        self._stop = threading.Event()
        self._stderr = ""
        self._drain_thread: threading.Thread | None = None

        self.track = None
        self.state = "idle"  # idle | loading | playing | paused
        self.error: str | None = None
        self.volume = 0.75
        self.duration = 0.0
        self.position = 0.0
        self._seek_to: float | None = None
        self._generation = 0  # bumped by play()/stop(); stale workers exit

        self.eq = Equalizer()  # equalizer preset, applied per chunk
        self.levels: list[float] = [0.0] * LEVEL_BANDS  # live spectrum for the UI
        self._viz_window = np.hanning(CHUNK_FRAMES).astype(np.float32)
        self._viz_edges = np.geomspace(40.0, 16000.0, LEVEL_BANDS + 1)

    # ------------------------------------------------------------ public
    @property
    def playing(self) -> bool:
        return self.state in ("loading", "playing", "paused")

    def play(self, track, start: float = 0.0) -> None:
        """Start *track* (optionally at *start* seconds); stops whatever was playing."""
        self.stop(quiet=True)
        with self._lock:
            self._generation += 1
            generation = self._generation
            stop = threading.Event()
            self._stop = stop
            self.track = track
            self.state = "loading"
            self.error = None
            self.duration = float(track.duration or 0)
            self.position = float(start)
            self._seek_to = float(start) if start > 0.05 else None
            self._stderr = ""
            worker = threading.Thread(
                target=self._run, args=(generation, stop, track), name="termify-player", daemon=True
            )
        self._emit("state", generation=generation)
        worker.start()

    def toggle_pause(self) -> None:
        if self.state == "playing":
            self.state = "paused"
            self.levels = [0.0] * LEVEL_BANDS  # freeze the visualizer quietly
        elif self.state == "paused":
            self.state = "playing"
        else:
            return
        self._emit("state")

    def pause(self) -> None:
        if self.state == "playing":
            self.state = "paused"
            self.levels = [0.0] * LEVEL_BANDS
            self._emit("state")

    def set_eq(self, preset: str) -> None:
        """Switch the equalizer preset (thread-safe)."""
        self.eq.set_preset(preset)

    def seek(self, delta: float) -> None:
        """Seek by *delta* seconds relative to the current position."""
        with self._lock:
            if self.track is None or self.state not in ("playing", "paused"):
                return
            target = self.position + delta
            if self.duration:
                target = min(target, max(self.duration - 1.0, 0.0))
            target = max(target, 0.0)
            self.position = target
            self._seek_to = target
            if self._proc is not None:
                self._kill(self._proc)

    def stop(self, quiet: bool = False) -> None:
        with self._lock:
            self._generation += 1  # any running worker is now stale and will exit
            self._stop.set()
            process = self._proc
            self._seek_to = None
            self.state = "idle"
            self.position = 0.0
            self.levels = [0.0] * LEVEL_BANDS
        if process is not None:
            self._kill(process)
        if not quiet:
            self._emit("state")

    def close(self) -> None:
        self.stop(quiet=True)
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    # ------------------------------------------------------------ worker
    def _run(self, generation: int, stop: threading.Event, track) -> None:
        try:
            self._play_loop(generation, stop, track)
        except Exception as exc:  # never let the worker die silently
            self._fail(generation, f"Playback engine error: {exc}")

    def _stale(self, generation: int, stop: threading.Event) -> bool:
        """True when this worker was replaced by a newer play()/stop() call."""
        return stop.is_set() or generation != self._generation

    def _play_loop(self, generation: int, stop: threading.Event, track) -> None:
        try:
            info = self._get_stream(track)
        except Exception as exc:
            self._fail(generation, str(exc))
            return
        if self._stale(generation, stop):
            return

        with self._lock:
            if self._stale(generation, stop):
                return
            if track.duration is None and getattr(info, "duration", None):
                track.duration = int(info.duration)
                self.duration = float(info.duration)
            self.state = "playing"
        self._emit("state", generation=generation)

        base = 0.0
        frames = 0
        while not self._stale(generation, stop):
            with self._lock:
                if generation == self._generation and self._seek_to is not None:
                    base = self._seek_to
                    self._seek_to = None
                    frames = 0
                    self.position = base

            try:
                proc = self._spawn(info, base)
            except Exception as exc:
                self._fail(generation, str(exc))
                return
            if self._stale(generation, stop):
                self._kill(proc)
                return
            with self._lock:
                self._proc = proc
            self._drain_stderr(proc)

            restart = False
            while not self._stale(generation, stop):
                with self._lock:
                    if generation != self._generation:
                        break
                    seek_pending = self._seek_to is not None
                if seek_pending:
                    restart = True
                    break
                if self.state == "paused":
                    time.sleep(0.05)
                    continue
                try:
                    stream = self._ensure_stream()
                except Exception as exc:
                    self._fail(generation, str(exc))
                    return
                try:
                    chunk = proc.stdout.read(CHUNK_FRAMES * FRAME_BYTES)
                except (ValueError, OSError):
                    # pipe was torn down by a concurrent stop/seek/skip
                    chunk = b""
                if not chunk:
                    with self._lock:
                        if generation == self._generation and self._seek_to is not None:
                            restart = True
                    break
                try:
                    stream.write(self._process_chunk(chunk))
                except Exception as exc:
                    self._fail(generation, f"Audio device problem: {exc}")
                    return
                frames += len(chunk) // FRAME_BYTES
                with self._lock:
                    self.position = base + frames / SAMPLE_RATE

            self._kill(proc)
            if self._stale(generation, stop):
                return
            if restart:
                continue

            # End of stream. rc != 0 usually means a network/decoder error.
            rc = proc.returncode
            if rc not in (0, None):
                drain = self._drain_thread
                if drain is not None:
                    drain.join(timeout=1.0)  # let the stderr collector catch up
                if self._stderr.strip():
                    self._fail(generation, self._short_stderr())
                    return
            self._emit("end", generation=generation)
            return

    # ------------------------------------------------------------ helpers
    def _spawn(self, info, start: float) -> subprocess.Popen:
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error"]
        if info.url.startswith(_HTTP_RE):
            cmd += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-rw_timeout", "20000000",
            ]
        if info.headers:
            blob = "".join(f"{k}: {v}\r\n" for k, v in info.headers.items())
            cmd += ["-headers", blob]
        if start > 0.05:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += [
            "-i", info.url,
            "-vn", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._lock:
            self._stderr = ""
        return proc

    def _ensure_stream(self) -> sd.OutputStream:
        if self._stream is not None:
            return self._stream
        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_FRAMES,
                latency="low",
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise RuntimeError(f"No audio output device available ({exc})") from exc
        return self._stream

    def _process_chunk(self, chunk: bytes) -> np.ndarray:
        """PCM bytes -> EQ -> volume -> (int16 frames, channels), + spectrum."""
        arr = np.frombuffer(chunk, dtype=np.int16)
        if arr.size % CHANNELS:
            arr = arr[: arr.size - arr.size % CHANNELS]
        arr = arr.reshape(-1, CHANNELS)  # sounddevice wants (frames, channels)
        vol = self.volume
        eq_on = self.eq.preset != "flat"
        if not eq_on and abs(vol - 1.0) <= 0.001 and vol > 0:
            self._update_levels(arr)
            return arr
        if vol <= 0.0:
            self.levels = [0.0] * LEVEL_BANDS
            return np.zeros_like(arr, dtype=np.int16)
        data = arr.astype(np.float32)
        if eq_on:
            data = self.eq.process(data)
        if abs(vol - 1.0) > 0.001:
            data = data * vol
        out = np.clip(data, -32768, 32767).astype(np.int16)
        self._update_levels(out)
        return out

    def _update_levels(self, arr: np.ndarray) -> None:
        """Per-band RMS -> 0..1 spectrum bars for the visualizer (log scale)."""
        try:
            frames = arr.shape[0]
            if frames < 64:
                return
            mono = arr.mean(axis=1).astype(np.float32) / 32768.0
            spec = np.abs(np.fft.rfft(mono * self._viz_window[:frames]))
            freqs = np.fft.rfftfreq(frames, 1.0 / SAMPLE_RATE)
            levels = self.levels
            ref = max(frames / 4.0, 1.0)  # a full-scale sine peaks near frames/4
            for i in range(LEVEL_BANDS):
                lo, hi = self._viz_edges[i], self._viz_edges[i + 1]
                band = spec[(freqs >= lo) & (freqs < hi)]
                value = 0.0
                if band.size:
                    rms = float(np.sqrt(np.mean(band * band))) / ref
                    db = 20.0 * np.log10(rms + 1e-9)
                    value = min(max((db + 60.0) / 60.0, 0.0), 1.0)
                levels[i] = value if value > levels[i] else levels[i] * 0.72
        except Exception:
            pass  # a cosmetic feature must never break playback

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        def work():
            try:
                stream = proc.stderr
                if stream is None:
                    return
                data = stream.read()
            except Exception:
                return  # process torn down while we were reading; nothing to collect
            if data:
                with self._lock:
                    self._stderr = data.decode("utf-8", "replace")[-1000:]

        self._drain_thread = threading.Thread(target=work, daemon=True)
        self._drain_thread.start()

    def _short_stderr(self) -> str:
        lines = [ln.strip() for ln in self._stderr.strip().splitlines() if ln.strip()]
        tail = lines[-1] if lines else "stream interrupted"
        return tail[:300]

    def _kill(self, proc: subprocess.Popen) -> None:
        """Stop ffmpeg. Never touch its pipes — reader threads may still hold them."""
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:
            pass

    def _fail(self, generation: int, message: str) -> None:
        with self._lock:
            if generation != self._generation:
                return  # a newer track took over; failure is no longer relevant
            self.state = "error"
            self.error = message
        self._emit("state")
        self._emit("failed", message)

    def _emit(self, event: str, arg=None, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return  # never report events from a replaced track
        try:
            self._on_event(event, arg)
        except Exception:
            pass
