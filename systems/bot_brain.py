"""
Rules-based brain for bot adventurers: no AI calls, cheap enough to run for
every bot every few turns.

Each act, a bot looks at its situation and does one thing, using the same
world the player uses:

- **Hurt?** Drink a potion in the dungeon, rest in a safe zone, or head home.
- **In the dungeon:** fight a real encounter from combat_system (XP, gold,
  loot from ENEMY_TEMPLATES), forage, or gather what world growth added,
  depending on archetype. Leave when the bag is full or the fight looks bad.
- **In a safe zone:** sell loot to the traders actually present
  (systems/trade_system), craft from the live recipe list (e.g. potions),
  buy a missing potion ingredient, then set off towards whatever its
  archetype wants.

Archetypes (systems/bot_system.ARCHETYPES) weight those choices: fighters
push to the deepest floor their level allows, gatherers forage and harvest,
traders shuttle between towns building stock to sell to the player,
crafters brew, explorers roam.

``tick_bot`` mutates the bot and returns what happened as ``BotEvent``s.
Pure logic: no rendering, no I/O.
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from entities.item import ItemRegistry
    from entities.npc import NPCTemplate
    from systems.bot_system import BotAgent

# Places the player can actually be (scene ids), and how bots walk between them.
BOT_MAP: dict[str, list[str]] = {
    "village_start": ["camp_rest", "dungeon_floor1", "verath_city"],
    "camp_rest": ["village_start"],
    "verath_city": ["village_start"],
    "dungeon_floor1": ["village_start", "dungeon_floor2"],
    "dungeon_floor2": ["dungeon_floor1", "dungeon_floor3"],
    "dungeon_floor3": ["dungeon_floor2"],
}
SAFE_ZONES = {"village_start", "camp_rest", "verath_city"}
HOME = "village_start"
MIN_LEVEL = {"dungeon_floor2": 3, "dungeon_floor3": 5}

ZONE_ENCOUNTERS = {
    "dungeon_floor1": ["single_slime", "goblin_patrol", "wolf_pack", "crystal_spider_den"],
    "dungeon_floor2": ["crystal_spider_den", "wolf_pack", "troll_patrol", "corrupted_golem"],
    "dungeon_floor3": ["corrupted_golem", "fracture_wraith"],
}
ENCOUNTER_NAMES = {
    "single_slime": "a dungeon slime", "goblin_patrol": "a goblin patrol",
    "wolf_pack": "a wolf pack", "crystal_spider_den": "a nest of crystal spiders",
    "corrupted_golem": "a corrupted golem", "fracture_wraith": "a pair of fracture wraiths",
    "troll_patrol": "a troll patrol",
}
FORAGE = {
    "dungeon_floor1": ["forest_herb", "forest_herb", "herb_bundle", "empty_vial"],
    "dungeon_floor2": ["mountain_root", "forest_herb"],
    "camp_rest": ["firewood", "forest_herb"],
}
PLACE = {
    "village_start": "the outer market", "camp_rest": "the roadside camp",
    "verath_city": "Verath", "dungeon_floor1": "Floor 1", "dungeon_floor2": "Floor 2",
    "dungeon_floor3": "Floor 3",
}
POTIONS = ("health_potion_major", "health_potion")
KEEP_POTIONS = 2
BAG_FULL = 10
MAX_LEVEL = 20


@dataclass
class BotWorld:
    """What a bot can see this turn."""
    item_registry: "ItemRegistry"
    traders_at: Callable[[str], list["NPCTemplate"]]
    recipes: list[dict] = field(default_factory=list)
    yields: dict[str, list[str]] = field(default_factory=dict)   # zone → world-growth yield items
    turn: int = 0
    # zone → occupied boss lairs (core/lair_flow.bot_lair_view): lair_id,
    # lair_name, title, min_level, enemies. A lair a bot clears is removed.
    lairs: dict[str, list[dict]] = field(default_factory=dict)
    # Gates bots can run from town (core/gate_flow.bot_gate_view): gate_id,
    # name, min_level, region, rooms (enemy lists), lair (or None), material.
    gates: list[dict] = field(default_factory=list)
    allies: dict[str, list] = field(default_factory=dict)   # zone → bots there (group Gates)
    # Guilds bots may join: [{guild_id, name, ranks, archetypes, player_founded}]
    guilds: list[dict] = field(default_factory=list)
    auction_zone: str = "verath_city"


@dataclass
class BotEvent:
    bot_id: str
    zone_id: str          # where it happened (the zone the bot is in afterwards for moves)
    text: str
    kind: str             # move | fight | defeat | level | trade | craft | gather | rest | potion | boss_kill
    #                       | party | guild | auction
    item_id: str = ""     # auction: what was listed
    price: int = 0        # auction: starting bid (copper)
    guild_id: str = ""    # guild: which guild was joined
    from_zone: str = ""
    lair_id: str = ""     # boss_kill: which lair was cleared
    killed_by: str = ""   # boss_kill: "magic" | "physical"


def place(zone_id: str) -> str:
    return PLACE.get(zone_id, zone_id.replace("_", " ").title())


# ── Combat numbers (derived from the bot's class-agnostic stats) ─────────────

def bot_attack(bot: "BotAgent") -> int:
    s = bot.stats
    return 3 + 2 * bot.level + max(s.STR, s.AGI, s.INT) // 2


def bot_defense(bot: "BotAgent") -> int:
    return bot.level // 2 + bot.stats.END // 4


def expected_damage(bot: "BotAgent", enemies: list) -> int:
    """Rough damage the bot takes clearing these enemies (no randomness)."""
    if not enemies:
        return 0
    per_round = max(1, bot_attack(bot) - sum(e.defense for e in enemies) // len(enemies))
    rounds = math.ceil(sum(e.max_hp for e in enemies) / per_round)
    incoming = sum(max(1, e.attack - bot_defense(bot)) for e in enemies)
    return int(rounds * incoming * 0.55)


def _fight(bot: "BotAgent", encounter_id: str, rng: random.Random, world: BotWorld) -> list[BotEvent]:
    from systems.combat_system import roll_loot, spawn_encounter

    enemies = spawn_encounter(encounter_id)
    taken = int(expected_damage(bot, enemies) * rng.uniform(0.75, 1.3))
    zone = bot.current_zone_id
    name = ENCOUNTER_NAMES.get(encounter_id, encounter_id.replace("_", " "))
    if taken >= bot.hp:
        bot.hp = 1
        bot.current_zone_id = HOME
        bot.activity = "licking their wounds in the outer market"
        bot.remember(f"Beaten by {name} on {place(zone)} (turn {world.turn})")
        return [BotEvent(bot.bot_id, zone, f"{bot.name} was driven off {place(zone)} by {name}.",
                         "defeat", from_zone=zone)]
    bot.hp -= taken
    xp = sum(e.xp_reward for e in enemies)
    gold = sum(e.gold_reward for e in enemies)
    bot.gold += gold
    bot.kills += len(enemies)
    for item_id in roll_loot(enemies):
        bot.add_item(item_id)
    bot.activity = f"hunting on {place(zone)}"
    bot.remember(f"Defeated {name} on {place(zone)} (turn {world.turn})")
    events = [BotEvent(bot.bot_id, zone, f"{bot.name} took down {name}.", "fight")]
    events += _gain_xp(bot, xp)
    return events


def bot_blow(bot: "BotAgent") -> str:
    """How a bot fights: casters (INT their best stat) kill with magic."""
    s = bot.stats
    return "magic" if s.INT > max(s.STR, s.AGI) else "physical"


def _try_boss(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """Fighters and explorers sometimes take on the floor's boss, if they
    meet the lair's level and the fight looks survivable."""
    from config import LAIR_BOT_CHALLENGE_CHANCE, LAIR_BOT_SAFETY

    for lair in world.lairs.get(bot.current_zone_id, []):
        if bot.level < lair["min_level"] or rng.random() >= LAIR_BOT_CHALLENGE_CHANCE:
            continue
        if expected_damage(bot, lair["enemies"]) >= bot.hp * LAIR_BOT_SAFETY:
            continue
        return _fight_boss(bot, lair, rng, world)
    return []


