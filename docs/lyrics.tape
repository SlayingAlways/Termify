# lyrics.tape - records docs/lyrics.png (the karaoke lyrics screen).
#
# Run from the repository ROOT:   vhs docs/lyrics.tape
# Needs VHS + ttyd + ffmpeg on PATH.
#
# Replace the search text below with any song LRCLIB has SYNCED lyrics for
# (popular tracks usually do - check lrclib.net).
#
# This tape has no Output: it only writes the PNG via the Screenshot command.

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

Type "/"
Sleep 400ms
Type "coldplay yellow"
Sleep 400ms
Enter
Sleep 4s

Enter
Sleep 6s

# Open the lyrics view, then give LRCLIB a moment to answer.
Type "l"
Sleep 8s

# Capture while the highlighted line is mid-song.
Screenshot docs/lyrics.png
