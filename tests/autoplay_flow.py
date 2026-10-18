"""Autoplay/radio flow test with fake I/O.

Plays a single song, then verifies that autoplay:
  1. tops the queue up on its own with "related" tracks,
  2. keeps playing when the current track ends,
  3. seeds the lookup from the song that is playing.

    .venv\\Scripts\\python.exe tests\\autoplay_flow.py
"""

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from termify.app import TermifyApp
from termify.moods import detect_mood
from termify.yt import StreamInfo, Track

SEED_URL = "https://youtu.be/aaa"
SEED = Track(id="aaa", title="Sad Song", artist="Gloomy", duration=200, url=SEED_URL)
RELATED = [
    Track(id=f"r0{i}", title=f"More Sadness {i}", artist="Sad Band", duration=180, url=f"https://y/r0{i}")
    for i in range(1, 6)
]


class FakeYouTube:
    def __init__(self):
        self.related_calls = 0
        self.related_seed = None

    def search(self, query, limit=25):
        return [SEED] if query.startswith("http") else []

    def lookup(self, url):
        return SEED

    def related(self, track, limit=20):
        self.related_calls += 1
        self.related_seed = track
        return list(RELATED)

    def mood_search(self, mood, limit=20):
        return []

    def resolve(self, track):
        return StreamInfo(url="http://example.invalid/stream")

    def prefetch(self, track):
        pass


class FakePlayer:
    def __init__(self):
        self.track = None
        self.state = "idle"
        self.volume = 0.75
        self.position = 0.0
        self.duration = 0.0
        self.played = []
        self.eq = "flat"
        self.levels = [0.0] * 16

    def play(self, track, start=0.0):
        self.played.append(track)
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


def drive(inp, app):
    time.sleep(0.4)
    inp.send_text(f"/{SEED_URL}\r")   # search for the single song
    time.sleep(1.2)
    inp.send_text("\r")                # play it
    time.sleep(1.2)                    # autoplay should top the queue up itself
    for _ in range(3):                 # pretend the track reached its end
        app._call_ui(lambda: app._handle_player_event("end"))
        time.sleep(0.8)
    inp.send_text("q")
    time.sleep(0.3)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="termify-autoplay-")
    with create_pipe_input() as inp:
        app = TermifyApp(input=inp, output=DummyOutput(), data_dir=tmp)
        fake_yt, fake_player = FakeYouTube(), FakePlayer()
        app.yt = fake_yt
        app.player = fake_player

        worker = threading.Thread(target=drive, args=(inp, app), daemon=True)
        worker.start()
        app.run()

    queue_ids = [t.id for t in app.views["queue"].tracks]
    checks = [
        ("autoplay defaults to on", app.autoplay is True),
        ("mood detector says sad", detect_mood(SEED.title, SEED.artist) == "sad"),
        ("related lookup ran", fake_yt.related_calls >= 1),
        ("lookup seeded from the playing song", fake_yt.related_seed is not None and fake_yt.related_seed.id == "aaa"),
        ("queue topped up automatically", len(queue_ids) > 1),
        ("seed not duplicated", queue_ids.count("aaa") == 1),
        ("related tracks in queue", all(t.id in queue_ids for t in RELATED)),
        ("playback continued into related tracks", fake_player.track is not None
         and fake_player.track.id.startswith("r0")),
        ("played at least two tracks", len({t.id for t in fake_player.played}) >= 2),
    ]

    failed = 0
    for name, ok in checks:
        print(("PASS  " if ok else "FAIL  ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
