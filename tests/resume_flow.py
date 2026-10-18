"""Resume-on-start flow test with fake I/O.

Run 1: play a track, move the position, quit. Run 2 (same data dir): the queue
and position must come back, and pressing space must resume from that spot.

    .venv\\Scripts\\python.exe tests\\resume_flow.py
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
from termify.yt import StreamInfo, Track

SEED = Track(id="aaa", title="Long Song", artist="Patient Band", duration=400, url="https://youtu.be/aaa")
RESUME_AT = 75.5


class FakeYouTube:
    def search(self, query, limit=25):
        return [SEED] if query.startswith("http") else []

    def lookup(self, url):
        return SEED

    def related(self, track, limit=20):
        return []

    def mood_search(self, mood, limit=20):
        return []

    def resolve(self, track):
        return StreamInfo(url="http://example.invalid/stream")

    def prefetch(self, track):
        pass

    def download(self, track, dest_dir):  # pragma: no cover - unused here
        raise RuntimeError("no downloads in this test")


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


def drive_first(inp, app):
    """Play the track, wind the position forward, quit."""
    time.sleep(0.4)
    inp.send_text("/https://youtu.be/aaa\r")
    time.sleep(1.2)
    inp.send_text("\r")
    time.sleep(0.6)

    def wind():
        app.player.position = RESUME_AT
        app._save_session()

    app._call_ui(wind)
    time.sleep(0.4)
    inp.send_text("q")
    time.sleep(0.3)


def drive_second(inp, app):
    """Press space to resume, then quit."""
    time.sleep(0.5)
    inp.send_text(" ")
    time.sleep(0.5)
    inp.send_text("q")
    time.sleep(0.3)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="termify-resume-")

    with create_pipe_input() as inp:
        app1 = TermifyApp(input=inp, output=DummyOutput(), data_dir=tmp)
        app1.yt, app1.player = FakeYouTube(), FakePlayer()
        threading.Thread(target=drive_first, args=(inp, app1), daemon=True).start()
        app1.run()

    resumed_at_start = app1.player.position > 0

    with create_pipe_input() as inp:
        app2 = TermifyApp(input=inp, output=DummyOutput(), data_dir=tmp)
        fresh_player = FakePlayer()
        app2.yt, app2.player = FakeYouTube(), fresh_player
        restored_index = app2._resume_index
        restored_position = app2._resume_position
        threading.Thread(target=drive_second, args=(inp, app2), daemon=True).start()
        app2.run()

    queue_ids = [t.id for t in app2.views["queue"].tracks]
    checks = [
        ("session file written on run 1", os.path.exists(os.path.join(tmp, "session.json"))),
        ("run 1 played the track", len(app1.player.played) == 1),
        ("run 1 position was wound forward", resumed_at_start),
        ("run 2 restored the queue", queue_ids == ["aaa"]),
        ("run 2 restored the resume index", restored_index == 0),
        ("run 2 restored the resume position", abs(restored_position - RESUME_AT) < 0.01),
        ("space resumed playback", len(fresh_player.played) == 1),
        ("playback started at the saved position", abs(fresh_player.starts[0] - RESUME_AT) < 0.01),
        ("resume state consumed after use", app2._resume_index == -1 and app2._resume_position == 0.0),
    ]

    failed = 0
    for name, ok in checks:
        print(("PASS  " if ok else "FAIL  ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
