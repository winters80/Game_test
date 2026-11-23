#!/usr/bin/env python3
"""
Master the game's sounds so they sit together: even loudness per kind,
headroom so layers don't distort, seamless loops, no clicks.

    python scripts/master_audio.py --src D:/Stuff/Sounds/staged    # originals laid out like assets/audio
    python scripts/master_audio.py --report                         # just measure assets/audio

``--src`` is a folder laid out like ``assets/audio`` (``music/explore.wav``,
``sfx/hit.wav`` …) holding the *original* files; the mastered ``.ogg`` files are
written to ``assets/audio`` (or ``--out``). Always master from originals: a loop
is shortened by its crossfade each time it's processed.

What it does, by kind (``KINDS``):

* every file: DC offset and rumble removed (30 Hz high-pass), loudness set to the
  kind's target RMS, peaks kept under -3 dBFS so music + ambience + effects can
  play together without clipping;
* loops (music, ambience, the heartbeat): the end is crossfaded into the start,
  so there is no pop when the file wraps around;
* one-shots (effects, voices): leading silence trimmed, 3 ms fade-in and a short
  fade-out so they never start or stop with a click;
* menu sounds: softened (5 kHz low-pass) and kept quiet, they play constantly.

Needs ffmpeg and numpy.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RATE = 44100
EXTS = (".wav", ".ogg", ".mp3", ".flac")

# kind: (target RMS dBFS, loop crossfade seconds or 0 for a one-shot, extra ffmpeg filters)
KINDS = {
    "music":    (-24.0, 3.0, ""),
    "ambience": (-30.0, 2.0, ""),
    "loop":     (-26.0, 0.12, "lowpass=f=4000"),   # the heartbeat
    "sfx":      (-21.0, 0, ""),
    "ui":       (-32.0, 0, "lowpass=f=5000"),
    "voice":    (-20.0, 0, ""),
}
PEAK_CEILING_DB = -3.0


def kind_of(rel: str) -> str:
    top = rel.split("/", 1)[0]
    name = Path(rel).stem
    if top == "sfx" and name == "heartbeat":
        return "loop"
    if top == "sfx" and (name.startswith("ui_") or name in ("denied", "menu_open")):
        return "ui"
    return top if top in KINDS else "sfx"


def decode(path: Path, extra: str) -> np.ndarray:
    af = "highpass=f=30" + (f",{extra}" if extra else "")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                          "-af", af, "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-12))


def rms(x: np.ndarray) -> float:
    """RMS of the louder half of 50 ms blocks, so silence doesn't drag it down."""
    n = int(RATE * 0.05)
    if len(x) < n * 2:
        return float(np.sqrt(np.mean(x ** 2)))
    blocks = np.sqrt(np.mean(x[: len(x) // n * n].reshape(-1, n) ** 2, axis=1))
    loud = np.sort(blocks)[len(blocks) // 2:]
    return float(np.sqrt(np.mean(loud ** 2)))


def seamless(x: np.ndarray, seconds: float) -> np.ndarray:
    """Crossfade the tail into the head: the result wraps around without a seam."""
    n = min(int(RATE * seconds), len(x) // 4)
    if n < 2:
        return x
    head, body, tail = x[:n], x[n:len(x) - n], x[len(x) - n:]
    t = np.linspace(0, np.pi / 2, n)
    blended = head * np.sin(t) + tail * np.cos(t)        # equal-power
    return np.concatenate([blended, body])


def one_shot(x: np.ndarray) -> np.ndarray:
    thr = max(np.max(np.abs(x)) * 10 ** (-45 / 20), 1e-4)    # 45 dB below the peak
    idx = np.flatnonzero(np.abs(x) > thr)
    if len(idx):
        x = x[max(0, idx[0] - int(RATE * 0.003)):]
    fi, fo = min(len(x), int(RATE * 0.003)), min(len(x) // 3, int(RATE * 0.03))
    x = x.copy()
    if fi:
        x[:fi] *= np.linspace(0, 1, fi)
    if fo:
        x[-fo:] *= np.linspace(1, 0, fo)
    return x


def master(x: np.ndarray, kind: str) -> np.ndarray:
    target, xfade, _ = KINDS[kind]
    x = x - np.mean(x)
    x = seamless(x, xfade) if xfade else one_shot(x)
    gain = 10 ** ((target - db(rms(x))) / 20)
    peak = np.max(np.abs(x)) * gain
    ceiling = 10 ** (PEAK_CEILING_DB / 20)
    if peak > ceiling:                                   # headroom wins over loudness
        gain *= ceiling / peak
    return x * gain


def encode(x: np.ndarray, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                    "-c:a", "libvorbis", "-q:a", "5", str(out)], input=x.astype(np.float64).tobytes(), check=True)


def measure(path: Path) -> tuple[float, float, float, float]:
    x = decode(path, "")
    return len(x) / RATE, db(np.max(np.abs(x))), db(rms(x)), float(abs(x[-1] - x[0]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", help="folder of originals laid out like assets/audio")
    ap.add_argument("--out", default=str(ROOT / "assets" / "audio"))
    ap.add_argument("--report", action="store_true", help="measure the files in --out and stop")
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("ffmpeg is needed: winget install ffmpeg / brew install ffmpeg")
        return 1
    out = Path(args.out)
    if args.report or not args.src:
        print(f"{'file':48} {'kind':8} {'sec':>5} {'peak':>6} {'rms':>6} {'seam':>5}")
        for p in sorted(out.rglob("*")):
            if p.suffix.lower() in EXTS:
                rel = p.relative_to(out).as_posix()
                s, pk, r, seam = measure(p)
                print(f"{rel:48} {kind_of(rel):8} {s:5.1f} {pk:6.1f} {r:6.1f} {seam:5.2f}")
        return 0
    src = Path(args.src)
    files = [p for p in sorted(src.rglob("*")) if p.suffix.lower() in EXTS]
    for p in files:
        rel = p.relative_to(src).with_suffix("").as_posix()
        kind = kind_of(rel)
        y = master(decode(p, KINDS[kind][2]), kind)
        encode(y, out / f"{rel}.ogg")
        print(f"  {rel:46} {kind:8} rms {db(rms(y)):6.1f}  peak {db(np.max(np.abs(y))):5.1f}")
    print(f"{len(files)} files mastered into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
