"""Real-audio test of the playback engine (ffmpeg + PortAudio).

Generates a 4-second sine WAV with ffmpeg, then plays/pauses/seeks it through
the real Player.  You may briefly hear a quiet tone — that means it works.

    .venv\\Scripts\\python.exe tests\\player_engine.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import imageio_ffmpeg

from termify.player import Player
from termify.yt import StreamInfo, Track

DURATION = 4


def make_wav(path: str) -> None:
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}", "-ac", "2", path],
        check=True,
        capture_output=True,
    )


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="termify-audio-")
    wav = os.path.join(tmp, "sine.wav")
    make_wav(wav)

    events = []
    player = Player(
        get_stream=lambda track: StreamInfo(url=wav),
        on_event=lambda event, arg=None: events.append((event, arg)),
    )
    player.volume = 0.15
    track = Track(id="local", title="Sine Test", artist="Termify", duration=DURATION, url=wav)
    player.play(track)

    t0 = time.time()
    while player.state != "playing" and time.time() - t0 < 10:
        time.sleep(0.05)
    assert player.state == "playing", f"never started: state={player.state} error={player.error}"

    time.sleep(1.0)
    pos1 = player.position
    assert 0.4 < pos1 < 2.2, f"position not advancing: {pos1}"

    player.toggle_pause()
    assert player.state == "paused"
    time.sleep(0.6)
    pos2 = player.position
    assert abs(pos2 - pos1) < 0.3, f"position moved while paused: {pos1} -> {pos2}"

    player.toggle_pause()
    assert player.state == "playing"
    player.seek(1.0)
    time.sleep(0.6)
    pos3 = player.position
    assert pos3 > pos2 + 0.8, f"seek did not jump forward: {pos2} -> {pos3}"

    t0 = time.time()
    while not any(e[0] == "end" for e in events) and time.time() - t0 < 10:
        time.sleep(0.1)
    ended = any(e[0] == "end" for e in events)
    failed = [e for e in events if e[0] == "failed"]
    player.close()

    print(f"state transitions seen: {[e[0] for e in events]}")
    print(f"pos after 1s: {pos1:.2f}s | paused at {pos2:.2f}s | after seek: {pos3:.2f}s")
    if failed:
        print(f"engine reported failure: {failed}")
        return 1
    if not ended:
        print("track never reached its natural end")
        return 1
    print("PASS  play / pause / resume / seek / natural end all work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
