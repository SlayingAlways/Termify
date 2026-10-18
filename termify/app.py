"""Termify's terminal UI, drawn with prompt_toolkit."""

from __future__ import annotations

import os
import random
import subprocess
import threading
import time
from bisect import bisect_right

import imageio_ffmpeg
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import DynamicStyle

from . import notify
from .eq import LABELS as EQ_LABELS
from .eq import ORDER as EQ_ORDER
from .importer import parse_lines
from .lyrics import LyricsClient
from .moods import MOOD_LABELS, detect_mood
from .player import Player
from .stats import fmt_hours, summarize
from .store import Store
from .themes import THEMES, build_style, next_theme
from .util import fmt_duration, progress_bar, shorten, volume_bar
from .yt import StreamInfo, Track, YouTube

TABS = [
    ("queue", "Queue"),
    ("results", "Results"),
    ("liked", "Liked"),
    ("recent", "Recent"),
    ("playlists", "Playlists"),
    ("downloads", "Downloads"),
]
TAB_KEYS = {key: str(i) for i, (key, _) in enumerate(TABS, start=1)}

# Plain-text fallback for terminals without Unicode: every fancy glyph the UI
# can emit gets an ASCII equivalent (no gradients, no special characters).
_ASCII_MAP = str.maketrans(
    {
        "♪": "*", "♫": "*", "♨": "~", "♥": "+", "✔": "+", "🌙": "zZ",
        "▶": ">", "⏸": "||", "⬇": "v", "⬆": "^",
        "·": "-", "—": "-", "–": "-", "−": "-", "…": "...", "×": "x", "°": "deg",
        "•": "*", "✓": "+",
        "’": "'", "‘": "'", "“": "\"", "”": "\"", "′": "'", "″": "\"",
        "─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
        "█": "@", "▇": "#", "▆": "*", "▅": "+", "▄": "=", "▃": "-", "▂": ":", "▁": ".",
        "░": "-", "▒": "=", "▓": "#",
        "←": "<-", "→": "->", "\u00a0": " ",
    }
)


def _ascii(text: str) -> str:
    return text.translate(_ASCII_MAP)

HINTS = (
    "space pause · n/b next · l lyrics · t stats · R radio · w save · e eq · z sleep · "
    "/ search · ? help · q quit"
)

HELP_LINES = [
    ("/", "search for music (or paste a YouTube link)"),
    ("Enter", "play the selected track (or open a playlist)"),
    ("l", "lyrics screen (synced, karaoke-style, when available)"),
    ("t", "listening stats — top songs, top artists, total hours"),
    ("R", "radio: queue more songs in the same mood as this one"),
    ("A", "toggle autoplay — keeps the music going, mood-matched"),
    ("w", "save offline: download the selected track (whole playlist in a playlist view)"),
    ("x", "export to mp3: current selection, playlist or all downloads"),
    ("i", "import a playlist from a text/CSV file ('Artist - Song' lines)"),
    ("e", "cycle equalizer: flat, bass boost, night, vocal, club"),
    ("v", "toggle the audio visualizer"),
    ("z", "sleep timer: 15/30/60 min, after this song, after the queue"),
    ("T", "cycle color theme"),
    ("u", "plain-text (ASCII) mode on / off, for terminals without fancy glyphs"),
    ("N", "desktop notifications on / off"),
    ("M", "mouse support on / off"),
    ("a", "add the selected track to a playlist (new name creates it)"),
    ("c", "create a playlist (Playlists tab)"),
    ("d", "remove selected: queue / playlist / liked / recent / downloads"),
    ("space", "pause / resume"),
    ("n / b", "next / previous track"),
    ("← / →", "seek 10 seconds back / forward"),
    ("+ / −", "volume up / down (m mutes)"),
    ("f", "like / unlike the selected track"),
    ("s", "toggle shuffle"),
    ("r", "cycle repeat mode: off, all, one"),
    ("1-6", "tabs: Queue, Results, Liked, Recent, Playlists, Downloads"),
    ("g / G", "jump to top / bottom"),
    ("PgUp / PgDn", "move a page at a time"),
    ("Esc", "close overlay (help, lyrics, stats) / go back"),
    ("q", "quit Termify"),
]

class View:
    """One tab: its track list plus cursor + scroll position."""

    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.tracks: list = []
        self.cursor = 0
        self.offset = 0


