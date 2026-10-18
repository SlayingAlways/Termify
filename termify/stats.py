"""Aggregate the play log into "wrapped"-style listening stats."""

from __future__ import annotations

from collections import Counter


def summarize(plays: list[dict], since: float | None = None, top: int = 10) -> dict:
    """Turn a list of play records into totals + top songs/artists."""
    songs: Counter = Counter()
    artists: Counter = Counter()
    details: dict[str, tuple[str, str]] = {}
    seconds = 0.0
    count = 0
    for play in plays:
        try:
            ts = float(play.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if since is not None and ts < since:
            continue
        count += 1
        key = str(play.get("id") or f"{play.get('title')}|{play.get('artist')}")
        songs[key] += 1
        details.setdefault(key, (str(play.get("title") or "?"), str(play.get("artist") or "?")))
        artists[str(play.get("artist") or "Unknown artist")] += 1
        try:
            seconds += float(play.get("duration") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "plays": count,
        "seconds": seconds,
        "songs": len(songs),
        "artists": len(artists),
        "top_songs": [(details[key][0], details[key][1], n) for key, n in songs.most_common(top)],
        "top_artists": artists.most_common(top),
    }


def fmt_hours(seconds: float) -> str:
    """Human reading time: 3h 12m, 45m, 12s."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
