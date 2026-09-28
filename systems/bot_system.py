"""
Bot agents: other adventurers living in the world.

BotAgent   — one bot's full state (class, level, HP, inventory, gold, what
             it's doing). Pydantic, stored as JSON in world_db.bot_instances.
BotRegistry — authored bot templates (data/bots/bot_templates.json) plus the
             name / archetype pools used to generate more
             (data/bots/bot_generation.json).
BotManager — live bots for the current save. ``populate`` loads this save's
             bots from world_db, or creates the authored ones plus
             generated ones up to BOT_COUNT for a new save.

Behaviour lives in systems/bot_brain.py (rules-based, no AI calls). The AI
is only used for what bots *say* (core/bot_flow.py).
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from entities.player import Stats

if TYPE_CHECKING:
    from persistence.world_db import WorldDatabase

ARCHETYPES = ("fighter", "explorer", "gatherer", "trader", "crafter")


class BotAgent(BaseModel):
    bot_id: str
    name: str
    personality_seed: str = ""
    archetype: str = "explorer"
    class_id: str = "warrior"
    current_goal: str = "idle"          # kept for the admin view / legacy AI path
    current_zone_id: str = "village_start"
    stats: Stats = Field(default_factory=Stats)
    level: int = 1
    xp: int = 0
    hp: int = 0                         # 0 = fill to max on first validation
    max_hp: int = 0
    inventory: dict[str, int] = Field(default_factory=dict)
    gold: int = 100                     # copper, like player.gold
    kills: int = 0
    activity: str = "getting their bearings"
    memory: list[str] = Field(default_factory=list)
    disposition: float = 0.0
    turn_last_acted: int = 0
    is_generated: bool = False

    @field_validator("inventory", mode="before")
    @classmethod
    def _counts(cls, v: object) -> dict[str, int]:
        """Older saves stored inventory as a list of ids; count them."""
        if isinstance(v, list):
            out: dict[str, int] = {}
            for item_id in v:
                out[str(item_id)] = out.get(str(item_id), 0) + 1
            return out
        return v or {}

    @field_validator("archetype", mode="before")
    @classmethod
    def _archetype(cls, v: object) -> str:
        return v if v in ARCHETYPES else "explorer"

    def model_post_init(self, __context) -> None:
        if self.max_hp <= 0:
            self.max_hp = bot_max_hp(self)
        if self.hp <= 0 or self.hp > self.max_hp:
            self.hp = self.max_hp

    # ── Inventory helpers ───────────────────────────────────────────────────
    def add_item(self, item_id: str, qty: int = 1) -> None:
        self.inventory[item_id] = self.inventory.get(item_id, 0) + qty

    def remove_item(self, item_id: str, qty: int = 1) -> bool:
        have = self.inventory.get(item_id, 0)
        if have < qty:
            return False
        if have == qty:
            del self.inventory[item_id]
        else:
            self.inventory[item_id] = have - qty
        return True

    def item_count(self) -> int:
        return sum(self.inventory.values())

    def remember(self, line: str, keep: int = 12) -> None:
        self.memory.append(line)
        if len(self.memory) > keep:
            self.memory = self.memory[-keep:]


def bot_max_hp(bot: BotAgent) -> int:
    return 30 + 8 * bot.level + 2 * bot.stats.VIT


# ── Templates + generation pools ─────────────────────────────────────────────

class BotRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, dict] = {}
        self.names: list[str] = []
        self.archetypes: list[dict] = []

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            self._templates[entry["bot_id"]] = entry

    def load_generation(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.names = list(data.get("names", []))
        self.archetypes = [a for a in data.get("archetypes", []) if a.get("archetype") in ARCHETYPES]

    def get_template(self, bot_id: str) -> dict | None:
        return self._templates.get(bot_id)

    def all_templates(self) -> list[dict]:
        return list(self._templates.values())


_ARCHETYPE_STATS = {
    # stat that gets the biggest boost for each archetype
    "fighter": ("STR", "VIT", "END"),
    "explorer": ("AGI", "LCK", "WIS"),
    "gatherer": ("VIT", "WIS", "AGI"),
    "trader": ("INT", "LCK", "WIS"),
    "crafter": ("INT", "WIS", "END"),
}
_START_ZONES = {
    "fighter": "dungeon_floor1", "explorer": "village_start", "gatherer": "dungeon_floor1",
    "trader": "verath_city", "crafter": "village_start",
}


def generate_bots(registry: BotRegistry, count: int, seed: str, taken_names: set[str]) -> list[BotAgent]:
    """Deterministically generate ``count`` bots for a save (same seed → same cast)."""
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))
    names = [n for n in registry.names if n not in taken_names]
    rng.shuffle(names)
    pool = registry.archetypes or [{"archetype": "explorer", "weight": 1,
                                    "classes": ["warrior"], "personalities": ["adventurer"]}]
    weights = [max(1, int(a.get("weight", 1))) for a in pool]
    bots: list[BotAgent] = []
    for i in range(count):
        arche = rng.choices(pool, weights=weights, k=1)[0]
        kind = arche["archetype"]
        name = names[i] if i < len(names) else f"Adventurer {i + 1}"
        stats = {s: rng.randint(4, 7) for s in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END")}
        for bonus, stat in zip((4, 3, 2), _ARCHETYPE_STATS[kind]):
            stats[stat] += bonus
        level = rng.choice((1, 1, 1, 2, 2, 3))
        bots.append(BotAgent(
            bot_id=f"bot_{name.lower().replace(' ', '_')}_{i}",
            name=name,
            personality_seed=rng.choice(arche.get("personalities") or ["adventurer"]),
            archetype=kind,
            class_id=rng.choice(arche.get("classes") or ["warrior"]),
            current_zone_id=_START_ZONES[kind],
            stats=Stats(**stats),
            level=level,
            gold=rng.randint(100, 900) if kind != "trader" else rng.randint(1500, 3000),
            is_generated=True,
        ))
    return bots


class BotManager:
    def __init__(self) -> None:
        self._bots: list[BotAgent] = []

    def load_from_templates(self, registry: BotRegistry) -> None:
        for tmpl in registry.all_templates():
            self._bots.append(BotAgent.model_validate(tmpl))

    def load_from_db(self, world_db: "WorldDatabase", registry: BotRegistry | None = None) -> None:
        rows = world_db.load_bot_instances()
        for row in rows:
            data = json.loads(row["definition"])
            # Saves from before bots had archetypes stored the authored bots
            # without one; take their identity from the template again.
            tmpl = registry.get_template(data.get("bot_id", "")) if registry else None
            if tmpl and "archetype" not in data:
                for key in ("archetype", "class_id", "personality_seed", "current_goal"):
                    if key in tmpl:
                        data[key] = tmpl[key]
            agent = BotAgent.model_validate(data)
            agent.current_zone_id = row["current_zone_id"] or agent.current_zone_id
            self._bots.append(agent)

    def populate(
        self, registry: BotRegistry, world_db: "WorldDatabase | None", count: int, seed: str,
    ) -> None:
        """Bots for the current save: this save's stored bots, topped up to
        ``count`` with generated ones (older saves stored fewer); a new save
        gets the authored bots plus generated ones."""
        self._bots = []
        if world_db is not None:
            self.load_from_db(world_db, registry)
        if not self._bots:
            self.load_from_templates(registry)
        missing = max(0, count - len(self._bots))
        if missing:
            taken = {b.name for b in self._bots}
            new = generate_bots(registry, missing, seed, taken)
            ids = {b.bot_id for b in self._bots}
            self._bots.extend(b for b in new if b.bot_id not in ids)
        if world_db is not None:
            self.save_to_db(world_db)

    def save_to_db(self, world_db: "WorldDatabase") -> None:
        for bot in self._bots:
            world_db.upsert_bot_instance(
                bot_id=bot.bot_id,
                definition=bot.model_dump(mode="json"),
                current_zone_id=bot.current_zone_id,
                last_active_turn=bot.turn_last_acted,
            )

    def active_count(self) -> int:
        return len(self._bots)

    def get(self, bot_id: str) -> BotAgent | None:
        return next((b for b in self._bots if b.bot_id == bot_id), None)

    def all(self) -> list[BotAgent]:
        return list(self._bots)

    def in_zone(self, zone_id: str) -> list[BotAgent]:
        return [b for b in self._bots if b.current_zone_id == zone_id]


# ── Talk (fallback when the AI is offline) ───────────────────────────────────

_GREETINGS = {
    "fighter": "'Floor's crawling today. Good. I need the practice.'",
    "explorer": "'Every time I map a tunnel, the dungeon grows another one.'",
    "gatherer": "'Mind your step, there's good stock growing round here.'",
    "trader": "'Buying, selling, or just admiring the goods?'",
    "crafter": "'Got a pot on the boil back at camp. What do you need?'",
}


def bot_profile(bot: BotAgent) -> dict:
    """Plain facts about a bot for prompts and the Who's-around list."""
    return {
        "name": bot.name, "level": bot.level, "class": bot.class_id,
        "archetype": bot.archetype, "personality": bot.personality_seed,
        "doing": bot.activity, "recent": bot.memory[-3:],
    }


def fallback_line(bot: BotAgent) -> str:
    """What a bot says when no AI line is available: greeting + latest news."""
    line = _GREETINGS.get(bot.archetype, "'Well met.'")
    if bot.memory:
        news = bot.memory[-1].split(" (turn")[0].rstrip(".")
        line += f" A shrug. 'Just {news[0].lower() + news[1:]}.'"
    return line