class TermifyApp:
    def __init__(
        self,
        query: str | None = None,
        input=None,
        output=None,
        data_dir: str | None = None,
        ascii_mode: bool | None = None,
    ):
        self.store = Store() if data_dir is None else Store(data_dir)
        self.yt = YouTube()
        self.player = Player(self._get_stream, self._player_event)
        self.player.volume = min(1.0, max(0.0, float(self.store.config.get("volume", 0.75))))
        self.shuffle = bool(self.store.config.get("shuffle", False))
        self.repeat = self.store.config.get("repeat", "off")
        if self.repeat not in ("off", "all", "one"):
            self.repeat = "off"

        self.views = {key: View(key, title) for key, title in TABS}
        self.views["liked"].tracks = list(self.store.liked)
        self.views["recent"].tracks = list(self.store.recent)

        self.view_key = "queue"
        self.prev_view = "queue"
        self.help_visible = False
        self.order: list[int] = []  # queue indices in play order
        self.order_pos = -1
        self.queue_index = -1

        self.autoplay = bool(self.store.config.get("autoplay", True))
        self._topup_running = False
        self.playlists_cursor = 0
        self.playlists_offset = 0
        self._delete_confirm: tuple[str, float] | None = None

        self.lyrics_client = LyricsClient()
        self.lyrics_visible = False
        self.lyrics_state = "idle"  # idle | loading | ready | none
        self.lyrics_track_id: str | None = None
        self.lyrics = None
        self.lyrics_scroll = 0
        self.lyrics_error: str | None = None

        # look & feel
        self.theme = self.store.config.get("theme", "termify")
        if self.theme == "spotify":  # pre-brand config value
            self.theme = "termify"
        if self.theme not in THEMES:
            self.theme = "termify"
        self._styles = {name: build_style(name) for name in THEMES}
        self.visualizer = bool(self.store.config.get("visualizer", True))
        self.notifications = bool(self.store.config.get("notifications", True))
        self.notifications_supported = notify.supported()
        self._last_notify = 0.0
        self.mouse = bool(self.store.config.get("mouse", True))
        self.ascii_mode = bool(self.store.config.get("ascii", False)) if ascii_mode is None else bool(ascii_mode)

        # equalizer
        self.eq_preset = self.store.config.get("eq", "flat")
        if self.eq_preset not in EQ_ORDER:
            self.eq_preset = "flat"
        self.player.set_eq(self.eq_preset)

        # sleep timer: off | 15m | 30m | 60m | song | queue
        self.sleep_mode = "off"
        self.sleep_until = 0.0

        # offline downloads
        self.cache_dir = self.store.cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.views["downloads"].tracks = list(self.store.downloads)
        self._saving = False
        self._exporting = False
        self._importing = False

        # stats screen
        self.stats_visible = False
        self._stats_cache: tuple[float, dict] | None = None

        # session resume
        self._resume_position = 0.0
        self._resume_index = -1
        self._last_session_save = 0.0

        # One shared input line: search, playlist names, ... (mode decides)
        self.input_mode: str | None = None  # None | "search" | "new-playlist" | "add-to-playlist"
        self.input_label = " / "
        self.input_buffer = Buffer(name="input", multiline=False, accept_handler=self._accept_input)
        self.input_control = BufferControl(buffer=self.input_buffer)

        self.msg = ""
        self.msg_style = "class:msg"
        self.msg_until = 0.0
        self.consecutive_failures = 0
        self._last_volume = self.player.volume or 0.75
        self.initial_query = query
        self._restore_session()

        self.body_control = FormattedTextControl(self._wrap_ascii(self._render_body), focusable=True)
        self.body_window = Window(self.body_control, wrap_lines=False)
        self.layout = Layout(self._build_frame())
        self.app = Application(
            layout=self.layout,
            key_bindings=self._build_keys(),
            style=DynamicStyle(lambda: self._styles.get(self.theme)),
            full_screen=True,
            refresh_interval=0.25,
            mouse_support=self.mouse,
            input=input,
            output=output,
        )
        if self._resume_index >= 0:
            track = self.views["queue"].tracks[self._resume_index]
            self._say(
                f"Press space to resume · {shorten(track.title, 30)} at {fmt_duration(self._resume_position)}",
                "class:msg",
                15,
            )

    # ------------------------------------------------------------ plumbing
    def run(self) -> None:
        pre_run = None
        if self.initial_query:
            pre_run = lambda: self._start_search(self.initial_query)
        try:
            self.app.run(pre_run=pre_run)
        except TypeError:  # very old prompt_toolkit without pre_run
            self.app.run()

    def _get_stream(self, track):
        cached = self.store.download_path(track)
        if cached:  # saved offline -> instant, no network at all
            return StreamInfo(url=cached)
        return self.yt.resolve(track)

    # ------------------------------------------------------------- session
    def _restore_session(self) -> None:
        """Bring back the last queue/position so space resumes where you left off."""
        session = self.store.load_session()
        if not isinstance(session, dict):
            return
        queue = []
        for entry in session.get("queue") or []:
            if isinstance(entry, dict):
                try:
                    queue.append(Track.from_dict(entry))
                except (KeyError, TypeError):
                    continue
        if not queue:
            return
        index = max(0, min(int(session.get("index") or 0), len(queue) - 1))
        self.views["queue"].tracks = queue
        self.views["queue"].cursor = index
        self.queue_index = index
        order = session.get("order")
        if isinstance(order, list) and sorted(order) == list(range(len(queue))):
            self.order = [int(i) for i in order]
            pos = int(session.get("order_pos") or 0)
            self.order_pos = max(0, min(pos, len(self.order) - 1))
        else:
            self._refresh_order(index)
        self._resume_position = max(0.0, float(session.get("position") or 0.0))
        self._resume_index = index

    def _maybe_save_session(self) -> None:
        now = time.time()
        if now - self._last_session_save < 5:
            return
        self._last_session_save = now
        self._save_session()

    def _save_session(self) -> None:
        track = self.player.track
        if track is None or self.player.state not in ("playing", "paused", "loading"):
            return
        try:
            self.store.save_session(
                {
                    "ts": time.time(),
                    "position": float(self.player.position),
                    "index": self.queue_index,
                    "order": list(self.order),
                    "order_pos": self.order_pos,
                    "queue": [t.to_dict() for t in self.views["queue"].tracks],
                }
            )
        except Exception:
            pass

    def _player_event(self, event: str, arg=None) -> None:
        """Called from the player thread; hops over to the UI thread."""
        self._call_ui(lambda: self._handle_player_event(event, arg))

    def _call_ui(self, fn) -> None:
        try:
            self.app.loop.call_soon_threadsafe(fn)
            return
        except Exception:
            pass
        try:
            fn()
        except Exception:
            pass

    def _invalidate(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _handle_player_event(self, event: str, arg=None) -> None:
        if event == "state":
            if self.player.state == "playing":
                self.consecutive_failures = 0
            self._invalidate()
        elif event == "end":
            self._advance_auto()
        elif event == "failed":
            self.consecutive_failures += 1
            self._say(f"Track failed: {shorten(arg or 'unknown error', 70)}", "class:msg.error", 6)
            if self.consecutive_failures >= 3:
                self.player.stop(quiet=True)
                self._say("Stopped after 3 failures in a row — try another track.", "class:msg.error", 8)
            else:
                self._advance_auto(force_next=True)
        self._invalidate()

    # ------------------------------------------------------------- layout
    def _build_frame(self):
        input_bar = ConditionalContainer(
            VSplit(
                [
                    Window(
                        FormattedTextControl(lambda: [("class:prompt", self.input_label)]),
                        width=18,
                        height=1,
                    ),
                    Window(self.input_control, height=1),
                ]
            ),
            filter=Condition(lambda: self.input_mode is not None),
        )
        self.bar_window = Window(
            FormattedTextControl(self._wrap_ascii(self._render_now_playing)),
            height=3 if self.visualizer else 2,
            style="class:bar",
        )
        return HSplit(
            [
                Window(FormattedTextControl(self._wrap_ascii(self._render_header)), height=1, style="class:header"),
                self.body_window,
                self.bar_window,
                Window(FormattedTextControl(self._wrap_ascii(self._render_bottom)), height=1, style="class:help"),
                input_bar,
            ]
        )

    def _build_keys(self) -> KeyBindings:
        kb = KeyBindings()
        main = Condition(lambda: self.input_mode is None)

        kb.add("c-c")(lambda event: self._quit())
        kb.add("q", filter=main)(lambda event: self._quit())
        kb.add("escape", filter=Condition(lambda: self.input_mode is not None))(
            lambda event: self._cancel_input()
        )

        for key, delta in (("up", -1), ("k", -1), ("down", 1), ("j", 1)):
            kb.add(key, filter=main)(lambda event, d=delta: self._move_cursor(d))
        kb.add("pageup", filter=main)(lambda event: self._move_cursor(-self._page()))
        kb.add("pagedown", filter=main)(lambda event: self._move_cursor(self._page()))
        kb.add("home", filter=main)(lambda event: self._move_cursor(-10 ** 9))
        kb.add("g", filter=main)(lambda event: self._move_cursor(-10 ** 9))
        kb.add("end", filter=main)(lambda event: self._move_cursor(10 ** 9))
        kb.add("G", filter=main)(lambda event: self._move_cursor(10 ** 9))

        kb.add("enter", filter=main)(lambda event: self._play_selected())
        self._bind_space(kb, lambda event: self._toggle_pause(), main)
        kb.add("n", filter=main)(lambda event: self._next())
        kb.add("b", filter=main)(lambda event: self._prev())
        kb.add("right", filter=main)(lambda event: self._seek(10))
        kb.add("left", filter=main)(lambda event: self._seek(-10))

        for key in ("+", "="):
            kb.add(key, filter=main)(lambda event: self._adjust_volume(0.05))
        for key in ("-", "_"):
            kb.add(key, filter=main)(lambda event: self._adjust_volume(-0.05))
        kb.add("m", filter=main)(lambda event: self._toggle_mute())

        kb.add("f", filter=main)(lambda event: self._toggle_like())
        kb.add("d", filter=main)(lambda event: self._remove_selected())
        kb.add("s", filter=main)(lambda event: self._toggle_shuffle())
        kb.add("r", filter=main)(lambda event: self._cycle_repeat())
        kb.add("l", filter=main)(lambda event: self._toggle_lyrics())
        kb.add("R", filter=main)(lambda event: self._radio_now())
        kb.add("A", filter=main)(lambda event: self._toggle_autoplay())
        kb.add("a", filter=main)(lambda event: self._prompt_add_to_playlist())
        kb.add("c", filter=main)(lambda event: self._prompt_new_playlist())
        kb.add("t", filter=main)(lambda event: self._toggle_stats())
        kb.add("T", filter=main)(lambda event: self._cycle_theme())
        kb.add("u", filter=main)(lambda event: self._toggle_ascii())
        kb.add("v", filter=main)(lambda event: self._toggle_visualizer())
        kb.add("e", filter=main)(lambda event: self._cycle_eq())
        kb.add("z", filter=main)(lambda event: self._cycle_sleep())
        kb.add("w", filter=main)(lambda event: self._save_offline())
        kb.add("x", filter=main)(lambda event: self._export_mp3())
        kb.add("i", filter=main)(lambda event: self._prompt_import())
        kb.add("N", filter=main)(lambda event: self._toggle_notifications())
        kb.add("M", filter=main)(lambda event: self._toggle_mouse())

        kb.add("/", filter=main)(lambda event: self._enter_search())
        kb.add("?", filter=main)(lambda event: self._toggle_help())
        kb.add("h", filter=main)(lambda event: self._toggle_help())
        kb.add("escape", filter=main)(lambda event: self._escape())
        kb.add("tab", filter=main)(lambda event: self._cycle_view(1))
        for i, (key, _title) in enumerate(TABS):
            kb.add(str(i + 1), filter=main)(lambda event, k=key: self._tab_to(k))
        return kb

    @staticmethod
    def _bind_space(kb: KeyBindings, handler, filt) -> None:
        for name in ("space", " "):
            try:
                kb.add(name, filter=filt)(handler)
                return
            except ValueError:
                continue

    # ----------------------------------------------------------- rendering
    def _size(self):
        try:
            return self.app.output.get_size()
        except Exception:
            import shutil

            size = shutil.get_terminal_size((100, 30))

            class _S:
                columns = size.columns
                rows = size.lines

            return _S()

    def _wrap_ascii(self, render):
        """Wrap a render callable so its output goes ASCII-only in fallback mode."""

        def wrapped():
            result = render()
            if not self.ascii_mode:
                return result
            try:
                frags = []
                for frag in result:
                    if len(frag) >= 3:
                        frags.append((frag[0], _ascii(frag[1]), *frag[2:]))
                    else:
                        frags.append((frag[0], _ascii(frag[1])))
                return FormattedText(frags)
            except Exception:
                return result

        return wrapped

    def _body_rows(self) -> int:
        bar = 3 if self.visualizer else 2
        return max(3, self._size().rows - bar - 2 - (1 if self.input_mode is not None else 0))

    def _view(self) -> View:
        return self.views[self.view_key]

    def _render_header(self):
        self._tick()
        cols = self._size().columns
        frags: list = [
            ("", " "),
            ("class:brand.term", "Term"),
            ("class:brand", "ify"),
            ("class:header.dim", " │ "),
        ]
        in_playlist = self.view_key.startswith("playlist:")
        for key, title in TABS:
            active = (not self.help_visible) and (
                self.view_key == key or (key == "playlists" and in_playlist)
            )
            frags.append(
                (
                    "class:tab.active" if active else "class:tab",
                    f" [{TAB_KEYS[key]}] {title} ",
                    self._tab_mouse_handler(key),
                )
            )
            frags.append(("", " "))
        if in_playlist:
            name = self.view_key.split(":", 1)[1]
            frags.append(("class:header.dim", "│ "))
            frags.append(("class:bar.live", f" {shorten(name, 24)} "))
            frags.append(("", " "))
        right = []
        if self.shuffle:
            right.append("shuffle on")
        if self.repeat != "off":
            right.append(f"repeat {self.repeat}")
        if self.autoplay:
            right.append("autoplay on")
        if self.eq_preset != "flat":
            right.append(f"eq {EQ_LABELS.get(self.eq_preset, self.eq_preset)}")
        if self.sleep_mode in ("15m", "30m", "60m"):
            left = max(0, int(self.sleep_until - time.time()))
            right.append(f"sleep {left // 60}:{left % 60:02d}")
        elif self.sleep_mode == "song":
            right.append("sleep after this song")
        elif self.sleep_mode == "queue":
            right.append("sleep after queue")
        if self.player.state == "loading":
            right.append("loading your track…")
        right_text = " · ".join(right) + (" " if right else "")
        used = sum(len(frag[1]) for frag in frags)
        pad = max(cols - used - len(right_text) - 1, 1)
        frags.append(("", " " * pad))
        frags.append(("class:header.dim", right_text))
        return FormattedText(frags)

    def _render_body(self):
        if self.help_visible:
            return self._render_help_view()
        rows, cols = self._body_rows(), self._size().columns
        if self.stats_visible:
            return self._render_stats(rows, cols)
        if self.lyrics_visible:
            return self._render_lyrics(rows, cols)
        if self.view_key == "playlists":
            return self._render_playlists(rows, cols)
        view = self._view()
        tracks = view.tracks
        if not tracks:
            if self.view_key.startswith("playlist:"):
                empty = "This playlist is empty — find something you like and press a to add it."
            else:
                empty = {
                    "queue": "The queue is empty — press / to search, then Enter to play something.",
                    "results": "No search yet — press / and type a song or artist name.",
                    "liked": "No liked songs yet — press f on a track to add it here.",
                    "recent": "Nothing played yet — your recently played tracks will show up here.",
                    "downloads": "Nothing saved offline yet — highlight a track and press w to keep it.",
                }.get(view.key, "Nothing here yet.")
            return FormattedText([("class:empty", f"\n {empty}")])

        view.cursor = max(0, min(view.cursor, len(tracks) - 1))
        if view.cursor < view.offset:
            view.offset = view.cursor
        elif view.cursor >= view.offset + rows:
            view.offset = view.cursor - rows + 1
        view.offset = max(0, min(view.offset, max(0, len(tracks) - rows)))

        dur_w = 7
        title_w = max(cols - 6 - dur_w - 1, 10)
        frags: list = []
        for j in range(rows):
            i = view.offset + j
            if i >= len(tracks):
                frags.append(("", "\n"))
                continue
            track = tracks[i]
            current = self._is_current(track)
            selected = i == view.cursor
            if current:
                marker = "⏸ " if self.player.state == "paused" else "▶ "
            else:
                marker = "  "
            marks = ""
            if self.store.is_liked(track):
                marks += " ♥"
            if self.store.is_downloaded(track):
                marks += " ⬇"
            name = shorten(f"{track.title} · {track.artist}{marks}", title_w)
            line = f"{i + 1:>3} {marker}{name}"
            line += " " * max(cols - len(line) - dur_w, 0) + f"{fmt_duration(track.duration):>{dur_w}}"
            if selected:
                style = "class:row.selected"
            elif current:
                style = "class:row.playing"
            else:
                style = "class:row"
            frags.append((style, line + "\n", self._row_mouse_handler(i)))
        return FormattedText(frags)

    @staticmethod
    def _center(text: str, cols: int) -> str:
        pad = max((cols - len(text)) // 2, 0)
        return " " * pad + text

    def _render_lyrics(self, rows: int, cols: int):
        track = self.player.track
        header = f"{track.title} · {track.artist}" if track else ""
        frags: list = [
            ("class:lyrics.head", self._center(f"♪ LYRICS — {shorten(header, max(cols - 14, 10))}", cols)),
            ("", "\n"),
        ]
        if self.lyrics_state == "loading":
            frags.append(("class:lyrics.note", self._center("looking for lyrics…", cols)))
            return FormattedText(frags)
        if self.lyrics is None or self.lyrics_state != "ready":
            note = self.lyrics_error or "No lyrics found for this track."
            frags.append(("class:lyrics.note", self._center(note, cols)))
            frags.append(("", "\n\n"))
            frags.append(("class:lyrics.note", self._center("(l or Esc closes · plays on)", cols)))
            return FormattedText(frags)
        data = self.lyrics
        if data.instrumental:
            frags.append(("class:lyrics.note", self._center("This one looks instrumental — enjoy the music. ♫", cols)))
            return FormattedText(frags)

        if data.has_synced:
            times = [t for t, _ in data.synced]
            texts = [text for _, text in data.synced]
            pos = self.player.position if self.player.state in ("playing", "paused") else 0.0
            current = max(0, bisect_right(times, pos) - 1)
            top = max(0, current - (rows - 2) // 3)
            self.lyrics_scroll = top
        else:
            texts = data.plain.splitlines() or [""]
            current = -1
            top = max(0, min(self.lyrics_scroll, max(0, len(texts) - (rows - 2))))
            self.lyrics_scroll = top

        for j in range(max(rows - 2, 1)):
            i = top + j
            if i >= len(texts):
                break
            line = (texts[i] or "").strip()
            style = "class:lyrics.current" if i == current else "class:lyrics"
            frags.append((style, self._center(line, cols)))
            frags.append(("", "\n"))
        if not data.has_synced:
            frags.append(("class:lyrics.note", self._center("— no timings for this one: j/k or PgUp/PgDn to scroll —", cols)))
        return FormattedText(frags)

    def _render_playlists(self, rows: int, cols: int):
        names = self.store.playlist_names()
        if not names:
            return FormattedText(
                [
                    (
                        "class:empty",
                        "\n   No playlists yet — press c to create one, or press a on any track "
                        "to add it to a new playlist.",
                    )
                ]
            )
        self.playlists_cursor = max(0, min(self.playlists_cursor, len(names) - 1))
        if self.playlists_cursor < self.playlists_offset:
            self.playlists_offset = self.playlists_cursor
        elif self.playlists_cursor >= self.playlists_offset + rows:
            self.playlists_offset = self.playlists_cursor - rows + 1
        self.playlists_offset = max(0, min(self.playlists_offset, max(0, len(names) - rows)))

        count_w = 10
        name_w = max(cols - 6 - count_w - 1, 10)
        frags: list = []
        for j in range(rows):
            i = self.playlists_offset + j
            if i >= len(names):
                frags.append(("", "\n"))
                continue
            name = names[i]
            count = len(self.store.playlists.get(name, []))
            label = shorten(f"{name}", name_w)
            line = f"{i + 1:>3}  ♪ {label}"
            count_text = f"{count} track" + ("" if count == 1 else "s")
            line += " " * max(cols - len(line) - count_w, 0) + f"{count_text:>{count_w}}"
            style = "class:row.selected" if i == self.playlists_cursor else "class:row"
            frags.append((style, line + "\n", self._playlist_mouse_handler(i)))
        return FormattedText(frags)

    def _render_help_view(self):
        frags: list = [("class:empty", "\n"), ("class:helpview.key", " Controls\n"), ("", "\n")]
        for key, desc in HELP_LINES:
            frags.append(("class:helpview.key", f"   {key:<12}"))
            frags.append(("class:helpview", f"{desc}\n"))
        frags.append(("class:empty", "\n   Press ? or Esc to close.\n"))
        return FormattedText(frags)

    def _stats_data(self) -> dict:
        now = time.time()
        if self._stats_cache and now - self._stats_cache[0] < 30:
            return self._stats_cache[1]
        plays = self.store.load_plays()
        data = {
            "all": summarize(plays),
            "week": summarize(plays, since=now - 7 * 86400),
        }
        self._stats_cache = (now, data)
        return data

    def _render_stats(self, rows: int, cols: int):
        data = self._stats_data()
        total, week = data["all"], data["week"]
        frags: list = [("class:stats.head", self._center("♪ TERMIFY WRAPPED", cols)), ("", "\n")]
        frags.append(
            (
                "class:stats.rank",
                self._center(
                    f"all time · {fmt_hours(total['seconds'])} · {total['plays']} plays · {total['songs']} songs",
                    cols,
                ),
            )
        )
        frags.append(("", "\n"))
        frags.append(
            (
                "class:stats.rank",
                self._center(f"last 7 days · {fmt_hours(week['seconds'])} · {week['plays']} plays", cols),
            )
        )
        frags.append(("", "\n\n"))
        if total["plays"] == 0:
            frags.append(
                ("class:empty", self._center("Nothing played yet — listen for a while and come back.", cols))
            )
            return FormattedText(frags)
        songs = week["top_songs"] or total["top_songs"]
        artists = week["top_artists"] or total["top_artists"]
        left_w = max(cols // 2 - 3, 16)
        right_w = max(cols - left_w - 2, 16)
        frags.append(("class:stats.head", f"  {'TOP SONGS':<{left_w}}{'TOP ARTISTS'}"))
        frags.append(("", "\n"))
        limit = max(min(rows - 8, 10), 3)
        for i in range(limit):
            left = ""
            if i < len(songs):
                title, artist, count = songs[i]
                left = shorten(f"{i + 1}. {title} · {artist}", left_w - 2) + f" {count}×"
            right = ""
            if i < len(artists):
                artist, count = artists[i]
                right = shorten(f"{i + 1}. {artist}", right_w - 2) + f" {count}×"
            style = "class:stats.top" if i == 0 else "class:stats.rank"
            frags.append((style, "  " + f"{left:<{left_w - 2}}{right}"))
            frags.append(("", "\n"))
        frags.append(("", "\n"))
        frags.append(("class:empty", "  t or Esc closes · numbers come from your local play history"))
        return FormattedText(frags)

    def _viz_text(self):
        """The live spectrum strip; dim dashes while silent."""
        cols = self._size().columns
        levels = self.player.levels
        blocks = "▁▂▃▄▅▆▇█"
        if levels and any(levels):
            chars = " ".join(blocks[min(len(blocks) - 1, int(level * len(blocks)))] for level in levels)
            text = " ♪ " + chars
            return "class:viz", text + " " * max(cols - len(text) - 1, 0)
        return "class:viz.dim", " " + "─" * max(cols - 2, 0)

    def _render_now_playing(self):
        cols = self._size().columns
        track = self.player.track
        if track is None:
            frags = [
                ("class:bar.dim", " ♪ "),
                ("class:bar.dim", "Nothing playing — press / to search, Enter to play"),
                ("", "\n"),
                ("class:bar.dim", " " + "─" * max(cols - 2, 0)),
            ]
            if self.visualizer:
                style, text = self._viz_text()
                frags.append(("", "\n"))
                frags.append((style, text))
            return FormattedText(frags)
        state = self.player.state
        icon = {"playing": "▶", "paused": "⏸", "loading": "…", "error": "!"}.get(state, "♪")
        right = f"vol {volume_bar(self.player.volume)} "
        left = f" {icon} "
        mood = detect_mood(track.title, track.artist)
        mood_tag = f"  ♨ {MOOD_LABELS[mood]}" if mood else ""
        base = f"{track.title} · {track.artist}"
        budget = max(cols - len(left) - len(right) - len(mood_tag) - 2, 10)
        base = shorten(base, budget)
        pad = max(cols - len(left) - len(base) - len(mood_tag) - len(right), 0)
        pos = fmt_duration(self.player.position)
        dur = fmt_duration(self.player.duration or track.duration)
        bar_w = max(cols - len(pos) - len(dur) - 6, 5)
        frac = (self.player.position / self.player.duration) if self.player.duration else 0.0
        line2 = f" {pos} {progress_bar(frac, bar_w)} {dur}"
        frags = [
            ("class:bar.title", left),
            ("class:bar", base),
            ("class:bar.live", mood_tag),
            ("", " " * pad),
            ("class:bar.dim", right),
            ("", "\n"),
            ("class:bar.live" if state == "playing" else "class:bar.dim", line2),
        ]
        if self.visualizer:
            style, text = self._viz_text()
            frags.append(("", "\n"))
            frags.append((style, text))
        return FormattedText(frags)

    def _render_bottom(self):
        cols = self._size().columns
        if self.msg and time.time() < self.msg_until:
            return FormattedText([(self.msg_style, " " + shorten(self.msg, max(cols - 2, 10)))])
        if self.stats_visible:
            hint = " t/Esc close stats · n/b skip songs · space pause · all data stays local"
            return FormattedText([("class:help", " " + shorten(hint, max(cols - 2, 10)))])
        if self.lyrics_visible:
            hint = " l/Esc close lyrics · space pause · n/b next/prev · j/k scroll (plain lyrics only)"
            return FormattedText([("class:help", " " + shorten(hint, max(cols - 2, 10)))])
        return FormattedText([("class:help", " " + shorten(HINTS, max(cols - 2, 10)))])

    def _say(self, text: str, style: str = "class:msg", seconds: float = 4.0) -> None:
        self.msg = text
        self.msg_style = style
        self.msg_until = time.time() + seconds
        self._invalidate()

    # --------------------------------------------------------------- state
    def _is_current(self, track) -> bool:
        return self.player.track is not None and track.id == self.player.track.id

    def _current_target(self):
        view = self._view()
        if view.tracks:
            return view.tracks[min(view.cursor, len(view.tracks) - 1)]
        return self.player.track

    def _refresh_order(self, start: int) -> None:
        queue = self.views["queue"].tracks
        n = len(queue)
        if n == 0:
            self.order, self.order_pos = [], -1
            return
        start = max(0, min(start, n - 1))
        if self.shuffle:
            rest = [i for i in range(n) if i != start]
            random.shuffle(rest)
            self.order = [start] + rest
        else:
            self.order = list(range(n))
        self.order_pos = self.order.index(start)

    def _play_index(self, i: int, start: float = 0.0) -> None:
        queue = self.views["queue"].tracks
        if not (0 <= i < len(queue)):
            return
        self.queue_index = i
        self.views["queue"].cursor = i
        if len(self.order) != len(queue) or i not in self.order:
            self._refresh_order(i)
        else:
            self.order_pos = self.order.index(i)
        track = queue[i]
        self.store.add_recent(track)
        self.store.log_play(track)  # feeds the stats screen
        self.views["recent"].tracks = list(self.store.recent)
        self.player.play(track, start=start)
        self._notify_track(track)
        self._set_title(track)
        self._prefetch_next()
        self._maybe_topup()
        if self.lyrics_visible:
            self._ensure_lyrics()

    def _prefetch_next(self) -> None:
        target = self._next_index()
        if target is not None:
            self.yt.prefetch(self.views["queue"].tracks[target])

    def _next_index(self):
        if not self.views["queue"].tracks or not self.order:
            return None
        if self.order_pos + 1 < len(self.order):
            return self.order[self.order_pos + 1]
        if self.repeat == "all":
            return self.order[0]
        return None

    def _prev_index(self):
        if not self.views["queue"].tracks or not self.order:
            return None
        if self.order_pos > 0:
            return self.order[self.order_pos - 1]
        if self.repeat == "all":
            return self.order[-1]
        return None

    def _set_title(self, track) -> None:
        title = f"♪ {track.title} · {track.artist}"
        if self.ascii_mode:
            title = _ascii(title)
        try:
            self.app.output.set_title(title)
        except Exception:
            pass

    # ------------------------------------------------------------- actions
    def _quit(self) -> None:
        self._save_session()
        self.player.close()
        self.app.exit()

    def _move_cursor(self, delta: int) -> None:
        if self.lyrics_visible:
            self._scroll_lyrics(delta)
            return
        if self.view_key == "playlists":
            names = self.store.playlist_names()
            if not names:
                return
            self.playlists_cursor = max(0, min(self.playlists_cursor + delta, len(names) - 1))
            self._invalidate()
            return
        view = self._view()
        if not view.tracks:
            return
        view.cursor = max(0, min(view.cursor + delta, len(view.tracks) - 1))
        self._invalidate()

    def _page(self) -> int:
        return max(self._body_rows() - 1, 1)

    def _play_selected(self) -> None:
        if self.view_key == "playlists":
            self._open_playlist_by_cursor()
            return
        view = self._view()
        if not view.tracks:
            return
        sel = max(0, min(view.cursor, len(view.tracks) - 1))
        if view.key != "queue":
            self.views["queue"].tracks = list(view.tracks)
            self.views["queue"].cursor = sel
            self._refresh_order(sel)
            self._play_index(sel)
        else:
            self._play_index(sel)

    def _toggle_pause(self) -> None:
        if self.player.state in ("playing", "paused"):
            self.player.toggle_pause()
        elif self.player.state == "idle":
            queue = self.views["queue"].tracks
            if queue:
                idx = self.queue_index if 0 <= self.queue_index < len(queue) else self.views["queue"].cursor
                start = self._resume_position if idx == self._resume_index else 0.0
                self._resume_position, self._resume_index = 0.0, -1
                self._play_index(idx, start=start)
        self._invalidate()

    def _advance_auto(self, force_next: bool = False) -> None:
        if self.player.track is None:
            return
        if self.sleep_mode == "song":
            self.sleep_mode, self.sleep_until = "off", 0.0
            self.player.stop()
            self._say("Sleep timer — stopping after this song. Good night 🌙", "class:msg.ok", 8)
            return
        if self.repeat == "one" and not force_next and 0 <= self.queue_index < len(self.views["queue"].tracks):
            self._play_index(self.queue_index)
            return
        target = self._next_index()
        if target is None:
            if self.sleep_mode == "queue":
                self.sleep_mode, self.sleep_until = "off", 0.0
                self.player.stop()
                self._say("Sleep timer — end of the queue. Good night 🌙", "class:msg.ok", 8)
                return
            if self.autoplay:
                # Queue finished -- keep the vibe going instead of stopping.
                self._start_topup(jump=True)
                return
            self.player.stop()
            self._say("Reached the end of the queue.", "class:msg", 5)
            return
        self._play_index(target)

    def _next(self) -> None:
        target = self._next_index()
        if target is None:
            self.player.stop()
            self._say("That is the end of the queue.", "class:msg", 4)
            return
        self._play_index(target)

    def _prev(self) -> None:
        if self.player.track is not None and self.player.position > 5 and self.player.state in ("playing", "paused"):
            pos = self.player.position
            self.player.seek(-pos)
            self._invalidate()
            return
        target = self._prev_index()
        if target is None:
            if self.player.state in ("playing", "paused"):
                pos = self.player.position
                self.player.seek(-pos)
                self._invalidate()
            return
        self._play_index(target)

    def _seek(self, delta: float) -> None:
        self.player.seek(delta)
        self._say(f"Seek {int(self.player.position // 60)}:{int(self.player.position % 60):02d}", "class:msg", 2)

    def _adjust_volume(self, delta: float) -> None:
        vol = max(0.0, min(1.0, round(self.player.volume + delta, 2)))
        self.player.volume = vol
        if vol > 0:
            self._last_volume = vol
        self.store.config["volume"] = vol
        self.store.save_config()
        self._say(f"Volume {int(round(vol * 100))}%", "class:msg", 2)

    def _toggle_mute(self) -> None:
        if self.player.volume > 0:
            self._last_volume = self.player.volume
            self.player.volume = 0.0
            self._say("Muted (m to unmute)", "class:msg", 2)
        else:
            self.player.volume = self._last_volume
            self._say(f"Volume {int(round(self.player.volume * 100))}%", "class:msg", 2)
        self._invalidate()

    def _toggle_like(self) -> None:
        track = self._current_target()
        if track is None:
            return
        added = self.store.toggle_like(track)
        self.views["liked"].tracks = list(self.store.liked)
        self._say("♥ Added to Liked Songs" if added else "Removed from Liked Songs",
                  "class:msg.ok" if added else "class:msg", 3)

    def _remove_selected(self) -> None:
        if self.view_key == "playlists":
            self._delete_selected_playlist()
            return
        view = self._view()
        if not view.tracks:
            return
        i = max(0, min(view.cursor, len(view.tracks) - 1))
        track = view.tracks[i]
        if view.key.startswith("playlist:"):
            name = view.key.split(":", 1)[1]
            self.store.remove_from_playlist(name, track)
            view.tracks = list(self.store.playlists.get(name, []))
            view.cursor = max(0, min(view.cursor, len(view.tracks) - 1))
            self._say(f"Removed from \"{shorten(name, 30)}\".", "class:msg", 3)
            return
        if view.key == "downloads":
            self.store.remove_download(track)
            view.tracks = list(self.store.downloads)
            view.cursor = max(0, min(i, len(view.tracks) - 1))
            self._say(f"Removed \"{shorten(track.title, 40)}\" from downloads.", "class:msg", 3)
            return
        if view.key == "results":
            self._say("Search results are not a playlist — play a track to build the queue.", "class:msg", 4)
            return
        if view.key == "liked":
            self.store.toggle_like(track)
            self.views["liked"].tracks = list(self.store.liked)
            self._say("Removed from Liked Songs", "class:msg", 3)
            return
        if view.key == "recent":
            self.store.remove_recent(track)
            self.views["recent"].tracks = list(self.store.recent)
            self._say("Removed from recent history", "class:msg", 3)
            return

        # queue view
        removed_playing = i == self.queue_index
        del view.tracks[i]
        view.cursor = max(0, min(i, len(view.tracks) - 1))
        if removed_playing:
            if view.tracks:
                self._refresh_order(view.cursor)
                self._play_index(view.cursor)
                self._say(f"Removed \"{shorten(track.title, 40)}\" — playing the next track.", "class:msg", 4)
            else:
                self.player.stop()
                self._refresh_order(0)
                self._say("Queue is empty.", "class:msg", 3)
            return
        # shift order/queue indices to account for the removal
        new_order = [idx - 1 if idx > i else idx for idx in self.order if idx != i]
        playing = self.queue_index - 1 if self.queue_index > i else self.queue_index
        self.order = new_order
        self.queue_index = playing
        self.order_pos = new_order.index(playing) if playing in new_order else 0
        self._say("Removed from queue", "class:msg", 3)

    def _toggle_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        self.store.config["shuffle"] = self.shuffle
        self.store.save_config()
        queue = self.views["queue"].tracks
        cur = self.queue_index if 0 <= self.queue_index < len(queue) else 0
        self._refresh_order(cur)
        self._say(f"Shuffle {'on' if self.shuffle else 'off'}", "class:msg", 2)
        self._prefetch_next()

    def _cycle_repeat(self) -> None:
        order = ("off", "all", "one")
        self.repeat = order[(order.index(self.repeat) + 1) % 3]
        self.store.config["repeat"] = self.repeat
        self.store.save_config()
        labels = {"off": "Repeat off", "all": "Repeat queue", "one": "Repeat this track"}
        self._say(labels[self.repeat], "class:msg", 2)

    # -------------------------------------------------- look, feel & timers
    def _tick(self) -> None:
        """Periodic housekeeping that piggybacks on the UI redraw."""
        if self.sleep_mode in ("15m", "30m", "60m") and self.sleep_until and time.time() >= self.sleep_until:
            self._fire_sleep()
        self._maybe_save_session()

    def _fire_sleep(self) -> None:
        self.sleep_mode, self.sleep_until = "off", 0.0
        if self.player.state in ("playing", "loading", "paused"):
            self.player.stop(quiet=True)
        self._say("Sleep timer — music stopped. Good night 🌙", "class:msg.ok", 8)

    def _cycle_sleep(self) -> None:
        order = ("off", "15m", "30m", "60m", "song", "queue")
        idx = (order.index(self.sleep_mode) + 1) % len(order) if self.sleep_mode in order else 0
        self.sleep_mode = order[idx]
        self.sleep_until = 0.0
        if self.sleep_mode in ("15m", "30m", "60m"):
            minutes = int(self.sleep_mode[:-1])
            self.sleep_until = time.time() + minutes * 60
            self._say(f"Sleep timer: stopping in {minutes} minutes.", "class:msg.ok", 3)
        elif self.sleep_mode == "song":
            self._say("Sleep timer: stopping after this song.", "class:msg.ok", 3)
        elif self.sleep_mode == "queue":
            self._say("Sleep timer: stopping when the queue ends.", "class:msg.ok", 3)
        else:
            self._say("Sleep timer off.", "class:msg", 2)
        self._invalidate()

    def _toggle_stats(self) -> None:
        self.stats_visible = not self.stats_visible
        if self.stats_visible:
            self.help_visible = False
            self.lyrics_visible = False
            self._stats_cache = None  # fresh numbers every time it opens
        self._invalidate()

    def _cycle_theme(self) -> None:
        self.theme = next_theme(self.theme)
        self.store.config["theme"] = self.theme
        self.store.save_config()
        self._say(f"Theme: {self.theme}", "class:msg.ok", 3)
        self._invalidate()

    def _toggle_ascii(self) -> None:
        self.ascii_mode = not self.ascii_mode
        self.store.config["ascii"] = self.ascii_mode
        self.store.save_config()
        self._say("Plain-text (ASCII) mode on" if self.ascii_mode else "Unicode mode on", "class:msg", 3)
        self._invalidate()

    def _toggle_visualizer(self) -> None:
        self.visualizer = not self.visualizer
        self.store.config["visualizer"] = self.visualizer
        self.store.save_config()
        self.bar_window.height = 3 if self.visualizer else 2
        self._say(f"Visualizer {'on' if self.visualizer else 'off'}", "class:msg", 2)
        self._invalidate()

    def _cycle_eq(self) -> None:
        idx = (EQ_ORDER.index(self.eq_preset) + 1) % len(EQ_ORDER) if self.eq_preset in EQ_ORDER else 0
        self.eq_preset = EQ_ORDER[idx]
        self.player.set_eq(self.eq_preset)
        self.store.config["eq"] = self.eq_preset
        self.store.save_config()
        self._say(f"Equalizer: {EQ_LABELS.get(self.eq_preset, self.eq_preset)}", "class:msg.ok", 2)
        self._invalidate()

    def _toggle_notifications(self) -> None:
        self.notifications = not self.notifications
        self.store.config["notifications"] = self.notifications
        self.store.save_config()
        if self.notifications and not self.notifications_supported:
            self._say("Notifications on — but this system has no notifier installed.", "class:msg", 5)
        else:
            self._say(f"Desktop notifications {'on' if self.notifications else 'off'}", "class:msg", 2)
        self._invalidate()

    def _notify_track(self, track) -> None:
        if not self.notifications or not self.notifications_supported:
            return
        now = time.time()
        if now - self._last_notify < 8:  # don't spam while the user skips around
            return
        self._last_notify = now
        notify.notify(f"♪ {shorten(track.title, 60)}", track.artist or "Termify")

    def _toggle_mouse(self) -> None:
        self.mouse = not self.mouse
        self.store.config["mouse"] = self.mouse
        self.store.save_config()
        try:
            self.app.mouse_support = self.mouse
        except Exception:
            pass
        self._say(
            "Mouse on — click rows/tabs, scroll the lists" if self.mouse else "Mouse off", "class:msg", 3
        )
        self._invalidate()

    # ----------------------------------------------------------- mouse glue
    def _tab_mouse_handler(self, key: str):
        def handle(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self._tab_to(key)
            return None

        return handle

    def _row_mouse_handler(self, index: int):
        def handle(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self._mouse_play(index)
            elif mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._move_cursor(-3)
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._move_cursor(3)
            return None

        return handle

    def _playlist_mouse_handler(self, index: int):
        def handle(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self.playlists_cursor = index
                self._open_playlist_by_cursor()
            elif mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._move_cursor(-3)
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._move_cursor(3)
            return None

        return handle

    def _mouse_play(self, index: int) -> None:
        if self.help_visible or self.stats_visible or self.lyrics_visible or self.input_mode is not None:
            return
        view = self._view()
        if self.view_key == "playlists" or not (0 <= index < len(view.tracks)):
            return
        view.cursor = index
        self._play_selected()

    # -------------------------------------------------------- offline saves
    @staticmethod
    def _fmt_size(num: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if num < 1024 or unit == "GB":
                return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} GB"

    def _save_offline(self) -> None:
        if self._saving:
            self._say("Still saving offline…", "class:msg", 3)
            return
        if self.view_key.startswith("playlist:"):
            name = self.view_key.split(":", 1)[1]
            targets = list(self._view().tracks)
            label = f"playlist \"{shorten(name, 30)}\""
        else:
            track = self._current_target()
            targets = [track] if track is not None else []
            label = f"\"{shorten(targets[0].title, 36)}\"" if targets else ""
        if not targets:
            self._say("Nothing to save — select a track first.", "class:msg", 4)
            return
        pending = [t for t in targets if not self.store.is_downloaded(t)]
        if not pending:
            self._say("Already saved offline ✔", "class:msg", 3)
            return
        self._saving = True
        count = len(pending)
        self._say(f"Saving {count} track{'s' if count != 1 else ''} offline…", "class:msg", 60)

        def work():
            done = failed = 0
            last_error = ""
            for n, track in enumerate(pending, start=1):
                try:
                    self.yt.download(track, self.cache_dir)
                    self.store.add_download(track)
                    done += 1
                except Exception as exc:
                    failed += 1
                    last_error = str(exc)
                self._call_ui(lambda n=n, t=track, c=count: self._download_progress(n, c, t))
            self._call_ui(lambda: self._download_done(done, failed, last_error))

        threading.Thread(target=work, name="termify-download", daemon=True).start()

    def _download_progress(self, n: int, total: int, track) -> None:
        self.views["downloads"].tracks = list(self.store.downloads)
        if total > 1 and n < total:
            self._say(f"Saving offline… {n}/{total} ({shorten(track.title, 28)})", "class:msg", 60)
        self._invalidate()

    def _download_done(self, done: int, failed: int, last_error: str) -> None:
        self._saving = False
        self.views["downloads"].tracks = list(self.store.downloads)
        if done and not failed:
            self._say(f"⬇ Saved {done} offline ({self._fmt_size(self.store.downloads_size())} total).", "class:msg.ok", 5)
        elif done:
            self._say(f"⬇ Saved {done}, {failed} failed — {shorten(last_error, 40)}", "class:msg", 6)
        else:
            self._say(f"Download failed: {shorten(last_error or 'unknown error', 60)}", "class:msg.error", 6)
        self._invalidate()

    # -------------------------------------------------------- mp3 export
    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in name).strip(" .")
        return (cleaned or "playlist")[:80]

    def _export_mp3(self) -> None:
        if self._exporting:
            self._say("Still exporting…", "class:msg", 3)
            return
        folder = ""
        if self.view_key == "playlists":
            names = self.store.playlist_names()
            if not names:
                self._say("No playlists yet — open one or select tracks to export.", "class:msg", 4)
                return
            name = names[max(0, min(self.playlists_cursor, len(names) - 1))]
            targets = list(self.store.playlists.get(name) or [])
            folder = self._safe_name(name)
        elif self.view_key.startswith("playlist:"):
            name = self.view_key.split(":", 1)[1]
            targets = list(self._view().tracks)
            folder = self._safe_name(name)
        elif self.view_key == "downloads":
            targets = list(self.store.downloads)
            folder = "Downloads"
        else:
            track = self._current_target()
            targets = [track] if track is not None else []
        if not targets:
            self._say("Nothing to export — select a track first.", "class:msg", 4)
            return
        single = len(targets) == 1
        self._exporting = True
        count = len(targets)
        self._say(f"Exporting {count} track{'s' if count != 1 else ''} to mp3…", "class:msg", 60)
        base = os.path.join(self.store.dir, "exports")
        dest = base if single else os.path.join(base, folder)

        def work():
            exported = failed = 0
            last_error = ""
            try:
                os.makedirs(dest, exist_ok=True)
            except OSError as exc:
                self._call_ui(lambda: self._export_done(0, count, str(exc), dest))
                return
            for n, track in enumerate(targets, start=1):
                try:
                    source = self.store.download_path(track)
                    if source is None:
                        source = self.yt.download(track, self.cache_dir)
                        self.store.add_download(track)
                        self._call_ui(lambda t=track: self._download_progress(0, 0, t))
                    out_name = self._safe_name(f"{track.title} - {track.artist}") + ".mp3"
                    out_path = os.path.join(dest, out_name)
                    self._convert_mp3(source, out_path)
                    exported += 1
                except Exception as exc:
                    failed += 1
                    last_error = str(exc)
                self._call_ui(lambda n=n: self._say(f"Exporting… {n}/{count}", "class:msg", 60))
            self._call_ui(lambda: self._export_done(exported, failed, last_error, dest))
            self._call_ui(lambda: self._invalidate())

        threading.Thread(target=work, name="termify-export", daemon=True).start()

    @staticmethod
    def _convert_mp3(source: str, dest: str) -> None:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-y", "-i", source, "-vn", "-codec:a", "libmp3lame", "-q:a", "2", dest],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not os.path.exists(dest):
            raise RuntimeError("ffmpeg could not convert this file")

    def _export_done(self, exported: int, failed: int, last_error: str, dest: str) -> None:
        self._exporting = False
        self.views["downloads"].tracks = list(self.store.downloads)
        shown = os.path.relpath(dest, os.path.dirname(self.store.dir)) if os.path.isdir(dest) else dest
        if exported and not failed:
            self._say(f"⬆ Exported {exported} to {shorten(shown.replace(chr(92), '/'), 44)}", "class:msg.ok", 6)
        elif exported:
            self._say(f"⬆ Exported {exported}, {failed} failed — {shorten(last_error, 36)}", "class:msg", 6)
        else:
            self._say(f"Export failed: {shorten(last_error or 'unknown error', 60)}", "class:msg.error", 6)
        self._invalidate()

    # -------------------------------------------------------- playlist import
    def _prompt_import(self) -> None:
        self._say("Point me at a file with 'Artist - Song' lines (or a CSV export).", "class:msg", 4)
        self._open_input(" import file: ", "import-file")

    def _import_playlist(self, path: str) -> None:
        if self._importing:
            self._say("Already importing…", "class:msg", 3)
            return
        path = os.path.expanduser(path.strip().strip('"'))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(1_000_000)
        except OSError as exc:
            self._say(f"Could not read that file: {shorten(str(exc), 50)}", "class:msg.error", 6)
            return
        items = parse_lines(text)
        if not items:
            self._say("No track lines found — expected lines like 'Artist - Song'.", "class:msg", 5)
            return
        name = self._safe_name(os.path.splitext(os.path.basename(path))[0])
        self._importing = True
        total = len(items)
        self._say(f"Importing {total} tracks into \"{name}\"…", "class:msg", 60)

        def work():
            found = 0
            for n, (artist, title) in enumerate(items, start=1):
                query = f"{artist} {title}".strip()
                try:
                    results = self.yt.search(query, limit=1)
                except Exception:
                    results = []
                if results:
                    track = results[0]
                    found += 1
                    self._call_ui(lambda t=track: self._import_add(name, t))
                if n % 5 == 0 or n == total:
                    self._call_ui(lambda n=n: self._say(f"Importing… {n}/{total}", "class:msg", 60))
            self._call_ui(lambda: self._import_done(name, found, total))

        threading.Thread(target=work, name="termify-import", daemon=True).start()

    def _import_add(self, name: str, track) -> None:
        self.store.add_to_playlist(name, track)
        self._refresh_playlist_views()

    def _import_done(self, name: str, found: int, total: int) -> None:
        self._importing = False
        self._refresh_playlist_views()
        if found:
            self._say(f"⬇ Imported {found}/{total} tracks into \"{shorten(name, 30)}\".", "class:msg.ok", 6)
            self._open_playlist(name)
        else:
            self._say("Import found nothing — check the file format.", "class:msg.error", 5)
        self._invalidate()

    # ------------------------------------------------------ radio & autoplay
    def _radio_now(self) -> None:
        """R: queue more songs in the same vibe as what is playing."""
        seed = self.player.track or self._current_target()
        if seed is None:
            self._say("Nothing to base the radio on — play something first.", "class:msg", 4)
            return
        self._start_topup(jump=True, seed=seed)

    def _toggle_autoplay(self) -> None:
        self.autoplay = not self.autoplay
        self.store.config["autoplay"] = self.autoplay
        self.store.save_config()
        if self.autoplay:
            self._maybe_topup()
            self._say("Autoplay on — Termify keeps the mood going.", "class:msg.ok", 3)
        else:
            self._say("Autoplay off — music stops at the end of the queue.", "class:msg", 3)
        self._invalidate()

    def _maybe_topup(self) -> None:
        """Silently queue more similar tracks when the queue is running low."""
        if not self.autoplay or self._topup_running:
            return
        if len(self.order) - self.order_pos - 1 >= 2:
            return
        self._start_topup(jump=False)

    def _start_topup(self, jump: bool, seed=None) -> None:
        if self._topup_running:
            if jump:
                self._say("Already digging up similar music…", "class:msg", 3)
            return
        seed = seed or self.player.track
        if seed is None:
            return
        self._topup_running = True
        mood = detect_mood(seed.title, seed.artist)
        label = MOOD_LABELS.get(mood, "") if mood else ""
        if jump:
            self._say(f"Tuning {'the ' + label + ' ' if label else ''}radio…", "class:msg", 30)

        def work():
            tracks = []
            try:
                tracks = self.yt.related(seed, limit=20)
            except Exception:
                tracks = []
            if not tracks:
                try:
                    tracks = self.yt.mood_search(mood, limit=20)
                except Exception:
                    tracks = []
            self._call_ui(lambda: self._topup_done(seed, tracks, jump, label))

        threading.Thread(target=work, name="termify-topup", daemon=True).start()

    def _topup_done(self, seed, tracks, jump: bool, label: str) -> None:
        self._topup_running = False
        moved_on = self.player.track is not seed
        if moved_on:
            jump = False  # user skipped while we were fetching; just queue them
        added = self._append_to_queue(tracks, exclude={seed.id}, next_up=jump)
        if added <= 0:
            if jump and not moved_on:
                self.player.stop(quiet=True)
                self._say("Couldn't find anything similar — try another track.", "class:msg", 5)
            return
        if jump:
            target = self._next_index()
            if target is not None:
                self._play_index(target)
        msg = f"♪ {label.capitalize()} radio — queued {added} more." if label else f"♪ Queued {added} similar songs."
        self._say(msg, "class:msg.ok", 4)
        self._prefetch_next()
        self._invalidate()

    def _append_to_queue(self, tracks, exclude: set[str] | None = None, next_up: bool = False) -> int:
        """Add tracks to the queue, skipping duplicates. Returns how many.

        With next_up=True the new tracks are spliced in right after the
        currently playing one, so radio takes over immediately.
        """
        if not tracks:
            return 0
        queue = self.views["queue"].tracks
        known = {t.id for t in queue} | (exclude or set())
        new = [t for t in tracks if t.id not in known]
        if not new:
            return 0
        start = len(queue)
        queue.extend(new)
        indices = list(range(start, len(queue)))
        if self.shuffle:
            random.shuffle(indices)
        if next_up and 0 <= self.order_pos < len(self.order):
            self.order[self.order_pos + 1 : self.order_pos + 1] = indices
        else:
            self.order.extend(indices)
        return len(new)

    # --------------------------------------------------------------- lyrics
    def _toggle_lyrics(self) -> None:
        if self.lyrics_visible:
            self._close_lyrics()
            return
        if self.player.track is None:
            self._say("Nothing is playing — play a song, then press l for lyrics.", "class:msg", 4)
            return
        self.lyrics_visible = True
        self.help_visible = False
        self.stats_visible = False
        self.lyrics_scroll = 0
        self._ensure_lyrics()
        self._invalidate()

    def _close_lyrics(self) -> None:
        if self.lyrics_visible:
            self.lyrics_visible = False
            self._invalidate()

    def _ensure_lyrics(self) -> None:
        """Show lyrics for the current track, fetching them in the background."""
        track = self.player.track
        if track is None:
            return
        if self.lyrics_track_id == track.id and self.lyrics_state in ("loading", "ready", "none"):
            return
        hit, cached = self.lyrics_client.cache_get(track.id)
        if hit:
            self.lyrics_track_id = track.id
            if cached is None:
                self.lyrics, self.lyrics_state = None, "none"
                self.lyrics_error = "No lyrics found for this track."
            else:
                self._lyrics_ready(track.id, cached)
            self._invalidate()
            return

        self.lyrics_track_id = track.id
        self.lyrics_state = "loading"
        self.lyrics = None
        self.lyrics_scroll = 0
        self.lyrics_error = None
        self._invalidate()

        def work():
            try:
                data, error = self.lyrics_client.fetch(track), None
            except Exception as exc:
                data, error = None, str(exc)
            self._call_ui(lambda: self._lyrics_done(track.id, data, error))

        threading.Thread(target=work, name="termify-lyrics", daemon=True).start()

    def _lyrics_done(self, track_id: str, data, error: str | None) -> None:
        if self.lyrics_track_id != track_id:
            return  # the user moved on to another song meanwhile
        if error:
            self.lyrics, self.lyrics_state = None, "none"
            self.lyrics_error = f"Lyrics lookup failed: {shorten(error, 60)}"
            self.lyrics_track_id = None  # allow a retry (toggle l again)
        elif data is None:
            self.lyrics, self.lyrics_state = None, "none"
            self.lyrics_error = "No lyrics found for this track."
        else:
            self._lyrics_ready(track_id, data)
        self._invalidate()

    def _lyrics_ready(self, track_id: str, data) -> None:
        self.lyrics_track_id = track_id
        self.lyrics = data
        self.lyrics_state = "ready"
        self.lyrics_error = None
        self.lyrics_scroll = 0

    def _scroll_lyrics(self, delta: int) -> None:
        if self.lyrics is None or self.lyrics.has_synced:
            return  # synced lyrics follow the song automatically
        total = len(self.lyrics.plain.splitlines())
        self.lyrics_scroll = max(0, min(self.lyrics_scroll + delta, max(0, total - 1)))
        self._invalidate()

    # -------------------------------------------------------------- playlists
    def _prompt_new_playlist(self) -> None:
        self._open_input(" new playlist: ", "new-playlist")

    def _prompt_add_to_playlist(self) -> None:
        if self._current_target() is None:
            self._say("Nothing to add — select or play a track first.", "class:msg", 4)
            return
        if self.store.playlists:
            names = ", ".join(self.store.playlist_names()[:6])
            self._say(f"Type a playlist name — you have: {names}", "class:msg", 4)
        else:
            self._say("Type a name — it creates the playlist if it is new.", "class:msg", 4)
        self._open_input(" add to playlist: ", "add-to-playlist")

    def _create_playlist(self, name: str) -> None:
        if self.store.create_playlist(name):
            self._show_view("playlists")
            self._say(
                f"Created playlist \"{shorten(name, 40)}\" — press a on any track to add songs.",
                "class:msg.ok",
                5,
            )
        else:
            self._say(f"A playlist named \"{shorten(name, 40)}\" already exists.", "class:msg", 4)

    def _add_to_playlist(self, name: str) -> None:
        track = self._current_target()
        if track is None:
            return
        existed = name in self.store.playlists
        if self.store.add_to_playlist(name, track):
            verb = "Added to" if existed else "Created"
            self._say(f"♥ {verb} \"{shorten(name, 30)}\": {shorten(track.title, 34)}", "class:msg.ok", 4)
        else:
            self._say(f"\"{shorten(track.title, 34)}\" is already in \"{shorten(name, 30)}\".", "class:msg", 4)
        self._refresh_playlist_views()

    def _open_playlist_by_cursor(self) -> None:
        names = self.store.playlist_names()
        if not names:
            self._say("No playlists yet — press c to create one.", "class:msg", 4)
            return
        i = max(0, min(self.playlists_cursor, len(names) - 1))
        self._open_playlist(names[i])

    def _open_playlist(self, name: str) -> None:
        tracks = self.store.playlists.get(name)
        if tracks is None:
            return
        key = f"playlist:{name}"
        view = self.views.get(key)
        if view is None:
            view = View(key, name)
            self.views[key] = view
        view.tracks = list(tracks)
        view.cursor = max(0, min(view.cursor, len(view.tracks) - 1))
        self._show_view(key)

    def _delete_selected_playlist(self) -> None:
        names = self.store.playlist_names()
        if not names:
            return
        i = max(0, min(self.playlists_cursor, len(names) - 1))
        name = names[i]
        now = time.time()
        if self._delete_confirm and self._delete_confirm[0] == name and now <= self._delete_confirm[1]:
            self._delete_confirm = None
            self.store.delete_playlist(name)
            self._forget_playlist_view(name)
            remaining = self.store.playlist_names()
            self.playlists_cursor = max(0, min(i, len(remaining) - 1))
            self._say(f"Deleted playlist \"{shorten(name, 40)}\".", "class:msg", 4)
        else:
            self._delete_confirm = (name, now + 3.0)
            self._say(f"Press d again to delete \"{shorten(name, 40)}\" (3s).", "class:msg.error", 3)
        self._invalidate()

    def _forget_playlist_view(self, name: str) -> None:
        key = f"playlist:{name}"
        self.views.pop(key, None)
        if self.view_key == key:
            self._show_view("playlists")

    def _refresh_playlist_views(self) -> None:
        for key in [k for k in self.views if k.startswith("playlist:")]:
            name = key.split(":", 1)[1]
            if name in self.store.playlists:
                self.views[key].tracks = list(self.store.playlists[name])
            else:
                self._forget_playlist_view(name)

    # --------------------------------------------------------------- input
    def _open_input(self, label: str, mode: str) -> None:
        self.input_mode = mode
        self.input_label = label
        self.input_buffer.reset()
        try:
            self.layout.focus(self.input_control)
        except Exception:
            pass
        self._invalidate()

    def _cancel_input(self) -> None:
        self.input_mode = None
        self._focus_body()
        self._invalidate()

    def _accept_input(self, buffer: Buffer) -> None:
        text = buffer.text.strip()
        mode = self.input_mode
        self.input_mode = None
        self._focus_body()
        if not text:
            self._invalidate()
            return
        if mode == "search":
            self._start_search(text)
        elif mode == "new-playlist":
            self._create_playlist(text)
        elif mode == "add-to-playlist":
            self._add_to_playlist(text)
        elif mode == "import-file":
            self._import_playlist(text)
        self._invalidate()

    def _enter_search(self) -> None:
        self._open_input(" / ", "search")

    def _focus_body(self) -> None:
        for target in (self.body_control, self.body_window):
            try:
                self.layout.focus(target)
                return
            except Exception:
                continue

    def _start_search(self, query: str) -> None:
        self.input_mode = None
        self._focus_body()
        self._say(f"Searching for \"{shorten(query, 40)}\"…", "class:msg", 30)
        self._show_view("results")

        def work():
            try:
                results, error = self.yt.search(query), None
            except Exception as exc:
                results, error = [], str(exc)
            self._call_ui(lambda: self._search_done(query, results, error))

        threading.Thread(target=work, name="termify-search", daemon=True).start()

    def _search_done(self, query: str, results: list, error: str | None) -> None:
        if error:
            self._say(f"Search failed: {shorten(error, 80)}", "class:msg.error", 6)
            return
        view = self.views["results"]
        view.tracks = results
        view.cursor = 0
        view.offset = 0
        self._show_view("results")
        if results:
            self._say(f"{len(results)} results for \"{shorten(query, 30)}\" — Enter plays the selected one.",
                      "class:msg.ok", 5)
        else:
            self._say("No results found.", "class:msg", 4)

    # --------------------------------------------------------------- views
    def _show_view(self, key: str) -> None:
        if key in self.views:
            self.view_key = key
            self.help_visible = False
        self._invalidate()

    def _tab_to(self, key: str) -> None:
        self._close_lyrics()
        self.stats_visible = False
        self._show_view(key)

    def _cycle_view(self, step: int) -> None:
        self._close_lyrics()
        self.stats_visible = False
        keys = [key for key, _ in TABS]
        current = "playlists" if self.view_key.startswith("playlist:") else self.view_key
        idx = keys.index(current) if current in keys else 0
        self._show_view(keys[(idx + step) % len(keys)])

    def _toggle_help(self) -> None:
        if self.help_visible:
            self.help_visible = False
            self._show_view(self.prev_view)
        else:
            self.prev_view = self.view_key
            self.help_visible = True
            self.stats_visible = False
            self.lyrics_visible = False
        self._invalidate()

    def _escape(self) -> None:
        if self.help_visible:
            self._toggle_help()
        elif self.stats_visible:
            self.stats_visible = False
            self._invalidate()
        elif self.lyrics_visible:
            self._close_lyrics()
        elif self.view_key.startswith("playlist:"):
            self._show_view("playlists")
        elif self.view_key != "queue":
            self._show_view("queue")
