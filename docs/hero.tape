# hero.tape - records docs/hero.gif for the README (search -> play -> visualizer).
#
# Run from the repository ROOT:   vhs docs/hero.tape
# Needs VHS + ttyd + ffmpeg on PATH:   scoop install vhs   (or: winget install charmbracelet.vhs)
#
# Optional: play a few tracks once before recording so the queue and the
# resume screen look alive. The launch line uses this repo's virtualenv;
# swap it for plain `termify` if you installed from npm.

Output docs/hero.gif

Set Shell powershell
Set FontSize 16
Set Width 1200
Set Height 780
Set Padding 16
Set WindowBar Colorful
Set Theme "Catppuccin Frappe"
Set TypingSpeed 45ms
Set Framerate 20
# Set Columns 120      # exact cell size on newer VHS builds (do not combine with Set Width)
# Set Rows 34

Type ".\.venv\Scripts\python.exe -m termify"
Enter
Sleep 5s

# Search for something instantly recognizable.
Type "/"
Sleep 400ms
Type "lofi beats to relax to"
Sleep 400ms
Enter
Sleep 4s

# Play the top result - the now-playing bar and the visualizer come alive.
Enter
Sleep 10s
