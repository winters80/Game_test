"""
Art and audio: everything optional, found by file name under ``assets/``.

Drop files in and they're used; leave them out and the game is silent and
text-only, exactly as before. Missing libraries (pygame-ce for sound,
textual-image for pictures) or a machine without a sound device are fine too.

    assets/images/scenes/<scene_id>.<node_id>.png   (falls back to <scene_id>.png)
    assets/images/npcs/<npc_id>.png
    assets/images/bosses/<occupant_id>.png          (or the enemy template id)
    assets/images/items/<item_id>.png
    assets/audio/sfx/<name>.ogg                     (see SFX)
    assets/audio/ambience/<scene_id>.ogg            (loops while you're there)
    assets/audio/music/<mood>.ogg                   (explore, combat, boss, ledger, verath …)
    assets/audio/voice/system/<key>.ogg             (the System's lines, see SYSTEM_LINES)
    assets/audio/voice/<npc_id>/greeting.ogg        (played when you talk to them)

Images: .png .jpg .jpeg .webp. Audio: .ogg .mp3 .wav. ``scripts/asset_list.py``
writes assets/ASSET_LIST.md: every file the game can use, with a prompt.

Pictures show in the panel and browser versions (the art box at the top of
the sidebar); sound plays on the machine running the game.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from pathlib import Path

from config import BASE_DIR, SAVES_DIR

logger = logging.getLogger(__name__)

ASSETS_DIR = BASE_DIR / "assets"
# Files you may not publish (Pixabay, Sonniss, ...): same layout, git-ignored, looked at first.
LOCAL_ASSETS_DIR = BASE_DIR / "assets_local"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXT = (".ogg", ".mp3", ".wav")
SETTINGS_FILE = SAVES_DIR / "settings" / "media.json"

DEFAULTS = {"images": True, "ui_sounds": True, "ui_hover": False, "muted": [], "sfx": True, "music": True, "ambience": True, "voice": True,
            "volume_sfx": 0.8, "volume_music": 0.45, "volume_ambience": 0.5, "volume_voice": 1.0}

# Sound effects the game plays, and when.
SFX = {
    "system_chime": "a System message or notice",
    "level_up": "you level up",
    "skill_acquired": "you learn a skill",
    "class_assigned": "the System assigns your class",
    "class_milestone": "a class milestone (every 4 levels)",
    "item_found": "you find an item",
    "hit": "a weapon hits",
    "spell": "a spell is cast",
    "enemy_hit": "an enemy hits you",
    "victory": "you win a fight",
    "defeat": "you fall",
    "warden_whistle": "a crime is seen / the wardens stop you",
    "coins": "gold changes hands (fines, sales)",
    "door": "you enter a new place",
    "rift_open": "a new Gate opens",
    "gate_enter": "you step through a Gate",
    "crystal_shatter": "a crystal creature breaks",
    "ledger_seal": "the Ledger records an entry (a crime on file, a fine paid)",
    "potion": "you drink a potion",
    "mana_potion": "you drink a mana potion",
    "ui_hover": "you move between choices (panel interface)",
    "ui_confirm": "you pick a choice",
    "ui_cancel": "you back out of a menu",
    "denied": "something is refused (not enough gold, an error)",
    "menu_open": "you open your items or the quest journal",
    "swing": "you swing a basic attack",
    "block": "a ward or evade takes an enemy's hit",
    "player_hurt": "a heavy hit lands on you (15%+ of your HP)",
    "dodge": "you dodge an attack, or get away from a fight",
    "chest_open": "a treasure room's chest opens",
    "trap": "a trap goes off",
    "lever": "you push into a boss's lair",
    "footstep_stone": "you arrive somewhere paved or rocky",
    "footstep_water": "you arrive in the flooded caverns",
    "footstep_dirt": "you arrive on dirt and cobbles",
    "heartbeat": "loops while your HP is below 30% in a fight",
    "boss_reveal": "a boss fight begins",
    "boss_warning": "an enemy gathers itself for a heavy blow next turn",
    "enrage": "an enemy becomes enraged",
    "summon": "an enemy calls help into the fight",
    "life_lost": "you lose one of your lives (or the last one)",
}

# The low-health heartbeat loops on its own channel (audio/sfx/heartbeat), below this share of HP.
HEARTBEAT_BELOW = 0.3
# Footsteps when you travel, by the ground where you arrive.
FOOTSTEP_FOR_SCENE = {"village_start": "dirt", "camp_rest": "dirt", "verath_city": "stone",
                      "dungeon_floor1": "stone", "dungeon_floor2": "water", "dungeon_floor3": "stone"}

# Moods for music: which file plays where.
MUSIC_FOR_SCENE = {"verath_city": "verath", "village_start": "explore", "camp_rest": "camp",
                   "dungeon_floor1": "dungeon", "dungeon_floor2": "dungeon", "dungeon_floor3": "dungeon_deep",
                   "prologue": "prologue", "character_creation": "prologue",
                   "ending_rule": "ending", "ending_destroy": "ending"}
MUSIC_FOR_NODE = {"ledger_house": "ledger", "undermarket": "undermarket", "core_chamber": "fracture_core",
                  "inn_entrance": "camp"}

# A mood without its own track plays a similar one (then explore).
MUSIC_FALLBACK = {"verath": "ledger", "undermarket": "ledger", "fracture_core": "boss",
                  "dungeon_deep": "dungeon", "ending": "camp", "prologue": "explore"}

# Bot adventurers' voices: one preset per archetype (audio/voice/bots/<archetype>/greeting).
BOT_VOICES = {"explorer": "the eager scout", "fighter": "the cynical sellsword",
              "crafter": "the bookish arcanist", "trader": "the pragmatic veteran",
              "gatherer": "the pragmatic veteran"}

# The System's voiced lines (record them with a TTS model; see ASSET_LIST.md).
SYSTEM_LINES = {
    "welcome": "Registration complete. Welcome to the System.",
    "classification_complete": "Classification complete.",
    "divergence_detected": "Divergence detected. Analysing.",
    "level_up": "Level up.",
    "skill_acquired": "New skill acquired.",
    "class_milestone": "Class milestone reached.",
    "reported_to_ledger": "The System has reported you to the Ledger.",
    "new_rift": "A new Gate has opened.",
    "quest_complete": "Quest complete. Rewards issued.",
    "evolution": "Evolution complete. Biological framework updated.",
    "rank_changed": "Standing updated. New rank recorded.",
    "life_lost": "A life has been spent. Restoring.",
    "game_over": "All lives spent. Your record is closed.",
}


# ── Settings ─────────────────────────────────────────────────────────────────

_settings: dict | None = None


def settings() -> dict:
    global _settings
    if _settings is None:
        try:
            _settings = {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
        except Exception:
            _settings = dict(DEFAULTS)
    return _settings


def set_setting(key: str, value) -> None:
    s = settings()
    s[key] = value
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        logger.info("Could not save media settings", exc_info=True)
    if key.startswith("volume_") or key in ("music", "ambience"):
        _player.apply_settings()


# ── Finding files ────────────────────────────────────────────────────────────

def _find(folder: Path, keys, exts) -> Path | None:
    for key in keys:
        if not key:
            continue
        for ext in exts:
            p = folder / f"{key}{ext}"
            if p.exists():
                return p
    return None


def asset_dirs() -> list[Path]:
    """Where files are looked for: assets_local/ (yours, git-ignored) first, then assets/."""
    return [LOCAL_ASSETS_DIR, ASSETS_DIR]


def is_muted(rel: str) -> bool:
    """``rel`` like ``music/explore`` or ``voice/torven_blacksmith/greeting_2`` (Admin → listen and mute)."""
    return rel in settings()["muted"]


def _find_any(sub: str, keys, exts) -> Path | None:
    audio = sub.startswith("audio/")
    for key in keys:                          # the most specific key wins, from either folder
        if not key or (audio and is_muted(f"{sub[6:]}/{key}")):
            continue
        for base in asset_dirs():
            hit = _find(base / sub, [key], exts)
            if hit:
                return hit
    return None


def find_image(kind: str, *keys: str) -> Path | None:
    """First existing image for ``keys`` (most specific first) under images/<kind>/."""
    return _find_any(f"images/{kind}", keys, IMAGE_EXT)


def find_audio(kind: str, *keys: str) -> Path | None:
    return _find_any(f"audio/{kind}", keys, AUDIO_EXT)


def variants(kind: str, key: str) -> list[Path]:
    """Every unmuted take of ``<key>`` (``<key>``, ``<key>_1``, ``<key>_2`` …) from both
    folders; a take in assets_local/ replaces the same-named one in assets/."""
    pat = re.compile(rf"^{re.escape(key)}(_\d+)?$")
    takes: dict[str, Path] = {}
    for base in asset_dirs():
        folder = base / "audio" / kind
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() in AUDIO_EXT and pat.match(f.stem) and not is_muted(f"{kind}/{f.stem}"):
                    takes.setdefault(f.stem, f)
    return [takes[k] for k in sorted(takes)]


def find_variant(kind: str, key: str) -> Path | None:
    """``<key>`` or one of ``<key>_1``, ``<key>_2`` … at random (several takes of a line or effect)."""
    hits = variants(kind, key)
    return random.choice(hits) if hits else None


def has_any_assets() -> bool:
    return any(d.exists() and any(p.is_file() and p.suffix.lower() in IMAGE_EXT + AUDIO_EXT
                                  for p in d.rglob("*")) for d in asset_dirs())


# ── Pictures: handed to the front end's ``art`` hook (Textual sidebar) ────────

_current_art: tuple[str, str] = ("", "")


def show_art(kind: str, *keys: str, caption: str = "", clear: bool = False) -> Path | None:
    """Show the first matching image, if images are on and the front end can.
    With ``clear`` (scenes), no match hides the picture instead of leaving the
    previous place's art up."""
    global _current_art
    if not settings()["images"]:
        return None
    path = find_image(kind, *keys)
    if path is None and not clear:
        return None
    state = (str(path), caption) if path is not None else ("", "")
    if _current_art == state:
        return path
    _current_art = state
    from ui.prompt import get_backend
    hook = getattr(get_backend(), "art", None)
    if hook:
        try:
            hook(path or "", caption if path is not None else "")
        except Exception:
            logger.info("Art hook failed for %s", path, exc_info=True)
    return path


