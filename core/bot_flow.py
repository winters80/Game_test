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
_LOGGED_KINDS = {"defeat", "level", "party", "guild"}
# Shown in the player's zone: milestones whenever they happen, routine
# actions at most every other turn; rest / gather / potion never.
_MILESTONE_KINDS = {"defeat", "level", "boss_kill"}
_ROUTINE_KINDS = {"fight", "craft", "trade", "party", "auction", "guild"}


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
    from core.gate_flow import bot_gate_view
    from core.lair_flow import bot_lair_view
    lairs = bot_lair_view(engine)
    allies: dict[str, list] = {}
    if engine._bot_manager is not None:
        for b in engine._bot_manager.all():
            allies.setdefault(b.current_zone_id, []).append(b)
    return BotWorld(engine.item_registry, traders_at, engine.recipes, yields, turn,
                    lairs=lairs, gates=bot_gate_view(engine, lairs), allies=allies,
                    guilds=bot_guilds(engine))


def bot_guilds(engine: "GameEngine") -> list[dict]:
    """Guilds bots may join: the authored ones, plus any the player founded."""
    out = []
    reg = getattr(engine, "guild_registry", None)
    if feature("guild_system") and reg is not None:
        for g in reg.all():
            out.append({"guild_id": g.guild_id, "name": g.name,
                        "ranks": [r.rank_id for r in getattr(g, "ranks", [])], "player_founded": False})
    db = engine.state.world_db if feature("world_db") and engine.state else None
    if db is not None:
        try:
            for row in db.get_all_guild_states():
                if row.get("is_player_founded"):
                    out.append({"guild_id": row["guild_id"], "name": row["name"],
                                "ranks": ["recruit", "member", "officer"], "player_founded": True})
        except Exception:
            logger.info("Could not read founded guilds for bots", exc_info=True)
    return out


def tick_bots(engine: "GameEngine", quiet: bool = False) -> list["BotEvent"]:
    """Let due bots act; surface what happens near the player (unless
    ``quiet``, e.g. while time passes during a rest). Returns all events."""
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
    _apply_social(engine, events)
    # Boss kills move the lair's succession on (and log it); see core/lair_flow.
    if any(ev.kind == "boss_kill" for ev in events):
        from core.lair_flow import bot_killed_boss
        for ev in events:
            if ev.kind == "boss_kill":
                bot = engine._bot_manager.get(ev.bot_id)
                if bot is not None:
                    bot_killed_boss(engine, ev.lair_id, bot, ev.killed_by)
    if not quiet:
        _surface(engine, events)
    _persist(engine, acted, events)
    return events


AUCTION_HOUSE_FEE = 0.1        # the house keeps 10% of a bot's sale
AUCTION_LISTING_TURNS = 144    # a day


def _apply_social(engine: "GameEngine", events: list) -> None:
    """Write bot guild joins and auction listings into the world DB."""
    db = engine.state.world_db if feature("world_db") else None
    if db is None:
        return
    turn = engine.state.player.turn_count
    for ev in events:
        try:
            if ev.kind == "auction" and feature("auction_house"):
                item = engine.item_registry.get(ev.item_id)
                rarity = getattr(getattr(item, "rarity", None), "value", "COMMON")
                db.add_auction_listing(
                    listing_id=f"bot_{ev.bot_id}_{turn}", item_id=ev.item_id, item_type="standard",
                    auction_guild="golden_scales" if rarity in ("RARE", "EPIC", "LEGENDARY") else "iron_hammer_exchange",
                    # The auction house expires listings by state.turn_number (its own clock).
                    quantity=1, starting_bid=ev.price, expires_turn=engine.state.turn_number + AUCTION_LISTING_TURNS,
                    seller_npc_id=f"bot:{ev.bot_id}")
            elif ev.kind == "guild" and ev.guild_id.startswith("guild_"):     # the player's own guild
                bot = engine._bot_manager.get(ev.bot_id)
                db.add_guild_member(ev.guild_id, f"bot_{ev.bot_id}", "npc", bot.guild_rank if bot else "recruit", turn)
        except Exception:
            logger.info("Could not record bot %s %s", ev.kind, ev.bot_id, exc_info=True)


