"""Headless smoke test for the TUI: drives the real app with fake I/O.

Runs the real TermifyApp (layout, keys, views, search, playlists, radio)
against a fake YouTube and a fake player, so no audio or network is touched.

    .venv\\Scripts\\python.exe tests\\tui_smoke.py
"""

import os
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

import termify.notify as notify_mod
from termify.app import TermifyApp
from termify.lyrics import Lyrics
from termify.yt import StreamInfo, Track

FAKE_RESULTS = [
    Track(id="aaa", title="Feel Good Inc.", artist="Gorillaz", duration=230, url="https://y/aaa"),
    Track(id="bbb", title="Song Two", artist="Artist Two", duration=180, url="https://y/bbb"),
    Track(id="ccc", title="Song Three", artist="Artist Three", duration=200, url="https://y/ccc"),
]

FAKE_RELATED = [
    Track(id="ddd", title="Happy Song One", artist="Band One", duration=210, url="https://y/ddd"),
    Track(id="eee", title="Happy Song Two", artist="Band Two", duration=190, url="https://y/eee"),
]

NOTIFY_CALLS: list[tuple[str, str]] = []


def fake_notify(title, body):
    NOTIFY_CALLS.append((title, body))


class FakeYouTube:
    def __init__(self):
        self.related_calls = 0
        self.downloads = 0

    def search(self, query, limit=25):
        return list(FAKE_RESULTS)

    def lookup(self, url):
        return FAKE_RESULTS[0]

    def related(self, track, limit=20):
        self.related_calls += 1
        return list(FAKE_RELATED)

    def mood_search(self, mood, limit=20):
        return []

    def resolve(self, track):
        return StreamInfo(url="http://example.invalid/stream")

    def prefetch(self, track):
        pass

    def download(self, track, dest_dir):
        """Produce a real (tiny, silent) wav so mp3 export can actually run."""
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{track.id}.wav")
        with wave.open(path, "wb") as fh:
            fh.setnchannels(2)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00\x00\x00" * 4410)  # 0.1 s
        self.downloads += 1
        return path


class FakeLyrics:
    """Canned lyrics: synced for 'aaa', plain-only for everything else."""

    def __init__(self):
        self.fetches = 0

    def cache_get(self, track_id):
        return False, None

    def fetch(self, track):
        self.fetches += 1
        if track.id == "aaa":
            return Lyrics(
                plain="Fallback plain text",
                synced=[(1.0, "First line of the song"), (5.0, "Second line"), (9.0, "Third line")],
                source_name="Fake — Lyrics",
            )
        return Lyrics(plain="Just plain words\nNo timings here", synced=[], source_name="Fake — Plain")


class FakePlayer:
    def __init__(self):
        self.track = None
        self.state = "idle"
        self.volume = 0.75
        self.position = 0.0
        self.duration = 0.0
        self.played = []
        self.starts = []
        self.eq = "flat"
        self.levels = [0.0] * 16

    def play(self, track, start=0.0):
        self.played.append(track)
        self.starts.append(start)
        self.track = track
        self.state = "playing"
        self.duration = float(track.duration or 0)
        self.position = float(start)

    def toggle_pause(self):
        self.state = "paused" if self.state == "playing" else "playing"

    def pause(self):
        self.state = "paused"

    def set_eq(self, preset):
        self.eq = preset

    def seek(self, delta):
        self.position = max(0.0, self.position + delta)

    def stop(self, quiet=False):
        self.state = "idle"

    def close(self):
        pass


def snapshot_body(app, bucket, key, tries=10):
    """Ask the UI thread for the rendered body and store it (for checks)."""
    result = {}

    def capture():
        try:
            result["text"] = "".join(part for _style, part in app._render_body())
            result["ok"] = True
        except Exception as exc:  # pragma: no cover - test diagnostics
            result["err"] = repr(exc)

    app._call_ui(capture)
    for _ in range(tries):
        if result:
            break
        time.sleep(0.1)
    bucket[key] = result


def snapshot_header(app, bucket, key, tries=10):
    """Same as snapshot_body but captures the header (with ASCII wrapping)."""
    result = {}

    def capture():
        try:
            frags = app._wrap_ascii(app._render_header)()
            result["text"] = "".join(frag[1] for frag in frags)
            result["ok"] = True
        except Exception as exc:  # pragma: no cover - test diagnostics
            result["err"] = repr(exc)

    app._call_ui(capture)
    for _ in range(tries):
        if result:
            break
        time.sleep(0.1)
    bucket[key] = result


