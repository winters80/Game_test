"""
Art and audio (ui/media.py): finding files, the art hook, sound that never
breaks the game, event sounds, the asset list, and pictures in the panel UI.

    python tests/test_media.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")          # real playback path, no speakers needed

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓  " if cond else "  ✗  ") + msg)
    if not cond:
        failures.append(msg)


def _png(path: Path, color=(40, 90, 160)) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color).save(path)


def _wav(path: Path) -> None:
    import struct
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(2205)))


def lookups(tmp: Path) -> None:
    from ui import media
    with mock.patch.object(media, "ASSETS_DIR", tmp), mock.patch.object(media, "SETTINGS_FILE", tmp / "m.json"):
        media._settings = None
        check(media.find_image("scenes", "verath_city.ledger_house", "verath_city") is None, "no files: nothing found")
        _png(tmp / "images" / "scenes" / "verath_city.png")
        check(media.find_image("scenes", "verath_city.ledger_house", "verath_city").name == "verath_city.png",
              "a node falls back to its scene's picture")
        _png(tmp / "images" / "scenes" / "verath_city.ledger_house.jpg")
        check(media.find_image("scenes", "verath_city.ledger_house", "verath_city").name == "verath_city.ledger_house.jpg",
              "a node's own picture wins (any image extension)")
        check(media.has_any_assets(), "has_any_assets sees the files")
        with mock.patch.object(media, "LOCAL_ASSETS_DIR", tmp / "local"):
            _png(tmp / "local" / "images" / "scenes" / "verath_city.png")
            check(media.find_image("scenes", "verath_city").parent.parent.parent.name == "local",
                  "a file in assets_local/ (yours, not published) wins over assets/")
            check(media.find_image("scenes", "verath_city.ledger_house", "verath_city").name
                  == "verath_city.ledger_house.jpg", "...but a more specific file in assets/ still wins")

        shown = []

        class Back:
            def art(self, path, caption=""):
                shown.append((Path(path).name, caption))
        from ui import prompt as uip
        uip.set_backend(Back())
        try:
            media._current_art = ("", "")
            media.on_scene("verath_city", "root", title="The City of Verath")
            check(shown == [("verath_city.png", "The City of Verath")], f"a scene shows its picture ({shown})")
            media.on_scene("verath_city", "root", title="The City of Verath")
            check(len(shown) == 1, "the same picture isn't re-sent")
            media.on_scene("dungeon_floor1", "root", title="Floor 1")
            check(shown[-1] == ("", ""), f"a scene without a picture hides the last one ({shown[-1]})")
            media.on_scene("dungeon_floor1", "corridor")
            check(len(shown) == 2, "...once")
            media.on_scene("verath_city", "root", title="The City of Verath")
            check(len(shown) == 3, "coming back shows the picture again")
            media.on_scene("dungeon_floor1", "corridor")
            media.on_combat_end("victory")                  # a boss portrait may be up
            media.on_scene("dungeon_floor1", "corridor")
            check(shown[-1] == ("", ""), "after a fight, a scene without a picture clears the boss's")
            media.set_setting("images", False)
            before = len(shown)
            media.on_scene("verath_city", "ledger_house")
            check(len(shown) == before, "pictures can be switched off")
            media.set_setting("images", True)
            check(json_load(tmp / "m.json")["images"] is True, "media settings are saved")
        finally:
            uip.set_backend(None)

        # Sound: missing file → False; real file → plays (dummy driver), never raises.
        check(media.sfx("level_up") is False, "a missing sound is simply skipped")
        _wav(tmp / "audio" / "sfx" / "level_up.wav")
        ok = media.sfx("level_up")
        check(ok in (True, False), f"a present sound plays or is skipped without error (played={ok})")
        check(media.sfx("level_up") is False, "the same effect isn't machine-gunned")
        _wav(tmp / "audio" / "voice" / "system" / "level_up.wav")
        media.system_voice("level_up")
        _wav(tmp / "audio" / "ambience" / "camp_rest.wav")
        _wav(tmp / "audio" / "music" / "explore.wav")
        played = []
        with mock.patch.object(media._player, "music", lambda p: played.append(p and p.name)):
            _wav(tmp / "audio" / "music" / "ledger.wav")
            media.music("verath")
            media.music("dungeon")
        check(played == ["ledger.wav", "explore.wav"],
              f"a mood without a track borrows a similar one, then explore ({played})")
        media.on_scene("camp_rest", "root")                    # ambience + default music
        media.on_combat_start()
        media.on_combat_end("victory")
        _wav(tmp / "audio" / "voice" / "bots" / "trader" / "greeting.wav")
        check(media.bot_voice("gatherer") in (True, False), "a bot without its own voice falls back to a preset")
        _wav(tmp / "audio" / "ambience" / "gate.wav")
        media.on_gate("gate_x", "Gate X")
        media.stop_all()
        check(True, "ambience, music, voice and combat sounds run without errors")

        # Several takes of a voice line: one of them, at random.
        for i in (1, 2, 3):
            _wav(tmp / "audio" / "voice" / "torven_blacksmith" / f"greeting_{i}.wav")
        _wav(tmp / "audio" / "voice" / "torven_blacksmith" / "greetings_extra.wav")
        takes = {media.find_variant("voice/torven_blacksmith", "greeting").name for _ in range(60)}
        check(takes == {"greeting_1.wav", "greeting_2.wav", "greeting_3.wav"},
              f"voice lines with several takes are picked at random ({sorted(takes)})")

        # Broken audio backend: still no exception.
        broken = media.AudioPlayer()
        broken._ok = False
        check(broken.play_sfx(tmp / "audio" / "sfx" / "level_up.wav", "x") is False, "no audio device: silent")
        media._settings = None


def json_load(p: Path) -> dict:
    import json
    return json.loads(p.read_text())


def events() -> None:
    from core.event_bus import Event, EventBus
    from ui import media
    bus = EventBus()
    played = []
    with mock.patch.object(media, "sfx", lambda n: played.append(("sfx", n))), \
            mock.patch.object(media, "system_voice", lambda k: played.append(("voice", k))), \
            mock.patch.object(media, "_events_registered", False):
        media.register_event_sounds(bus)
        bus.publish(Event("LEVEL_UP", {"level": 2}))
        bus.publish(Event("CRIME_COMMITTED", {"kind": "theft", "witnessed": False}))
        bus.publish(Event("CRIME_COMMITTED", {"kind": "sedition", "witnessed": True}))
        bus.publish(Event("QUEST_STARTED", {"quest_id": "q", "title": "T"}))
        bus.publish(Event("QUEST_COMPLETED", {"quest_id": "q", "outcome": "success"}))
        bus.publish(Event("SPECIES_EVOLVED", {"species_id": "human", "stage": 1, "new_name": "X"}))
        bus.publish(Event("FACTION_RANK_CHANGED", {"faction_id": "f", "old_rank": "a", "new_rank": "b"}))
        bus.flush()
    check(("sfx", "level_up") in played and ("voice", "level_up") in played, "level up: chime and the System's voice")
    check(played.count(("sfx", "warden_whistle")) == 1 and ("voice", "reported_to_ledger") in played,
          "only a seen crime gets the whistle; sedition gets the System's report")
    check(("sfx", "system_chime") in played and ("voice", "quest_complete") in played
          and ("voice", "evolution") in played and ("voice", "rank_changed") in played,
          "quests, evolution and faction ranks are heard too")
    check(all(k in media.SYSTEM_LINES for kind, k in played if kind == "voice"), "every voice line is a System line")


def asset_list(tmp: Path) -> None:
    import scripts.asset_list as al
    from core.bootstrap import load_registries
    from core.game_engine import GameEngine
    from ui import media
    eng = GameEngine()
    load_registries(eng)
    with mock.patch.object(media, "ASSETS_DIR", tmp):
        _png(tmp / "images" / "npcs" / "auditor_castell.png")
        text = al.build_markdown(eng)
        missing = al.build_markdown(eng, missing_only=True)
    check("## Bosses" in text and "## The System's voice" in text and "images/items/rusty_sword" in text,
          "the asset list covers scenes, people, bosses, items, sounds and voices")
    check("| ✓ | `images/npcs/auditor_castell`" in text and "auditor_castell`" not in missing,
          "files you have are ticked (and left out of --missing)")
    check("{name}" not in text and "| , the" not in text, "boss prompts have real names in them")
    stems = {stem for _, rows in al.collect(eng) for stem, _, _ in rows}
    check({s for s, _ in al.MOST_WANTED} <= stems,
          f"every 'most wanted' file is one the game uses ({ {s for s, _ in al.MOST_WANTED} - stems })")
    check("## Most wanted" in text and text.index("## Most wanted") < text.index("## Bosses"),
          "the missing 'most wanted' files are listed first")
    pack = al.load_pack()
    check(len(pack) == 32 and set(al.PACK_TARGETS) == set(pack),
          "every spec in the audio prompt pack is mapped to a game file")
    sfx_targets = {t.rsplit("/", 1)[1] for ts in al.PACK_TARGETS.values() for t in ts if t.startswith("audio/sfx/")}
    check(sfx_targets <= set(media.SFX), f"the pack's effects are ones the game plays ({sfx_targets - set(media.SFX)})")
    for tag in ("[AMB-06]", "[SFX-10]", "[MUS-05]", "[VOX-CORE-05]", "[VOX-BOT-02]"):
        check(tag in text, f"the asset list uses the pack's prompt {tag}")


async def _tui(tmp: Path) -> None:
    from ui import media
    from ui.tui_app import GameApp
    _png(tmp / "images" / "scenes" / "village_start.png", (120, 80, 40))
    app = GameApp()
    async with app.run_test(size=(140, 45)) as pilot:
        for _ in range(900):
            await pilot.pause(0.1)
            if app._kind:
                break
        await pilot.press("enter")                              # New Game
        for _ in range(60):
            for _ in range(300):
                await pilot.pause(0.1)
                if app._kind:
                    break
            if getattr(app, "art_path", "").endswith("village_start.png"):
                break
            if app._kind == "text":
                app.query_one("#entry").value = "Pic"
            await pilot.press("enter")
        check(getattr(app, "art_path", "").endswith("village_start.png") and app.query_one("#art").has_class("on"),
              "the panel UI shows the scene's picture in the sidebar")
        await pilot.press("ctrl+q")


def tui(tmp: Path) -> None:
    try:
        import textual  # noqa: F401
    except ImportError:
        print("  -  textual not installed: skipped")
        return
    from ui import media
    with mock.patch.object(media, "ASSETS_DIR", tmp), mock.patch.object(media, "SETTINGS_FILE", tmp / "m.json"):
        media._settings = None
        media._current_art = ("", "")
        asyncio.run(_tui(tmp))
    media._settings = None
    for f in list(ROOT.glob("saves/pic*")):
        for _ in range(20):                     # the game thread may still hold the .db (Windows)
            try:
                f.unlink(missing_ok=True)       # -wal / -shm vanish when the .db closes
                break
            except PermissionError:
                time.sleep(0.25)


def status(tmp: Path) -> None:
    """Why there's no sound is said out loud (title screen, Admin → Art & audio)."""
    from ui import media
    with mock.patch.object(media, "ASSETS_DIR", tmp / "a"), mock.patch.object(media, "LOCAL_ASSETS_DIR", tmp / "l"), \
            mock.patch.object(media, "SETTINGS_FILE", tmp / "m.json"):
        media._settings = None
        ok, msg = media.audio_status()
        check(not ok and "no sound files" in msg and media.startup_notice() is None,
              "no sound files: said in Admin, no title-screen warning")
        _wav(tmp / "a" / "audio" / "sfx" / "system_chime.wav")
        broken = media.AudioPlayer()
        broken._ok, broken.error = False, "the sound library isn't installed (run: python -m pip install pygame-ce)"
        with mock.patch.object(media, "_player", broken):
            ok, msg = media.audio_status()
            check(not ok and "pip install pygame-ce" in msg and media.startup_notice() == msg,
                  "sound files but no library: the title screen says how to fix it")
            check("pip install pygame-ce" in media.test_sound(), "Test sound reports the same reason")
        fresh = media.AudioPlayer()
        with mock.patch.object(media, "_player", fresh):
            ok, msg = media.audio_status()
            check(ok and msg.startswith("Sound: on (1 files") and media.startup_notice() is None,
                  f"working sound: a status line, no warning ({msg})")
            check("Playing: effect system_chime.wav" in media.test_sound(), "Test sound plays the chime")
            media.set_setting("sfx", False); media.set_setting("music", False)
            media.set_setting("ambience", False); media.set_setting("voice", False)
            check("switched off" in media.audio_status()[1] and media.startup_notice() is None,
                  "all sound switched off: said in Admin, not nagged about")
        media._settings = None


