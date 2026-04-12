from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from core.event_bus import Event, bus

if TYPE_CHECKING:
    from rich.console import Console


_RARITY_COLORS = {
    "COMMON": "white",
    "UNCOMMON": "green",
    "RARE": "bright_blue",
    "EPIC": "magenta",
    "LEGENDARY": "bright_yellow",
}


def _system_notify(console: "Console", lines: list[str], style: str = "system_msg", pause: float = 0.8) -> None:
    text = Text(justify="center")
    for line in lines:
        text.append(line + "\n", style=style)
    panel = Panel(Align.center(text), border_style=style, padding=(0, 4))
    console.print(panel)
    time.sleep(pause)


def notify_level_up(console: "Console", level: int, messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "LEVEL UP."
    _system_notify(
        console,
        [msg, f"YOU ARE NOW LEVEL {level}"],
        style="system_msg",
        pause=1.0,
    )


def notify_skill_acquired(console: "Console", skill_name: str, rarity: str, messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "NEW SKILL ACQUIRED."
    color = _RARITY_COLORS.get(rarity, "white")
    console.print(Panel(
        Align.center(Text.assemble(
            (msg + "\n", "system_msg"),
            (f"[ {skill_name} ]", f"bold {color}"),
            (" acquired.", "dim_text"),
        )),
        border_style=color,
        padding=(0, 4),
    ))
    time.sleep(0.8)


def notify_class_assigned(console: "Console", class_name: str, rarity: str, messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "CLASS DESIGNATION COMPLETE."
    color = _RARITY_COLORS.get(rarity, "white")
    _system_notify(
        console,
        [msg, f"[ {class_name.upper()} ]"],
        style=f"bold {color}",
        pause=1.5,
    )


def notify_anomaly(console: "Console", messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "ANOMALY DETECTED."
    _system_notify(console, [msg], style="system_anomaly", pause=2.0)


def notify_item_found(console: "Console", item_name: str, rarity: str, messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "ITEM ACQUIRED."
    color = _RARITY_COLORS.get(rarity, "white")
    console.print(Panel(
        Align.center(Text.assemble(
            (msg + "\n", "system_msg"),
            (f"[ {item_name} ]", f"bold {color}"),
        )),
        border_style="border",
        padding=(0, 2),
    ))
    time.sleep(0.5)


def notify_warning(console: "Console", messages: list[str]) -> None:
    msg = random.choice(messages) if messages else "WARNING."
    _system_notify(console, [msg], style="system_warning", pause=1.0)


def notify_alignment_shift(console: "Console", old: float, new: float, label: str) -> None:
    delta = new - old
    arrow = "(+)" if delta > 0 else "(-)"
    color = "green" if delta > 0 else "red"
    console.print(
        f"  [dim_text]Alignment {arrow}[/dim_text] [{color}]{label}[/{color}] [dim_text]({new:+.0f})[/dim_text]"
    )


def notify_quest_started(console: "Console", title: str) -> None:
    _system_notify(console, ["NEW OBJECTIVE ADDED", title.upper()], style="system_msg", pause=0.8)


def notify_quest_completed(console: "Console", title: str, reward_gold: int, reward_xp: int) -> None:
    lines = ["OBJECTIVE COMPLETE", title.upper()]
    if reward_gold or reward_xp:
        parts = []
        if reward_gold:
            parts.append(f"{reward_gold}g")
        if reward_xp:
            parts.append(f"{reward_xp} XP")
        lines.append("Rewards: " + "  ".join(parts))
    _system_notify(console, lines, style="success", pause=1.0)


def notify_quest_failed(console: "Console", title: str) -> None:
    _system_notify(console, ["OBJECTIVE FAILED", title.upper()], style="system_warning", pause=1.0)


def notify_guild_joined(console: "Console", guild_name: str, rank_name: str) -> None:
    _system_notify(
        console,
        ["GUILD MEMBERSHIP GRANTED", f"[ {guild_name.upper()} ]", f"Rank: {rank_name}"],
        style="system_msg", pause=1.0,
    )


def notify_guild_rank_changed(console: "Console", guild_name: str, old_rank: str, new_rank: str) -> None:
    _system_notify(
        console,
        ["RANK ADVANCEMENT", f"{guild_name}", f"{old_rank} -> {new_rank}"],
        style="system_msg", pause=1.0,
    )


def notify_faction_rank_changed(console: "Console", faction_name: str, new_rank_name: str) -> None:
    _system_notify(
        console,
        ["FACTION STANDING UPDATED", faction_name.upper(), f"New standing: {new_rank_name}"],
        style="system_msg", pause=0.8,
    )


def notify_player_died(console: "Console", lives_remaining: int, cause: str) -> None:
    lines = ["YOU HAVE FALLEN"]
    cause_clean = cause.replace("combat:", "Defeated in combat: ").replace("_", " ")
    lines.append(cause_clean)
    if lives_remaining > 0:
        lines.append(f"{lives_remaining} {'life' if lives_remaining == 1 else 'lives'} remaining.")
    else:
        lines.append("ALL LIVES SPENT.")
    _system_notify(console, lines, style="system_warning", pause=1.5)


def notify_life_token_purchased(console: "Console", cost: int, lives_remaining: int) -> None:
    _system_notify(
        console,
        ["LIFE TOKEN ACQUIRED", f"Cost: {cost:,} Shards", f"Lives: {lives_remaining}"],
        style="system_msg", pause=1.0,
    )


def notify_game_over(console: "Console", cause: str, total_deaths: int) -> None:
    _system_notify(
        console,
        ["GAME OVER", "ALL LIVES SPENT", f"Deaths: {total_deaths}", "Your story ends here."],
        style="system_warning", pause=2.5,
    )


def notify_species_evolved(console: "Console", new_name: str, stage: int, description: str, flavor_text: str) -> None:
    lines = Text(justify="center")
    lines.append("EVOLUTION UNLOCKED\n", style="system_msg")
    lines.append(f"[ {new_name.upper()} ]\n", style="bold bright_yellow")
    lines.append(description, style="scene_text")
    if flavor_text:
        lines.append(f'\n"{flavor_text}"', style="italic dim_text")
    console.print(Panel(
        Align.center(lines),
        border_style="bright_yellow",
        padding=(0, 4),
    ))
    time.sleep(2.0)


def setup_notification_listeners(console: "Console", system_messages: dict) -> None:
    """Wire event bus events to notification functions."""

    def on_level_up(event: Event) -> None:
        notify_level_up(console, event.data.get("level", 1), system_messages.get("level_up", []))

    def on_skill_acquired(event: Event) -> None:
        notify_skill_acquired(
            console,
            event.data.get("skill_name", "Unknown Skill"),
            event.data.get("rarity", "COMMON"),
            system_messages.get("skill_acquired", []),
        )

    def on_class_assigned(event: Event) -> None:
        notify_class_assigned(
            console,
            event.data.get("class_name", "Unknown Class"),
            event.data.get("rarity", "COMMON"),
            system_messages.get("class_assigned", []),
        )

    def on_anomaly(event: Event) -> None:
        notify_anomaly(console, system_messages.get("anomaly_detected", []))

    def on_item_found(event: Event) -> None:
        notify_item_found(
            console,
            event.data.get("item_name", "Unknown Item"),
            event.data.get("rarity", "COMMON"),
            system_messages.get("item_found", []),
        )

    def on_warning(event: Event) -> None:
        notify_warning(console, system_messages.get("warning", []))

    def on_alignment_shifted(event: Event) -> None:
        notify_alignment_shift(
            console,
            event.data.get("old", 0.0),
            event.data.get("new", 0.0),
            event.data.get("label", "Neutral"),
        )

    def on_species_evolved(event: Event) -> None:
        notify_species_evolved(
            console,
            event.data.get("new_name", "Unknown Form"),
            event.data.get("stage", 1),
            event.data.get("description", ""),
            event.data.get("flavor_text", ""),
        )

    bus.subscribe("LEVEL_UP", on_level_up)
    bus.subscribe("SKILL_ACQUIRED", on_skill_acquired)
    bus.subscribe("CLASS_ASSIGNED", on_class_assigned)
    bus.subscribe("ANOMALY_DETECTED", on_anomaly)
    bus.subscribe("ITEM_FOUND", on_item_found)
    bus.subscribe("WARNING", on_warning)
    def on_quest_started(event: Event) -> None:
        notify_quest_started(console, event.data.get("title", "Unknown Quest"))

    def on_quest_completed(event: Event) -> None:
        notify_quest_completed(
            console,
            event.data.get("title", "Unknown Quest"),
            event.data.get("reward_gold", 0),
            event.data.get("reward_xp", 0),
        )

    def on_quest_failed(event: Event) -> None:
        notify_quest_failed(console, event.data.get("title", "Unknown Quest"))

    def on_guild_joined(event: Event) -> None:
        notify_guild_joined(
            console,
            event.data.get("guild_name", "Unknown Guild"),
            event.data.get("rank_name", event.data.get("rank_id", "Recruit")),
        )

    def on_guild_rank_changed(event: Event) -> None:
        notify_guild_rank_changed(
            console,
            event.data.get("guild_name", "Unknown Guild"),
            event.data.get("old_rank", "?"),
            event.data.get("new_rank", "?"),
        )

    def on_faction_rank_changed(event: Event) -> None:
        notify_faction_rank_changed(
            console,
            event.data.get("faction_name", "Unknown Faction"),
            event.data.get("new_rank_name", event.data.get("new_rank", "?")),
        )

    def on_player_died(event: Event) -> None:
        notify_player_died(
            console,
            event.data.get("lives_remaining", 0),
            event.data.get("cause", "Unknown"),
        )

    def on_life_token_purchased(event: Event) -> None:
        notify_life_token_purchased(
            console,
            event.data.get("cost", 0),
            event.data.get("lives_remaining", 0),
        )

    def on_game_over(event: Event) -> None:
        notify_game_over(
            console,
            event.data.get("cause", "Unknown"),
            event.data.get("total_deaths", 0),
        )

    bus.subscribe("ALIGNMENT_SHIFTED", on_alignment_shifted)
    bus.subscribe("SPECIES_EVOLVED", on_species_evolved)
    bus.subscribe("QUEST_STARTED", on_quest_started)
    bus.subscribe("QUEST_COMPLETED", on_quest_completed)
    bus.subscribe("QUEST_FAILED", on_quest_failed)
    bus.subscribe("GUILD_JOINED", on_guild_joined)
    bus.subscribe("GUILD_RANK_CHANGED", on_guild_rank_changed)
    bus.subscribe("FACTION_RANK_CHANGED", on_faction_rank_changed)
    bus.subscribe("PLAYER_DIED", on_player_died)
    bus.subscribe("LIFE_TOKEN_PURCHASED", on_life_token_purchased)
    bus.subscribe("GAME_OVER", on_game_over)
