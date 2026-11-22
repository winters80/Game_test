"""
Content health: what in data/ and scenes/ a player can never reach or use.

    python scripts/content_health.py          # or Admin → Content health

Findings (none of them crash the game; they are wasted or broken content):

- **unreachable scenes / nodes**: no option chain from the prologue leads there
- **dead links**: an option leads to a scene or node that doesn't exist
- **unoffered quests**: no NPC seed, ``start_quest:`` trigger or chain step offers them
- **orphan items**: defined but never mentioned anywhere else (no drop, shop,
  recipe, quest, Gate, scene or starting kit)
- **dead-end materials**: materials nothing uses (no recipe, quest or trader buys them)

The checks read the content files directly, so they also see what the
engine only builds at runtime (Gate loot, trophies, bot kits).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_SCENES = ("prologue",)
# Scenes the engine enters by code (death, endings, menus) rather than an option.
CODE_ENTERED = {"death_screen", "game_over", "ending", "respawn"}


@dataclass
class HealthReport:
    unreachable_scenes: list[str] = field(default_factory=list)
    unreachable_nodes: list[str] = field(default_factory=list)
    dead_links: list[str] = field(default_factory=list)
    unoffered_quests: list[str] = field(default_factory=list)
    orphan_items: list[str] = field(default_factory=list)
    dead_end_materials: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def problems(self) -> int:
        return sum(len(getattr(self, k)) for k in (
            "unreachable_scenes", "unreachable_nodes", "dead_links",
            "unoffered_quests", "orphan_items", "dead_end_materials"))

    def lines(self, limit: int = 12) -> list[str]:
        out = [f"{k.replace('_', ' ')}: {v}" for k, v in self.counts.items()]
        for key, title in (("dead_links", "Dead links (broken)"),
                           ("unreachable_scenes", "Scenes nothing leads to"),
                           ("unreachable_nodes", "Scene nodes nothing leads to"),
                           ("unoffered_quests", "Quests nobody offers"),
                           ("orphan_items", "Items that never appear"),
                           ("dead_end_materials", "Materials nothing uses")):
            items = getattr(self, key)
            out.append(f"{title}: {len(items)}")
            out += [f"   - {x}" for x in items[:limit]]
            if len(items) > limit:
                out.append(f"   … and {len(items) - limit} more")
        return out


def _load_json_texts(*folders: Path) -> dict[Path, str]:
    texts = {}
    for folder in folders:
        for f in folder.rglob("*.json"):
            try:
                texts[f] = f.read_text(encoding="utf-8")
            except OSError:
                continue
    return texts


def check(engine) -> HealthReport:
    rep = HealthReport()
    scenes_dir = ROOT / "scenes" / "data"
    data_dir = ROOT / "data"

    # ── Scenes: reachability from the prologue ──
    raw_scenes: dict[str, dict] = {}
    for f in scenes_dir.glob("*.json"):
        try:
            raw_scenes[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    seen_nodes: set[tuple[str, str]] = set()
    todo = [(s, "root") for s in ENTRY_SCENES if s in raw_scenes]
    while todo:
        scene_id, node_id = todo.pop()
        if (scene_id, node_id) in seen_nodes:
            continue
        seen_nodes.add((scene_id, node_id))
        node = (raw_scenes.get(scene_id, {}).get("nodes") or {}).get(node_id)
        if node is None:
            continue
        for opt in node.get("options", []):
            target = opt.get("leads_to") or "__stay__"
            tnode = opt.get("leads_to_node") or "root"
            tscene = scene_id if target == "__stay__" else target
            if tscene not in raw_scenes:
                rep.dead_links.append(f"{scene_id}.{node_id} → {tscene} (no such scene)")
                continue
            if tnode not in (raw_scenes[tscene].get("nodes") or {}):
                rep.dead_links.append(f"{scene_id}.{node_id} → {tscene}.{tnode} (no such node)")
                continue
            todo.append((tscene, tnode))
    reached_scenes = {s for s, _ in seen_nodes}
    rep.unreachable_scenes = sorted(s for s in raw_scenes if s not in reached_scenes and s not in CODE_ENTERED)
    for scene_id in sorted(reached_scenes):
        for node_id in (raw_scenes[scene_id].get("nodes") or {}):
            if (scene_id, node_id) not in seen_nodes:
                rep.unreachable_nodes.append(f"{scene_id}.{node_id}")

    # ── Quests: who offers them ──
    texts = _load_json_texts(data_dir, scenes_dir)
    blob = "\n".join(texts.values())
    offered: set[str] = set()
    for npc in (engine.npc_registry.all() if getattr(engine, "npc_registry", None) else []):
        for seed in getattr(npc, "quest_seeds", None) or []:
            tid = getattr(seed, "quest_template_id", None) or (seed.get("quest_template_id") if isinstance(seed, dict) else None)
            if tid:
                offered.add(tid)
    offered |= set(re.findall(r'"start_quest:([a-z0-9_]+)', blob))
    quests = engine.quest_registry.all() if getattr(engine, "quest_registry", None) else []
    rep.unoffered_quests = sorted(q.template_id for q in quests
                                  if q.template_id not in offered and not q.template_id.startswith("ai_"))

    # ── Items: mentioned anywhere besides their own definition? ──
    from systems.combat_system import ENCOUNTERS, ENEMY_TEMPLATES
    code_refs = json.dumps([v.get("loot_table") for v in ENEMY_TEMPLATES.values()]) + json.dumps(ENCOUNTERS, default=str)
    try:
        code_refs += (ROOT / "core" / "menu_flow.py").read_text(encoding="utf-8")       # starting kit
        code_refs += (ROOT / "systems" / "bot_brain.py").read_text(encoding="utf-8")
    except OSError:
        pass
    items_dir = data_dir / "items"
    item_def_text = "\n".join(t for p, t in texts.items() if items_dir in p.parents)
    other_text = "\n".join(t for p, t in texts.items() if items_dir not in p.parents) + code_refs
    recipes = getattr(engine, "recipes", None) or []
    ingredients = {i["item_id"] for r in recipes for i in r.get("ingredients", [])}
    for item in engine.item_registry.all():
        iid = item.item_id
        if iid.startswith("gen_"):
            continue
        quoted = f'"{iid}"'
        mentioned = (quoted in other_text or f":{iid}" in other_text or f"'{iid}'" in other_text
                     or item_def_text.count(quoted) > 1)
        if not mentioned:
            rep.orphan_items.append(iid)
        elif item.item_type.value == "MATERIAL" and iid not in ingredients:
            # Used if a quest or trader asks for it.
            if not re.search(rf'"{re.escape(iid)}"\s*:\s*\d|"has_item"\s*:\s*"{re.escape(iid)}"|"buys"[^\]]*"{re.escape(iid)}"|take_item:{re.escape(iid)}',
                             blob) and '"type:MATERIAL"' not in blob:
                rep.dead_end_materials.append(iid)

    rep.counts = {"scenes": len(raw_scenes), "scene nodes reached": len(seen_nodes),
                  "quests": len(quests), "items": len(engine.item_registry.all()),
                  "recipes": len(recipes)}
    rep.dead_links.sort()
    rep.orphan_items.sort()
    rep.dead_end_materials.sort()
    return rep
