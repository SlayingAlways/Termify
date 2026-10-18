"""End-to-end live test: searches YouTube and streams real audio for ~6s.

You should hear a short piece of actual music at reduced volume.  Handy for
verifying sound output on a new machine:

    .venv\\Scripts\\python.exe tests\\live_playback.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify.player import Player
from termify.yt import YouTube

QUERY = "ncs no copyright music"
SECONDS = 6


def main() -> int:
    yt = YouTube()
    print(f"searching for {QUERY!r} …")
    results = [t for t in yt.search(QUERY, limit=8) if t.duration and t.duration < 900]
    if not results:
        print("no results (network problem?)")
        return 1
    track = results[0]
    print(f"playing: {track.title} — {track.artist} ({track.duration}s)")

    events = []
    player = Player(get_stream=yt.resolve, on_event=lambda e, a=None: events.append(e))
    player.volume = 0.25
    player.play(track)

    t0 = time.time()
    while player.state != "playing" and time.time() - t0 < 20:
        time.sleep(0.05)
    if player.state != "playing":
        print(f"FAILED to start: state={player.state} error={player.error}")
        return 1
    time.sleep(SECONDS)
    print(f"after {SECONDS}s: position={player.position:.1f}s  state={player.state}  error={player.error}")
    print(f"events: {events}")
    player.close()

    if "failed" in events:
        print("engine reported a failure")
        return 1
    if player.position < 3:
        print("audio did not really flow")
        return 1
    print("PASS  real music streamed and played")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
