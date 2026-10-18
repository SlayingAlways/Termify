# themes.tape - records docs/themes.png (an alternate color theme).
#
# Run from the repository ROOT:   vhs docs/themes.tape
# Needs VHS + ttyd + ffmpeg on PATH.
#
# T cycles the app themes: termify -> synthwave -> gruvbox -> ocean -> mono.
# This captures synthwave (one press). For the 5-theme collage instead, copy
# this tape, press T one extra time per copy, capture each, and stitch the
# PNGs side by side (see docs/CAPTURE.md).

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

# One press of T: synthwave.
Type "T"
Sleep 1500ms

Screenshot docs/themes.png
