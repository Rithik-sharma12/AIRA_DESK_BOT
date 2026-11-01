"""Pitch shifting that keeps the speed: makes the offline Piper voice sound younger.

Method: stretch the audio in time with WSOLA (overlap-add of short windows, each one
aligned to the previous by cross-correlation so the waveform stays smooth), then
resample it back to the original length. The result is the same speech, the same
length, a few semitones higher. Raising the pitch this way also lifts the formants a
little, which is exactly what makes a voice sound younger. Needs numpy (installed with
piper-tts); without it the audio is returned unchanged.
"""

from __future__ import annotations

import logging

from .audio import AudioClip

log = logging.getLogger("aira.voice.pitch")


def _time_stretch(x, factor: float, sr: int):
    import numpy as np

    frame = max(64, int(0.030 * sr))
    hop_out = frame // 2
    hop_in = hop_out / factor
    tol = int(0.010 * sr)
    win = np.hanning(frame)
    n_out = int(len(x) * factor) + frame
    out = np.zeros(n_out)
    norm = np.zeros(n_out)
    pos_in, pos_out, prev = 0.0, 0, None
    while int(pos_in) + frame + tol < len(x) and pos_out + frame < n_out:
        center = int(pos_in)
        if prev is None:
            best = center
        else:
            lo, hi = max(0, center - tol), min(len(x) - frame, center + tol)
            corr = np.correlate(x[lo:hi + frame], prev, mode="valid")
            best = lo + int(np.argmax(corr))
        out[pos_out:pos_out + frame] += x[best:best + frame] * win
        norm[pos_out:pos_out + frame] += win
        nxt = best + hop_out
        prev = x[nxt:nxt + frame] if nxt + frame <= len(x) else None
        pos_in += hop_in
        pos_out += hop_out
    norm[norm < 1e-3] = 1.0
    return out[:pos_out + hop_out] / norm[:pos_out + hop_out]


def speed_up(clip: AudioClip, factor: float) -> AudioClip:
    """Play the audio ``factor`` times faster: pitch and formants rise by ``factor``, the length
    shrinks by ``factor``. Band-limited (FFT), so nothing aliases and no frames are re-stitched.

    With Piper this is the clean way to a younger voice: ask Piper to speak ``factor`` times
    slower (its own length_scale, natural phoneme timing), then speed it up here. The result
    has normal speed, a higher voice, and none of the smearing a time-stretch adds."""
    if factor == 1 or clip.channels != 1:
        return clip
    try:
        import numpy as np
    except ImportError:
        log.warning("numpy not installed: offline voice pitch unchanged")
        return clip
    x = np.frombuffer(clip.pcm16, dtype="<i2").astype(np.float64)
    n = len(x)
    m = max(1, int(round(n / factor)))
    if n < clip.sample_rate // 20:
        return clip
    spec = np.fft.rfft(x)
    keep = m // 2 + 1
    out = np.zeros(keep, dtype=complex)
    out[:min(keep, len(spec))] = spec[:keep]
    y = np.fft.irfft(out, m) * (m / n)
    fade = min(len(y) // 4, int(0.004 * clip.sample_rate))     # soften the ends (FFT wrap-around)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]
    peak = np.max(np.abs(y)) or 1.0
    if peak > 32000:
        y *= 32000 / peak
    return AudioClip(pcm16=y.astype("<i2").tobytes(), sample_rate=clip.sample_rate, channels=1)


def pitch_shift(clip: AudioClip, semitones: float) -> AudioClip:
    if not semitones or clip.channels != 1:
        return clip
    try:
        import numpy as np
    except ImportError:
        log.warning("numpy not installed: offline voice pitch unchanged")
        return clip
    x = np.frombuffer(clip.pcm16, dtype="<i2").astype(np.float64)
    if len(x) < clip.sample_rate // 10:
        return clip
    factor = 2 ** (semitones / 12)
    stretched = _time_stretch(x, factor, clip.sample_rate)
    target = int(round(len(x) * factor))
    if len(stretched) < target:
        stretched = np.pad(stretched, (0, target - len(stretched)))
    stretched = stretched[:target]
    # Resample the longer signal back to the original length: pitch goes up by `factor`.
    idx = np.arange(len(x)) * factor
    y = np.interp(idx, np.arange(len(stretched)), stretched)
    peak = np.max(np.abs(y)) or 1.0
    if peak > 32000:
        y *= 32000 / peak
    return AudioClip(pcm16=y.astype("<i2").tobytes(), sample_rate=clip.sample_rate, channels=1)