# ── Sound ────────────────────────────────────────────────────────────────────

class AudioPlayer:
    """pygame-ce mixer with channels for ambience, voice and effects, and
    pygame's music stream for music. Never raises; silent when unavailable."""

    def __init__(self) -> None:
        self._ok: bool | None = None
        self._lock = threading.Lock()
        self._sounds: dict[str, object] = {}
        self._last_sfx: dict[str, float] = {}
        self._music = ""
        self._ambience = ""
        self._amb_ch = self._voice_ch = self._loop_ch = None
        self._loop = ""
        self.error = ""                              # why sound is off, for the status line

    def _init(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            import os
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            pygame.mixer.init()
            pygame.mixer.set_num_channels(16)
            # set_reserved(n) keeps channels 0..n-1 away from Sound.play(), so the
            # long-running ones live there: ambience, voice and the heartbeat loop.
            pygame.mixer.set_reserved(3)
            self._amb_ch = pygame.mixer.Channel(0)
            self._voice_ch = pygame.mixer.Channel(1)
            self._loop_ch = pygame.mixer.Channel(2)
            self._pg = pygame
            self._ok = True
        except ImportError:
            self.error = "the sound library isn't installed (run: python -m pip install pygame-ce)"
            logger.info("Audio off: %s", self.error)
            self._ok = False
        except Exception as e:                      # no sound device, driver trouble …
            self.error = f"the sound device couldn't be opened ({e})"
            logger.info("Audio off: %s", self.error)
            self._ok = False
        return self._ok

    def _sound(self, path: Path):
        key = str(path)
        if key not in self._sounds:
            self._sounds[key] = self._pg.mixer.Sound(key)
        return self._sounds[key]

    def play_sfx(self, path: Path, name: str) -> bool:
        now = time.monotonic()
        if now - self._last_sfx.get(name, 0) < 0.25:          # no machine-gunning
            return False
        self._last_sfx[name] = now
        with self._lock:
            if not self._init():
                return False
            try:
                snd = self._sound(path)
                snd.set_volume(settings()["volume_sfx"])
                snd.play()
                # Stamp again after playing: the first call loads the mixer,
                # which can take longer than the throttle window itself.
                self._last_sfx[name] = time.monotonic()
                return True
            except Exception:
                logger.info("Could not play %s", path, exc_info=True)
                return False

    def play_voice(self, path: Path) -> bool:
        with self._lock:
            if not self._init():
                return False
            try:
                snd = self._sound(path)
                snd.set_volume(settings()["volume_voice"])
                self._voice_ch.play(snd)
                return True
            except Exception:
                logger.info("Could not play %s", path, exc_info=True)
                return False

    def loop(self, path: Path | None) -> None:
        """Loop a short sound (the heartbeat) until called with None."""
        key = str(path or "")
        with self._lock:
            if key == self._loop or not self._init():
                return
            self._loop = key
            try:
                if path is None:
                    self._loop_ch.fadeout(400)
                    return
                snd = self._sound(path)
                snd.set_volume(settings()["volume_sfx"])
                self._loop_ch.play(snd, loops=-1)
            except Exception:
                logger.info("Could not loop %s", path, exc_info=True)

    def ambience(self, path: Path | None) -> None:
        key = str(path or "")
        with self._lock:
            if key == self._ambience or not self._init():
                return
            self._ambience = key
            try:
                if path is None:
                    self._amb_ch.fadeout(800)
                    return
                snd = self._sound(path)
                snd.set_volume(settings()["volume_ambience"])
                self._amb_ch.play(snd, loops=-1, fade_ms=800)
            except Exception:
                logger.info("Could not loop %s", path, exc_info=True)

    def music(self, path: Path | None) -> None:
        key = str(path or "")
        with self._lock:
            if key == self._music or not self._init():
                return
            self._music = key
            try:
                if path is None:
                    self._pg.mixer.music.fadeout(1000)
                    return
                self._pg.mixer.music.load(key)
                self._pg.mixer.music.set_volume(settings()["volume_music"])
                self._pg.mixer.music.play(loops=-1, fade_ms=1000)
            except Exception:
                logger.info("Could not play music %s", path, exc_info=True)

    def apply_settings(self) -> None:
        if not self._ok:
            return
        s = settings()
        try:
            self._pg.mixer.music.set_volume(s["volume_music"] if s["music"] else 0)
            if not s["ambience"]:
                self.ambience(None)
            if not s["music"]:
                self.music(None)
        except Exception:
            pass

    def stop_all(self) -> None:
        if self._ok:
            try:
                self._pg.mixer.stop()
                self._pg.mixer.music.stop()
                self._pg.mixer.music.unload()          # let go of the file (Windows locks it)
            except Exception:
                pass
        self._music = self._ambience = self._loop = ""


_player = AudioPlayer()


def audio_files() -> int:
    return sum(1 for d in asset_dirs() if (d / "audio").is_dir()
               for p in (d / "audio").rglob("*") if p.suffix.lower() in AUDIO_EXT)


def audio_status() -> tuple[bool, str]:
    """(sound works, a sentence saying why or why not) for Admin and the title screen."""
    n = audio_files()
    if not n:
        return False, "Sound: no sound files in assets/audio yet."
    s = settings()
    if not any(s[k] for k in ("sfx", "music", "ambience", "voice")):
        return False, "Sound: switched off in Admin → Art & audio."
    if not _player._init():
        return False, f"Sound is off: {_player.error or 'audio is unavailable'}."
    try:
        import pygame
        ver = getattr(pygame, "version", None)
        ver = getattr(ver, "ver", "") if ver else ""
        freq, _fmt, ch = pygame.mixer.get_init() or (0, 0, 0)
        driver = ""
        try:
            driver = pygame.mixer.get_driver() if hasattr(pygame.mixer, "get_driver") else ""
        except Exception:
            pass
    except Exception:
        ver, freq, ch, driver = "", 0, 0, ""
    vols = ", ".join(f"{k} {s['volume_' + k]:.0%}" for k in ("music", "sfx", "ambience", "voice") if s[k])
    dev = f", {driver}" if driver else ""
    return True, f"Sound: on ({n} files; pygame-ce {ver}, {freq} Hz{dev}; {vols})."


def test_sound() -> str:
    """Play the System chime and a voice line now (Admin → Art & audio → Test sound)."""
    ok, msg = audio_status()
    if not ok:
        return msg
    played = []
    chime = find_audio("sfx", "system_chime", "level_up", "item_found")
    if chime:
        _player._last_sfx.pop(chime.stem, None)
        if _player.play_sfx(chime, chime.stem):
            played.append(f"effect {chime.name}")
    line = find_audio("voice/system", "classification_complete", "welcome")
    if line and _player.play_voice(line):
        played.append(f"voice {line.name}")
    if not played:
        return msg + " Nothing could be played; see logs/ for the error."
    return msg + " Playing: " + ", ".join(played) + ". If you hear nothing, check your volume and output device."


def ui_sound(kind: str) -> bool:
    """Menu clicks (hover / confirm / cancel), switchable on their own in Admin."""
    s = settings()
    if not s["ui_sounds"] or (kind == "hover" and not s["ui_hover"]):
        return False
    return sfx(f"ui_{kind}")


def all_sounds() -> list[tuple[str, Path]]:
    """Every sound file the game can play, as (``music/explore``, path), for Admin."""
    seen: dict[str, Path] = {}
    for base in asset_dirs():
        root = base / "audio"
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.suffix.lower() in AUDIO_EXT:
                    seen.setdefault(p.relative_to(root).with_suffix("").as_posix(), p)
    order = {"music": 0, "ambience": 1, "sfx": 2, "voice": 3}
    return sorted(seen.items(), key=lambda kv: (order.get(kv[0].split("/")[0], 9), kv[0]))


# Where each music mood plays (Admin → Art & audio → Music room).
MUSIC_PLACES = {
    "explore": "the village, the road, and anywhere without its own",
    "combat": "fights",
    "boss": "boss fights",
    "camp": "the camp and the inn",
    "verath": "Verath",
    "ledger": "the Ledger House",
    "undermarket": "the Undermarket",
    "dungeon": "dungeon Floors 1 and 2",
    "dungeon_deep": "Floor 3",
    "fracture_core": "the Fracture Core",
    "prologue": "the prologue and the Rite",
    "ending": "the endings",
}
COMPOSED_MANIFEST = "composed.json"        # written by scripts/compose_music.py: {mood: version}


def music_file(mood: str) -> Path | None:
    """The file a mood would play, muted or not (assets_local/ first)."""
    for base in asset_dirs():
        hit = _find(base / "audio" / "music", [mood], AUDIO_EXT)
        if hit:
            return hit
    return None


def composed_versions() -> dict[str, int]:
    """Moods whose track in assets/ was composed by scripts/compose_music.py, and which version."""
    try:
        raw = json.loads((ASSETS_DIR / "audio" / "music" / COMPOSED_MANIFEST).read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def set_composed_version(mood: str, version: int) -> None:
    """Record which composed version of a mood is now its track in assets/."""
    path = ASSETS_DIR / "audio" / "music" / COMPOSED_MANIFEST
    versions = composed_versions()
    versions[mood] = int(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(versions.items())), indent=1) + "\n", encoding="utf-8")


