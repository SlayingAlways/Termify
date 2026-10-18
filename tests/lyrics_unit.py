"""Offline unit checks for termify.lyrics (no network).

    .venv\\Scripts\\python.exe tests\\lyrics_unit.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify.lyrics import Lyrics, candidates, clean_title, parse_lrc


class FakeTrack:
    def __init__(self, id, title, artist, duration=None):
        self.id, self.title, self.artist, self.duration = id, title, artist, duration


def main() -> int:
    checks = []

    # --- title cleaning ----------------------------------------------------
    checks.append(("strips 'official video' junk",
                   clean_title("Adele - Skyfall (Official Lyric Video)") == "Adele - Skyfall"))
    checks.append(("strips brackets and noise words",
                   clean_title("Song Name [HD] (Lyrics)") == "Song Name"))
    checks.append(("keeps the real title",
                   clean_title("Take Me To Church") == "Take Me To Church"))

    # --- candidates ---------------------------------------------------------
    cands = candidates(FakeTrack("x", "Tom Odell - Another Love (Official Video)", "Tom Odell", 241))
    checks.append(("splits 'Artist - Song'",
                   ("Another Love", "Tom Odell") in cands))
    checks.append(("also offers the cleaned full title",
                   ("Tom Odell - Another Love", "Tom Odell") in cands))
    cands2 = candidates(FakeTrack("y", "lovely (with Khalid)", "Billie Eilish"))
    checks.append(("works without a dash",
                   cands2[0][0] == "lovely" and cands2[0][1] == "Billie Eilish"))
    cands3 = candidates(FakeTrack("z", "Some Song", "Unknown artist"))
    checks.append(("drops placeholder artists",
                   cands3 and all(artist == "" for _name, artist in cands3)))

    # --- LRC parsing ---------------------------------------------------------
    lrc = "[00:21.06] I wanna take you somewhere\n[00:25.63] But it is cold\n[00:25.63] But it is cold\n"
    parsed = parse_lrc(lrc)
    checks.append(("parses timestamps in order",
                   [round(t, 2) for t, _ in parsed] == [21.06, 25.63]))
    checks.append(("drops duplicate lines", len(parsed) == 2))
    checks.append(("content is stripped of the tag",
                   parsed[0][1] == "I wanna take you somewhere"))
    multi = parse_lrc("[00:10.00][00:20.00] repeated chorus\n")
    checks.append(("handles multiple tags per line", [round(t) for t, _ in multi] == [10, 20]))
    checks.append(("ignores plain text without tags", parse_lrc("no tags here") == []))

    # --- Lyrics helper -------------------------------------------------------
    checks.append(("has_synced needs 2+ lines", not Lyrics(plain="x", synced=[]).has_synced
                   and Lyrics(plain="x", synced=[(0.0, "a"), (1.0, "b")]).has_synced))

    failed = 0
    for name, ok in checks:
        print(("PASS  " if ok else "FAIL  ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