def hooks(tmp: Path) -> None:
    """When each of the newer sounds plays."""
    from types import SimpleNamespace as NS
    from ui import media
    from core.combat_handler import _combat_sounds
    played, loops = [], []
    with mock.patch.object(media, "sfx", lambda n: played.append(n) or True), \
            mock.patch.object(media._player, "loop", lambda p: loops.append(p and p.name)), \
            mock.patch.object(media, "find_audio", lambda kind, *k: Path(f"{k[0]}.ogg")):
        p = NS(max_hp=100, current_hp=90)
        _combat_sounds(NS(damage=20, cues=[], lines=[]), p, {}, (0, 0))
        _combat_sounds(NS(damage=5, cues=[], lines=[]), p, {}, (0, 0))
        _combat_sounds(NS(damage=0, cues=[], lines=[("Orc swings — you dodge!", "miss")]), p, {}, (0, 0))
        _combat_sounds(NS(damage=0, cues=[], lines=[("Orc swings — you dodge!", "miss")]), p, {"_evade": 1}, (0, 2))
        check(played == ["player_hurt", "enemy_hit", "dodge", "block"],
              f"heavy hit / light hit / dodge / ward or evade each sound different ({played})")
        p.current_hp = 20
        _combat_sounds(NS(damage=0, cues=[], lines=[]), p, {}, (0, 0))
        p.current_hp = 80
        _combat_sounds(NS(damage=0, cues=[], lines=[]), p, {}, (0, 0))
        check(loops[-2:] == ["heartbeat.ogg", None], f"the heartbeat loops below 30% HP and stops above ({loops})")
        played.clear()
        media.ui_answer("fight"); media.ui_answer(""); media.ui_answer(None); media.ui_answer("← Back")
        check(played == ["ui_confirm", "ui_cancel", "ui_cancel", "ui_cancel"], f"menu confirm / cancel clicks ({played})")
        played.clear()
        with mock.patch.object(media, "show_art", lambda *a, **k: None), mock.patch.object(media, "music", lambda m: None), \
                mock.patch.object(media._player, "ambience", lambda p: None):
            media._last_scene = ""
            for sc in ("village_start", "village_start", "dungeon_floor2", "verath_city", "prologue"):
                media.on_scene(sc)
        check(played == ["footstep_water", "footstep_stone"],
              f"footsteps only when you arrive somewhere new, by its ground ({played})")
    from ui import prompt as uip

    class Back:
        def select(self, message, choices, default=None):
            return choices[-1].value
    clicks = []
    uip.set_backend(Back())
    try:
        with mock.patch.object(media, "ui_answer", lambda a: clicks.append(a)):
            uip.select("Pick:", ["a", uip.Choice("← Back", "")]).ask()
            uip.text("Name:").ask if False else None
    finally:
        uip.set_backend(None)
    check(clicks == [""], "every menu answer goes through the click sound")
    check(set(media.SFX) <= {t.path.rsplit("/", 1)[1] for t in __import__("scripts.fetch_sounds", fromlist=["x"]).TARGETS
                             if t.path.startswith("audio/sfx/")}, "the sound-pack script has a target for every effect")


