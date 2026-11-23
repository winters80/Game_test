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
    return sections


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