def music_catalog() -> list[dict]:
    """One row per mood: where it plays, its file, where the file came from, muted or not."""
    versions = composed_versions()
    rows = []
    for mood, where in MUSIC_PLACES.items():
        path = music_file(mood)
        if path is None:
            seen, other = {mood}, MUSIC_FALLBACK.get(mood, "explore")
            while other not in seen and music_file(other) is None:
                seen.add(other)
                other = MUSIC_FALLBACK.get(other, "explore")
            source = f"no track of its own: borrows {other.replace('_', ' ')}" if music_file(other) else "no track yet"
        elif LOCAL_ASSETS_DIR in path.parents:
            source = "yours (assets_local)"
        elif mood in versions:
            source = f"composed in code, version {versions[mood]}"
        else:
            source = "recorded"
        rows.append({"mood": mood, "title": mood.replace("_", " ").capitalize(), "where": where, "path": path,
                     "source": source, "muted": is_muted(f"music/{mood}"),
                     "composed": path is not None and mood in versions and LOCAL_ASSETS_DIR not in path.parents,
                     "version": versions.get(mood, 0)})
    return rows


def play_music_file(path: Path | None) -> None:
    """Play one music file on the music channel, looping (the Music room); None fades it out."""
    _player.music(path)


def release_music() -> None:
    """Stop the music at once and let go of its file, so it can be replaced (Windows locks it)."""
    if not _player._init():
        return
    with _player._lock:
        try:
            _player._pg.mixer.music.stop()
            _player._pg.mixer.music.unload()
        except Exception:
            pass
        _player._music = ""