def _fight_boss(bot: "BotAgent", lair: dict, rng: random.Random, world: BotWorld,
                party: list | None = None) -> list[BotEvent]:
    from systems.combat_system import roll_loot

    party = party or [bot]
    enemies = lair["enemies"]
    zone = bot.current_zone_id
    taken = int(expected_damage(bot, enemies) * rng.uniform(0.75, 1.3) / len(party) ** 1.5)
    for m in party[1:]:
        m.hp = max(1, m.hp - taken)
        m.remember(f"Fought {lair['title']} in {lair['lair_name']} with {bot.name} (turn {world.turn})")
    if taken >= bot.hp:
        bot.hp = 1
        bot.current_zone_id = HOME
        bot.activity = "licking their wounds in the outer market"
        bot.remember(f"Beaten by {lair['title']} in {lair['lair_name']} (turn {world.turn})")
        return [BotEvent(bot.bot_id, zone, f"{bot.name} was driven out of {lair['lair_name']} by {lair['title']}.",
                         "defeat", from_zone=zone)]
    bot.hp -= taken
    bot.gold += sum(e.gold_reward for e in enemies)
    bot.kills += len(enemies)
    for item_id in roll_loot(enemies):
        bot.add_item(item_id)
    lzone = lair.get("zone_id", zone)
    world.lairs[lzone] = [l for l in world.lairs.get(lzone, []) if l is not lair]
    for gate in world.gates:
        if gate.get("lair") is lair:
            gate["lair"] = None
    bot.activity = f"celebrating a kill on {place(zone)}"
    bot.remember(f"Slew {lair['title']} in {lair['lair_name']} (turn {world.turn})")
    who = _names(party)
    events = [BotEvent(bot.bot_id, zone, f"{who} slew {lair['title']} in {lair['lair_name']}!",
                       "boss_kill", lair_id=lair["lair_id"], killed_by=bot_blow(bot))]
    events += _gain_xp(bot, sum(e.xp_reward for e in enemies))
    return events


