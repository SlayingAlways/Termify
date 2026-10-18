"""Command line entry point.

    python -m termify                 start the player
    python -m termify lofi beats      start and search right away
    python -m termify --diag          check the whole pipeline (network, ffmpeg, audio)
    python -m termify --help
"""

from __future__ import annotations

import sys

HELP = """Termify — a Spotify-style music player for your terminal.

Usage:
  python -m termify              start the player
  python -m termify <query>      start and search immediately
  python -m termify --ascii      force plain-text (ASCII) mode
  python -m termify --diag       run a self-check of the whole audio pipeline
  python -m termify --version

Inside the app, press ? for the key map.
"""


def _set_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _detect_ascii() -> bool:
    """True when the terminal is clearly not UTF-8 capable (old Windows consoles)."""
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return bool(encoding) and "utf" not in encoding


def main(argv=None) -> int:
    ascii_auto = _detect_ascii()
    _set_utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if argv and argv[0] == "--version":
        from . import __version__

        print(f"Termify {__version__}")
        return 0
    if argv and argv[0] in ("--diag", "--check"):
        return diagnostics()

    force_ascii = False
    if argv and argv[0] == "--ascii":
        force_ascii = True
        argv = argv[1:]
    query = " ".join(argv) if argv else None

    try:
        from .app import TermifyApp
    except ImportError as exc:
        print(f"Termify could not start: missing dependency ({exc}).\n")
        print("Set everything up with one of these:")
        print("  Windows:      run.bat")
        print("  macOS/Linux:  ./run.sh")
        print("  or manually:  pip install -r requirements.txt")
        return 1

    ascii_mode = True if (force_ascii or ascii_auto) else None
    app = TermifyApp(query=query, ascii_mode=ascii_mode)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.player.close()
    return 0


def diagnostics() -> int:
    import subprocess

    from . import __version__

    print(f"Termify {__version__} — diagnostics")
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    ok = True

    try:
        import yt_dlp

        print(f"yt-dlp: {yt_dlp.version.__version__}")
    except Exception as exc:
        ok = False
        print(f"yt-dlp: MISSING ({exc})")

    try:
        import numpy

        print(f"numpy: {numpy.__version__}")
    except Exception as exc:
        ok = False
        print(f"numpy: MISSING ({exc})")

    try:
        import imageio_ffmpeg

        print(f"ffmpeg: {imageio_ffmpeg.get_ffmpeg_exe()}")
    except Exception as exc:
        ok = False
        print(f"ffmpeg: MISSING ({exc})")

    try:
        import numpy as np
        import sounddevice as sd

        devices = sd.query_devices()
        try:
            default_out = sd.query_devices(kind="output")["name"]
        except Exception:
            default_out = "none"
        print(f"audio devices: {len(devices)} found, default output: {default_out}")
        stream = sd.OutputStream(samplerate=44100, channels=2, dtype="int16", latency="low")
        stream.start()
        stream.write(np.zeros((2205, 2), dtype=np.int16))  # 50 ms of silence
        stream.close()
        print("audio output: OK (silent test write)")
    except Exception as exc:
        ok = False
        print(f"audio output: FAILED ({exc})")
        if "portaudio" in str(exc).lower():
            print("  PortAudio is missing — install it and try again:")
            print("    Debian/Ubuntu: sudo apt install libportaudio2")
            print("    Fedora:        sudo dnf install portaudio")
            print("    Arch:          sudo pacman -S portaudio")

    try:
        from .yt import YouTube

        print("youtube search: searching for 'lofi' …")
        yt = YouTube()
        results = yt.search("lofi", limit=3)
        if not results:
            ok = False
            print("youtube search: no results (network blocked?)")
        else:
            for track in results:
                print(f"  - {track.title} [{track.artist}]")
            print("stream resolve: resolving the first result …")
            info = yt.resolve(results[0])
            print(f"  got stream URL ({info.url[:50]}…)")
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            proc = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-i", info.url,
                 "-t", "2", "-f", "s16le", "-ac", "2", "-ar", "44100", "pipe:1"],
                capture_output=True,
                timeout=90,
            )
            got = len(proc.stdout)
            print(f"  decoded {got} bytes of audio in 2 seconds")
            if got <= 0:
                ok = False
                print(f"  ffmpeg said: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    except Exception as exc:
        ok = False
        print(f"youtube: FAILED ({exc})")

    if ok:
        print("ALL GOOD — start it with `termify` (npm), run.bat (Windows) or ./run.sh (macOS/Linux).")
    else:
        print("Some checks failed — see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