def preview(path: Path | None) -> bool:
    """Play one file now on the voice channel (stops the previous preview); None stops it."""
    if not _player._init():
        return False
    with _player._lock:
        try:
            _player._voice_ch.stop()
            if path is None:
                return True
            snd = _player._sound(path)
            snd.set_volume(0.9)
            _player._voice_ch.play(snd)
            return True
        except Exception:
            logger.info("Could not preview %s", path, exc_info=True)
            return False


def set_muted(rel: str, muted: bool) -> None:
    m = [x for x in settings()["muted"] if x != rel] + ([rel] if muted else [])
    set_setting("muted", sorted(m))
    if muted and rel.startswith("music/"):
        _player.music(None)                      # stop it now if it's playing
    if muted and rel.startswith("ambience/"):
        _player.ambience(None)


def ui_answer(answer) -> None:
    """A menu was answered: cancel for Back / nothing, else confirm."""
    if answer in (None, "", False) or str(answer).lstrip().startswith(("←", "Back", "Cancel")):
        ui_sound("cancel")
    else:
        ui_sound("confirm")


def heartbeat(hp: int, max_hp: int) -> None:
    """Loop the heartbeat while HP is low in a fight; stop it otherwise."""
    low = max_hp > 0 and 0 < hp < max_hp * HEARTBEAT_BELOW and settings()["sfx"]
    _player.loop(find_audio("sfx", "heartbeat") if low else None)


