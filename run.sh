#!/usr/bin/env bash
# Termify launcher for macOS and Linux.   (Windows: use run.bat)
set -e
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    echo "=== Termify first-time setup ==="
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON=python
    else
        echo "Python 3.10+ is required but was not found."
        echo "  macOS:         brew install python   (or https://www.python.org/downloads/)"
        echo "  Debian/Ubuntu: sudo apt install python3 python3-venv"
        echo "  Fedora:        sudo dnf install python3"
        echo "  Arch:          sudo pacman -S python"
        exit 1
    fi
    if ! "$PYTHON" -m venv "$VENV"; then
        echo
        echo "Could not create the virtual environment."
        echo "On Debian/Ubuntu this usually means: sudo apt install python3-venv"
        exit 1
    fi
    echo "Installing dependencies (this can take a minute)..."
    if ! "$PY" -m pip install --disable-pip-version-check -q -r requirements.txt; then
        echo
        echo "Dependency install failed. Check your internet connection and try again."
        exit 1
    fi
    echo "Setup complete."
fi

# On Linux, PortAudio comes from the system package manager (macOS bundles it).
if ! "$PY" -c "import sounddevice" >/dev/null 2>&1; then
    echo "Termify could not load the audio library (PortAudio)."
    echo "Install it and run this script again:"
    echo "  Debian/Ubuntu: sudo apt install libportaudio2"
    echo "  Fedora:        sudo dnf install portaudio"
    echo "  Arch:          sudo pacman -S portaudio"
    exit 1
fi

exec "$PY" -m termify "$@"