def settle_bot_auctions(engine: "GameEngine", settled: list) -> list[str]:
    """Pay bots for their auction sales (or give unsold items back). ``settled``
    is ``auction_system.tick_auction``'s [(listing, top_bid or None)]."""
    lines = []
    mgr = engine._bot_manager
    if not feature("bot_system") or mgr is None:
        return lines
    for listing, top in settled:
        seller = listing.get("seller_npc_id") or ""
        if not seller.startswith("bot:"):
            continue
        bot = mgr.get(seller[4:])
        if bot is None:
            continue
        item = engine.item_registry.get(listing.get("item_id") or "")
        name = item.name if item else listing.get("item_id")
        if top:
            earned = int(top["bid_amount"] * (1 - AUCTION_HOUSE_FEE))
            bot.gold += earned
            buyer = "you" if top["bidder"] == "player" else "a guild buyer"
            bot.remember(f"Sold {name} at auction to {buyer} (turn {engine.state.player.turn_count})")
            lines.append(f"{bot.name} sold {name} at auction.")
        elif listing.get("item_id"):
            bot.add_item(listing["item_id"])
        _save_bot(engine, bot)
    return lines


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
    from systems.bot_brain import guild_rank_for
    ranks = {g["guild_id"]: g["ranks"] for g in bot_guilds(engine)}
    for bot in acted:
        if bot.guild_id in ranks:
            bot.guild_rank = guild_rank_for(bot, ranks[bot.guild_id])
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
    """Chat with a bot like you would with another player: catch up, swap
    tips, ask about their build or guild, team up for a Gate, or trade.
    Lines come from the fast model when it's online (grounded in a summary of
    what the bot really did), else from systems/bot_chat."""
    from ui import prompt as uip
    from systems import bot_chat
    from systems.bot_system import bot_profile

    bot = engine._bot_manager.get(bot_id) if engine._bot_manager else None
    if bot is None or bot.current_zone_id != engine.state.current_scene_id:
        renderer.print_system_message("They've moved on.", style="dim_text")
        return

    ctx = _chat_context(engine, bot)
    said: list[str] = []

    def say(topic: str) -> str:
        line = None
        if engine._ai_online():
            with renderer.show_ai_thinking_spinner(f"{bot.name} thinks about it..."):
                line = engine.ai_service.generate_bot_reply(
                    bot_profile(bot), engine.state.player.name, topic, _facts(ctx, topic), said)
            if line and bot_chat.looks_robotic(line, bot.memory):
                line = None
        line = line or bot_chat.reply(bot, topic, ctx)
        said.append(line)
        return line

    subtitle = (f"Level {bot.level} {ctx.bot_class} · {bot.archetype} · "
                f"{bot.activity} · {bot.hp}/{bot.max_hp} HP")
    renderer.clear()
    renderer.print_title()
    renderer.print_npc_dialogue(bot.name, subtitle, say("greeting"))

    while True:
        options = [("catch_up", "What have you been up to?"),
                   ("tips", "Got any tips?"),
                   ("build", "What's your build?")]
        if ctx.guild_name or bot.level >= 3:
            options.append(("guild", "In a guild?"))
        if bot.waiting_until <= engine.state.player.turn_count:
            options.append(("team_up", "Want to team up for a Gate?"))
        plain = [label for _, label in options] + ["« Buy from them »", "« Sell to them »", "Farewell."]
        choice = uip.select("Say:", choices=plain).ask()
        if choice in (None, "Farewell."):
            renderer.print_npc_response(bot.name, _farewell(bot, ctx))
            return
        if choice.startswith("« Buy"):
            _buy_from_bot_menu(engine, bot)
            continue
        if choice.startswith("« Sell"):
            _sell_to_bot_menu(engine, bot)
            continue
        topic = next(key for key, label in options if label == choice)
        ctx.seed += 1                        # a different line if you ask again
        renderer.print_npc_response(bot.name, say(topic))
        if topic == "team_up" and ctx.can_party:
            bot.waiting_until = engine.state.player.turn_count + TEAM_UP_WAIT_TURNS
            bot.activity = f"waiting to run a Gate with {engine.state.player.name}"
            bot.remember(f"Agreed to run a Gate with {engine.state.player.name} (turn {engine.state.player.turn_count})")
            _save_bot(engine, bot)


TEAM_UP_WAIT_TURNS = 30          # five in-game hours of waiting around for you


def _farewell(bot, ctx) -> str:
    import random
    rng = random.Random(f"{bot.bot_id}:bye:{ctx.seed}")
    return rng.choice(["See you around.", "Don't die out there.", "Good hunting.",
                       "Later.", "Watch your back down there."])


