"""A tiny FIR equalizer with presets -- pure numpy, no extra dependencies.

The desired frequency response of a preset is defined as gains (in dB) at a
few anchor frequencies; a windowed linear-phase FIR kernel is built from it
via inverse FFT.  Processing is overlap-save so it can run chunk by chunk
without clicks.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 44100
TAPS = 257   # ~2.9 ms group delay; keeps the low shelves accurate

# Gains (dB) at ANCHOR_HZ, in order.
PRESETS: dict[str, list[float]] = {
    "flat": [0.0, 0.0, 0.0, 0.0, 0.0],
    "bass": [9.0, 4.0, 0.0, -1.0, -2.0],
    "night": [3.0, 0.0, -2.0, -5.0, -9.0],
    "vocal": [-5.0, -1.0, 3.0, 4.0, 0.0],
    "club": [7.0, 2.0, -1.0, 3.0, 6.0],
}
ANCHOR_HZ = np.array([60.0, 250.0, 1000.0, 4000.0, 12000.0])
LABELS = {
    "flat": "flat",
    "bass": "bass boost",
    "night": "night",
    "vocal": "vocal",
    "club": "club",
}
ORDER = ["flat", "bass", "night", "vocal", "club"]


def design_kernel(preset: str, sample_rate: int = SAMPLE_RATE, taps: int = TAPS) -> np.ndarray | None:
    """Build the FIR kernel for *preset*; None means "no filtering"."""
    gains = PRESETS.get(preset, PRESETS["flat"])
    if not any(gains):
        return None
    n = 4096
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    db = np.interp(
        np.log10(np.clip(freqs, 30.0, None)),
        np.log10(ANCHOR_HZ),
        np.array(gains, dtype=np.float64),
    )
    target = 10.0 ** (db / 20.0)
    impulse = np.fft.irfft(target, n)
    centered = np.roll(impulse, n // 2)  # time 0 now lives at n//2
    half = taps // 2
    vector = centered[n // 2 - half : n // 2 + half + (taps % 2)]
    kernel = vector * np.hanning(len(vector))
    return kernel.astype(np.float32)


class Equalizer:
    """Applies the selected preset; keeps filter state between chunks."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, taps: int = TAPS):
        self._sample_rate = sample_rate
        self._taps = taps
        self._kernels: dict[str, np.ndarray | None] = {}
        self.preset = "flat"
        self._state: np.ndarray | None = None

    @property
    def active(self) -> bool:
        return self.kernel() is not None

    def kernel(self) -> np.ndarray | None:
        if self.preset not in self._kernels:
            self._kernels[self.preset] = design_kernel(self.preset, self._sample_rate, self._taps)
        return self._kernels[self.preset]

    def set_preset(self, name: str) -> None:
        self.preset = name if name in PRESETS else "flat"
        self._state = None  # fresh state, avoids a pop when kernels change

    def process(self, arr: np.ndarray) -> np.ndarray:
        """Filter (frames, channels) float32 audio in place-ish."""
        kernel = self.kernel()
        if kernel is None or arr.size == 0:
            return arr
        tail = self._taps - 1
        channels = arr.shape[1]
        if self._state is None or self._state.shape != (tail, channels):
            self._state = np.zeros((tail, channels), dtype=np.float32)
        padded = np.vstack([self._state, arr])
        out = np.empty_like(arr, dtype=np.float32)
        for channel in range(channels):
            out[:, channel] = np.convolve(padded[:, channel], kernel, mode="valid")
        self._state = padded[-tail:] if tail else None
        return out
