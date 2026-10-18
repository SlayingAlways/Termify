"""Desktop notifications -- no extra dependencies.

  macOS   osascript `display notification`
  Linux   notify-send (libnotify)
  Windows a tray balloon via PowerShell

Failures are swallowed: a notification is never worth breaking playback.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def supported() -> bool:
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    if sys.platform.startswith("linux"):
        return shutil.which("notify-send") is not None
    if sys.platform.startswith("win"):
        return shutil.which("powershell") is not None or shutil.which("pwsh") is not None
    return False


def notify(title: str, body: str) -> None:
    """Fire-and-forget desktop notification (runs on a daemon thread)."""
    threading.Thread(target=_notify, args=(title, body), name="termify-notify", daemon=True).start()


def _notify(title: str, body: str) -> None:
    try:
        if sys.platform == "darwin":
            script = f'display notification {_osa(body)} with title {_osa(title)}'
            _spawn(["osascript", "-e", script])
        elif sys.platform.startswith("linux"):
            _spawn(["notify-send", "-a", "Termify", "-t", "5000", title, body])
        elif sys.platform.startswith("win"):
            _spawn(_windows_command(title, body))
    except Exception:
        pass


def _osa(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _spawn(cmd: list[str]) -> None:
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


def _windows_command(title: str, body: str) -> list[str]:
    shell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Application;"
        "$n.Visible=$true;"
        f"$n.ShowBalloonTip(4000,{_ps(title)},{_ps(body)},"
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Seconds 6;$n.Dispose()"
    )
    return [shell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]


def _ps(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"
