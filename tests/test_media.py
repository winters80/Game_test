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
            media.set_setting("images", False)
            media.on_scene("verath_city", "ledger_house")
            check(len(shown) == 1, "pictures can be switched off")
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
        media.on_scene("camp_rest", "root")                    # ambience + default music
        media.on_combat_start()
        media.on_combat_end("victory")
        _wav(tmp / "audio" / "voice" / "bots" / "trader" / "greeting.wav")
        check(media.bot_voice("gatherer") in (True, False), "a bot without its own voice falls back to a preset")
        _wav(tmp / "audio" / "ambience" / "gate.wav")
        media.on_gate("gate_x", "Gate X")
        media.stop_all()
        check(True, "ambience, music, voice and combat sounds run without errors")

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
        bus.flush()
    check(("sfx", "level_up") in played and ("voice", "level_up") in played, "level up: chime and the System's voice")
    check(played.count(("sfx", "warden_whistle")) == 1 and ("voice", "reported_to_ledger") in played,
          "only a seen crime gets the whistle; sedition gets the System's report")


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
        f.unlink()


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


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()
    for name, fn in (("files, art, sound", lookups), ("asset list", asset_list), ("panel UI", tui), ("browser", web)):
        print(f"\n  {name}")
        with tempfile.TemporaryDirectory() as tmp:
            fn(Path(tmp))
    print("\n  events")
    events()
    print()
    if failures:
        print(f"  {len(failures)} media check(s) failed.")
        sys.exit(1)
    print("  All media checks passed.")
