# README screenshots

This folder holds every image the README uses. The files here right now are
**placeholders** - replace them with real captures (same filenames) and the
README keeps working. Nothing else needs editing.

## The required set

| File | Capture | Shows up in README |
| --- | --- | --- |
| `hero.gif` | 10-20 s loop: search, play, visualizer moving | top, under the intro |
| `lyrics.png` | lyrics screen (`l`) with the current line highlighted | Lyrics |
| `playlists.png` | a playlist open from the Playlists tab (`5`) | Playlists |
| `wrapped.png` | Termify Wrapped (`t`) with some history | Listening stats |
| `themes.png` | an alternate theme (`T`), e.g. synthwave | Quality of life |

Keep them consistent: same window size, same terminal theme, ideally the same
song throughout.

## Capturing with VHS (repeatable)

[VHS](https://github.com/charmbracelet/vhs) renders terminal GIFs and PNGs
from `.tape` scripts, so all five shots stay reproducible - and can be
regenerated in one go whenever the UI changes.

1. Install it: `scoop install vhs` or `winget install charmbracelet.vhs`
   (it needs `ttyd` and `ffmpeg` on PATH; scoop pulls them in for you).
2. From the repository root, run:

   ```powershell
   vhs docs/hero.tape
   vhs docs/lyrics.tape
   vhs docs/playlists.tape
   vhs docs/wrapped.tape
   vhs docs/themes.tape
   ```

3. Look at the results (`start docs`), tweak the `Sleep` lines in the tapes to
   match your machine's speed, and re-run.

Notes:

- The tapes launch Termify from this repo's virtualenv
  (`.\.venv\Scripts\python.exe -m termify`); swap that for plain `termify` if
  you use the npm install instead.
- The static PNGs use VHS's `Screenshot` command - it exists on recent VHS
  builds; if an older one rejects it, update VHS (`scoop update vhs`).
- The settings give roughly 120 columns x 34 rows at font size 16, which is
  the right density for these shots.
- **Keep `hero.gif` under ~5 MB** so the README loads fast. If it is heavy,
  lower `Set Framerate` (20 -> 15) or shorten the `Sleep`s.
- To re-render everything at once:
  `vhs docs/hero.tape; vhs docs/lyrics.tape; vhs docs/playlists.tape; vhs docs/wrapped.tape; vhs docs/themes.tape`

## Capturing manually (no tooling)

- Windows Terminal, dark background, a good mono font (Cascadia Code,
  JetBrains Mono) at 14-16 pt, window around 120 x 34.
- PNGs: `Win+Shift+S`, then crop to the terminal only.
- GIF: [ScreenToGif](https://www.screentogif.com) - record the terminal
  region at 12-15 fps, trim, save as a looping GIF.
- The README draws every image at `width="880"`, so capture at 1200 px wide or
  more and it will downscale crisply.

## Demo data (worth 5 minutes)

- Pick one recognizable, family-friendly song and use it in every shot.
- Play a few tracks before recording so Queue, Recent, Wrapped and Playlists
  do not look empty. Likes and playlists help too (`f`, `a`, `c`).
- Wrapped is computed from `data/plays.jsonl`, which grows as you listen.

## Optional extras (not wired into the README yet)

Capture these if you want more; paste the block somewhere it fits:

| File | Capture | Good spot |
| --- | --- | --- |
| `search.png` | results after `/` | Quick start |
| `help.png` | the `?` help overlay | near Controls |
| `diag.png` | `--diag` doctor output | If something is wrong |
| `ascii.png` | `u` plain-text mode | Quality of life |

```html
<p align="center">
  <img src="docs/search.png" width="880" alt="Termify search results">
  <br>
  <em>Search anything - press <code>/</code>, type, hit <code>Enter</code>.</em>
</p>
```
