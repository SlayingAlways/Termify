"""Local persistence: liked songs, listening history, settings.

Everything is plain JSON inside the ``data/`` folder next to the project, so
it is easy to back up, edit, or delete.
"""

from __future__ import annotations

import glob
import json
import os
import threading
import time

from .yt import Track

DATA_DIR = os.environ.get("TERMIFY_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)

_MAX_RECENT = 100
_MAX_PLAYS = 40000


class Store:
    def __init__(self, data_dir: str = DATA_DIR):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()
        self.liked: list[Track] = self._load_tracks("liked.json")
        self.recent: list[Track] = self._load_tracks("recent.json")
        self.playlists: dict[str, list[Track]] = self._load_playlists()
        self.downloads: list[Track] = self._load_tracks("downloads.json")
        self.config: dict = self._load_json("config.json", {})
        self._liked_ids = {t.id for t in self.liked}
        self._download_ids = {t.id for t in self.downloads}
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ io
    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _load_json(self, name: str, default):
        try:
            with open(self._path(name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _save_json(self, name: str, value) -> None:
        try:
            tmp = self._path(name) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path(name))
        except OSError:
            pass  # likes not saving is not worth crashing the player over

    def _load_tracks(self, name: str) -> list[Track]:
        raw = self._load_json(name, [])
        tracks = []
        for item in raw if isinstance(raw, list) else []:
            try:
                tracks.append(Track.from_dict(item))
            except Exception:
                continue
        return tracks

    def _load_playlists(self) -> dict[str, list[Track]]:
        raw = self._load_json("playlists.json", {})
        playlists: dict[str, list[Track]] = {}
        for name, items in (raw or {}).items():
            tracks = []
            for item in items if isinstance(items, list) else []:
                try:
                    tracks.append(Track.from_dict(item))
                except Exception:
                    continue
            playlists[str(name)] = tracks
        return playlists

    # --------------------------------------------------------------- liked
    def is_liked(self, track: Track) -> bool:
        return track.id in self._liked_ids

    def toggle_like(self, track: Track) -> bool:
        """Like/unlike *track*. Returns True when it was added."""
        with self._lock:
            if track.id in self._liked_ids:
                self.liked = [t for t in self.liked if t.id != track.id]
                self._liked_ids.discard(track.id)
                added = False
            else:
                self.liked.insert(0, track)
                self._liked_ids.add(track.id)
                added = True
            self._save_json("liked.json", [t.to_dict() for t in self.liked])
            return added

    # ----------------------------------------------------------- playlists
    def playlist_names(self) -> list[str]:
        return list(self.playlists.keys())

    def create_playlist(self, name: str) -> bool:
        """Create an empty playlist. False when the name already exists."""
        name = (name or "").strip()
        if not name or name in self.playlists:
            return False
        with self._lock:
            self.playlists[name] = []
            self._save_playlists()
        return True

    def delete_playlist(self, name: str) -> bool:
        with self._lock:
            if name not in self.playlists:
                return False
            del self.playlists[name]
            self._save_playlists()
        return True

    def add_to_playlist(self, name: str, track: Track) -> bool:
        """Add *track* to *name* (creating it if needed). False when already there."""
        name = (name or "").strip()
        if not name:
            return False
        with self._lock:
            playlist = self.playlists.setdefault(name, [])
            if any(t.id == track.id for t in playlist):
                return False
            playlist.append(track)
            self._save_playlists()
        return True

    def remove_from_playlist(self, name: str, track: Track) -> bool:
        with self._lock:
            playlist = self.playlists.get(name)
            if not playlist:
                return False
            before = len(playlist)
            self.playlists[name] = [t for t in playlist if t.id != track.id]
            changed = len(self.playlists[name]) != before
            if changed:
                self._save_playlists()
            return changed

    def _save_playlists(self) -> None:
        self._save_json(
            "playlists.json",
            {name: [t.to_dict() for t in tracks] for name, tracks in self.playlists.items()},
        )

    # -------------------------------------------------------------- recent
    def add_recent(self, track: Track) -> None:
        with self._lock:
            self.recent = [t for t in self.recent if t.id != track.id]
            self.recent.insert(0, track)
            del self.recent[_MAX_RECENT:]
            self._save_json("recent.json", [t.to_dict() for t in self.recent])

    def remove_recent(self, track: Track) -> None:
        with self._lock:
            self.recent = [t for t in self.recent if t.id != track.id]
            self._save_json("recent.json", [t.to_dict() for t in self.recent])

    # ----------------------------------------------------------- downloads
    @property
    def cache_dir(self) -> str:
        return os.path.join(self.dir, "cache")

    def is_downloaded(self, track: Track) -> bool:
        return track.id in self._download_ids

    def add_download(self, track: Track) -> bool:
        """Mark *track* as saved offline. False when it was already there."""
        with self._lock:
            if track.id in self._download_ids:
                return False
            self.downloads.insert(0, track)
            self._download_ids.add(track.id)
            self._save_json("downloads.json", [t.to_dict() for t in self.downloads])
        return True

    def remove_download(self, track: Track) -> bool:
        path = self.download_path(track)
        with self._lock:
            if track.id not in self._download_ids:
                return False
            self.downloads = [t for t in self.downloads if t.id != track.id]
            self._download_ids.discard(track.id)
            self._save_json("downloads.json", [t.to_dict() for t in self.downloads])
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        return True

    def download_path(self, track: Track) -> str | None:
        """The cached audio file for *track*, if it exists on disk."""
        if track.id not in self._download_ids:
            return None
        matches = glob.glob(os.path.join(self.cache_dir, glob.escape(track.id) + ".*"))
        matches = [m for m in matches if not m.endswith(".part") and not m.endswith(".ytdl")]
        return matches[0] if matches else None

    def downloads_size(self) -> int:
        total = 0
        for track in self.downloads:
            path = self.download_path(track)
            if path:
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return total

    # ------------------------------------------------------------ play log
    def log_play(self, track: Track) -> None:
        """Append one play record (used by the stats screen)."""
        record = {
            "ts": time.time(),
            "id": track.id,
            "title": track.title,
            "artist": track.artist,
            "duration": track.duration or 0,
        }
        with self._lock:
            path = self._path("plays.jsonl")
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                return
            try:
                if os.path.getsize(path) > 4_000_000:  # keep the log from growing forever
                    self._trim_plays(path)
            except OSError:
                pass

    def _trim_plays(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return
        if len(lines) > _MAX_PLAYS // 2:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-_MAX_PLAYS // 2 :])
            except OSError:
                pass

    def load_plays(self) -> list[dict]:
        plays = []
        try:
            with open(self._path("plays.jsonl"), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        plays.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        return plays

    # -------------------------------------------------------------- session
    def save_session(self, data: dict) -> None:
        with self._lock:
            self._save_json("session.json", data)

    def load_session(self) -> dict | None:
        data = self._load_json("session.json", None)
        return data if isinstance(data, dict) else None

    # -------------------------------------------------------------- config
    def save_config(self) -> None:
        with self._lock:
            self._save_json("config.json", self.config)
