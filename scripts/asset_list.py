#!/usr/bin/env python3
"""
Every picture, sound and voice line the game can use, with a ready prompt.

    python scripts/asset_list.py            # writes assets/ASSET_LIST.md
    python scripts/asset_list.py --missing  # only what you don't have yet

Prompts are built from the game's own text (scene descriptions, NPC
descriptions, boss intros, item descriptions), so they match what the player
reads. Paste an image prompt into an image model, a voice line into a
text-to-speech model, a sound prompt into a sound or music model, and save
the result under the file name shown (any of .png/.jpg/.webp, .ogg/.mp3/.wav).
Files already present are ticked.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STYLE = ("painterly dark fantasy illustration, muted earth tones with cold cyan accents from the "
         "System's light, soft volumetric light, visible brushwork, cinematic, no text, no watermark")
ICON_STYLE = "single object centred, painterly game icon, dark vignette background, no text, square"
PORTRAIT = "head-and-shoulders portrait, 3:4, neutral dark background"

SFX_PROMPTS = {
    "system_chime": "A clean crystalline chime with a soft digital shimmer, cold and precise, under 1 second.",
    "level_up": "A bright ascending three-note chime with a soft glow, about 1 second.",
    "skill_acquired": "A short magical shimmer rising into a soft bell, under 1 second.",
    "class_assigned": "A deep resonant arcane swell into a glassy impact, otherworldly, about 2 seconds.",
    "class_milestone": "A firm rising tone with a metallic ring, confident, about 1 second.",
    "item_found": "A light pickup jingle: a coin and a soft sparkle, under 1 second.",
    "hit": "A sword striking leather armour, punchy and short.",
    "spell": "A fireball cast: whoosh then a fiery burst, about 1 second.",
    "enemy_hit": "A heavy blunt hit with a grunt, short.",
    "victory": "A short triumphant brass and string sting, 2 seconds.",
    "defeat": "A low mournful cello note fading out, 2 seconds.",
    "warden_whistle": "A sharp metal whistle blast followed by quick boots on stone, 1.5 seconds.",
    "coins": "A small handful of coins counted onto a wooden counter, 1 second.",
    "door": "A heavy wooden door opening with an iron latch, 1 second.",
    "rift_open": "Reality tearing: a deep crackle rising into a glassy roar, 2 seconds.",
}
MUSIC_PROMPTS = {
    "explore": "Melancholic dark-fantasy ambient: solo cello, soft strings, distant choir pads, sparse piano, slow, a hopeful undertone. Seamless 90-second instrumental loop.",
    "combat": "Tense orchestral combat: driving low strings, taiko drums, brass stabs, 130 BPM. Seamless 60-second instrumental loop.",
    "boss": "Epic dark boss battle: full orchestra, choir chants, pounding percussion, ominous brass, 140 BPM. Seamless 75-second instrumental loop.",
    "verath": "A grand but cold capital city: stately strings, harp, a distant bell, quietly watchful. Seamless 90-second loop.",
    "ledger": "Cold and orderly: a ticking clock rhythm, staccato strings, low choir, quietly menacing. Seamless 60-second loop.",
    "undermarket": "A shady underground market: low plucked strings, hand drum, muted clarinet, sly and tense. Seamless 60-second loop.",
    "camp": "A quiet roadside campfire at night: soft acoustic guitar, warm pads, crickets. Seamless 90-second loop.",
    "dungeon": "A dark dungeon descent: low drones, distant drips, slow metallic percussion, unease. Seamless 90-second loop.",
    "dungeon_deep": "The deepest dungeon: subterranean drones, whispering choir, cracking crystal tones, dread. Seamless 90-second loop.",
    "fracture_core": "The origin of the Fracture: a vast low hum, glass-like crackles, ethereal tones, awe and dread. Seamless 60-second loop.",
    "prologue": "An opening theme: a lone piano under rising strings, mysterious and fateful, 60 seconds.",
    "ending": "A closing theme: bittersweet strings and choir resolving slowly, 90 seconds.",
}
SYSTEM_VOICE = ("Neutral, genderless, calm synthetic voice; precise diction; slight digital resonance; "
                "no emotion; even pacing.")


def _clean(text: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("{name}", "")).strip()
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def _first_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", _clean(text, 600))
    return " ".join(parts[:n])


def _have(path: str) -> bool:
    from ui.media import ASSETS_DIR, AUDIO_EXT, IMAGE_EXT
    base = ASSETS_DIR / path
    exts = IMAGE_EXT if path.startswith("images/") else AUDIO_EXT
    return any(base.with_name(base.name + e).exists() for e in exts)


def collect(engine) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """[(section, [(file stem under assets/, what, prompt)])]."""
    sections: list[tuple[str, list]] = []

    # Scenes and their nodes
    rows = []
    for f in sorted((ROOT / "scenes" / "data").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        sid, title = d.get("scene_id", f.stem), d.get("title", f.stem)
        nodes = d.get("nodes") or {}
        root_text = (nodes.get("root") or {}).get("text") or " ".join(d.get("entrance_text") or [])
        rows.append((f"images/scenes/{sid}", f"{title} (the whole scene)",
                     f"{_first_sentences(root_text, 3)} Wide establishing shot, 16:9. Style: {STYLE}"))
        for nid, node in nodes.items():
            if nid == "root" or not node.get("text"):
                continue
            rows.append((f"images/scenes/{sid}.{nid}", f"{title}: {nid.replace('_', ' ')} (optional)",
                         f"{_first_sentences(node['text'], 2)} 16:9. Style: {STYLE}"))
    rows.append(("images/scenes/gate", "Any Gate (rift) you enter, and the Gate Halls",
                 f"A vertical tear in reality in a forest clearing, edged with crackling cyan light, another world visible through it. 16:9. Style: {STYLE}"))
    greg = getattr(engine, "gate_registry", None)
    for g in sorted(greg.all() if greg else [], key=lambda g: g.min_level):
        rows.append((f"images/scenes/gate.{g.gate_id}", f"{g.name} (a Gate, optional)",
                     f"{_first_sentences(g.entry_text or g.description, 2)} 16:9. Style: {STYLE}"))
    sections.append(("Scenes (images/scenes)", rows))

    # NPCs
    rows = [("images/npcs/ledger_warden", "A Ledger warden (patrols)",
             f"A warden of the Ledger in a long grey coat with an iron badge, a ledger open in one gloved hand, a hard neutral expression. {PORTRAIT}. Style: {STYLE}"),
            ("images/npcs/ledger_hunter", "Ledger hunters",
             f"Three Ledger hunters in grey coats spreading out in a street at dusk, the leader holding up a writ. 3:4. Style: {STYLE}")]
    npcs = engine.npc_registry.all() if getattr(engine, "npc_registry", None) else []
    for n in sorted(npcs, key=lambda n: n.npc_id):
        desc = _clean(getattr(n, "description", "") or "", 260)
        rows.append((f"images/npcs/{n.npc_id}", n.name, f"{n.name}: {desc} {PORTRAIT}. Style: {STYLE}"))
    sections.append(("People (images/npcs)", rows))

    # Bosses (fixed lairs and every Gate master)
    rows = []
    lreg = getattr(engine, "lair_registry", None)
    for lair in sorted(lreg.all() if lreg else [], key=lambda l: l.lair_id):
        lair_name = next((o.name_pool[0] for o in lair.occupants.values() if o.name_pool), "")
        for oid, occ in lair.occupants.items():
            name = (occ.name_pool or [lair_name])[0]          # inheritors keep a lair name
            title = occ.title.replace("{name}", name).strip()
            rows.append((f"images/bosses/{oid}", f"{title} ({lair.name})",
                         f"{title}. {_first_sentences(occ.intro.replace('{name}', name), 2)} Full body, dramatic, 3:4. Style: {STYLE}"))
    sections.append(("Bosses (images/bosses)", rows))

    # Items
    rows = []
    for it in sorted(engine.item_registry.all(), key=lambda i: i.item_id):
        if it.item_id.startswith("gen_"):
            continue
        rows.append((f"images/items/{it.item_id}", it.name,
                     f"{it.name}: {_clean(it.description, 160)} {ICON_STYLE}. Style: {STYLE}"))
    sections.append(("Items (images/items)", rows))

    # Sound effects, ambience, music
    from ui.media import MUSIC_FOR_NODE, MUSIC_FOR_SCENE, SFX, SYSTEM_LINES
    sections.append(("Sound effects (audio/sfx)",
                     [(f"audio/sfx/{k}", f"when {v}", SFX_PROMPTS.get(k, v)) for k, v in SFX.items()]))
    rows = []
    for f in sorted((ROOT / "scenes" / "data").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        sid = d.get("scene_id", f.stem)
        text = ((d.get("nodes") or {}).get("root") or {}).get("text") or ""
        rows.append((f"audio/ambience/{sid}", d.get("title", sid),
                     f"Ambient soundscape, no music, no voices you can make out: {_first_sentences(text, 2)} Seamless 30-second loop."))
    sections.append(("Ambience loops (audio/ambience)", rows))
    moods = sorted(set(MUSIC_FOR_SCENE.values()) | set(MUSIC_FOR_NODE.values()) | {"explore", "combat", "boss"})
    sections.append(("Music (audio/music)",
                     [(f"audio/music/{m}", m.replace("_", " "), MUSIC_PROMPTS.get(m, "Instrumental loop.")) for m in moods]))

    # Voices
    sections.append(("The System's voice (audio/voice/system)",
                     [(f"audio/voice/system/{k}", f"Says: \"{line}\"", f"Voice: {SYSTEM_VOICE} Line: \"{line}\"")
                      for k, line in SYSTEM_LINES.items()]))
    rows = []
    for n in sorted(npcs, key=lambda n: n.npc_id):
        root = (getattr(n, "dialogue_nodes", None) or {}).get("root")
        text = getattr(root, "npc_text", "") if root is not None else ""
        quotes = re.findall(r"'([^']{8,160})'", text or "")
        if not quotes:
            continue
        rows.append((f"audio/voice/{n.npc_id}/greeting", f"{n.name} greets you",
                     f"Voice: {_clean(getattr(n, 'description', ''), 160)} Line: \"{quotes[0]}\""))
    sections.append(("NPC greetings (audio/voice/<npc_id>/greeting)", rows))
    from ui.media import BOT_VOICES
    sections.append(("Bot adventurers' voices (audio/voice/bots/<archetype>/greeting)",
                     [(f"audio/voice/bots/{a}/greeting", f"a {a} bot greets you ({v})",
                       f"Voice: {v}. Line: \"Hey. Heading for the Gates?\"") for a, v in BOT_VOICES.items()]))
    _apply_pack(sections)
    return sections


# The audio prompt pack (assets/audio_prompts/manifest.json): which game file each
# spec is for. Its prompts replace the generated ones; NPC and bot voices take
# its voice direction (and its sample line when the game has none).
PACK = ROOT / "assets" / "audio_prompts" / "manifest.json"
PACK_TARGETS = {
    "AMB-01": ["audio/ambience/village_start"], "AMB-02": ["audio/ambience/verath_city.undermarket"],
    "AMB-03": ["audio/ambience/dungeon_floor3.core_chamber"], "AMB-04": ["audio/ambience/verath_city"],
    "AMB-05": ["audio/ambience/verath_city.ledger_house"], "AMB-06": ["audio/ambience/gate"],
    "SFX-01": ["audio/sfx/system_chime"], "SFX-02": ["audio/sfx/class_assigned"],
    "SFX-03": ["audio/sfx/warden_whistle"], "SFX-04": ["audio/sfx/level_up"], "SFX-05": ["audio/sfx/hit"],
    "SFX-06": ["audio/sfx/spell"], "SFX-07": ["audio/sfx/crystal_shatter"], "SFX-08": ["audio/sfx/ledger_seal"],
    "SFX-09": ["audio/sfx/potion"], "SFX-10": ["audio/sfx/gate_enter"],
    "MUS-01": ["audio/music/explore"], "MUS-02": ["audio/music/combat"],
    "MUS-03": ["audio/music/ledger", "audio/music/verath"], "MUS-04": ["audio/music/boss"],
    "MUS-05": ["audio/music/camp"],
    "VOX-CORE-01": ["audio/voice/system/*"], "VOX-CORE-02": ["audio/voice/auditor_castell/greeting"],
    "VOX-CORE-03": ["audio/voice/silas_fence/greeting"], "VOX-CORE-04": ["audio/voice/torven_blacksmith/greeting"],
    "VOX-CORE-05": ["audio/voice/ledger_warden/greeting"], "VOX-CORE-06": ["audio/voice/mira_innkeeper/greeting"],
    "VOX-CORE-07": ["audio/voice/gray_wanderer/greeting"],
    "VOX-BOT-01": ["audio/voice/bots/explorer/greeting"], "VOX-BOT-02": ["audio/voice/bots/fighter/greeting"],
    "VOX-BOT-03": ["audio/voice/bots/crafter/greeting"],
    "VOX-BOT-04": ["audio/voice/bots/trader/greeting", "audio/voice/bots/gatherer/greeting"],
}


def load_pack() -> dict[str, dict]:
    try:
        return {a["id"]: a for a in json.loads(PACK.read_text(encoding="utf-8"))["assets"]}
    except (OSError, ValueError, KeyError):
        return {}


def _apply_pack(sections) -> None:
    pack = load_pack()
    if not pack:
        return
    rows = {r[0]: (sec, i) for sec, (_, rs) in enumerate(sections) for i, r in enumerate(rs)}
    for pid, targets in PACK_TARGETS.items():
        spec = pack.get(pid)
        if not spec:
            continue
        text = spec.get("prompt_or_direction", "")
        extra = " ".join(x for x in (spec.get("duration", ""), spec.get("tempo", "")) if x and x != "variable")
        for t in targets:
            hits = [k for k in rows if k.startswith(t[:-1])] if t.endswith("*") else [t]
            for key in hits:
                where = rows.get(key)
                if spec["category"] == "voice":
                    line = (spec.get("sample_lines") or [""])[0]
                    if where is not None:
                        old = sections[where[0]][1][where[1]]
                        m = re.search(r'Line: "(.*)"', old[2])
                        line = m.group(1) if m and not key.startswith("audio/voice/bots/") else line
                    row = (key, (sections[where[0]][1][where[1]][1] if where else f"{spec['name']} speaks"),
                           f"Voice: {text} Line: \"{line}\" [{pid}]")
                else:
                    what = sections[where[0]][1][where[1]][1] if where else spec["name"]
                    row = (key, what, f"{text}{' (' + extra + ')' if extra else ''} [{pid}]")
                if where is not None:
                    sections[where[0]][1][where[1]] = row
                else:
                    sec = next(i for i, (n, _) in enumerate(sections) if n.startswith(
                        "NPC greetings" if spec["category"] == "voice" else
                        {"ambience": "Ambience", "sfx": "Sound effects", "music": "Music"}[spec["category"]]))
                    sections[sec][1].append(row)
                    rows[key] = (sec, len(sections[sec][1]) - 1)


def build_markdown(engine, missing_only: bool = False) -> str:
    out = ["# Asset list", "",
           "Everything the game can show or play. Save each file under `assets/` with the name shown "
           "(images: .png/.jpg/.webp; sound: .ogg/.mp3/.wav). Nothing is required: whatever is missing "
           "is simply left out. Regenerate with `python scripts/asset_list.py`.", "",
           f"**Image style** (already in every image prompt): {STYLE}", ""]
    total = have = 0
    for title, rows in collect(engine):
        lines = []
        for stem, what, prompt in rows:
            ok = _have(stem)
            total += 1
            have += ok
            if missing_only and ok:
                continue
            lines.append(f"| {'✓' if ok else ' '} | `{stem}` | {what} | {prompt.replace('|', '/')} |")
        if lines:
            out += [f"## {title}", "", "| | File | What | Prompt |", "|---|---|---|---|", *lines, ""]
    out.insert(2, f"**{have} of {total} files present.**\n")
    return "\n".join(out)


def main() -> int:
    from core.bootstrap import load_registries
    from core.game_engine import GameEngine
    engine = GameEngine()
    load_registries(engine)
    text = build_markdown(engine, missing_only="--missing" in sys.argv)
    out = ROOT / "assets" / "ASSET_LIST.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text.splitlines()[2])
    print(f"Written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
