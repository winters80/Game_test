#!/usr/bin/env python3
"""
Build a sound pack for SYSTEM BREAKER on your own computer, then zip it.

    python scripts/fetch_sounds.py                      # Kenney's free CC0 packs only
    python scripts/fetch_sounds.py --extra "D:/Downloads/pixabay:pixabay" \\
                                   --extra "D:/Sonniss GDC 2024:sonniss"
    python scripts/fetch_sounds.py --list               # show what it would pick, write nothing

What it does:

1. Downloads Kenney's CC0 (public domain) audio packs from kenney.nl into
   ``sound_downloads/`` (once; later runs reuse them). Skip with --no-kenney.
2. Adds any folders you pass with --extra FOLDER:LICENSE, e.g. sounds you
   downloaded by hand from Pixabay or the Sonniss GDC bundle. LICENSE is a
   short label (cc0, pixabay, sonniss, ...); only ``cc0`` files may be
   committed to the public repo, the rest stay on your computer.
3. Picks a file for every sound the game plays (effects, music, ambience) by
   keywords in the file names and folders, trims it to a sensible length and
   converts it to .ogg when ffmpeg is installed (otherwise copies it as is).
4. Writes ``system_breaker_sounds.zip`` with the files under their game names,
   ``sounds_manifest.json`` (what came from where, under which license) and
   ``CREDITS.md``. Upload the zip and it gets integrated into the game.

Needs only Python 3.9+. ffmpeg (optional, recommended) trims and converts:
Windows ``winget install ffmpeg``, macOS ``brew install ffmpeg``.

Nothing here is run by the game. Keep your own Pixabay / Sonniss files out of
public repositories: their licenses allow them inside a game, not as raw files.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO_EXTS = (".ogg", ".wav", ".mp3", ".flac")
UA = {"User-Agent": "Mozilla/5.0 (sound-pack builder)"}

# Kenney asset pages (all CC0). The zip link is read from each page, so a new
# file name on Kenney's side doesn't break the script.
KENNEY_PACKS = {
    "rpg-audio": "RPG Audio",
    "impact-sounds": "Impact Sounds",
    "interface-sounds": "Interface Sounds",
    "ui-audio": "UI Audio",
    "sci-fi-sounds": "Sci-fi Sounds",
    "casino-audio": "Casino Audio",
    "music-jingles": "Music Jingles",
    "digital-audio": "Digital Audio",
}


@dataclass
class Target:
    path: str                         # game file without extension, under assets/
    keywords: list[list[str]]         # alternatives, best first; every word in one must match
    max_seconds: float                # trimmed to this
    kind: str = "sfx"                 # sfx / music / ambience
    avoid: list[str] = field(default_factory=list)


def _t(path, keywords, secs, kind="sfx", avoid=None):
    return Target(path, [k.split() for k in keywords], secs, kind, avoid or [])


# What the game plays (ui/media.SFX, MUSIC_FOR_*, scene ambience) and what to look for.
TARGETS = [
    _t("audio/sfx/system_chime", ["notification", "confirmation", "glass 00", "chime", "bong"], 1.5),
    _t("audio/sfx/level_up", ["level up", "jingles pizzi", "power up", "powerup", "success", "achievement"], 3),
    _t("audio/sfx/skill_acquired", ["magic", "maximize", "upgrade", "power up", "chime"], 2),
    _t("audio/sfx/class_assigned", ["jingles steel", "spell", "force field", "forcefield", "magic", "gong"], 3),
    _t("audio/sfx/class_milestone", ["jingles sax", "upgrade", "power up", "success"], 3),
    _t("audio/sfx/item_found", ["handle coins", "pickup", "pick up", "collect", "coin"], 1.5),
    _t("audio/sfx/hit", ["impact metal", "sword hit", "knife slice", "blade", "slash", "impact"], 1.2,
       avoid=["glass", "punch"]),
    _t("audio/sfx/spell", ["fireball", "spell", "laser large", "magic", "whoosh", "zap"], 1.8),
    _t("audio/sfx/enemy_hit", ["impact punch heavy", "punch", "body hit", "impact heavy"], 1.2),
    _t("audio/sfx/victory", ["jingles nes", "victory", "win", "fanfare", "jingles"], 4, kind="music"),
    _t("audio/sfx/defeat", ["defeat", "game over", "lose", "fail", "error"], 4),
    _t("audio/sfx/warden_whistle", ["whistle", "referee", "alarm", "siren"], 2),
    _t("audio/sfx/coins", ["handle coins", "coins", "chips stack", "chip lay", "cash", "coin"], 1.5),
    _t("audio/sfx/door", ["door open", "door", "creak", "metal latch"], 2),
    _t("audio/sfx/rift_open", ["portal", "force field", "forcefield", "space engine", "rumble", "explosion"], 3),
    _t("audio/sfx/gate_enter", ["portal", "teleport", "warp", "force field", "phaser"], 2.5),
    _t("audio/sfx/crystal_shatter", ["impact glass heavy", "glass break", "shatter", "impact glass", "glass"], 2),
    _t("audio/sfx/ledger_seal", ["stamp", "book close", "metal click", "impact plate", "click"], 1.2),
    _t("audio/sfx/potion", ["potion", "bottle", "drink", "liquid", "cork", "bubble", "metal pot"], 2),
    # Music (loops)
    _t("audio/music/explore", ["fantasy explore", "medieval", "adventure", "exploration", "ambient music"], 240, "music"),
    _t("audio/music/combat", ["battle", "combat", "fight", "action music"], 240, "music"),
    _t("audio/music/boss", ["boss", "epic battle", "epic", "dark battle"], 240, "music"),
    _t("audio/music/verath", ["city", "town", "kingdom", "castle", "medieval"], 240, "music"),
    _t("audio/music/ledger", ["tension", "suspense", "clock", "dark ambient"], 240, "music"),
    _t("audio/music/undermarket", ["tavern", "thief", "sneak", "stealth", "mystery"], 240, "music"),
    _t("audio/music/camp", ["campfire", "calm", "peaceful", "relax", "acoustic"], 240, "music"),
    _t("audio/music/dungeon", ["dungeon", "cave", "dark ambient", "horror ambient"], 240, "music"),
    _t("audio/music/dungeon_deep", ["deep dungeon", "dread", "horror", "dark drone", "drone"], 240, "music"),
    _t("audio/music/fracture_core", ["sci fi ambient", "cosmic", "space ambient", "drone"], 240, "music"),
    _t("audio/music/prologue", ["intro", "opening", "main theme", "title theme", "theme"], 240, "music"),
    _t("audio/music/ending", ["ending", "outro", "credits", "sad", "emotional"], 240, "music"),
    # Ambience (loops)
    _t("audio/ambience/village_start", ["market crowd", "medieval market", "market", "town crowd", "crowd"], 90, "ambience"),
    _t("audio/ambience/verath_city", ["city crowd", "town square", "medieval city", "city", "crowd"], 90, "ambience"),
    _t("audio/ambience/verath_city.undermarket", ["sewer", "drip", "underground", "cave water"], 90, "ambience"),
    _t("audio/ambience/verath_city.ledger_house", ["clock tick", "ticking", "office", "library", "room tone"], 90, "ambience"),
    _t("audio/ambience/dungeon_floor3.core_chamber", ["energy hum", "hum", "drone", "machine", "reactor"], 90, "ambience"),
    _t("audio/ambience/gate", ["portal loop", "eerie wind", "otherworld", "wind howl", "wind"], 90, "ambience"),
    _t("audio/ambience/camp_rest", ["campfire", "fire crackle", "crickets", "night forest", "fire"], 90, "ambience"),
    _t("audio/ambience/dungeon_floor1", ["cave", "dungeon", "drip", "underground"], 90, "ambience"),
    _t("audio/ambience/dungeon_floor2", ["crystal cave", "cave", "dungeon", "underground"], 90, "ambience"),
    _t("audio/ambience/dungeon_floor3", ["dungeon", "catacomb", "crypt", "dark ambience", "cave"], 90, "ambience"),
]


@dataclass
class Source:
    path: Path
    license: str
    origin: str                       # pack / folder name
    words: list[str]                  # lowercase words from the path


def words_of(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # handleCoins -> handle Coins
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)            # glass_001 -> glass 001
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


# ── Downloading ──────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def kenney_zip_url(slug: str) -> str | None:
    page = _get(f"https://kenney.nl/assets/{slug}").decode("utf-8", "replace")
    links = re.findall(r'href="([^"]+\.zip)"', page)
    if not links:
        return None
    url = links[0]
    return url if url.startswith("http") else "https://kenney.nl" + url


def download_kenney(cache: Path) -> list[tuple[Path, str, str]]:
    """[(extracted folder, license, origin)] for every Kenney pack we could get."""
    out = []
    for slug, name in KENNEY_PACKS.items():
        folder = cache / f"kenney_{slug}"
        if folder.exists() and any(folder.rglob("*")):
            print(f"  ✓ {name} (already downloaded)")
            out.append((folder, "cc0", f"Kenney — {name}"))
            continue
        try:
            url = kenney_zip_url(slug)
            if not url:
                print(f"  ✗ {name}: no download link on its page (skipped)")
                continue
            print(f"  ↓ {name} …", end="", flush=True)
            data = _get(url, timeout=300)
            zpath = cache / f"kenney_{slug}.zip"
            zpath.write_bytes(data)
            extract_audio(zpath, folder)
            zpath.unlink()
            print(f" {len(data) // 1024} KB")
            out.append((folder, "cc0", f"Kenney — {name}"))
        except Exception as e:                       # network, 404, bad zip: carry on
            print(f"  ✗ {name}: {e}")
    return out


def extract_audio(zpath: Path, folder: Path) -> None:
    """Extract only audio files, safely (no paths outside ``folder``)."""
    folder.mkdir(parents=True, exist_ok=True)
    base = folder.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or not name.lower().endswith(AUDIO_EXTS):
                continue
            target = (folder / name).resolve()
            if base not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


# ── Choosing ─────────────────────────────────────────────────────────────────

def index(folders: list[tuple[Path, str, str]]) -> list[Source]:
    out = []
    for folder, lic, origin in folders:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS and p.stat().st_size < 200_000_000:
                rel = p.relative_to(folder)
                out.append(Source(p, lic, origin, words_of(" ".join(rel.with_suffix("").parts))))
    return out


def duration(p: Path) -> float | None:
    if p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p)) as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            return None
    if shutil.which("ffprobe"):
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, timeout=30)
            return float(r.stdout.strip())
        except Exception:
            return None
    return None


def _matches(words: list[str], key: list[str]) -> bool:
    """Every keyword starts some word ("coin" matches "coins", "lose" doesn't match "close")."""
    return all(any(w.startswith(k) for w in words) if len(k) > 3 else k in words for k in key)


# Words that rule a file out for a kind of target (a crowd recording isn't music).
KIND_AVOID = {
    "music": ["ambience", "ambiance", "atmosphere", "crowd", "crackling", "crackle", "foley", "sfx", "footstep"],
    "ambience": ["music", "song", "theme", "jingle", "jingles"],
    "sfx": ["music", "song", "loop", "ambience", "ambiance"],
}


def score(src: Source, target: Target) -> float | None:
    if any(a in src.words for a in target.avoid):
        return None
    # Jingles may be sfx (victory) even though they're "music".
    kind_avoid = [w for w in KIND_AVOID.get(target.kind, []) if not any(w in k for k in target.keywords)]
    if any(w in src.words for w in kind_avoid):
        return None
    for rank, key in enumerate(target.keywords):
        if _matches(src.words, key):
            s = 100.0 - rank * 10 + len(key) * 3
            if src.license == "cc0":
                s += 2                                  # prefer files we can commit
            if target.kind in ("music", "ambience") and src.license != "cc0":
                s += 4                                  # but Pixabay/Sonniss loops beat short jingles
            nums = [int(w) for w in src.words if w.isdigit()]
            s -= min(nums) * 0.01 if nums else 0        # first variant of a numbered set
            return s
    return None


def choose(sources: list[Source], targets: list[Target]) -> dict[str, Source]:
    picked: dict[str, Source] = {}
    used: set[Path] = set()
    for t in targets:
        best, best_s = None, None
        for src in sources:
            s = score(src, t)
            if s is None:
                continue
            if src.path in used:
                s -= 15                                  # vary: a different file if there is one
            if best_s is None or s > best_s:
                best, best_s = src, s
        if best is not None:
            picked[t.path] = best
            used.add(best.path)
    return picked


# ── Writing ──────────────────────────────────────────────────────────────────

def convert(src: Path, dst_stem: Path, target: Target, ffmpeg: str | None) -> Path:
    dst_stem.parent.mkdir(parents=True, exist_ok=True)
    if ffmpeg:
        dst = dst_stem.with_suffix(".ogg")
        fade = 0.05 if target.kind == "sfx" else 1.0
        cmd = [ffmpeg, "-y", "-v", "error", "-i", str(src), "-t", str(target.max_seconds),
               "-af", f"silenceremove=start_periods=1:start_threshold=-50dB,afade=t=out:st={max(0.0, target.max_seconds - fade)}:d={fade}",
               "-ac", "1" if target.kind == "sfx" else "2", "-c:a", "libvorbis", "-q:a", "4", str(dst)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return dst
        print(f"    (ffmpeg couldn't convert {src.name}; copying it as is)")
    dst = dst_stem.with_suffix(src.suffix.lower())
    shutil.copyfile(src, dst)
    return dst


def build(picked: dict[str, Source], targets: list[Target], out_dir: Path, zip_path: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    by_path = {t.path: t for t in targets}
    manifest = {"version": 1, "files": []}
    for path, src in picked.items():
        dst = convert(src.path, out_dir / path, by_path[path], ffmpeg)
        manifest["files"].append({
            "file": dst.relative_to(out_dir).as_posix(),
            "source": src.path.name,
            "origin": src.origin,
            "license": src.license,
            "seconds": duration(dst),
        })
    manifest["missing"] = [t.path for t in targets if t.path not in picked]
    (out_dir / "sounds_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    lines = ["# Sound credits", ""]
    for origin in sorted({f["origin"] for f in manifest["files"]}):
        lic = next(f["license"] for f in manifest["files"] if f["origin"] == origin)
        lines.append(f"## {origin} ({lic})")
        lines += [f"- `{f['file']}` ← {f['source']}" for f in manifest["files"] if f["origin"] == origin]
        lines.append("")
    (out_dir / "CREDITS.md").write_text("\n".join(lines), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(out_dir).as_posix())
    return manifest


def parse_extra(spec: str) -> tuple[Path, str, str]:
    # "D:/folder:pixabay" — the license label is after the last colon (Windows drives have one too)
    folder, _, lic = spec.rpartition(":")
    if not folder or "/" in lic or "\\" in lic:
        folder, lic = spec, "other"
    p = Path(folder).expanduser()
    return p, lic.strip().lower() or "other", p.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--extra", action="append", default=[], metavar="FOLDER:LICENSE",
                    help="a folder of sounds you downloaded (e.g. 'D:/pixabay:pixabay'); repeatable")
    ap.add_argument("--no-kenney", action="store_true", help="don't download Kenney's CC0 packs")
    ap.add_argument("--cache", default=str(ROOT / "sound_downloads"), help="where downloads are kept")
    ap.add_argument("--out", default=str(ROOT / "system_breaker_sounds.zip"), help="the zip to write")
    ap.add_argument("--list", action="store_true", help="only show what would be picked")
    args = ap.parse_args(argv)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    folders: list[tuple[Path, str, str]] = []
    if not args.no_kenney:
        print("Kenney CC0 packs:")
        folders += download_kenney(cache)
    for spec in args.extra:
        p, lic, origin = parse_extra(spec)
        if not p.is_dir():
            print(f"  ✗ {p} isn't a folder (skipped)")
            continue
        for z in p.rglob("*.zip"):                       # unpack zips found in your folder
            dest = cache / f"extra_{z.stem}"
            if not dest.exists():
                extract_audio(z, dest)
            folders.append((dest, lic, f"{origin}/{z.stem}"))
        folders.append((p, lic, origin))

    sources = index(folders)
    print(f"\n{len(sources)} sound files found.")
    if not sources:
        print("Nothing to pick from. Check your internet connection or pass --extra FOLDER:LICENSE.")
        return 1
    picked = choose(sources, TARGETS)
    for t in TARGETS:
        s = picked.get(t.path)
        print(f"  {t.path:45} ← {s.origin}: {s.path.name} [{s.license}]" if s else f"  {t.path:45}   (nothing found)")
    if args.list:
        return 0

    out_zip = Path(args.out)
    manifest = build(picked, TARGETS, cache / "_pack", out_zip)
    n, miss = len(manifest["files"]), len(manifest["missing"])
    print(f"\n✓ {n} sounds written to {out_zip}  ({miss} not found"
          f"{'' if shutil.which('ffmpeg') else '; install ffmpeg to trim and convert them'})")
    print("Upload that zip to integrate it into the game.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