def muting(tmp: Path) -> None:
    """Admin → listen and mute: a muted file is skipped; menu ticks are off by default."""
    from ui import media
    with mock.patch.object(media, "ASSETS_DIR", tmp / "a"), mock.patch.object(media, "LOCAL_ASSETS_DIR", tmp / "l"), \
            mock.patch.object(media, "SETTINGS_FILE", tmp / "m.json"):
        media._settings = None
        _wav(tmp / "a" / "audio" / "music" / "camp.wav")
        _wav(tmp / "a" / "audio" / "sfx" / "ui_hover.wav")
        for i in (1, 2):
            _wav(tmp / "a" / "audio" / "voice" / "mira_innkeeper" / f"greeting_{i}.wav")
        check([r for r, _ in media.all_sounds()][0] == "music/camp" and len(media.all_sounds()) == 4,
              "the listen page lists every sound file, music first")
        media.set_muted("music/camp", True)
        media.set_muted("voice/mira_innkeeper/greeting_2", True)
        check(media.find_audio("music", "camp") is None, "a muted track is never played")
        check({media.find_variant("voice/mira_innkeeper", "greeting").name for _ in range(30)} == {"greeting_1.wav"},
              "a muted take is skipped, the others still play")
        check(_json_muted(tmp) == ["music/camp", "voice/mira_innkeeper/greeting_2"], "mutes are saved")
        media.set_muted("music/camp", False)
        check(media.find_audio("music", "camp") is not None, "and can be undone")
        with mock.patch.object(media, "sfx", lambda n: True):
            check(media.ui_sound("hover") is False and media.ui_sound("confirm") is True,
                  "the tick while moving between choices is off by default; clicks stay on")
        media._settings = None