def _gate_run(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """Run a registered Gate from town: clear its rooms (if survivable),
    loot them, and maybe take on its master. The bot stays where it is."""
    from config import LAIR_BOT_CHALLENGE_CHANCE, LAIR_BOT_SAFETY
    from systems.combat_system import roll_loot

    zone = bot.current_zone_id
    fits = [g for g in world.gates if g["min_level"] <= bot.level <= g["min_level"] + 6 and g["rooms"]]
    rng.shuffle(fits)
    if rng.random() < 0.5:     # half the time, look for a party first
        fits.sort(key=lambda g: -g.get("party_size", 1))
    for gate in fits:
        size = gate.get("party_size", 1)
        party = [bot]
        if size > 1:          # a group Gate: find allies in town, or try another Gate
            pool = [b for b in world.allies.get(zone, []) if b is not bot and b.hp > b.max_hp * 0.5
                    and b.level >= gate["min_level"] - 3 and b.archetype in ("fighter", "explorer", "gatherer")]
            if len(pool) < size - 1:
                continue
            party += rng.sample(pool, size - 1)
        return _run_gate_as_party(bot, party, gate, rng, world)
    return []


def _run_gate_as_party(bot: "BotAgent", party: list, gate: dict, rng: random.Random,
                       world: BotWorld) -> list[BotEvent]:
    """The party clears the rooms (if survivable) and maybe the master; the
    damage is shared, everyone gets the XP, the leader carries the loot."""
    from config import LAIR_BOT_CHALLENGE_CHANCE, LAIR_BOT_SAFETY
    from systems.combat_system import roll_loot

    zone = bot.current_zone_id
    n = len(party)
    enemies = [e for room in gate["rooms"] for e in room]
    # A party shares the hits and kills faster: damage per member falls as n^1.5.
    taken = int(sum(expected_damage(bot, room) for room in gate["rooms"]) * rng.uniform(0.75, 1.3) / n ** 1.5)
    if any(taken >= m.hp * 0.8 for m in party):
        return []
    gold = sum(e.gold_reward for e in enemies)
    xp = sum(e.xp_reward for e in enemies)
    for m in party:
        m.hp -= taken
        m.gold += gold // n
        m.kills += len(enemies) // n
    for item_id in roll_loot(enemies):
        bot.add_item(item_id)
    names = _names(party)
    for m in party:
        m.activity = f"back from {gate['name']}"
        m.remember(f"Ran {gate['name']}{' with ' + _names([x for x in party if x is not m]) if n > 1 else ''} (turn {world.turn})")
    kind = "party" if n > 1 else "fight"
    events = [BotEvent(bot.bot_id, zone, f"{names} came back from {gate['name']}"
                       f"{' together' if n > 1 else ''}.", kind)]
    for m in party:
        events += _gain_xp(m, xp)
    lair = gate.get("lair")
    if (lair and bot.level >= lair["min_level"] and rng.random() < LAIR_BOT_CHALLENGE_CHANCE
            and expected_damage(bot, lair["enemies"]) / n ** 1.5 < min(m.hp for m in party) * LAIR_BOT_SAFETY):
        boss = _fight_boss(bot, lair, rng, world, party=party)
        if boss and boss[0].kind == "defeat":
            for m in party:
                m.current_zone_id = zone      # beaten inside a gate: home is where it is
        events += boss
    return events


GUILD_FOR = {"fighter": ["iron_vanguard", "silver_fangs"], "explorer": ["silver_fangs", "mages_conclave"],
             "gatherer": ["golden_scales", "iron_hammer_exchange"], "trader": ["golden_scales", "iron_hammer_exchange"],
             "crafter": ["mages_conclave", "iron_hammer_exchange"]}
JOIN_GUILD_LEVEL = 3
AUCTION_MIN_VALUE = 15       # gold: only worth listing items at least this valuable


def _join_guild(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """An unaffiliated bot (level 3+) joins a guild that suits it, or the
    player's own guild if they've founded one."""
    if bot.guild_id or bot.level < JOIN_GUILD_LEVEL or not world.guilds:
        return []
    mine = [g for g in world.guilds if g.get("player_founded")]
    pool = mine if mine and rng.random() < 0.3 else \
        [g for g in world.guilds if g["guild_id"] in GUILD_FOR.get(bot.archetype, [])]
    if not pool:
        return []
    g = rng.choice(pool)
    bot.guild_id = g["guild_id"]
    bot.guild_rank = (g.get("ranks") or ["member"])[0]
    bot.remember(f"Joined {g['name']} (turn {world.turn})")
    return [BotEvent(bot.bot_id, bot.current_zone_id, f"{bot.name} joined {g['name']}.", "guild",
                     guild_id=g["guild_id"])]


def guild_rank_for(bot: "BotAgent", ranks: list[str]) -> str:
    """Bots rise one rank every 4 levels (never to the top rank: that's for leaders)."""
    if not ranks:
        return bot.guild_rank
    return ranks[min(len(ranks) - 2 if len(ranks) > 1 else 0, max(0, (bot.level - JOIN_GUILD_LEVEL) // 4))]


def _auction(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """From any town, consign the most valuable thing in the bag to the
    auction house in Verath."""
    if bot.current_zone_id not in SAFE_ZONES or not bot.inventory:
        return []
    best, best_val = None, 0
    for item_id, qty in bot.inventory.items():
        item = world.item_registry.get(item_id)
        if item is None or item_id in POTIONS:
            continue
        val = int(getattr(item, "value_gold", 0) or 0)
        if val >= AUCTION_MIN_VALUE and val > best_val:
            best, best_val = item_id, val
    if best is None:
        return []
    bot.inventory[best] -= 1
    if bot.inventory[best] <= 0:
        del bot.inventory[best]
    price = int(best_val * 100 * rng.uniform(0.9, 1.3))
    name = world.item_registry.get(best).name
    bot.activity = "haggling at the auction house" if bot.current_zone_id == world.auction_zone \
        else "sending goods to the Verath auction"
    bot.remember(f"Listed {name} at auction (turn {world.turn})")
    return [BotEvent(bot.bot_id, bot.current_zone_id, f"{bot.name} put {name} up for auction.", "auction",
                     item_id=best, price=price)]


def _names(bots: list) -> str:
    names = [b.name for b in bots]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _gain_xp(bot: "BotAgent", xp: int) -> list[BotEvent]:
    from systems.bot_system import bot_max_hp
    from systems.level_system import xp_for_level

    bot.xp += xp
    events = []
    while bot.level < MAX_LEVEL and bot.xp >= xp_for_level(bot.level):
        bot.xp -= xp_for_level(bot.level)
        bot.level += 1
        bot.max_hp = bot_max_hp(bot)
        bot.hp = bot.max_hp
        events.append(BotEvent(bot.bot_id, bot.current_zone_id,
                               f"{bot.name} reached level {bot.level}.", "level"))
    return events


# ── Movement ─────────────────────────────────────────────────────────────────

def _allowed(bot: "BotAgent", zone: str) -> bool:
    return bot.level >= MIN_LEVEL.get(zone, 1)


def next_step(bot: "BotAgent", target: str) -> str | None:
    """First zone on the shortest allowed path to ``target`` (None if here / unreachable)."""
    start = bot.current_zone_id
    if start == target or start not in BOT_MAP:
        return None if start == target else HOME
    prev: dict[str, str] = {start: start}
    queue = deque([start])
    while queue:
        z = queue.popleft()
        for n in BOT_MAP.get(z, []):
            if n not in prev and _allowed(bot, n):
                prev[n] = z
                queue.append(n)
    if target not in prev:
        return None
    step = target
    while prev[step] != start:
        step = prev[step]
    return step


def _move(bot: "BotAgent", target: str, doing: str) -> list[BotEvent]:
    step = next_step(bot, target)
    if step is None:
        return []
    old = bot.current_zone_id
    bot.current_zone_id = step
    # En route, say where they're going; once there, say what they're doing.
    if step == target:
        bot.activity = (f"looking for a fight on {place(step)}" if step not in SAFE_ZONES
                        else f"just got back to {place(step)}")
    else:
        bot.activity = doing
    return [BotEvent(bot.bot_id, step, f"{bot.name} headed to {place(step)}.", "move", from_zone=old)]


def _can_handle(bot: "BotAgent", zone: str) -> bool:
    """At full health, at least one of the floor's fights looks comfortable."""
    return any(expected_damage(bot, _preview(e)) < bot.max_hp * 0.4
               for e in ZONE_ENCOUNTERS.get(zone, []))


def _deepest_floor(bot: "BotAgent") -> str:
    for zone in ("dungeon_floor3", "dungeon_floor2", "dungeon_floor1"):
        if _allowed(bot, zone):
            return zone
    return "dungeon_floor1"


def _destination(bot: "BotAgent", world: BotWorld, rng: random.Random) -> tuple[str, str]:
    """Where the bot wants to go next, and how its [W] line reads meanwhile."""
    kind = bot.archetype
    if kind == "fighter":
        floor = _deepest_floor(bot)
        return floor, f"heading down to {place(floor)} to fight"
    if kind == "gatherer":
        harvest = [z for z in world.yields if z in BOT_MAP and _allowed(bot, z)]
        zone = rng.choice(harvest) if harvest and rng.random() < 0.6 else rng.choice(
            ["dungeon_floor1", "dungeon_floor1", "camp_rest"])
        return zone, f"off to gather at {place(zone)}"
    if kind == "trader":
        zone = rng.choice([z for z in SAFE_ZONES if z != bot.current_zone_id])
        return zone, f"on the road to trade in {place(zone)}"
    if kind == "crafter":
        zone = "dungeon_floor1" if rng.random() < 0.35 else rng.choice(sorted(SAFE_ZONES))
        return zone, f"looking for materials in {place(zone)}"
    zone = rng.choice([z for z in BOT_MAP if _allowed(bot, z)])
    return zone, f"exploring towards {place(zone)}"


# ── Inventory / economy ──────────────────────────────────────────────────────

def _potion_count(bot: "BotAgent") -> int:
    return sum(bot.inventory.get(p, 0) for p in POTIONS)


def _drink(bot: "BotAgent", world: BotWorld) -> list[BotEvent]:
    for pid in POTIONS:
        if bot.inventory.get(pid):
            item = world.item_registry.get(pid)
            heal = (item.effect_value if item else 30) or 30
            bot.remove_item(pid)
            bot.hp = min(bot.max_hp, bot.hp + heal)
            return [BotEvent(bot.bot_id, bot.current_zone_id,
                             f"{bot.name} downed a potion mid-fight.", "potion")]
    return []


TRADER_STOCK = 6    # items a trader bot holds back to sell to the player


def _keep(bot: "BotAgent", item_id: str, world: BotWorld) -> int:
    """How many of an item the bot holds on to instead of selling."""
    if item_id in POTIONS:
        return KEEP_POTIONS
    if bot.archetype == "trader":
        return TRADER_STOCK      # stock for players, never dumped on NPC traders
    if bot.archetype == "crafter" or _potion_count(bot) < KEEP_POTIONS:
        needed = 0
        for r in world.recipes:
            for ing in r.get("ingredients", []):
                if ing["item_id"] == item_id:
                    needed = max(needed, ing["qty"] * 2)
        return needed
    return 0


def _sell_all(bot: "BotAgent", world: BotWorld) -> list[BotEvent]:
    from config import format_currency
    from systems.trade_system import sell_price, trader_buys

    traders = world.traders_at(bot.current_zone_id)
    if not traders:
        return []
    earned, sold_to, lines = 0, None, 0
    for item_id, qty in list(bot.inventory.items()):
        item = world.item_registry.get(item_id)
        spare = qty - _keep(bot, item_id, world)
        if item is None or spare <= 0:
            continue
        best = max(traders, key=lambda t: sell_price(t, item) if trader_buys(t, item) else 0)
        price = sell_price(best, item) if trader_buys(best, item) else 0
        if price <= 0:
            continue
        bot.remove_item(item_id, spare)
        earned += price * spare
        sold_to = best
        lines += spare
    if not earned:
        return []
    bot.gold += earned
    bot.activity = f"trading in {place(bot.current_zone_id)}"
    bot.remember(f"Sold {lines} item{'s' if lines != 1 else ''} to {sold_to.name} "
                 f"for {format_currency(earned)} (turn {world.turn})")
    return [BotEvent(bot.bot_id, bot.current_zone_id,
                     f"{bot.name} sold a bag of goods to {sold_to.name}.", "trade")]


def _craft(bot: "BotAgent", world: BotWorld) -> list[BotEvent]:
    for recipe in world.recipes:
        out_id = recipe.get("output_item_id", "")
        if bot.archetype != "crafter" and out_id not in POTIONS:
            continue
        if out_id in POTIONS and _potion_count(bot) >= KEEP_POTIONS + (2 if bot.archetype == "crafter" else 0):
            continue
        ings = recipe.get("ingredients", [])
        if ings and all(bot.inventory.get(i["item_id"], 0) >= i["qty"] for i in ings):
            for i in ings:
                bot.remove_item(i["item_id"], i["qty"])
            bot.add_item(out_id, recipe.get("output_qty", 1))
            item = world.item_registry.get(out_id)
            name = item.name if item else out_id
            bot.activity = f"crafting in {place(bot.current_zone_id)}"
            return [BotEvent(bot.bot_id, bot.current_zone_id,
                             f"{bot.name} crafted {name}.", "craft")]
    return []


def _buy(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """Buy the one missing ingredient for a potion (a vial), and only when the
    bot already holds the herbs, so the purchase is actually used."""
    from systems.trade_system import wares

    if (bot.inventory.get("empty_vial", 0) or _potion_count(bot) >= KEEP_POTIONS
            or bot.inventory.get("forest_herb", 0) < 2):
        return []
    for trader in world.traders_at(bot.current_zone_id):
        wanted = [w for w in wares(trader, world.item_registry) if w.item.item_id == "empty_vial"]
        for offer in wanted:
            if bot.gold - offer.price >= 200:
                bot.gold -= offer.price
                bot.add_item(offer.item.item_id)
                bot.activity = f"trading in {place(bot.current_zone_id)}"
                return [BotEvent(bot.bot_id, bot.current_zone_id,
                                 f"{bot.name} bought {offer.item.name} from {trader.name}.", "trade")]
    return []


def _gather(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    zone = bot.current_zone_id
    pool = list(world.yields.get(zone, [])) * 2 + FORAGE.get(zone, [])
    if not pool:
        return []
    item_id = rng.choice(pool)
    item = world.item_registry.get(item_id)
    if item is None:
        return []
    bot.add_item(item_id)
    bot.activity = f"gathering on {place(zone)}" if zone.startswith("dungeon") else f"gathering near {place(zone)}"
    return [BotEvent(bot.bot_id, zone, f"{bot.name} gathered {item.name}.", "gather")]


# ── The brain ────────────────────────────────────────────────────────────────

def tick_bot(bot: "BotAgent", world: BotWorld, rng: random.Random) -> list[BotEvent]:
    """Decide and perform one action for this bot."""
    zone = bot.current_zone_id
    if zone not in BOT_MAP:
        bot.current_zone_id = HOME
        return []
    safe = zone in SAFE_ZONES
    bot.turn_last_acted = world.turn

    # 1. Hurt: potion, rest, or retreat.
    if bot.hp <= bot.max_hp * 0.35:
        if not safe and _potion_count(bot):
            return _drink(bot, world)
        if safe:
            bot.hp = bot.max_hp
            bot.activity = f"resting in {place(zone)}"
            return [BotEvent(bot.bot_id, zone, f"{bot.name} patched themselves up.", "rest")]
        return _move(bot, HOME, "limping back to the outer market")

    # 1b. Promised the player a Gate run: hang around town until they go.
    if safe and world.turn < bot.waiting_until:
        bot.activity = "waiting around for a Gate run"
        return []

    # 2. In the dungeon: fight, forage, or head home.
    if not safe:
        if bot.item_count() >= BAG_FULL or (bot.archetype in ("trader", "crafter") and rng.random() < 0.3):
            return _move(bot, HOME, "hauling loot back to the market")
        # Healthy fighters and explorers push on to the deepest floor their
        # level allows (without this they never left Floor 1).
        if bot.archetype in ("fighter", "explorer") and bot.hp > bot.max_hp * 0.6:
            deepest = _deepest_floor(bot)
            if deepest != zone and _can_handle(bot, deepest) and \
                    rng.random() < (0.5 if bot.archetype == "fighter" else 0.25):
                events = _move(bot, deepest, f"pushing on to {place(deepest)}")
                if events:
                    return events
        fighty = {"fighter": 0.9, "explorer": 0.5, "gatherer": 0.3, "crafter": 0.1, "trader": 0.1}[bot.archetype]
        if rng.random() < fighty:
            if bot.archetype in ("fighter", "explorer"):
                events = _try_boss(bot, world, rng)
                if events:
                    return events
            options = [
                e for e in ZONE_ENCOUNTERS.get(zone, [])
                if expected_damage(bot, _preview(e)) < bot.hp * (0.7 if bot.archetype == "fighter" else 0.45)
            ]
            if options:
                return _fight(bot, rng.choice(options), rng, world)
            if bot.archetype == "fighter":
                return _move(bot, HOME, "regrouping after a tough floor")
        events = _gather(bot, world, rng)
        return events or _move(bot, HOME, "heading back to the market")

    # 3. In a safe zone: guild, auction, trade, craft, restock, then set off.
    if rng.random() < 0.15:
        events = _join_guild(bot, world, rng)
        if events:
            return events
    if bot.archetype in ("trader", "gatherer", "fighter") and rng.random() < 0.4:
        events = _auction(bot, world, rng)
        if events:
            return events
    for step in (_sell_all, _craft):
        events = step(bot, world)
        if events:
            return events
    events = _buy(bot, world, rng)
    if events:
        return events
    if zone in world.yields and bot.archetype == "gatherer" and rng.random() < 0.5:
        return _gather(bot, world, rng)
    from config import BOT_GATE_CHANCE
    if (bot.archetype in ("fighter", "explorer") and world.gates
            and bot.hp >= bot.max_hp * 0.8 and rng.random() < BOT_GATE_CHANCE):
        events = _gate_run(bot, world, rng)
        if events:
            return events
    if bot.archetype == "trader" and bot.item_count() < TRADER_STOCK and rng.random() < 0.5:
        events = _gather(bot, world, rng)      # traders build stock at the camp
        if events:
            return events
    target, doing = _destination(bot, world, rng)
    if target == zone:
        bot.activity = f"taking it easy in {place(zone)}"
        return []
    return _move(bot, target, doing)


_PREVIEW_CACHE: dict[str, list] = {}


def _preview(encounter_id: str) -> list:
    """Enemy stats for risk estimates (cached; never mutated)."""
    if encounter_id not in _PREVIEW_CACHE:
        from systems.combat_system import spawn_encounter
        _PREVIEW_CACHE[encounter_id] = spawn_encounter(encounter_id)
    return _PREVIEW_CACHE[encounter_id]