def _chat_context(engine: "GameEngine", bot):
    """Facts for the conversation: the player, Gates that suit them, lairs."""
    from systems.bot_chat import ChatContext, party_refusal
    p = engine.state.player
    player_class = ""
    if p.active_class or p.base_class:
        player_class = _class_name(engine, p.active_class or p.base_class)
    guild_name = ""
    if bot.guild_id:
        joined = [m for m in bot.memory if m.startswith("Joined ")]
        guild_name = (joined[-1][7:].split(" (turn")[0] if joined
                      else bot.guild_id.replace("_", " ").title())
    picks: list[str] = []
    reg = getattr(engine, "gate_registry", None)
    if reg is not None:
        fits = sorted((g for g in reg.all() if p.level - 2 <= g.min_level <= p.level + 1),
                      key=lambda g: abs(g.min_level - p.level))
        picks = [f"{g.name} (Lv {g.min_level})" for g in fits[:2]]
    lair_note = ""
    try:
        from core.lair_flow import get_state
        lreg = getattr(engine, "lair_registry", None)
        for lair in (lreg.all() if lreg else []):
            if lair.zone_id.startswith("gate:"):
                continue
            st = get_state(engine, lair.lair_id)
            occ = lair.occupant(st.occupant_id) if st else None
            if occ is not None and st.status == "occupied" and st.occupant_id != lair.first_occupant:
                title = occ.title.replace("{name}", st.lineage_name or "").strip()
                lair_note = f"{title} holds {lair.name} now, not what was there before"
                break
    except Exception:
        lair_note = ""
    refusal = party_refusal(bot, None, p.level)
    return ChatContext(player_name=p.name, player_level=p.level, player_class=player_class,
                       bot_class=_class_name(engine, bot.class_id), guild_name=guild_name,
                       gate_picks=picks, lair_note=lair_note, can_party=not refusal,
                       party_reason=refusal, seed=engine.state.turn_number)


def _facts(ctx, topic: str) -> str:
    bits = [f"you are a {ctx.bot_class}"]
    if ctx.player_class:
        bits.append(f"the other player is a level {ctx.player_level} {ctx.player_class}")
    if topic == "tips" and ctx.gate_picks:
        bits.append("Gates that suit their level: " + ", ".join(ctx.gate_picks))
    if topic == "tips" and ctx.lair_note:
        bits.append(ctx.lair_note)
    if topic == "guild":
        bits.append(f"your guild: {ctx.guild_name}" if ctx.guild_name else "you are not in a guild")
    if topic == "team_up":
        bits.append("you agree: tell them to pick you when they enter a Gate from here"
                    if ctx.can_party else f"you decline because: {ctx.party_reason}")
    return "; ".join(bits)


def _buy_from_bot_menu(engine: "GameEngine", bot) -> None:
    from ui import prompt as uip
    from config import format_currency as _fc
    from systems import bot_trade

    while True:
        offers = bot_trade.bot_sells(bot, engine.item_registry)
        if not offers:
            renderer.print_npc_response(bot.name, "Nothing spare, sorry. Check back after my next run.")
            return
        renderer.console.print(f"\n  [gold]Your gold: {_fc(engine.state.player.gold)}[/gold]")
        labels = [f"{o.item.name} ×{o.quantity} — {_fc(o.price)} each" for o in offers] + ["← Done"]
        answer = uip.select(f"{bot.name}'s spare goods:", choices=labels).ask()
        if answer in (None, "← Done"):
            return
        offer = offers[labels.index(answer)]
        ok, msg = bot_trade.buy_from_bot(engine.state.player, bot, offer.item.item_id, engine.item_registry)
        (renderer.print_success if ok else renderer.print_error)(msg)
        _save_bot(engine, bot)


def _sell_to_bot_menu(engine: "GameEngine", bot) -> None:
    from ui import prompt as uip
    from config import format_currency as _fc
    from systems import bot_trade

    while True:
        goods = bot_trade.player_sellables(engine.state.player, bot, engine.item_registry)
        if not goods:
            renderer.print_npc_response(
                bot.name, "Nothing I need, or nothing I can afford." if bot.gold else "I'm skint.")
            return
        labels = [f"{g.item.name} ×{g.quantity} — {_fc(g.price)} each" for g in goods] + ["← Done"]
        answer = uip.select(f"Sell to {bot.name} (they have {_fc(bot.gold)}):", choices=labels).ask()
        if answer in (None, "← Done"):
            return
        chosen = goods[labels.index(answer)]
        qty = 1
        if chosen.quantity > 1:
            raw = uip.text(f"How many? (1-{chosen.quantity})", default=str(chosen.quantity)).ask()
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
        guild = ""
        if b.guild_id:
            g = next((x for x in bot_guilds(engine) if x["guild_id"] == b.guild_id), None)
            guild = f" [{g['name'] if g else b.guild_id}]"
        lines.append(f"{b.name:<10} Lv {b.level:<2} {_class_name(engine, b.class_id):<12} "
                     f"{where:<18} {b.activity}{guild}")
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
