"""
Engine side of the bot adventurers (behaviour: systems/bot_brain.py).

- ``setup_bots``: on new game / load, give the save its bots: the stored
  ones, or the authored ones plus generated ones up to BOT_COUNT.
- ``tick_bots``: once per player turn, each bot acts every
  BOT_ACT_EVERY_TURNS turns (staggered). What happens in the player's zone
  is shown as a short line; milestones go to the World Log.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from config import (
    BOT_ACT_EVERY_TURNS, BOT_ARRIVALS_EVERY_TURNS, BOT_COUNT, BOT_MAX_EVENT_LINES, feature,
)
from ui import renderer

if TYPE_CHECKING:
    from core.game_engine import GameEngine
    from systems.bot_brain import BotEvent

logger = logging.getLogger(__name__)

# Milestones worth a World Log ([L]) line. Everyday fights, trades and moves
# would crowd out rumours and lore (the log shows the last 30 entries).
_LOGGED_KINDS = {"defeat", "level"}
# Shown in the player's zone: milestones whenever they happen, routine
# actions at most every other turn; rest / gather / potion never.
_MILESTONE_KINDS = {"defeat", "level"}
_ROUTINE_KINDS = {"fight", "craft", "trade"}


def setup_bots(engine: "GameEngine") -> None:
    if not feature("bot_system") or engine._bot_manager is None or engine._bot_registry is None:
        return
    world_db = engine.state.world_db if feature("world_db") else None
    engine._bot_manager.populate(
        engine._bot_registry, world_db, BOT_COUNT, seed=engine.state.player.player_id,
    )


def bot_world(engine: "GameEngine"):
    """What bots can see this turn: traders by zone, recipes, world-growth yields."""
    from systems.bot_brain import BotWorld
    from systems.npc_system import get_npc_zone

    turn = engine.state.player.turn_count
    npcs = engine.npc_registry.all() if engine.npc_registry else []
    traders = [n for n in npcs if n.trades is not None]

    def traders_at(zone: str):
        return [n for n in traders if get_npc_zone(n, turn) == zone]

    yields: dict[str, list[str]] = {}
    db = engine.state.world_db if feature("world_db") else None
    if db is not None:
        for row in db.load_world_expansions():
            b = row["bundle"]
            if b.get("yield_item_id") and engine.item_registry.get(b["yield_item_id"]):
                yields.setdefault(b.get("zone_id") or "", []).append(b["yield_item_id"])
    return BotWorld(engine.item_registry, traders_at, engine.recipes, yields, turn)


def tick_bots(engine: "GameEngine") -> list["BotEvent"]:
    """Let due bots act; surface what happens near the player. Returns all events."""
    if not feature("bot_system") or not engine._bot_manager or engine.state is None:
        return []
    from systems.bot_brain import tick_bot

    bots = engine._bot_manager.all()
    if not bots:
        return []
    turn = engine.state.player.turn_count
    world = None
    events: list = []
    acted = []
    for i, bot in enumerate(bots):
        if (turn + i) % max(1, BOT_ACT_EVERY_TURNS):
            continue
        if world is None:
            world = bot_world(engine)
        rng = random.Random(f"{bot.bot_id}:{turn}")
        try:
            events.extend(tick_bot(bot, world, rng))
            acted.append(bot)
        except Exception:
            logger.warning("Bot %s failed to act", bot.bot_id, exc_info=True)
    _surface(engine, events)
    _persist(engine, acted, events)
    return events


def _surface(engine: "GameEngine", events: list) -> list[str]:
    """Show what bots did in the player's zone, at most BOT_MAX_EVENT_LINES
    lines per turn. Milestones (level-ups, defeats) show at once; routine
    actions (fights, crafts, trades) at most every other turn. Arrivals are batched and announced every BOT_ARRIVALS_EVERY_TURNS
    turns (only bots still here); departures aren't announced, since [W]
    shows where everyone went. Returns the lines shown."""
    here = engine.state.current_scene_id
    pending: list[str] = getattr(engine, "_bot_arrivals", [])
    for ev in events:
        if ev.kind == "move" and ev.zone_id == here and ev.bot_id not in pending:
            pending.append(ev.bot_id)
    engine._bot_arrivals = pending

    turn = engine.state.player.turn_count
    local = [ev for ev in events if ev.zone_id == here]
    lines = [ev.text for ev in local if ev.kind in _MILESTONE_KINDS]
    if not lines and turn % 2 == 0:
        lines = [ev.text for ev in local if ev.kind in _ROUTINE_KINDS]
    if not lines and pending and turn % BOT_ARRIVALS_EVERY_TURNS == 0:
        names = [b.name for b in (engine._bot_manager.get(i) for i in pending)
                 if b is not None and b.current_zone_id == here]
        engine._bot_arrivals = []
        if names:
            who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
            lines = [f"✦  {who} {'is' if len(names) == 1 else 'are'} about."]
    lines = lines[:BOT_MAX_EVENT_LINES]
    for line in lines:
        renderer.console.print(f"  [dim_text]{line}[/dim_text]")
    return lines


def _persist(engine: "GameEngine", acted: list, events: list) -> None:
    db = engine.state.world_db if feature("world_db") else None
    if db is None:
        return
    for bot in acted:
        db.upsert_bot_instance(
            bot_id=bot.bot_id, definition=bot.model_dump(mode="json"),
            current_zone_id=bot.current_zone_id, last_active_turn=bot.turn_last_acted,
        )
    turn = engine.state.player.turn_count
    for ev in events:
        if ev.kind in _LOGGED_KINDS:
            try:
                db.store_world_event("bot_activity", ev.text, zone_id=ev.zone_id,
                                     title="Adventurers", generated_turn=turn)
            except Exception:
                logger.info("Could not log bot event", exc_info=True)


# ── Meeting bots ──────────────────────────────────────────────────────────────

def _class_name(engine: "GameEngine", class_id: str) -> str:
    cls = engine.class_registry.get(class_id) if engine.class_registry else None
    return cls.name if cls else class_id.replace("_", " ").title()


def bot_label(engine: "GameEngine", bot) -> str:
    return f"{bot.name} (Lv {bot.level} {_class_name(engine, bot.class_id)})"


def talk_to_bot(engine: "GameEngine", bot_id: str) -> None:
    """Conversation with a bot: an AI (or fallback) line, news, and trading."""
    import questionary
    from systems.bot_system import bot_profile, fallback_line

    bot = engine._bot_manager.get(bot_id) if engine._bot_manager else None
    if bot is None or bot.current_zone_id != engine.state.current_scene_id:
        renderer.print_system_message("They've moved on.", style="dim_text")
        return

    line = None
    if engine._ai_online():
        with renderer.show_ai_thinking_spinner(f"{bot.name} considers you..."):
            line = engine.ai_service.generate_bot_line(bot_profile(bot), engine.state.player.name)
    line = line or fallback_line(bot)

    subtitle = (f"Level {bot.level} {_class_name(engine, bot.class_id)} · {bot.archetype} · "
                f"{bot.activity} · {bot.hp}/{bot.max_hp} HP")
    renderer.clear()
    renderer.print_title()
    renderer.print_npc_dialogue(bot.name, subtitle, line)

    while True:
        choice = questionary.select("Say:", choices=[
            "What have you been up to?", "« Buy from them »", "« Sell to them »", "Farewell.",
        ]).ask()
        if choice in (None, "Farewell."):
            return
        if choice.startswith("What have"):
            news = [m.split(" (turn")[0] for m in bot.memory[-4:]] or ["Not much worth telling."]
            renderer.print_npc_response(bot.name, " ".join(n.rstrip(".") + "." for n in news))
        elif choice.startswith("« Buy"):
            _buy_from_bot_menu(engine, bot)
        else:
            _sell_to_bot_menu(engine, bot)


def _buy_from_bot_menu(engine: "GameEngine", bot) -> None:
    import questionary
    from config import format_currency as _fc
    from systems import bot_trade

    while True:
        offers = bot_trade.bot_sells(bot, engine.item_registry)
        if not offers:
            renderer.print_npc_response(bot.name, "Nothing spare, sorry. Check back after my next run.")
            return
        renderer.console.print(f"\n  [gold]Your gold: {_fc(engine.state.player.gold)}[/gold]")
        labels = [f"{o.item.name} ×{o.quantity} — {_fc(o.price)} each" for o in offers] + ["← Done"]
        answer = questionary.select(f"{bot.name}'s spare goods:", choices=labels).ask()
        if answer in (None, "← Done"):
            return
        offer = offers[labels.index(answer)]
        ok, msg = bot_trade.buy_from_bot(engine.state.player, bot, offer.item.item_id, engine.item_registry)
        (renderer.print_success if ok else renderer.print_error)(msg)
        _save_bot(engine, bot)


def _sell_to_bot_menu(engine: "GameEngine", bot) -> None:
    import questionary
    from config import format_currency as _fc
    from systems import bot_trade

    while True:
        goods = bot_trade.player_sellables(engine.state.player, bot, engine.item_registry)
        if not goods:
            renderer.print_npc_response(
                bot.name, "Nothing I need, or nothing I can afford." if bot.gold else "I'm skint.")
            return
        labels = [f"{g.item.name} ×{g.quantity} — {_fc(g.price)} each" for g in goods] + ["← Done"]
        answer = questionary.select(f"Sell to {bot.name} (they have {_fc(bot.gold)}):", choices=labels).ask()
        if answer in (None, "← Done"):
            return
        chosen = goods[labels.index(answer)]
        qty = 1
        if chosen.quantity > 1:
            raw = questionary.text(f"How many? (1-{chosen.quantity})", default=str(chosen.quantity)).ask()
            try:
                qty = int(raw or 1)
            except ValueError:
                qty = 1
        ok, msg, _ = bot_trade.sell_to_bot(engine.state.player, bot, chosen.item.item_id,
                                           engine.item_registry, qty)
        (renderer.print_success if ok else renderer.print_error)(msg)
        _save_bot(engine, bot)


def _save_bot(engine: "GameEngine", bot) -> None:
    engine.state.mark_dirty()
    db = engine.state.world_db if feature("world_db") else None
    if db is not None:
        db.upsert_bot_instance(bot_id=bot.bot_id, definition=bot.model_dump(mode="json"),
                               current_zone_id=bot.current_zone_id,
                               last_active_turn=bot.turn_last_acted)


def whos_around_lines(engine: "GameEngine") -> list[str]:
    """[W] list: every bot, the ones in your zone first."""
    from systems.bot_brain import place

    if not engine._bot_manager or not engine._bot_manager.all():
        return ["No other adventurers are about."]
    here = engine.state.current_scene_id
    bots = sorted(engine._bot_manager.all(),
                  key=lambda b: (b.current_zone_id != here, place(b.current_zone_id), -b.level))
    lines = []
    for b in bots:
        where = "HERE" if b.current_zone_id == here else place(b.current_zone_id)
        lines.append(f"{b.name:<10} Lv {b.level:<2} {_class_name(engine, b.class_id):<12} "
                     f"{where:<18} {b.activity}")
    return lines


def show_whos_around(engine: "GameEngine") -> None:
    renderer.console.print(
        f"\n  [system_msg][ WHO'S AROUND — {engine._bot_manager.active_count() if engine._bot_manager else 0}"
        f" adventurers ][/system_msg]\n")
    for line in whos_around_lines(engine):
        style = "scene_text" if " HERE " in line else "dim_text"
        renderer.console.print(f"  [{style}]{line}[/{style}]")
    renderer.console.print()
    renderer.prompt_any_key()