def startup_notice() -> str | None:
    """One line for the title screen when there are sounds but they can't play."""
    ok, msg = audio_status()
    logger.info(msg)
    if ok or not audio_files() or msg.startswith("Sound: switched off"):
        return None
    return msg


def sfx(name: str) -> bool:
    if not settings()["sfx"]:
        return False
    path = find_variant("sfx", name)                       # swing, swing_2 … one at random
    return bool(path) and _player.play_sfx(path, name)


def system_voice(key: str) -> bool:
    if not settings()["voice"]:
        return False
    path = find_audio("voice/system", key)
    return bool(path) and _player.play_voice(path)


def npc_voice(npc_id: str, key: str = "greeting") -> bool:
    if not settings()["voice"]:
        return False
    path = find_variant(f"voice/{npc_id}", key)
    return bool(path) and _player.play_voice(path)


def bot_voice(archetype: str) -> bool:
    """A bot's greeting, by archetype (a pragmatic veteran when there's no file for it)."""
    if not settings()["voice"]:
        return False
    path = find_variant(f"voice/bots/{archetype}", "greeting") or find_variant("voice/bots/trader", "greeting")
    return bool(path) and _player.play_voice(path)


def music(mood: str | None) -> None:
    if not settings()["music"]:
        return
    path = None
    seen = set()
    while mood and path is None and mood not in seen:      # a missing mood borrows a similar one
        seen.add(mood)
        path = find_audio("music", mood)
        mood = MUSIC_FALLBACK.get(mood, "explore")
    _player.music(path)


