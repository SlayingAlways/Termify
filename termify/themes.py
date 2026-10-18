"""Color themes for the terminal UI.

The default "termify" palette is the brand look:

    primary    neon green  #00E5A0
    background black       #000000
    text       white       #FFFFFF

Every theme defines the same set of style classes the app uses, so switching
themes is just swapping a Style object (the Application reads it through a
DynamicStyle on each render).
"""

from __future__ import annotations

from prompt_toolkit.styles import Style

THEMES = ["termify", "synthwave", "gruvbox", "ocean", "mono"]

_PALETTES: dict[str, dict[str, str]] = {
    "termify": dict(
        accent="#00e5a0", on_accent="#000000", text="#ffffff", dim="#767c7a",
        live="#00e5a0", error="#ff6b6b", panel="#101413", mid="#9aa3a0", bright="#ffffff",
        bg="#000000",
    ),
    "synthwave": dict(
        accent="#ff6ac1", on_accent="#1a0f2e", text="#e8dcf4", dim="#7a6f99",
        live="#00e5ff", error="#ff5f7e", panel="#241a3d", mid="#9d8fc4", bright="#ffffff",
        bg="#120822",
    ),
    "gruvbox": dict(
        accent="#fabd2f", on_accent="#282828", text="#ebdbb2", dim="#928374",
        live="#b8bb26", error="#fb4934", panel="#3c3836", mid="#a89984", bright="#fbf1c7",
        bg="#1d2021",
    ),
    "ocean": dict(
        accent="#22b8cf", on_accent="#04222b", text="#d8f0f5", dim="#5f7f8a",
        live="#4dd4ac", error="#ff7a7a", panel="#0f2b33", mid="#7fa6b0", bright="#ffffff",
        bg="#041c22",
    ),
    "mono": dict(
        accent="#ffffff", on_accent="#111111", text="#dddddd", dim="#777777",
        live="#ffffff", error="#ff8888", panel="#2a2a2a", mid="#9a9a9a", bright="#ffffff",
        bg="#000000",
    ),
}


def build_style(theme: str) -> Style:
    p = _PALETTES.get(theme) or _PALETTES["termify"]
    return Style.from_dict(
        {
            "": f"bg:{p['bg']} {p['text']}",
            "brand": f"{p['accent']} bold",
            "brand.term": f"{p['bright']} bold",
            "header": p["mid"],
            "header.dim": p["dim"],
            "tab": p["dim"],
            "tab.active": f"bg:{p['accent']} {p['on_accent']} bold",
            "row": p["text"],
            "row.selected": f"bg:{p['accent']} {p['on_accent']} bold",
            "row.playing": f"{p['live']} bold",
            "bar": p["mid"],
            "bar.title": f"{p['bright']} bold",
            "bar.live": p["live"],
            "bar.dim": p["dim"],
            "msg": p["text"],
            "msg.ok": p["live"],
            "msg.error": p["error"],
            "help": f"bg:{p['panel']} {p['mid']}",
            "empty": p["dim"],
            "prompt": f"{p['accent']} bold",
            "helpview.key": f"{p['accent']} bold",
            "helpview": p["mid"],
            "lyrics": p["dim"],
            "lyrics.current": f"{p['accent']} bold",
            "lyrics.head": f"{p['accent']} bold",
            "lyrics.note": p["dim"],
            "stats.head": f"{p['accent']} bold",
            "stats.rank": p["dim"],
            "stats.top": f"{p['bright']}",
            "viz": p["accent"],
            "viz.dim": p["dim"],
        }
    )


def next_theme(current: str) -> str:
    if current == "spotify":  # pre-brand config value
        current = "termify"
    try:
        return THEMES[(THEMES.index(current) + 1) % len(THEMES)]
    except ValueError:
        return THEMES[0]
