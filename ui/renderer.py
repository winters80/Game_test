from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text
from rich.live import Live

from ui.themes import GAME_THEME
from config import format_currency

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassDefinition
    from scenes.scene_base import SceneOption

# Single console instance — all rendering goes through here
console = Console(theme=GAME_THEME, width=100)


def clear() -> None:
    console.clear()


def print_title() -> None:
    from config import GAME_TITLE, GAME_SUBTITLE
    console.print()
    console.print(Align.center(Text(GAME_TITLE, style="title")))
    console.print(Align.center(Text(GAME_SUBTITLE, style="subtitle")))
    console.print(Rule(style="border"))
    console.print()


def print_system_message(message: str, style: str = "system_msg") -> None:
    console.print(f"\n[{style}]  {message}  [/{style}]\n")


def print_scene_header(title: str) -> None:
    console.print(Rule(f"[scene_title]{title}[/scene_title]", style="border"))
    console.print()


def print_scene_text(lines: list[str], pause_between: float = 0.0) -> None:
    for line in lines:
        console.print(f"  [scene_text]{line}[/scene_text]")
        if pause_between > 0:
            time.sleep(pause_between)
    console.print()


def print_options(options: list["SceneOption"]) -> None:
    from rich.markup import escape
    console.print()
    for i, opt in enumerate(options, 1):
        safe_label = escape(opt.label)  # prevent [AI], [tag], etc. from corrupting Rich markup
        if opt.locked:
            console.print(f"  [option_locked]  {i}. {safe_label}[/option_locked]")
            if opt.lock_reason:
                console.print(f"     [dim_text]({escape(opt.lock_reason)})[/dim_text]")
            if opt.hint_text:
                console.print(f"     [italic dim_text]~ {escape(opt.hint_text)}[/italic dim_text]")
        else:
            # AI-generated options get a distinct colour
            if opt.option_id.startswith("ai_"):
                console.print(f"  [option_number]{i}.[/option_number] [cyan]{safe_label}[/cyan]")
            else:
                console.print(f"  [option_number]{i}.[/option_number] [option_label]{safe_label}[/option_label]")
    console.print()


def print_status_bar(player: "Player", item_registry: Any = None) -> None:
    from ui.panels import build_status_panel, build_stats_panel, build_compact_inventory_panel, build_equipment_panel
    panels: list = [build_status_panel(player), build_stats_panel(player)]
    if item_registry is not None:
        panels.append(build_compact_inventory_panel(player, item_registry))
        panels.append(build_equipment_panel(player, item_registry))
    console.print(Columns(panels, equal=False))


def print_full_status(player: "Player", item_registry: Any, skill_registry: Any) -> None:
    from ui.panels import (
        build_status_panel, build_stats_panel,
        build_inventory_panel, build_skills_panel,
        build_guild_panel, build_faction_panel,
    )
    console.print(Columns([build_status_panel(player), build_stats_panel(player)]))
    console.print(build_inventory_panel(player, item_registry))
    console.print(build_skills_panel(player, skill_registry))
    if player.guild_memberships:
        console.print(build_guild_panel(player))
    if player.faction_standing_cache:
        console.print(build_faction_panel(player))


def print_class_reveal(class_def: "ClassDefinition") -> None:
    from ui.panels import build_class_panel
    console.print()
    console.print(build_class_panel(class_def))
    console.print()


def show_ai_thinking_spinner(message: str = "THE SYSTEM IS CONTEMPLATING...") -> "Live":
    """Return a Rich Live context displaying a spinner. Caller must use as context manager."""
    spinner = Spinner("dots", text=f"[system_anomaly]{message}[/system_anomaly]")
    return Live(Align.center(spinner), console=console, refresh_per_second=10)


def print_combat_header(enemy_name: str, enemy_hp: int, enemy_max_hp: int, player: Any = None) -> None:
    bar_width = 20
    ratio = enemy_hp / enemy_max_hp if enemy_max_hp > 0 else 0
    filled = int(ratio * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    console.print(Rule(f"[damage]⚔  {enemy_name}[/damage]  [{bar_color(ratio)}]{bar}[/{bar_color(ratio)}]  [damage]{enemy_hp}/{enemy_max_hp}[/damage]", style="border"))
    if player is not None:
        try:
            from systems.synergy_system import get_active_synergies
            active = get_active_synergies(player)
            if active:
                names = "  ".join(f"[cyan]{s['name']}[/cyan]" for s in active)
                console.print(f"  [dim_text]Synergies:[/dim_text] {names}")
        except Exception:
            pass


def bar_color(ratio: float) -> str:
    if ratio > 0.6:
        return "hp_high"
    elif ratio > 0.3:
        return "hp_mid"
    return "hp_low"


def print_combat_action(actor: str, action: str, value: int | None = None, style: str = "scene_text") -> None:
    if value is not None:
        console.print(f"  [{style}]{actor}[/{style}] {action} [{style}]{value}[/{style}]")
    else:
        console.print(f"  [{style}]{actor}[/{style}] {action}")


def print_divider() -> None:
    console.print(Rule(style="border"))


def print_error(message: str) -> None:
    console.print(f"[error]  Error: {message}[/error]")


def print_success(message: str) -> None:
    console.print(f"[success]  {message}[/success]")


def print_gold_change(amount: int, gained: bool = True) -> None:
    if gained:
        console.print(f"  [gold]+ {format_currency(amount)}[/gold]")
    else:
        console.print(f"  [damage]- {format_currency(amount)}[/damage]")


def print_npc_dialogue(npc_name: str, npc_description: str, text: str) -> None:
    """Render an NPC speech panel — name as title, description as subtitle, text as body."""
    from rich.text import Text as RichText
    body = RichText()
    body.append(f"{npc_description}\n\n", style="dim_text")
    body.append(text, style="scene_text")
    console.print(Panel(
        body,
        title=f"[scene_title]{npc_name}[/scene_title]",
        border_style="border",
        padding=(0, 2),
    ))
    console.print()


def print_npc_response(npc_name: str, text: str) -> None:
    """Render the NPC's reply to a player choice — inline, italicised."""
    console.print(f"\n  [italic scene_text]{npc_name}: {text}[/italic scene_text]\n")
    time.sleep(0.3)


def print_quest_log(quests: list[dict]) -> None:
    """Render a compact quest log panel."""
    from rich.table import Table
    table = Table.grid(padding=(0, 1))
    table.add_column(style="system_msg", width=30)
    table.add_column(style="dim_text")
    if not quests:
        table.add_row("No active quests.", "")
    else:
        for q in quests:
            table.add_row(q["title"], q["objective"])
    console.print(Panel(table, title="[system_msg][ QUESTS ][/system_msg]", border_style="border"))


def prompt_any_key() -> None:
    console.print("\n  [dim_text]Press Enter to continue...[/dim_text]")
    input()
