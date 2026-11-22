"""
Turn an AI proposal into a safe Gate or lair successor. Pure logic, no I/O.

The model only supplies names and texts (ai/response_validator.AIGateResponse,
AISuccessorResponse). Everything that matters for balance comes from here:

- every id is new and prefixed ``gen_`` (never overwrites authored content)
- enemy stats, XP and gold come from the gate level (``mob_stats`` /
  ``boss_stats``, the same formulas the authored Gates use)
- the material's value is capped by ``economy.generated_item_value_cap``
- the chain always loops (A → B / C (magic) → A), so it never dead-ends
- a successor slots in front of the lair's planned next occupant, so the
  authored chain carries on after it

``mob_stats`` / ``boss_stats`` / ``abilities_for`` are also the single source
for the authored Gate files' numbers.
"""
from __future__ import annotations

import re
import zlib

STATUSES = {"poison", "bleed", "burn", "stun", "frozen"}

MOVES = {  # theme -> (heavy blows, secondary moves); used when a name is missing
    "mythical": (["Titan's Wrath", "Horned Charge", "Ancient Fury", "Earthshaker", "Doom of Legend"],
                 ["Curse of the Old Ones", "Hero's Bane", "Primordial Roar"]),
    "technology": (["Overclock Slam", "Piston Strike", "Core Overload", "Railgun Burst", "Hydraulic Crush"],
                   ["System Siphon", "Repair Protocol", "Shock Pulse"]),
    "paranormal": (["Grave Grasp", "Wail of the Dead", "Spectral Rend", "Haunting Blow", "Soul Rip"],
                   ["Life Leech", "Chill of the Grave", "Mourning Hymn"]),
    "elemental": (["Cataclysm", "Elemental Surge", "Primal Eruption", "Storm of Ruin", "Avalanche"],
                  ["Elemental Siphon", "Surging Tide", "Ember Wreath"]),
    "outlaw": (["Dirty Haymaker", "Backstab", "Brutal Cleave"], ["Call the Gang", "Second Wind"]),
    "fungal": (["Spore Burst", "Rot Slam"], ["Mycelial Mend", "Blight Cloud"]),
    "insect": (["Mandible Crush", "Stinger Lance"], ["Call the Swarm", "Royal Venom"]),
    "cosmic": (["Starfall", "Gravity Well"], ["Light Drain", "Cosmic Flare"]),
    "time": (["Stopped Moment", "Rewind Strike"], ["Stolen Hour", "Paradox"]),
    "celestial": (["Judgement", "Radiant Smite"], ["Choir of Light", "Benediction"]),
    "eldritch": (["Madness Unbound", "Dream Crush"], ["Devour Thought", "Waking Nightmare"]),
}


def rank(level: int) -> str:
    return "E" if level <= 3 else "D" if level <= 6 else "C" if level <= 10 else "B" if level <= 14 else "A"


def mob_stats(level: int) -> dict:
    return dict(max_hp=int(14 + 7 * level), attack=int(4 + 1.3 * level), defense=int(1 + level / 3),
                xp_reward=int(10 + 9 * level), gold_reward=int(60 + 45 * level), rank=rank(level))


def boss_stats(level: int, mult: float = 1.0) -> dict:
    return dict(max_hp=int((45 + 16 * level) * mult), attack=int((8 + 1.6 * level) * mult),
                defense=int(3 + 0.55 * level), xp_reward=int((60 + 30 * level) * mult),
                gold_reward=int((400 + 250 * level) * mult), rank=rank(level + 2))


def group_scale(stats: dict, party_size: int) -> dict:
    """Group Gates: masters get HP and rewards for the whole party, but hit
    only +20% harder per extra member (the party shares the blows)."""
    n = max(1, int(party_size))
    if n == 1:
        return stats
    out = dict(stats)
    hp_mult = 1 + 0.6 * (n - 1)
    for k in ("max_hp", "xp_reward", "gold_reward"):
        out[k] = int(out[k] * hp_mult)
    out["attack"] = int(out["attack"] * (1 + 0.2 * (n - 1)))
    return out


def potion(level: int) -> str:
    return "health_potion_major" if level >= 6 else "health_potion"


