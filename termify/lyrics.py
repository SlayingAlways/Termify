"""Lyrics lookup via LRCLIB (lrclib.net) -- free, no API key, no account.

Returns plain lyrics plus, when available, time-synced lyrics (LRC), which
the UI uses for karaoke-style highlighting while the song plays.

YouTube titles are messy ("Artist - Song (Official Video) [4K]"), so the
client builds a handful of cleaned-up candidates and picks the best hit by
name similarity and duration.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
_TIMEOUT = 12
_HEADERS = {
    "User-Agent": "Termify/1.0 (personal terminal music player; lrclib.net client)",
    "Accept": "application/json",
}

# "[01:23.45]" style time tags, possibly several per line.
_TAG_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
# Bracketed junk: (Official Video), [Lyrics], {HD}
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Words that describe the upload, not the song.
_NOISE_RE = re.compile(
    r"\b(official|videoclip|video|lyric|lyrics|lyric\s+video|audio|visuali[sz]er|m/v|mv|hd|4k|8k"
    r"|full\s+song|slowed|reverb|sped\s+up|nightcore|remaster(?:ed)?|live|performance|explicit)\b",
    re.IGNORECASE,
)
_EDGE_RE = re.compile(r"^[\s\-–—|/·•]+|[\s\-–—|/·•]+$")

UNKNOWN_ARTISTS = {"", "unknown artist", "unknown", "various artists", "va"}


class Lyrics:
    """One lyrics record."""

    def __init__(self, plain: str = "", synced: list | None = None,
                 instrumental: bool = False, source_name: str = ""):
        self.plain = plain or ""
        self.synced = synced or []  # sorted list of (seconds, text)
        self.instrumental = instrumental
        self.source_name = source_name

    @property
    def has_synced(self) -> bool:
        return len(self.synced) > 1


def clean_title(title: str) -> str:
    """Strip upload noise from a YouTube title: brackets, 'official video', ..."""
    text = _BRACKETS_RE.sub(" ", title or "")
    text = _NOISE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    text = _EDGE_RE.sub("", text)
    return text.strip()


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into sorted (seconds, line) pairs."""
    out: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        stamps = _TAG_RE.findall(raw)
        if not stamps:
            continue
        content = _TAG_RE.sub("", raw).strip()
        for minutes, seconds in stamps:
            out.append((int(minutes) * 60 + float(seconds), content))
    out.sort(key=lambda item: item[0])
    dedup: list[tuple[float, str]] = []
    for item in out:
        if dedup and item == dedup[-1]:
            continue
        dedup.append(item)
    return dedup


def _artist_ok(artist: str) -> bool:
    return bool(artist) and artist.strip().lower() not in UNKNOWN_ARTISTS


def candidates(track) -> list[tuple[str, str]]:
    """(track_name, artist_name) pairs to try, best guesses first."""
    title = (track.title or "").strip()
    artist = (track.artist or "").strip()
    cleaned = clean_title(title) or title
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", title) if p.strip()]

    found: list[tuple[str, str]] = []

    def add(name: str, who: str) -> None:
        name, who = (name or "").strip(), (who or "").strip()
        pair = (name, who if _artist_ok(who) else "")
        if name and pair not in found:
            found.append(pair)

    if _artist_ok(artist):
        add(cleaned, artist)
    if len(parts) >= 2:
        add(clean_title(" ".join(parts[1:])) or " ".join(parts[1:]), clean_title(parts[0]) or parts[0])
        add(clean_title(parts[-1]) or parts[-1], artist)
    add(cleaned, artist)
    return found


class LyricsClient:
    """LRCLIB client with an in-memory cache (one lookup per track)."""

    def __init__(self):
        self._cache: dict[str, Lyrics | None] = {}

    def cache_get(self, track_id: str) -> tuple[bool, Lyrics | None]:
        """(was it looked up before, cached result)."""
        if track_id in self._cache:
            return True, self._cache[track_id]
        return False, None

    def fetch(self, track) -> Lyrics | None:
        """Blocking lookup; returns None when nothing was found."""
        hit, cached = self.cache_get(track.id)
        if hit:
            return cached
        if not (track.title and track.title.strip()):
            self._cache[track.id] = None
            return None
        try:
            result = self._lookup(track)
        except Exception:
            # network hiccup -- do not cache, so a later attempt can retry
            return None
        self._cache[track.id] = result
        return result

    # ------------------------------------------------------------- internals
    def _lookup(self, track) -> Lyrics | None:
        best = None
        best_score = None
        for name, artist in candidates(track):
            params = {"track_name": name}
            if artist:
                params["artist_name"] = artist
            for hit in self._search(params):
                score = self._score(hit, track)
                if score is None:
                    continue
                if best_score is None or score < best_score:
                    best, best_score = hit, score
            if best_score is not None and best_score <= 1.0:
                break  # close enough; no need to try the other candidates

        if best is None:
            query = " ".join(part for part in (clean_title(track.title), track.artist) if part)
            for hit in self._search({"q": query}):
                score = self._score(hit, track)
                if score is None:
                    continue
                if best_score is None or score < best_score:
                    best, best_score = hit, score

        if best is None:
            return None
        return self._to_lyrics(best)

    @staticmethod
    def _to_lyrics(hit: dict) -> Lyrics:
        synced = parse_lrc(hit.get("syncedLyrics") or "")
        plain = (hit.get("plainLyrics") or "").strip()
        if not plain and synced:
            plain = "\n".join(text for _, text in synced)
        source = f"{hit.get('artistName', '')} — {hit.get('trackName', '')}".strip(" —")
        return Lyrics(plain=plain, synced=synced,
                      instrumental=bool(hit.get("instrumental")), source_name=source)

    @staticmethod
    def _score(hit: dict, track) -> float | None:
        """Lower is better; None means "not usable"."""
        has_lyrics = bool(hit.get("plainLyrics") or hit.get("syncedLyrics"))
        if not has_lyrics and not hit.get("instrumental"):
            return None
        score = 0.0
        wanted = track.duration or 0
        got = hit.get("duration") or 0
        if wanted and got:
            diff = abs(wanted - got)
            if diff > 30:
                return None  # different recording (radio edits, hour-long mixes, ...)
            score += diff / 15.0
        else:
            score += 1.0
        if not hit.get("syncedLyrics"):
            score += 1.5  # prefer synced when both kinds are in the results
        return score

    @staticmethod
    def _search(params: dict) -> list[dict]:
        url = f"{API}/search?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=dict(_HEADERS))
        for attempt in (0, 1):
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
                return data if isinstance(data, list) else []
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    time.sleep(2.5)  # rate limited -- wait a moment, then retry once
                    continue
                raise
            except Exception:
                if attempt == 0:
                    time.sleep(0.6)  # transient network hiccup -- one quick retry
                    continue
                raise
        return []