# ── Game moments ─────────────────────────────────────────────────────────────

_in_combat = False
_events_registered = False


# Spots that share another picture (looked up after their own file, before the scene's).
ART_ALIASES = {
    "gate_hall": "gate",                                   # both Gate Halls: the rift
    "examine_core": "dungeon_floor3.core_chamber",         # the rest of the Fracture Core
    "system_terminal": "dungeon_floor3.core_chamber",
}


_last_scene = ""


def on_scene(scene_id: str, node_id: str = "root", title: str = "") -> None:
    """Entering / showing a scene node: its art, its ambience, its music, and
    footsteps when you've just travelled there."""
    global _last_scene
    if _last_scene and scene_id != _last_scene and scene_id in FOOTSTEP_FOR_SCENE:
        sfx(f"footstep_{FOOTSTEP_FOR_SCENE[scene_id]}")
    _last_scene = scene_id
    show_art("scenes", f"{scene_id}.{node_id}", ART_ALIASES.get(node_id, ""), scene_id, caption=title,
             clear=True)
    if settings()["ambience"]:
        _player.ambience(find_audio("ambience", f"{scene_id}.{node_id}", scene_id))
    if not _in_combat:
        music(MUSIC_FOR_NODE.get(node_id) or MUSIC_FOR_SCENE.get(scene_id) or "explore")