def _json_muted(tmp: Path) -> list:
    import json
    return json.loads((tmp / "m.json").read_text())["muted"]


def sound_pack(tmp: Path) -> None:
    """scripts/fetch_sounds.py: picks a file per game sound by name, zips it with credits."""
    import zipfile
    import json as _json
    import scripts.fetch_sounds as fs
    from ui import media
    for name in ("Audio/handleCoins", "Audio/impactGlass_heavy_000", "Audio/impactMetal_heavy_001",
                 "Audio/jingles_NES00", "Audio/bookClose", "Audio/error_002"):
        _wav(tmp / "kenney" / f"{name}.wav")
    for name in ("medieval-market-crowd-ambience", "epic-boss-battle-music", "referee-whistle"):
        _wav(tmp / "pix" / f"{name}.wav")
    srcs = fs.index([(tmp / "kenney", "cc0", "Kenney"), (tmp / "pix", "pixabay", "Pixabay")])
    picked = fs.choose(srcs, fs.TARGETS)
    name = lambda t: picked[t].path.name if t in picked else None
    check(name("audio/sfx/crystal_shatter") == "impactGlass_heavy_000.wav"
          and name("audio/sfx/coins") == "handleCoins.wav" and name("audio/sfx/warden_whistle") == "referee-whistle.wav",
          "effects are matched by the words in their file names")
    check(name("audio/sfx/defeat") == "error_002.wav", "keywords match word starts only ('lose' isn't 'close')")
    check(name("audio/music/boss") == "epic-boss-battle-music.wav" and name("audio/music/explore") is None
          and name("audio/ambience/village_start") == "medieval-market-crowd-ambience.wav",
          "a crowd recording is ambience, never music")
    targets = {t.path for t in fs.TARGETS}
    check({f"audio/sfx/{k}" for k in media.SFX} <= targets, "every sound effect the game plays has a target")
    out = tmp / "pack.zip"
    with mock.patch("shutil.which", lambda n: None):          # no ffmpeg: files are copied as is
        manifest = fs.build(picked, fs.TARGETS, tmp / "_pack", out)
    names = zipfile.ZipFile(out).namelist()
    check("sounds_manifest.json" in names and "CREDITS.md" in names and "audio/sfx/coins.wav" in names,
          "the zip has the files under their game names, a manifest and credits")
    lic = {f["file"]: f["license"] for f in _json.loads(zipfile.ZipFile(out).read("sounds_manifest.json"))["files"]}
    check(lic["audio/sfx/coins.wav"] == "cc0" and lic["audio/sfx/warden_whistle.wav"] == "pixabay",
          "the manifest records each file's license")
    check(fs.parse_extra("D:/Downloads/pix:pixabay")[1] == "pixabay" and fs.parse_extra("D:/x")[1] == "other",
          "--extra FOLDER:LICENSE copes with Windows drive letters")


