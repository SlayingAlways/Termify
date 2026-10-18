"""Music discovery and stream resolution.

Everything here talks to YouTube through yt-dlp, which needs **no API key**
and no account.  yt-dlp resolves a track to a direct audio-stream URL and we
hand that URL to ffmpeg for playback.

No Spotify API, no accounts, no ads.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

import yt_dlp

# googlevideo URLs expire after ~6 hours; refresh a bit earlier than that.
_STREAM_TTL = 4 * 60 * 60

_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class Track:
    id: str
    title: str
    artist: str = "Unknown artist"
    duration: int | None = None
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        return cls(
            id=str(data["id"]),
            title=data.get("title") or "Unknown title",
            artist=data.get("artist") or "Unknown artist",
            duration=data.get("duration"),
            url=data.get("url") or f"https://www.youtube.com/watch?v={data['id']}",
        )


@dataclass
class StreamInfo:
    url: str
    headers: dict = field(default_factory=dict)


class YouTube:
    """Thin yt-dlp wrapper with a small in-memory stream cache."""

    def __init__(self, logger=None):
        self._log = logger or (lambda *a, **k: None)
        self._cache: dict[str, tuple[float, StreamInfo]] = {}
        self._lock = threading.Lock()
        self._search_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "retries": 2,
            "extractor_retries": 1,
        }
        self._resolve_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "socket_timeout": 15,
            "retries": 2,
            "extractor_retries": 1,
        }

    # ------------------------------------------------------------- search
    def search(self, query: str, limit: int = 25) -> list[Track]:
        """Search YouTube. Accepts a plain query or a pasted video URL."""
        query = (query or "").strip()
        if not query:
            return []
        if _URL_RE.match(query):
            return [self.lookup(query)]

        with yt_dlp.YoutubeDL(self._search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

        tracks: list[Track] = []
        for entry in (info or {}).get("entries") or []:
            track = self._track_from_entry(entry)
            if track is not None:
                tracks.append(track)
        return tracks

    def related(self, track: Track, limit: int = 20) -> list[Track]:
        """Songs in the same vibe as *track*.

        Uses YouTube's own "mix" (the RD playlist) -- the same similarity
        signal the website's autoplay uses.  No API key involved.
        """
        url = f"https://www.youtube.com/watch?v={track.id}&list=RD{track.id}&start_radio=1"
        opts = dict(self._search_opts)
        opts["playlistend"] = limit + 8
        opts["noplaylist"] = False
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        tracks: list[Track] = []
        seen = {track.id}
        for entry in (info or {}).get("entries") or []:
            candidate = self._track_from_entry(entry)
            if candidate is not None and candidate.id not in seen:
                seen.add(candidate.id)
                tracks.append(candidate)
            if len(tracks) >= limit:
                break
        return tracks

    def mood_search(self, mood: str | None, limit: int = 20) -> list[Track]:
        """Fallback discovery: search for songs matching a mood keyword."""
        from .moods import random_query

        return self.search(random_query(mood), limit)

    def download(self, track: Track, dest_dir: str) -> str:
        """Save the best audio stream of *track* into *dest_dir*.

        Returns the path of the downloaded file.  Blocking; run it in a
        background thread from the UI.
        """
        os.makedirs(dest_dir, exist_ok=True)
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": os.path.join(dest_dir, f"{track.id}.%(ext)s"),
            "cachedir": os.path.join(dest_dir, ".ytdlp-cache"),
            "retries": 3,
            "socket_timeout": 30,
        }
        url = track.url or f"https://www.youtube.com/watch?v={track.id}"
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
        requested = (info or {}).get("requested_downloads") or []
        if requested and requested[0].get("filepath"):
            path = requested[0]["filepath"]
            if os.path.exists(path):
                return path
        for name in os.listdir(dest_dir):
            if name.startswith(track.id + ".") and not name.endswith((".part", ".ytdl")):
                return os.path.join(dest_dir, name)
        raise RuntimeError("download finished but produced no file")

    def lookup(self, url: str) -> Track:
        """Turn a single video URL into a Track (and warm the stream cache)."""
        with yt_dlp.YoutubeDL(self._resolve_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("entries"):  # someone pasted a playlist link
            info = info["entries"][0]
        if not info:
            raise RuntimeError("Could not load that URL.")
        track = self._track_from_entry(info, flat=False)
        if track is None:
            raise RuntimeError("Could not load that URL.")
        self._remember(track.id, self._stream_from_info(info))
        return track

    # -------------------------------------------------------------- resolve
    def resolve(self, track: Track) -> StreamInfo:
        """Return a direct audio URL for *track* (cached for a few hours)."""
        with self._lock:
            hit = self._cache.get(track.id)
            if hit and hit[0] > time.time():
                return hit[1]

        url = track.url or f"https://www.youtube.com/watch?v={track.id}"
        try:
            with yt_dlp.YoutubeDL(self._resolve_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(self._clean_error(exc)) from exc

        if info and info.get("entries"):
            info = info["entries"][0]
        if not info:
            raise RuntimeError("No playable stream found.")

        if track.duration is None and info.get("duration"):
            track.duration = int(info["duration"])
        if (not track.artist or track.artist == "Unknown artist") and info.get("uploader"):
            track.artist = _TOPIC_SUFFIX.sub("", info["uploader"]).strip() or track.artist

        stream = self._stream_from_info(info)
        if stream is None:
            raise RuntimeError("No playable audio stream found for this track.")
        self._remember(track.id, stream)
        return stream

    def prefetch(self, track: Track | None) -> None:
        """Warm the cache for an upcoming track, in the background."""
        if track is None:
            return
        with self._lock:
            hit = self._cache.get(track.id)
            if hit and hit[0] > time.time():
                return

        def work():
            try:
                self.resolve(track)
            except Exception:
                pass  # best effort only

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _track_from_entry(entry: dict | None, flat: bool = True) -> Track | None:
        if not entry:
            return None
        if entry.get("live_status") in ("is_live", "is_upcoming"):
            return None
        vid = entry.get("id")
        if not vid:
            return None
        artist = entry.get("uploader") or entry.get("channel") or entry.get("uploader_id") or ""
        artist = _TOPIC_SUFFIX.sub("", artist).strip() or "Unknown artist"
        url = entry.get("url") or ""
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={vid}"
        return Track(
            id=str(vid),
            title=entry.get("title") or str(vid),
            artist=artist,
            duration=int(entry["duration"]) if entry.get("duration") else None,
            url=url,
        )

    @staticmethod
    def _stream_from_info(info: dict) -> StreamInfo | None:
        url = info.get("url")
        if not url:
            best = None
            for fmt in info.get("formats") or []:
                if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                    score = (fmt.get("abr") or fmt.get("tbr") or 0, fmt.get("filesize") or 0)
                    if best is None or score > best[0]:
                        best = (score, fmt)
            if best is None:
                return None
            url = best[1]["url"]
            headers = best[1].get("http_headers") or {}
        else:
            headers = info.get("http_headers") or {}
        return StreamInfo(url=url, headers=dict(headers))

    def _remember(self, track_id: str, stream: StreamInfo | None) -> None:
        if stream is None:
            return
        with self._lock:
            self._cache[track_id] = (time.time() + _STREAM_TTL, stream)

    @staticmethod
    def _clean_error(exc: Exception) -> str:
        msg = str(exc)
        msg = re.sub(r"^ERROR:\s*", "", msg)
        first = msg.splitlines()[0] if msg else "yt-dlp could not resolve this track."
        return first[:300]