def on_npc(npc_id: str, name: str = "") -> None:
    show_art("npcs", npc_id, caption=name)
    npc_voice(npc_id, "greeting")


def on_boss(occupant_id: str, enemy_ids: list[str] | None = None, title: str = "") -> None:
    show_art("bosses", occupant_id, *(enemy_ids or []), caption=title)


def on_gate(gate_id: str, name: str = "") -> None:
    """Running a Gate: its own picture, else the rift."""
    show_art("scenes", f"gate.{gate_id}", "gate", caption=name)
    if not sfx("gate_enter"):
        sfx("rift_open")
    if settings()["ambience"]:
        _player.ambience(find_audio("ambience", f"gate.{gate_id}", "gate"))


def on_item(item_id: str, name: str = "") -> None:
    show_art("items", item_id, caption=name)


def on_combat_start(boss: bool = False) -> None:
    global _in_combat
    _in_combat = True
    music("boss" if boss and find_audio("music", "boss") else "combat")
    if boss:
        sfx("boss_reveal")


def on_combat_end(result: str) -> None:
    global _in_combat
    _in_combat = False
    _player.loop(None)                          # the heartbeat stops with the fight
    if result == "victory":
        sfx("victory")
    elif result == "defeat":
        sfx("defeat")
    global _current_art
    _current_art = ("<combat>", "")             # the next scene re-sends its art (or clears the boss's)


def stop_all() -> None:
    _player.stop_all()


def register_event_sounds(bus) -> None:
    """Sounds and System voice lines for game events (once; later calls do nothing)."""
    global _events_registered
    if _events_registered:
        return
    _events_registered = True

    def on(name, sound=None, voice=None, when=None):
        def handler(event):
            if when is not None and not when(event.data or {}):
                return
            if sound:
                sfx(sound)
            if voice:
                system_voice(voice)
        bus.subscribe(name, handler)

    on("LEVEL_UP", "level_up", "level_up")
    on("SKILL_ACQUIRED", "skill_acquired", "skill_acquired")
    on("CLASS_ASSIGNED", "class_assigned", "classification_complete")
    on("ANOMALY_DETECTED", None, "divergence_detected")
    on("CLASS_MILESTONE", "class_milestone", "class_milestone")
    on("ITEM_FOUND", "item_found")
    on("QUEST_STARTED", "system_chime")
    on("QUEST_COMPLETED", "class_milestone", "quest_complete")
    on("SPECIES_EVOLVED", "class_assigned", "evolution")
    on("FACTION_RANK_CHANGED", "class_milestone", "rank_changed")
    on("CRIME_COMMITTED", "warden_whistle", None, when=lambda d: d.get("witnessed"))
    on("CRIME_COMMITTED", "ledger_seal", None, when=lambda d: not d.get("witnessed"))
    on("CRIME_COMMITTED", None, "reported_to_ledger", when=lambda d: d.get("kind") == "sedition")
