# playlists.tape - records docs/playlists.png (a playlist from the Playlists tab).
#
# Run from the repository ROOT:   vhs docs/playlists.tape
# Needs VHS + ttyd + ffmpeg on PATH.
#
# Have a couple of playlists first (press `a` on tracks to add them; `c` in
# the Playlists tab creates one). The tape opens whichever playlist the
# cursor sits on, so make the first one a full one.

Set Shell powershell
Set FontSize 16
Set Width 1200
Set Height 780
Set Padding 16
Set WindowBar Colorful
Set Theme "Catppuccin Frappe"
Set TypingSpeed 45ms

Type ".\.venv\Scripts\python.exe -m termify"
Enter
Sleep 5s

# 5 = Playlists tab; Enter = open the highlighted playlist.
Type "5"
Sleep 1s
Enter
Sleep 1s

Screenshot docs/playlists.png
