"""
Compose the game's missing music from code: original, so it ships in the repo.

    python scripts/compose_music.py                 # every mood that has no file yet
    python scripts/compose_music.py dungeon verath  # just these (overwrites)
    python scripts/compose_music.py --all           # every mood this script knows

Each mood is a short score (chords, a generated melody, ostinatos, percussion)
played by small additive / wavetable instruments: strings, cello, piano, harp,
bells, glass, choir, clarinet, drones, hand drum, metal hits, drips. Fixed
seeds make every run identical. Tracks are built as perfect loops: notes and
the reverb tail that run past the end wrap round to the start, so the seam is
silent without a crossfade. Output is mastered like ``master_audio.py`` music
(-24 dBFS RMS, peaks under -3 dBFS) and written to ``assets/audio/music``.

Everything this script writes is the project's own work (CC0, like ``assets/``).
Needs numpy and ffmpeg.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

RATE = 44100
TAU = 2 * np.pi
TABLE = 4096
OUT_DIR = ROOT / "assets" / "audio" / "music"
TARGET_RMS_DB = -24.0
PEAK_CEILING_DB = -3.0

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
NOTE.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})
QUALITY = {"M": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11),
           "m7": (0, 3, 7, 10), "sus": (0, 5, 7), "dim": (0, 3, 6)}


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord(name: str) -> tuple[int, ...]:
    """'Dm' / 'Bb' / 'B7' / 'Fmaj7' → pitch classes."""
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    q = name[len(root):] or "M"
    return tuple((NOTE[root] + i) % 12 for i in QUALITY[q])


def scale(root: str, steps: tuple[int, ...]) -> tuple[int, ...]:
    return tuple((NOTE[root] + s) % 12 for s in steps)


MINOR, DORIAN, PHRYGIAN = (0, 2, 3, 5, 7, 8, 10), (0, 2, 3, 5, 7, 9, 10), (0, 1, 3, 5, 7, 8, 10)
MAJOR, PHRYG_DOM, HARM_MINOR = (0, 2, 4, 5, 7, 9, 11), (0, 1, 4, 5, 7, 8, 10), (0, 2, 3, 5, 7, 8, 11)


def voicing(pcs, lo: int, hi: int) -> list[int]:
    return [m for m in range(lo, hi + 1) if m % 12 in pcs]


# ── Instruments ──────────────────────────────────────────────────────────────

class Synth:
    """Instruments share one random generator, so a mood renders the same every time."""

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    # Wavetable voices: any number of harmonics for the cost of one lookup.
    def _table(self, partials, formant=None, freq=1.0):
        x = np.arange(TABLE) / TABLE
        out = np.zeros(TABLE)
        for k, a in partials:
            if freq * k > 15000:
                break
            g = a * (formant(freq * k) if formant else 1.0)
            out += g * np.sin(TAU * k * x + self.rng.uniform(0, TAU))
        return out / (np.max(np.abs(out)) or 1)

    @staticmethod
    def _read(table, phase):
        idx = (phase % 1.0) * TABLE
        i0 = idx.astype(np.int64)
        frac = idx - i0
        return table[i0 % TABLE] * (1 - frac) + table[(i0 + 1) % TABLE] * frac

    def sustained(self, midi, dur, partials, attack=0.3, release=1.0, vibrato=0.0, vib_rate=5.0,
                  detune=(0.0,), formant=None, swell=False):
        f0 = hz(midi)
        n = int((dur + release) * RATE)
        t = np.arange(n) / RATE
        table = self._table(partials, formant, f0)
        out = np.zeros(n)
        for c in detune:
            rate = vib_rate * self.rng.uniform(0.85, 1.15)
            vib = 1 + vibrato * np.sin(TAU * rate * t + self.rng.uniform(0, TAU)) * np.minimum(1, t / 0.8)
            phase = np.cumsum(f0 * 2 ** (c / 1200) * vib) / RATE + self.rng.uniform()
            out += self._read(table, phase)
        env = np.minimum(1.0, t / max(attack, 1e-3))
        if swell:                                       # rises through the note, then fades
            env *= np.sin(np.pi * np.minimum(t / max(dur, 1e-3), 1.0) * 0.5 + 0.25 * np.pi) ** 2
        tail = t > dur
        env[tail] *= np.exp(-(t[tail] - dur) * 6.9 / release)
        return out * env / len(detune) ** 0.5

    def struck(self, midi, partials, decay, length, attack=0.002):
        """partials: (ratio, amp, decay multiplier). Bells, piano, harp, glass."""
        f0 = hz(midi)
        n = int(length * RATE)
        t = np.arange(n) / RATE
        out = np.zeros(n)
        for r, a, dm in partials:
            if f0 * r > 16000:
                continue
            out += a * np.exp(-t * decay * dm) * np.sin(TAU * f0 * r * t + self.rng.uniform(0, TAU))
        out *= np.minimum(1.0, t / attack)
        fade = min(n, int(0.25 * RATE))                   # never stop a ringing note dead
        out[n - fade:] *= np.linspace(1, 0, fade) ** 2
        return out

    def noise(self, length, lo, hi):
        n = int(length * RATE)
        spec = np.fft.rfft(self.rng.standard_normal(n))
        f = np.fft.rfftfreq(n, 1 / RATE)
        spec[(f < lo) | (f > hi)] = 0
        x = np.fft.irfft(spec, n)
        return x / (np.std(x) or 1)

    # The orchestra.
    def strings(self, m, dur, gain=1.0, attack=0.5):
        p = [(k, (1 / k) * np.exp(-k / 9)) for k in range(1, 16)]
        return gain * self.sustained(m, dur, p, attack, 1.2, 0.003, 5.2, (-7, 0, 6))

    def pad(self, m, dur, gain=1.0):
        p = [(k, 1 / k ** 1.6) for k in range(1, 9)]
        return gain * self.sustained(m, dur, p, 1.8, 2.5, 0.0015, 0.3, (-9, 0, 8))

    def cello(self, m, dur, gain=1.0):
        body = lambda f: 1.0 + 1.6 * np.exp(-((f - 450) / 300) ** 2) + 0.5 * np.exp(-((f - 1100) / 400) ** 2)
        p = [(k, (1 / k) * np.exp(-k / 10)) for k in range(1, 16)]
        return gain * self.sustained(m, dur, p, 0.22, 0.6, 0.0045, 5.0, (0, 4), formant=body)

    def clarinet(self, m, dur, gain=1.0):
        p = [(k, (1 / k if k % 2 else 0.08 / k) * np.exp(-k / 6)) for k in range(1, 12)]
        return gain * self.sustained(m, dur, p, 0.05, 0.25, 0.002, 4.6)

    VOWELS = {"ah": ((700, 1.0), (1150, 0.5), (2700, 0.18)), "oo": ((320, 1.0), (870, 0.3), (2250, 0.08))}

    def choir(self, m, dur, gain=1.0, vowel="ah"):
        fm = self.VOWELS[vowel]
        formant = lambda f: sum(a * np.exp(-((f - c) / (c * 0.18)) ** 2) for c, a in fm) + 0.02
        p = [(k, 1 / k ** 0.6) for k in range(1, 36)]
        return gain * self.sustained(m, dur, p, 1.2, 1.8, 0.006, 5.4, (-12, -4, 3, 11), formant=formant)

    def piano(self, m, gain=1.0, length=4.0):
        b = 0.00035
        p = [(k * np.sqrt(1 + b * k * k), 1 / k ** 1.3, 1 + 0.55 * (k - 1)) for k in range(1, 12)]
        decay = 0.9 * (hz(m) / 262) ** 0.45
        return gain * self.struck(m, p, decay, length, 0.003)

    def harp(self, m, gain=1.0):
        p = [(k, 1 / k ** 1.7, 1 + 0.8 * (k - 1)) for k in range(1, 10)]
        return gain * self.struck(m, p, 1.6, 3.0, 0.004)

    def pluck_bass(self, m, gain=1.0):
        p = [(k, 1 / k, 1 + 0.9 * (k - 1)) for k in range(1, 10)]
        return gain * self.struck(m, p, 3.2, 1.4, 0.004)

    def bell(self, m, gain=1.0, length=6.0):
        p = [(0.5, 0.5, 0.5), (1, 1, 1), (1.19, 0.55, 1.3), (1.56, 0.45, 1.6), (2, 0.42, 1.8),
             (2.51, 0.28, 2.3), (2.66, 0.22, 2.5), (3.01, 0.18, 2.9), (4.1, 0.1, 3.6)]
        return gain * self.struck(m, p, 0.55, length, 0.002)

    def glass(self, m, gain=1.0):
        p = [(1, 1, 1), (2.32, 0.45, 1.6), (4.25, 0.25, 2.4), (6.63, 0.12, 3.2)]
        return gain * self.struck(m, p, 5.0, 1.2, 0.001)

    def metal(self, m, gain=1.0):
        p = [(1, 1, 1), (1.47, 0.7, 1.2), (2.09, 0.55, 1.5), (2.56, 0.4, 1.9), (3.37, 0.3, 2.4), (4.2, 0.2, 3.0)]
        hit = self.struck(m, p, 1.4, 4.0, 0.001)
        click = self.noise(0.08, 800, 6000) * np.exp(-np.arange(int(0.08 * RATE)) / RATE * 60) * 0.15
        hit[: len(click)] += click
        return gain * hit

    def drum(self, f0=60.0, f1=42.0, decay=6.0, gain=1.0, length=1.6):
        t = np.arange(int(length * RATE)) / RATE
        f = f1 + (f0 - f1) * np.exp(-t * 25)
        body = np.sin(TAU * np.cumsum(f) / RATE) * np.exp(-t * decay)
        skin = self.noise(length, 80, 900) * np.exp(-t * 45) * 0.25
        return gain * (body + skin) * np.minimum(1, t / 0.002)

    def hand_drum(self, low=True, gain=1.0):
        if low:
            return self.drum(140, 95, 9.0, gain, 0.6)
        t = np.arange(int(0.25 * RATE)) / RATE
        return gain * (self.noise(0.25, 1800, 7000) * np.exp(-t * 55) * 0.6
                       + np.sin(TAU * 420 * t) * np.exp(-t * 35))

    def shaker(self, gain=1.0):
        t = np.arange(int(0.12 * RATE)) / RATE
        return gain * self.noise(0.12, 5000, 12000) * np.minimum(1, t / 0.012) * np.exp(-t * 30)

    def drip(self, gain=1.0):
        t = np.arange(int(0.18 * RATE)) / RATE
        f = self.rng.uniform(700, 1300) * (1 + 1.2 * np.minimum(1, t / 0.03))
        return gain * np.sin(TAU * np.cumsum(f) / RATE) * np.exp(-t * 28)

    def breath(self, dur, gain=1.0):
        t = np.arange(int(dur * RATE)) / RATE
        return gain * self.noise(dur, 400, 3000) * np.sin(np.pi * t / dur) ** 2


# ── The loop buffer ──────────────────────────────────────────────────────────

class Loop:
    """A loop of fixed length. Anything that runs past the end wraps to the start;
    the reverb is a circular convolution, so its tail wraps too."""

    def __init__(self, seconds: float, synth: Synth):
        self.n = int(round(seconds * RATE))
        self.dry = np.zeros(self.n)
        self.send = np.zeros(self.n)
        self.s = synth

    def add(self, t: float, sig: np.ndarray, gain: float = 1.0, wet: float = 0.3) -> None:
        sig = sig * gain
        fade = min(len(sig) // 4, int(0.03 * RATE))      # a sound cut off mid-ring clicks, and the
        if fade:                                          # reverb smears the click into seconds of hiss
            sig[len(sig) - fade:] *= np.linspace(1, 0, fade) ** 2
        i = int(round(t * RATE)) % self.n
        pos = 0
        while pos < len(sig):
            take = min(len(sig) - pos, self.n - i)
            self.dry[i:i + take] += sig[pos:pos + take] * (1 - wet * 0.5)
            self.send[i:i + take] += sig[pos:pos + take] * wet
            pos += take
            i = 0

    def drone(self, midi: float, partials, gain: float, swells: int = 2, depth: float = 0.5) -> None:
        """A tone held for the whole loop, with a whole number of cycles (and of
        swells) per loop, so it joins itself exactly."""
        cycles = max(1, round(hz(midi) * self.n / RATE))
        t = np.arange(self.n) / self.n
        table = self.s._table(partials)
        tone = self.s._read(table, cycles * t + self.s.rng.uniform())
        lfo = 1 - depth * 0.5 * (1 - np.cos(TAU * swells * t + self.s.rng.uniform(0, TAU)))
        self.dry += gain * tone * lfo
        self.send += gain * tone * lfo * 0.4

    def render(self, t60: float = 3.0, darkness: float = 3000.0) -> np.ndarray:
        ln = min(self.n, int((t60 + 0.05) * RATE))
        t = np.arange(ln) / RATE
        ir = self.s.rng.standard_normal(ln) * np.exp(-t * 6.9 / t60)
        ir[: int(0.025 * RATE)] = 0                       # pre-delay
        spec = np.fft.rfft(ir)
        f = np.fft.rfftfreq(ln, 1 / RATE)
        ir = np.fft.irfft(spec / (1 + (f / darkness) ** 2), ln)
        ir /= np.sqrt(np.sum(ir ** 2)) or 1
        wet = np.fft.irfft(np.fft.rfft(self.send) * np.fft.rfft(ir, self.n), self.n)
        return self.dry + wet * 0.9


# ── Melody ───────────────────────────────────────────────────────────────────

def melody(rng, bars, scale_pcs, lo, hi, rhythms, beats=4, phrase=4, start=None, rest_bars=()):
    """A singable line over ``bars`` (one chord's pitch classes per bar): two-bar
    motifs, reused with their contour snapped to the new chord, a held chord tone
    closing each phrase. Returns (bar, beat, beats_long, midi)."""
    pitches = [m for m in range(lo, hi + 1) if m % 12 in scale_pcs]
    idx = len(pitches) // 2 if start is None else min(range(len(pitches)), key=lambda i: abs(pitches[i] - start))
    motif = None
    out = []
    for b, cpcs in enumerate(bars):
        if b in rest_bars:
            continue
        if b % phrase == phrase - 1:                      # cadence: a long chord tone
            tones = [i for i, m in enumerate(pitches) if m % 12 in cpcs]
            idx = min(tones, key=lambda i: abs(i - idx))
            out.append((b, 0, beats - 0.5 if rng.random() < 0.5 else beats * 0.75, pitches[idx]))
            continue
        if b % 2 == 0 and (motif is None or rng.random() < 0.35):
            motif = []
            for _ in range(2):
                pat = rhythms[rng.integers(len(rhythms))]
                motif.append([(d, int(rng.choice([-2, -1, -1, 1, 1, 2, 0, 3]))) for d in pat])
        pos = 0.0
        for d, step in motif[b % 2]:
            if d < 0:                                     # a rest
                pos += -d
                continue
            idx = int(np.clip(idx + step, 0, len(pitches) - 1))
            if pos % 2 == 0:                              # strong beats land on the chord
                tones = [i for i, m in enumerate(pitches) if m % 12 in cpcs]
                idx = min(tones, key=lambda i: abs(i - idx))
            if idx in (0, len(pitches) - 1):              # bounce off the edges of the range
                idx += 2 if idx == 0 else -2
            out.append((b, pos, d, pitches[idx]))
            pos += d
    return out


# ── The moods ────────────────────────────────────────────────────────────────

def _per_bar(prog: list[str], bars_each: int) -> list[tuple[int, ...]]:
    return [chord(c) for c in prog for _ in range(bars_each)]


def explore(bars=24):
    """The village, the road: melancholy cello, soft strings, sparse piano, a hopeful lift."""
    s = Synth(11)
    bpm, beat = 66, 60 / 66
    bar = 4 * beat
    prog = ["Dm", "Bb", "F", "C", "Dm", "Bb", "Gm", "A", "Bb", "F", "Gm", "A"]
    per = _per_bar(prog, 2)[:bars]
    L = Loop(len(per) * bar, s)
    L.drone(38, [(1, 1), (2, 0.3)], 0.035, swells=3)
    for i, c in enumerate(prog):
        t = i * 2 * bar
        if t >= L.n / RATE:
            break
        for m in voicing(chord(c), 50, 66)[:4]:
            L.add(t, s.strings(m, 2 * bar - 0.2, attack=1.2), 0.085, 0.5)
        L.add(t, s.cello(voicing(chord(c), 38, 49)[0], 2 * bar - 0.3), 0.12, 0.3)
    line = melody(s.rng, per, scale("D", MINOR), 55, 72, [[2, 1, 1], [1.5, 0.5, 2], [3, 1], [1, 1, 2]],
                  rest_bars=(0, 1, 2, 3))
    for b, pos, d, m in line:
        L.add(b * bar + pos * beat, s.cello(m, d * beat), 0.2, 0.35)
    for b, cpcs in enumerate(per):
        for slot in (0, 1.5, 3):
            if s.rng.random() < (0.85 if b < 4 else 0.45):
                m = s.rng.choice(voicing(cpcs, 69, 84))
                L.add(b * bar + slot * beat, s.piano(int(m), length=3.5), 0.11, 0.45)
    for b in range(16, len(per)):
        for m in voicing(per[b], 62, 74)[:3]:
            L.add(b * bar, s.choir(m, bar - 0.2, vowel="oo"), 0.028, 0.6)
    return L.render(3.2, 3500)


def dungeon(bars=18):
    """The descent: low drones, slow metal strikes, drips, a bell that shouldn't be there."""
    s = Synth(23)
    beat = 60 / 52
    bar = 4 * beat
    L = Loop(bars * bar, s)
    L.drone(33, [(1, 1), (2, 0.5), (3, 0.25), (4, 0.15), (5, 0.08)], 0.12, swells=3, depth=0.6)
    L.drone(40, [(1, 1), (2, 0.3), (3, 0.1)], 0.05, swells=2, depth=0.8)
    prog = ["Am", "Bb", "Am", "Gm", "Am", "Bb", "F", "E"]
    per = _per_bar(prog, 2)
    for b in range(0, bars, 2):
        for m in voicing(per[b % len(per)], 45, 57)[:3]:
            L.add(b * bar, s.pad(m, 2 * bar - 0.5), 0.05, 0.6)
    for b in range(bars):
        L.add(b * bar, s.metal(33 + 12, 1.0), 0.22, 0.6)
        if s.rng.random() < 0.45:
            L.add(b * bar + 2.5 * beat, s.metal(57 + int(s.rng.choice([0, 1, 3])), 1.0), 0.06, 0.8)
    for _ in range(16):
        L.add(s.rng.uniform(0, bars * bar), s.drip(), 0.09, 0.85)
    motifs = [[69, 70, 72, 70], [76, 74, 70, 69], [69, 72, 76, 75]]
    for k, b in enumerate(range(2, bars, 4)):
        for j, m in enumerate(motifs[k % len(motifs)]):
            L.add(b * bar + j * 1.5 * beat, s.bell(m, length=5.0), 0.07, 0.8)
    for b in range(8, min(bars, 12)):
        for m in (81, 82):
            L.add(b * bar, s.strings(m, bar, attack=2.0), 0.012, 0.8)
    return L.render(5.0, 2500)


def dungeon_deep(bars=16):
    """Floor 3: subterranean drones, whispering choir, cracking crystal, dread."""
    s = Synth(37)
    beat = 60 / 44
    bar = 4 * beat
    L = Loop(bars * bar, s)
    L.drone(25, [(1, 1), (2, 0.6), (3, 0.35), (4, 0.2), (6, 0.1)], 0.13, swells=2, depth=0.5)
    L.drone(32, [(1, 1), (2, 0.4)], 0.05, swells=3, depth=0.7)
    L.drone(43, [(1, 1), (3, 0.2)], 0.018, swells=1, depth=1.0)       # the tritone, barely there
    clusters = [(61, 62), (56, 57), (61, 64), (55, 56)]
    for k, b in enumerate(range(0, bars, 2)):
        for m in clusters[k % len(clusters)]:
            L.add(b * bar + beat, s.choir(m, 2 * bar - beat, vowel="oo"), 0.03, 0.7)
        L.add(b * bar + beat, s.breath(2 * bar - beat), 0.012, 0.8)
    for b in range(0, bars, 2):
        L.add(b * bar, s.drum(48, 34, 3.5, length=2.5), 0.3, 0.4)
        L.add(b * bar + 0.75 * beat, s.drum(44, 32, 4.5, length=2.0), 0.16, 0.4)
    crystal = voicing((1, 2, 4, 6, 7, 9, 11), 85, 99)
    for _ in range(26):
        t = s.rng.uniform(0, bars * bar)
        for j in range(int(s.rng.integers(2, 5))):
            L.add(t + j * s.rng.uniform(0.04, 0.12), s.glass(int(s.rng.choice(crystal))), 0.035, 0.85)
    return L.render(7.0, 2200)


def verath(bars=28):
    """The capital: stately strings, harp, a distant bell, quietly watchful."""
    s = Synth(41)
    beat = 60 / 76
    bar = 4 * beat
    prog = ["Em", "C", "G", "D", "Em", "Am", "C", "B7", "C", "G", "Am", "Em", "C", "B7"]
    per = _per_bar(prog, 2)[:bars]
    L = Loop(len(per) * bar, s)
    for i, c in enumerate(prog):
        t = i * 2 * bar
        for m in voicing(chord(c), 52, 67)[:4]:
            L.add(t, s.strings(m, 2 * bar - 0.15, attack=0.8), 0.075, 0.45)
        L.add(t, s.cello(voicing(chord(c), 36, 47)[0], 2 * bar - 0.2), 0.13, 0.3)
    for b, cpcs in enumerate(per):
        notes = voicing(cpcs, 64, 86)
        up = notes[:8] if b % 2 == 0 else notes[:8][::-1]
        for j, m in enumerate(up):
            L.add(b * bar + j * 0.5 * beat, s.harp(m), 0.06, 0.4)
    for b in range(0, len(per), 4):
        L.add(b * bar, s.bell(64, length=7.0), 0.1, 0.95)
    line = melody(s.rng, per, scale("E", HARM_MINOR), 55, 71, [[2, 2], [3, 1], [2, 1, 1], [1, 1, 2]],
                  rest_bars=tuple(range(8)))
    for b, pos, d, m in line:
        L.add(b * bar + pos * beat, s.cello(m, d * beat), 0.17, 0.35)
    for b in range(8, len(per)):
        L.add(b * bar, s.drum(70, 55, 4.0), 0.12, 0.4)
    for b in range(16, 24):
        L.add(b * bar, s.strings(83, bar, attack=1.5), 0.012, 0.7)
    return L.render(3.0, 3800)


def undermarket(bars=24):
    """The Undermarket: plucked bass, hand drum, a muted clarinet up to something."""
    s = Synth(53)
    beat = 60 / 96
    bar = 4 * beat
    L = Loop(bars * bar, s)
    riff = [(0, 40), (0.75, 40), (1.5, 41), (2, 40), (2.75, 35), (3.5, 38)]
    turn = [(0, 40), (0.75, 40), (1.5, 41), (2, 40), (3, 44), (3.5, 41)]
    prog = ["E", "E", "F", "E"]
    per = [chord(prog[(b // 2) % 4]) for b in range(bars)]
    for b in range(bars):
        shift = 1 if prog[(b // 2) % 4] == "F" else 0
        for pos, m in (turn if b % 4 == 3 else riff):
            L.add(b * bar + pos * beat, s.pluck_bass(m + shift), 0.3, 0.15)
        for pos, low in ((0, True), (0.5, False), (1.5, False), (2, True), (3, False)):
            L.add(b * bar + pos * beat, s.hand_drum(low), 0.22 if low else 0.08, 0.15)
        if b >= 4:
            for k in range(8):
                L.add(b * bar + k * 0.5 * beat, s.shaker(), 0.025 if k % 2 else 0.04, 0.1)
        if b % 2 == 0:
            for m in voicing(per[b], 52, 64)[:3]:
                L.add(b * bar, s.strings(m, 2 * bar - 0.3, attack=0.9), 0.03, 0.4)
    line = melody(s.rng, per, scale("E", PHRYG_DOM), 62, 76,
                  [[0.5, 0.5, -1, 1, 1], [1, 0.5, 0.5, -2], [-0.5, 0.5, 1, 1.5, 0.5], [1.5, 0.5, 2]],
                  rest_bars=(0, 1, 2, 3, 20, 21))
    for b, pos, d, m in line:
        L.add(b * bar + pos * beat, s.clarinet(m, d * beat * 0.9), 0.12, 0.3)
    return L.render(1.6, 3000)


def prologue(bars=16):
    """The opening: a lone piano, then strings rising under it, fateful."""
    s = Synth(67)
    beat = 1.0
    bar = 4 * beat
    prog = ["Am", "F", "C", "G", "Am", "F", "Dm", "E", "Am", "F", "C", "G", "F", "Dm", "E", "E"]
    per = [chord(c) for c in prog[:bars]]
    L = Loop(len(per) * bar, s)
    for b, cpcs in enumerate(per):
        low = voicing(cpcs, 45, 56)[0]
        L.add(b * bar, s.piano(low, length=4.5), 0.16, 0.4)
        arp = voicing(cpcs, 57, 76)
        for j, slot in enumerate((1, 2, 3)):
            L.add(b * bar + slot * beat, s.piano(arp[(j * 2 + b) % len(arp)], length=3.0), 0.1, 0.4)
    line = melody(s.rng, per, scale("A", HARM_MINOR), 69, 84, [[2, 2], [3, 1], [1, 1, 2], [4]])
    for b, pos, d, m in line:
        L.add(b * bar + pos * beat, s.piano(m, length=3.5), 0.14, 0.45)
    for b in range(4, len(per)):
        rise = min(1.0, (b - 3) / 8) * (1.0 if b < 13 else (len(per) - b) / 4)
        for m in voicing(per[b], 52, 69)[:4]:
            L.add(b * bar, s.strings(m, bar - 0.1, attack=1.0), 0.07 * rise, 0.5)
        L.add(b * bar, s.cello(voicing(per[b], 33, 44)[0], bar - 0.2), 0.12 * rise, 0.3)
    for b in range(8, min(14, len(per))):
        for m in voicing(per[b], 64, 76)[:3]:
            L.add(b * bar, s.choir(m, bar), 0.022, 0.6)
    return L.render(3.5, 3500)


def fracture_core(bars=15):
    """Where the Fracture began: a vast hum, glass crackling, voices high above."""
    s = Synth(79)
    bar = 4.0
    L = Loop(bars * bar, s)
    L.drone(26, [(k, 1 / k) for k in range(1, 12)], 0.1, swells=3, depth=0.5)
    L.drone(26.12, [(1, 1), (2, 0.5), (3, 0.3)], 0.06, swells=5, depth=0.6)    # beats against the hum
    L.drone(33, [(1, 1), (2, 0.2)], 0.03, swells=2, depth=0.9)
    for b in range(bars):
        L.add(b * bar, s.drum(40, 30, 3.0, length=2.5), 0.22, 0.5)
    tones = [(74, 81, 88), (74, 81, 84), (72, 79, 86)]
    for k, b in enumerate(range(0, bars, 3)):
        for m in tones[k % len(tones)]:
            L.add(b * bar + 1, s.choir(m, 7.0), 0.022, 0.8)
    for _ in range(45):
        t = s.rng.uniform(0, bars * bar)
        for j in range(int(s.rng.integers(1, 5))):
            L.add(t + j * s.rng.uniform(0.02, 0.09), s.glass(int(s.rng.integers(88, 101))), 0.03, 0.85)
    return L.render(6.0, 2600)


def ending(bars=20):
    """The close: bittersweet strings and choir, resolving slowly."""
    s = Synth(83)
    beat = 60 / 56
    bar = 4 * beat
    prog = ["Bb", "F", "Gm", "Eb", "Bb", "F", "Eb", "F", "Gm", "Eb", "Bb", "F",
            "Cm", "F", "Bb", "Gm", "Eb", "F", "Bb", "Bb"]
    per = [chord(c) for c in prog[:bars]]
    L = Loop(len(per) * bar, s)
    for b, cpcs in enumerate(per):
        for m in voicing(cpcs, 53, 69)[:4]:
            L.add(b * bar, s.strings(m, bar - 0.05, attack=0.9), 0.07, 0.5)
        L.add(b * bar, s.cello(voicing(cpcs, 34, 45)[0], bar - 0.1), 0.12, 0.3)
        if b >= 8:
            for m in voicing(cpcs, 62, 74)[:3]:
                L.add(b * bar, s.choir(m, bar), 0.022, 0.6)
        if s.rng.random() < 0.6:
            L.add(b * bar + 2 * beat, s.piano(int(s.rng.choice(voicing(cpcs, 74, 86))), length=4.0), 0.07, 0.5)
    line = melody(s.rng, per, scale("Bb", MAJOR), 55, 72, [[2, 2], [3, 1], [1, 1, 2], [2, 1, 1]])
    for b, pos, d, m in line:
        L.add(b * bar + pos * beat, s.cello(m, d * beat), 0.17, 0.4)
    return L.render(3.8, 3600)


MOODS = {"explore": explore, "dungeon": dungeon, "dungeon_deep": dungeon_deep, "verath": verath,
         "undermarket": undermarket, "prologue": prologue, "fracture_core": fracture_core, "ending": ending}


# ── Output ───────────────────────────────────────────────────────────────────

def master(x: np.ndarray) -> np.ndarray:
    """Loudness as master_audio measures it (the louder half of the track), so
    composed tracks sit level with the recorded ones."""
    from master_audio import rms as loud_rms
    x = x - np.mean(x)
    gain = 10 ** (TARGET_RMS_DB / 20) / (loud_rms(x) or 1e-9)
    ceiling = 10 ** (PEAK_CEILING_DB / 20)
    peak = np.max(np.abs(x)) * gain
    if peak > ceiling:
        gain *= ceiling / peak
    return x * gain


def render(mood: str, bars: int | None = None) -> np.ndarray:
    fn = MOODS[mood]
    return master(fn(bars) if bars else fn())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("moods", nargs="*", help=f"any of: {', '.join(MOODS)}")
    ap.add_argument("--all", action="store_true", help="every mood, overwriting")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("ffmpeg is needed: winget install ffmpeg / brew install ffmpeg")
        return 1
    from master_audio import encode
    out = Path(args.out)
    bad = [m for m in args.moods if m not in MOODS]
    if bad:
        print(f"Unknown mood(s): {bad}. Known: {', '.join(MOODS)}")
        return 1
    todo = args.moods or [m for m in MOODS if args.all or not any((out / f"{m}{e}").exists()
                                                                   for e in (".ogg", ".wav", ".mp3", ".flac"))]
    for mood in todo:
        y = render(mood)
        encode(y, out / f"{mood}.ogg")
        from master_audio import db, rms
        print(f"  {mood:14} {len(y) / RATE:5.1f}s  rms {db(rms(y)):6.1f}  peak {20 * np.log10(np.max(np.abs(y))):5.1f}")
    print(f"{len(todo)} track(s) written to {out}" if todo else "Every mood already has a track (--all to redo).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