def web(tmp: Path) -> None:
    """Browser play: each tab's picture is served full size, and only from assets/."""
    try:
        import textual_serve  # noqa: F401
        from aiohttp.test_utils import TestClient, TestServer
    except ImportError:
        print("  -  textual-serve not installed: skipped")
        return
    from ui import media, web_server
    _png(tmp / "assets" / "images" / "scenes" / "verath_city.png")
    (tmp / "secret.png").write_bytes((tmp / "assets" / "images" / "scenes" / "verath_city.png").read_bytes())
    art_id = "ab" * 16

    async def run() -> None:
        srv = web_server.make_server("true")
        client = TestClient(TestServer(await srv._make_app()))
        await client.start_server()
        try:
            page = await (await client.get("/")).text()
            check('id="art-panel"' in page and "/art/" in page and "/ws/" in page,
                  "the browser page has a picture panel and a per-tab art id")
            # After quitting, the dead terminal kept the keyboard and ate F5 /
            # Ctrl+R, so a reload left "Session ended." up. The page takes them back.
            check('contains("-closed")' in page and '"F5"' in page and "stopImmediatePropagation" in page,
                  "after quitting, F5 / Ctrl+R / Enter start a fresh game")
            r = await client.get(f"/art/{art_id}")
            check((await r.json())["src"] == "", "nothing to show before the game picks a picture")
            web_server.publish(art_id, str(tmp / "assets" / "images" / "scenes" / "verath_city.png"), "Verath")
            d = await (await client.get(f"/art/{art_id}")).json()
            check(d["src"].endswith(f"/art-file/{art_id}/1") and d["caption"] == "Verath",
                  "the game's picture is published for its tab")
            img = await client.get(f"/art-file/{art_id}/1")
            check(img.status == 200 and (await img.read())[:4] == b"\x89PNG", "the full-size file is served")
            web_server.publish(art_id, str(tmp / "secret.png"))
            check((await client.get(f"/art-file/{art_id}/2")).status == 404, "files outside assets/ are never served")
            check((await client.get("/art/..%2f..%2fconfig")).status == 404, "art ids are checked")
        finally:
            await client.close()

    with mock.patch.object(media, "ASSETS_DIR", tmp / "assets"), \
            mock.patch.object(web_server, "ART_DIR", tmp / "web_art"):
        asyncio.run(run())


