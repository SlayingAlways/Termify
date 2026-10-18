"""Offline checks for the equalizer DSP.

Validates the FIR design (preset curves match their anchor gains), the flat
passthrough, and overlap-save continuity across chunks.

    .venv\\Scripts\\python.exe tests\\eq_unit.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from termify.eq import ANCHOR_HZ, ORDER, PRESETS, Equalizer, design_kernel

checks = []

# 1. Every preset's measured response should be close to its intended gains.
worst = 0.0
detail = []
for name in ORDER:
    gains = PRESETS[name]
    kernel = design_kernel(name)
    if kernel is None:
        continue
    response = np.abs(np.fft.rfft(kernel, 16384))
    freqs = np.fft.rfftfreq(16384, 1.0 / 44100)
    for anchor, wanted in zip(ANCHOR_HZ, gains):
        got = float(20 * np.log10(response[np.argmin(np.abs(freqs - anchor))] + 1e-12))
        worst = max(worst, abs(got - wanted))
    detail.append(name)
checks.append((f"preset curves match anchors (worst {worst:.1f} dB <= 2.5)", worst <= 2.5))
checks.append(("all four shaped presets designed", len(detail) == 4))

# 2. Flat must be a true passthrough.
checks.append(("flat preset is a passthrough", design_kernel("flat") is None))

# 3. A bass boost must actually raise low-frequency energy.
rng = np.random.default_rng(7)
t = np.arange(8192) / 44100.0
sine = (0.4 * np.sin(2 * np.pi * 80 * t)).astype(np.float32)
sig = np.stack([sine, sine], axis=1)
eq = Equalizer()
eq.set_preset("flat")
flat_rms = float(np.sqrt(np.mean(eq.process(sig)[4096:].astype(np.float64) ** 2)))
eq.set_preset("bass")
bass_rms = float(np.sqrt(np.mean(eq.process(sig)[4096:].astype(np.float64) ** 2)))
checks.append(("bass preset boosts an 80 Hz tone", bass_rms > flat_rms * 1.5))

# 4. Chunked processing must equal whole-signal processing (no seam clicks).
noise = (rng.standard_normal((8192, 2)) * 3000).astype(np.float32)
eq.set_preset("vocal")
whole = eq.process(noise)
eq.set_preset("vocal")  # fresh state
chunked = np.vstack([eq.process(noise[:4096]), eq.process(noise[4096:])])
checks.append(("overlap-save chunks are continuous", bool(np.allclose(whole, chunked, atol=2.0))))

# 5. Output shape must match input (the player stream depends on it).
checks.append(("output shape matches input", chunked.shape == noise.shape))

failed = 0
for name, ok in checks:
    print(("PASS  " if ok else "FAIL  ") + name)
    failed += 0 if ok else 1
print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
raise SystemExit(1 if failed else 0)
