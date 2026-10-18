"""Regression tests for the player engine's concurrency:

1. Rapid seek/skip must never crash worker threads
   (used to raise ``ValueError: read of closed file``).
2. Skipping to a new track must never leave the old track alive
   (used to emit a phantom ``end`` event and respawn its ffmpeg, so two
   songs played over each other -- the "glitchy background song").

    .venv\\Scripts\\python.exe tests\\player_stress.py
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import imageio_ffmpeg

from termify.player import Player
from termify.yt import StreamInfo, Track


def make_wav(path: str, seconds: int = 60, freq: int = 440) -> None:
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "2", path],
        check=True, capture_output=True,
    )


def main() -> int:
    failures = []
    exceptions = []
    threading.excepthook = lambda args: exceptions.append((args.exc_type.__name__, str(args.exc_value)))

    tmp = tempfile.mkdtemp(prefix="termify-stress-")
    wa, wb = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
    make_wav(wa, freq=440)
    make_wav(wb, freq=880)

    events = []
    player = Player(
        get_stream=lambda t: StreamInfo(url=wa if t.id == "A" else wb),
        on_event=lambda e, a=None: events.append(e),
    )
    player.volume = 0.0  # silent: this test is about control flow, not sound
    track_a = Track(id="A", title="Track A", duration=60, url=wa)
    track_b = Track(id="B", title="Track B", duration=60, url=wb)

    # -- scenario 1: hammer seek/restart ----------------------------------
    player.play(track_a)
    time.sleep(0.6)
    for i in range(40):
        player.seek(5.0)  # kill ffmpeg while its reader may be mid-read
        time.sleep(0.02)
        if i % 5 == 0:
            player.play(track_a)  # stop + restart while pipes are in flight
        time.sleep(0.02)
    time.sleep(0.5)
    player.stop()
    time.sleep(0.5)
    if exceptions:
        failures.append(f"scenario 1: worker threads raised {exceptions}")
    if player.state != "idle":
        failures.append(f"scenario 1: expected idle, got {player.state}")

    # -- scenario 2: skip must retire the old track completely -------------
    spawns = []
    original_spawn = player._spawn

    def recording_spawn(info, start):
        spawns.append(info.url)
        return original_spawn(info, start)

    player._spawn = recording_spawn
    player.play(track_a)
    time.sleep(1.0)
    spawns.clear()
    events.clear()
    player.play(track_b)  # user skips mid-play
    time.sleep(1.5)
    if "end" in events:
        failures.append("scenario 2: skipping emitted a phantom 'end' event")
    if wa in spawns:
        failures.append("scenario 2: the old track respawned its ffmpeg after the skip")
    if player.track is not track_b or player.state != "playing":
        failures.append(f"scenario 2: expected B playing, got {player.track.id} / {player.state}")

    player.close()
    time.sleep(0.3)
    if exceptions:
        failures.append(f"after close: worker threads raised {exceptions}")

    if failures:
        for line in failures:
            print(f"FAIL  {line}")
        return 1
    print("PASS  scenario 1: 40 seeks + 8 restarts, zero thread exceptions, clean idle")
    print("PASS  scenario 2: skip retires the old track, no phantom end, no respawn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