def drive(inp, app, bucket, import_path):
    """Send keystrokes with pauses so async work (search, radio, lyrics) settles."""
    time.sleep(0.4)
    inp.send_text("/feel good songs\r")     # search
    time.sleep(1.2)
    inp.send_text("\r")                     # play the first result
    time.sleep(0.5)
    inp.send_text("l")                      # lyrics screen (synced lyrics here)
    time.sleep(0.8)
    snapshot_body(app, bucket, "lyrics_synced")
    inp.send_text("l")                      # close lyrics
    time.sleep(0.3)
    inp.send_text("f")                      # like it
    time.sleep(0.2)
    inp.send_text("a")                      # "add to playlist" prompt
    time.sleep(0.3)
    inp.send_text("Road Trip\r")            # new playlist, track added
    time.sleep(0.3)
    inp.send_text("5")                      # Playlists tab
    time.sleep(0.3)
    inp.send_text("\r")                     # open "Road Trip"
    time.sleep(0.3)
    inp.send_text("R")                      # radio: more in this mood
    time.sleep(1.2)
    inp.send_text("l")                      # lyrics for the new track (plain only)
    time.sleep(0.8)
    snapshot_body(app, bucket, "lyrics_plain")
    inp.send_text("\x1b")                   # Esc: close lyrics
    time.sleep(0.6)
    inp.send_text("\x1b")                   # Esc: back to the Playlists tab
    time.sleep(0.6)
    inp.send_text("c")                      # create playlist prompt
    time.sleep(0.3)
    inp.send_text("Chill\r")
    time.sleep(0.3)
    inp.send_text("j")                      # move to "Chill"
    time.sleep(0.2)
    inp.send_text("d")                      # arm delete
    time.sleep(0.3)
    inp.send_text("d")                      # confirm delete
    time.sleep(0.3)
    inp.send_text("A")                      # autoplay off
    time.sleep(0.2)
    inp.send_text("A")                      # autoplay back on
    time.sleep(0.2)
    inp.send_text("t")                      # stats screen (Wrapped)
    time.sleep(0.5)
    snapshot_body(app, bucket, "stats")
    inp.send_text("\x1b")                   # close stats
    time.sleep(0.3)
    inp.send_text("T")                      # cycle theme
    time.sleep(0.2)
    inp.send_text("u")                      # ASCII mode on
    time.sleep(0.3)
    snapshot_header(app, bucket, "ascii_header")
    inp.send_text("u")                      # back to Unicode
    time.sleep(0.2)
    inp.send_text("v")                      # visualizer off
    time.sleep(0.2)
    inp.send_text("e")                      # equalizer: bass boost
    time.sleep(0.2)
    inp.send_text("z")                      # sleep timer: 15 minutes
    time.sleep(0.3)
    snapshot_header(app, bucket, "sleep_header")
    for _ in range(5):                      # cycle back to "off"
        inp.send_text("z")
        time.sleep(0.15)
    inp.send_text("w")                      # save the current track offline
    time.sleep(1.5)
    inp.send_text("x")                      # export the selected playlist to mp3
    time.sleep(6.0)
    inp.send_text("i")                      # import prompt
    time.sleep(0.3)
    inp.send_text(import_path + "\r")       # parses the fake list, resolves it
    time.sleep(1.5)
    inp.send_text("N")                      # notifications off
    time.sleep(0.2)
    inp.send_text("N")                      # notifications back on
    time.sleep(0.2)
    inp.send_text("M")                      # mouse off
    time.sleep(0.2)
    inp.send_text("M")                      # mouse back on
    time.sleep(0.2)
    inp.send_text("?")                      # help
    time.sleep(0.2)
    inp.send_text("?")                      # close help
    time.sleep(0.2)
    inp.send_text("s")                      # shuffle on
    time.sleep(0.1)
    inp.send_text("r")                      # repeat all
    time.sleep(0.1)
    inp.send_text("+")                      # volume up
    time.sleep(0.1)
    inp.send_text("m")                      # mute
    time.sleep(0.1)
    inp.send_text("m")                      # unmute
    time.sleep(0.2)
    inp.send_text("q")                      # quit


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="termify-smoke-")
    bucket = {}
    notify_mod.notify = fake_notify  # don't fire real toasts during tests
    import_path = os.path.join(tmp, "imports.txt")
    with open(import_path, "w", encoding="utf-8") as fh:
        fh.write("One - First Song\nTwo - Second Song\n")
    with create_pipe_input() as inp:
        app = TermifyApp(input=inp, output=DummyOutput(), data_dir=tmp)
        fake_yt, fake_player, fake_lyrics = FakeYouTube(), FakePlayer(), FakeLyrics()
        app.yt = fake_yt
        app.player = fake_player
        app.lyrics_client = fake_lyrics

        threading.Thread(target=drive, args=(inp, app, bucket, import_path), daemon=True).start()
        app.run()  # should process keys and quit cleanly

    playlist = app.store.playlists.get("Road Trip", [])
    synced_shot = bucket.get("lyrics_synced", {})
    plain_shot = bucket.get("lyrics_plain", {})
    stats_shot = bucket.get("stats", {})
    ascii_shot = bucket.get("ascii_header", {})
    sleep_shot = bucket.get("sleep_header", {})
    import glob as _glob

    exported = _glob.glob(os.path.join(tmp, "exports", "**", "*.mp3"), recursive=True)
    cached = _glob.glob(os.path.join(tmp, "cache", "*.wav"))
    checks = [
        ("search produced results", len(app.views["results"].tracks) == 3),
        ("queue mirrors results", 3 <= len(app.views["queue"].tracks) <= 5),
        ("first result is playing", fake_player.played[0].id == "aaa"),
        ("track liked", app.store.is_liked(FAKE_RESULTS[0])),
        ("liked view has it", any(t.id == "aaa" for t in app.views["liked"].tracks)),
        ("recent view has it", any(t.id == "aaa" for t in app.views["recent"].tracks)),
        ("playlist created via prompt", "Road Trip" in app.store.playlists),
        ("playlist has the liked track", any(t.id == "aaa" for t in playlist)),
        ("playlist view opened", "playlist:Road Trip" in app.views),
        ("playlist view mirrors store", len(app.views["playlist:Road Trip"].tracks) == len(playlist)),
        ("radio fetched related tracks", fake_yt.related_calls == 1),
        ("radio tracks entered the queue", all(any(q.id == t.id for q in app.views["queue"].tracks)
                                                for t in FAKE_RELATED)),
        ("radio jumped to a new track", fake_player.played[-1].id in ("ddd", "eee")),
        ("lyrics fetched for both tracks", fake_lyrics.fetches == 2),
        ("synced lyrics loaded for first song", synced_shot.get("ok") is True
         and "First line of the song" in synced_shot.get("text", "")),
        ("plain lyrics loaded for the radio song", plain_shot.get("ok") is True
         and "No timings here" in plain_shot.get("text", "")),
        ("lyrics state is 'ready'", app.lyrics_state == "ready" and app.lyrics is not None
         and not app.lyrics.has_synced),
        ("lyrics screen closed at the end", app.lyrics_visible is False),
        ("second playlist created then deleted", "Chill" not in app.store.playlists),
        ("autoplay back on", app.autoplay is True),
        # --- new feature checks -------------------------------------------
        ("stats screen rendered", stats_shot.get("ok") is True
         and "TERMIFY WRAPPED" in stats_shot.get("text", "")
         and "plays" in stats_shot.get("text", "")),
        ("play log written", os.path.exists(os.path.join(tmp, "plays.jsonl"))),
        ("theme cycled to synthwave", app.theme == "synthwave"),
        ("ascii fallback header is plain text", ascii_shot.get("ok") is True
         and "Termify" in ascii_shot.get("text", "") and "│" not in ascii_shot.get("text", "")),
        ("ascii mode toggled back off", app.ascii_mode is False),
        ("visualizer off + bar shrinks", app.visualizer is False and app.bar_window.height == 2),
        ("equalizer set to bass", app.eq_preset == "bass" and fake_player.eq == "bass"),
        ("sleep timer counted down in header", sleep_shot.get("ok") is True
         and "sleep" in sleep_shot.get("text", "")),
        ("sleep timer cycled back off", app.sleep_mode == "off"),
        ("track saved offline", len(app.store.downloads) >= 1 and len(cached) >= 1),
        ("downloads view populated", len(app.views["downloads"].tracks) >= 1),
        ("mp3 export produced a file", len(exported) >= 1),
        ("import created a playlist", len(app.store.playlists.get("imports", [])) >= 1),
        ("desktop notification fired (fake)", len(NOTIFY_CALLS) >= 1),
        ("notifications toggled back on", app.notifications is True),
        ("mouse toggled back on", app.mouse is True),
        ("session saved for resume", os.path.exists(os.path.join(tmp, "session.json"))),
        # --- existing final-state checks ----------------------------------
        ("shuffle toggled on", app.shuffle is True),
        ("repeat is 'all'", app.repeat == "all"),
        ("volume increased", fake_player.volume > 0.75),
        ("fake player not muted at end", fake_player.volume > 0),
        ("liked.json written", os.path.exists(os.path.join(tmp, "liked.json"))),
        ("playlists.json written", os.path.exists(os.path.join(tmp, "playlists.json"))),
        ("config.json written", os.path.exists(os.path.join(tmp, "config.json"))),
    ]

    failed = 0
    for name, ok in checks:
        print(("PASS  " if ok else "FAIL  ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
