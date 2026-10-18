"""Small formatting helpers used across Termify."""

from __future__ import annotations


def fmt_duration(seconds) -> str:
    """Format seconds as ``0:42`` / ``1:02:03``; ``--:--`` when unknown."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "--:--"
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def shorten(text: str, width: int) -> str:
    """Trim *text* so it fits in *width* cells (adds an ellipsis when cut)."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def progress_bar(fraction: float, width: int) -> str:
    """A thin seek bar like ``──────●────────``."""
    if width <= 0:
        return ""
    fraction = max(0.0, min(1.0, fraction or 0.0))
    pos = int(round(fraction * (width - 1)))
    return "─" * pos + "●" + "─" * (width - pos - 1)


def volume_bar(volume: float, width: int = 10) -> str:
    """A blocky volume meter like ``██████░░░░``."""
    volume = max(0.0, min(1.0, volume or 0.0))
    filled = int(round(volume * width))
    return "█" * filled + "░" * (width - filled)