def cues(tmp: Path) -> None:
    """Boss cues (enrage, summon, a heavy blow coming), the boss sting, and
    extra takes of an effect pooled from assets/ and assets_local/."""
    from types import SimpleNamespace as NS
    from ui import media
    from core.combat_handler import _combat_sounds
    from entities.player import Player
    from systems import combat_effects as fx
    from systems.combat_system import Enemy
    boss = Enemy(enemy_id="b", name="Brute", max_hp=100, current_hp=40, attack=5, defense=0,
                 xp_reward=1, gold_reward=1, enrage_below=0.5,
                 abilities=[{"name": "Crush", "kind": "heavy", "every": 2, "power": 2.0}])
    out = fx.enemy_turn(boss, Player(name="P"), {})
    check(out.cues == ["enrage", "boss_warning"], f"enrage and the heavy-blow warning are cued ({out.cues})")
    played = []
    with mock.patch.object(media, "sfx", lambda n: played.append(n) or True),             mock.patch.object(media, "heartbeat", lambda *a: None), mock.patch.object(media, "music", lambda m: None):
        _combat_sounds(NS(damage=0, cues=["summon"], lines=[]), NS(max_hp=10, current_hp=10), {}, (0, 0))
        media.on_combat_start(boss=True)
        media.on_combat_start(boss=False)
        media.on_combat_end("none")
    check(played == ["summon", "boss_reveal"], f"summons and boss fights have their sounds ({played})")
    for rel in ("a/audio/sfx/swing.ogg", "l/audio/sfx/swing_2.ogg", "l/audio/sfx/swing.ogg", "a/audio/sfx/swing_x.ogg"):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_bytes(b"x")
    with mock.patch.object(media, "ASSETS_DIR", tmp / "a"), mock.patch.object(media, "LOCAL_ASSETS_DIR", tmp / "l"),             mock.patch.object(media, "SETTINGS_FILE", tmp / "m.json"):
        media._settings = None
        takes = [p.relative_to(tmp).as_posix() for p in media.variants("sfx", "swing")]
        check(takes == ["l/audio/sfx/swing.ogg", "l/audio/sfx/swing_2.ogg"],
              f"takes pool across folders, a local file replaces its namesake ({takes})")
        media.set_muted("sfx/swing_2", True)
        check([p.name for p in media.variants("sfx", "swing")] == ["swing.ogg"], "a muted take is skipped")
    media._settings = None


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    from ui import media
    for name, fn in (("files, art, sound", lookups), ("asset list", asset_list), ("panel UI", tui), ("browser", web),
                     ("sound pack", sound_pack), ("sound status", status),
                     ("sound hooks", hooks), ("muting", muting), ("boss cues and takes", cues)):
        print(f"\n  {name}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # The real assets_local/ (yours, git-ignored) must not leak into the checks.
            with mock.patch.object(media, "LOCAL_ASSETS_DIR", Path(tmp) / "_no_local"):
                try:
                    fn(Path(tmp))
                finally:
                    media.stop_all()                 # let go of files the real mixer opened
    print("\n  events")
    events()
    print()
    if failures:
        print(f"  {len(failures)} media check(s) failed.")
        sys.exit(1)
    print("  All media checks passed.")