def abilities_for(boss_id: str, theme: str, status: str, minion: str, mult: float,
                  heavy_name: str = "", second_name: str = "", escorted: bool = False) -> list[dict]:
    """A telegraphed heavy blow, plus one more move. An ``escorted`` boss
    (it fights with minions) gets a heal rather than more pressure: three
    sources of status damage at once wiped out casters in the balance sim."""
    h = zlib.crc32(boss_id.encode())
    heavy, second = MOVES.get(theme, MOVES["mythical"])
    out = [{"name": heavy_name or heavy[h % len(heavy)], "kind": "heavy",
            "every": 3 if mult >= 1.1 else 4, "power": round(1.7 + 0.1 * (h % 4), 2)}]
    name2 = second_name or second[(h // 7) % len(second)]
    if escorted:
        out.append({"name": name2, "kind": "heal", "every": 5, "power": 0.12})
    elif status:
        out.append({"name": name2, "kind": "status", "every": 5, "power": 1.0, "status": status})
    elif minion:
        out.append({"name": name2, "kind": "summon", "every": 5, "power": 1.0, "summon": minion})
    elif h % 2:
        out.append({"name": name2, "kind": "drain", "every": 5, "power": 0.5})
    else:
        out.append({"name": name2, "kind": "heal", "every": 5, "power": 0.15})
    return out


def slug(text: str, n: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s or "thing")[:n].strip("_")


def _master(m, oid: str, level: int, mult: float, theme: str, minion: str,
            party_size: int = 1) -> tuple[dict, dict]:
    """(occupant dict, boss enemy template) for one AI master."""
    boss_id = f"{oid}_boss"
    status = m.status if m.status in STATUSES else ""
    boss_name = m.boss_name or m.title
    tmpl = {"name": boss_name, **group_scale(boss_stats(level, mult), party_size),
            "loot_table": [[potion(level), 0.5], ["mana_potion_major" if level >= 6 else "mana_potion", 0.25]],
            "abilities": abilities_for(boss_id, theme, status, minion, mult, m.heavy_move, m.second_move),
            "enrage_below": 0.35}
    if status:
        tmpl["status_on_hit"] = [status, 0.3]
    occ = {"title": m.title, "enemies": [boss_id], "intro": m.intro or f"{m.title} waits for you.",
           "victory": m.victory or f"{m.title} falls.", "arrival_omen": m.omen, "arrival_rumour": m.rumour}
    if m.names:
        occ["name_pool"] = list(m.names)
    return occ, {boss_id: tmpl}


def build_gate(resp, level: int, seq: int, gate_id: str = "", party_size: int = 1) -> dict:
    """A full gate file fragment {"gate": {...}, "enemy_templates": {...}, "material": {...}}
    from an AIGateResponse."""
    from systems.economy import generated_item_value_cap

    level = max(1, min(50, int(level)))
    gid = gate_id or f"gen_gate_{slug(resp.name, 20)}_{seq}"
    party_size = max(1, min(5, int(party_size)))

    theme = slug(resp.theme, 16) or "mythical"
    mat_id = f"{gid}_material"
    material = {
        "item_id": mat_id, "name": (resp.material_name or f"{resp.name} Shard")[:60], "rarity": "UNCOMMON" if level < 10 else "RARE",
        "item_type": "MATERIAL", "description": resp.material_description or f"Something taken from {resp.name}.",
        "flavor_text": "The System lists it under GATE MATERIALS (unverified).",
        "value_gold": min(10 + 4 * level, generated_item_value_cap(level, "MATERIAL")), "weight": 0.2, "stackable": True,
    }
    mobs = (list(resp.mob_names) + ["Rift Spawn", "Rift Stalker"])[:2]
    et: dict[str, dict] = {}
    mob_ids = []
    for i, name in enumerate(mobs):
        mid = f"{gid}_mob{i}"
        stats = mob_stats(level)
        stats["max_hp"] = int(stats["max_hp"] * (1 + 0.3 * (party_size - 1)))
        et[mid] = {"name": name, **stats, "loot_table": [[mat_id, 0.35], [potion(level), 0.12]]}
        mob_ids.append(mid)
    texts = (list(resp.room_texts) + ["Something moves in the dark.", "The floor here is not to be trusted.",
                                      "They were waiting for you."])[:3]
    rooms = [
        {"kind": "fight", "text": texts[0], "enemies": [mob_ids[0], mob_ids[0]]},
        {"kind": "trap", "text": texts[1], "loot": [[mat_id, 0.6]],
         "check": {"stat": "AGI", "min": 9 + level // 2, "success": "You get through and find something on the far side.",
                   "fail": "It catches you.", "damage_pct": 0.18}},
        {"kind": "fight", "text": texts[2], "enemies": [mob_ids[1], mob_ids[0]]},
    ]
    masters = list(resp.masters)[:3]
    mults = [1.0, 1.1, 1.15]
    occs: dict[str, dict] = {}
    ids = [f"{gid}_m{i}" for i in range(len(masters))]
    for i, (m, oid) in enumerate(zip(masters, ids)):
        occ, t = _master(m, oid, level, mults[i], theme, mob_ids[0] if i == 1 else "", party_size)
        for v in t.values():
            v["loot_table"] = [[mat_id, 1.0], [mat_id, 0.6]] + v["loot_table"]
        et.update(t)
        occs[oid] = occ
    if len(ids) >= 3:
        occs[ids[0]]["successors"] = [{"to": ids[1], "weight": 2}, {"to": ids[2], "weight": 3, "when": {"killed_by": "magic"}}]
        occs[ids[1]]["successors"] = [{"to": ids[0]}]
        occs[ids[2]]["successors"] = [{"to": ids[0]}]
    else:
        occs[ids[0]]["successors"] = [{"to": ids[1]}]
        occs[ids[1]]["successors"] = [{"to": ids[0]}]
    gate = {
        "gate_id": gid, "name": resp.name, "theme": theme, "min_level": level, "party_size": party_size,
        "region": "village_start" if level <= 6 else "verath_city",
        "description": resp.description or f"A new rift, registered at level {level}.",
        "entry_text": resp.entry_text or "The rift closes behind you.", "rooms": rooms, "material": mat_id,
        "lair": {"name": resp.heart or f"the heart of {resp.name}", "respawn_turns": 25 + level,
                 "first_occupant": ids[0], "fallback": [ids[0]], "occupants": occs},
    }
    return {"gate": gate, "enemy_templates": et, "material": material}


def build_successor(resp, lair, level: int, seq: int, next_occupant_id: str, theme: str = "mythical") -> dict:
    """{"occupant_id", "occupant", "enemy_templates"} for an AISuccessorResponse.
    Its successor is the lair's planned next occupant, so the chain carries on."""
    oid = f"gen_{slug(lair.lair_id, 20)}_s{seq}"
    occ, et = _master(resp.master, oid, level, 1.1, theme, "")
    occ["successors"] = [{"to": next_occupant_id if next_occupant_id in lair.occupants else lair.first_occupant}]
    return {"occupant_id": oid, "occupant": occ, "enemy_templates": et}
