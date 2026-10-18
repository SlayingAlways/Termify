"""Offline checks for the data helpers: importer, stats, store persistence.

    .venv\\Scripts\\python.exe tests\\data_unit.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify.importer import parse_lines
from termify.stats import fmt_hours, summarize
from termify.store import Store
from termify.yt import Track

checks = []

# ---------------------------------------------------------------- importer
items = parse_lines("Artist - Song\nOther Song,Other Artist\n# comment\n\nArtist - Song")
checks.append(("importer parses 'Artist - Song' lines", items[0] == ("Artist", "Song")))
checks.append(("importer parses CSV (title,artist)", items[1] == ("Other Artist", "Other Song")))
checks.append(("importer dedupes repeated rows", len(items) == 2))
header = parse_lines("Track Name,Artist Name\nSong A,Artist B\n")
checks.append(("importer skips CSV headers", header == [("Artist B", "Song A")]))
explicit = parse_lines("Band [Explicit] - Tune (Explicit)")
checks.append(("importer strips explicit tags", explicit == [("Band", "Tune")]))
checks.append(("importer ignores empty input", parse_lines("") == []))

# ------------------------------------------------------------------- stats
now = time.time()
plays = [
    {"ts": now, "id": "a", "title": "T1", "artist": "A1", "duration": 200},
    {"ts": now - 10, "id": "a", "title": "T1", "artist": "A1", "duration": 200},
    {"ts": now - 10, "id": "b", "title": "T2", "artist": "A2", "duration": 100},
    {"ts": now - 30 * 86400, "id": "c", "title": "Old", "artist": "A3", "duration": 300},
]
all_time = summarize(plays)
week = summarize(plays, since=now - 7 * 86400)
checks.append(("stats totals all plays", all_time["plays"] == 4 and all_time["songs"] == 3))
checks.append(("stats week window filters old plays", week["plays"] == 3 and week["seconds"] == 500))
checks.append(("stats top song is most played", week["top_songs"][0][0] == "T1" and week["top_songs"][0][2] == 2))
checks.append(("stats top artist is most played", week["top_artists"][0][0] == "A1"))
checks.append(("fmt_hours reads nicely", fmt_hours(3900) == "1h 05m" and fmt_hours(125) == "2m 05s"))

# ------------------------------------------------------------------- store
tmp = tempfile.mkdtemp(prefix="termify-data-")
store = Store(tmp)
track_a = Track(id="x1", title="First", artist="Band", duration=120, url="https://y/x1")
track_b = Track(id="x2", title="Second", artist="Band", duration=120, url="https://y/x2")

checks.append(("add_download reports first save", store.add_download(track_a) is True))
checks.append(("add_download dedupes", store.add_download(track_a) is False))
checks.append(("is_downloaded works", store.is_downloaded(track_a) is True))
checks.append(("download_path is None before the file exists", store.download_path(track_a) is None))
cache_file = os.path.join(store.cache_dir, "x1.wav")
with open(cache_file, "wb") as fh:
    fh.write(b"RIFFfake")
checks.append(("download_path finds the cached file", store.download_path(track_a) == cache_file))
checks.append(("remove_download deletes the file", store.remove_download(track_a) is True and not os.path.exists(cache_file)))
checks.append(("downloads.json persists across instances", len(Store(tmp).downloads) == 0))

store.log_play(track_b)
store.log_play(track_b)
loaded = store.load_plays()
checks.append(("play log round-trips", len(loaded) == 2 and loaded[0]["id"] == "x2" and loaded[0]["duration"] == 120))

store.save_session({"index": 3, "queue": [track_a.to_dict()]})
session = Store(tmp).load_session()
checks.append(("session round-trips", session is not None and session["index"] == 3
               and session["queue"][0]["id"] == "x1"))

checks.append(("downloads_size is measurable", isinstance(store.downloads_size(), int)))

failed = 0
for name, ok in checks:
    print(("PASS  " if ok else "FAIL  ") + name)
    failed += 0 if ok else 1
print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
raise SystemExit(1 if failed else 0)
