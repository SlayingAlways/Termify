# wrapped.tape - records docs/wrapped.png (the Termify Wrapped stats screen).
#
# Run from the repository ROOT:   vhs docs/wrapped.tape
# Needs VHS + ttyd + ffmpeg on PATH, plus a little listening history
# (data/plays.jsonl) - play some tracks first if the numbers look too empty.

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

# t opens Termify Wrapped.
Type "t"
Sleep 2s

Screenshot docs/wrapped.png
