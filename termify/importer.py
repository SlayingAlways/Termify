"""Parse pasted / exported track lists into (artist, title) pairs.

Understands the two shapes people actually have:

    Artist - Song            (YouTube / plain text style)
    Song,Artist              (CSV exports, extra columns ignored)

Blank lines and '#' comments are skipped; duplicates are removed.
"""

from __future__ import annotations


def parse_lines(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if lowered.startswith("track name") or lowered.startswith("title,"):
            continue  # CSV header row
        artist = title = ""
        if " - " in line:
            left, right = line.split(" - ", 1)
            artist, title = left.strip(), right.strip()
        elif "," in line:
            cols = [c.strip().strip('"') for c in line.split(",")]
            cols = [c for c in cols if c]
            if len(cols) >= 2:
                title, artist = cols[0], cols[1]
        else:
            title = line
        title = _clean(title)
        artist = _clean(artist)
        if not title:
            continue
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((artist, title))
    return out


def _clean(value: str) -> str:
    text = (value or "").strip().strip('"').strip()
    for junk in ("[Explicit]", "(Explicit)", "[explicit]"):
        text = text.replace(junk, "")
    return " ".join(text.split())
