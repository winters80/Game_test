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
    console.print()
    for i, opt in enumerate(options, 1):
        if opt.locked:
            console.print(f"  [option_locked]  {i}. {opt.label}[/option_locked]")
            if opt.lock_reason:
                console.print(f"     [dim_text]({opt.lock_reason})[/dim_text]")
        else:
            console.print(f"  [option_number]{i}.[/option_number] [option_label]{opt.label}[/option_label]")
    console.print()


def print_status_bar(player: "Player") -> None:
    from ui.panels import build_status_panel, build_stats_panel
    columns = Columns([build_status_panel(player), build_stats_panel(player)], equal=False)
    console.print(columns)


def print_full_status(player: "Player", item_registry: Any, skill_registry: Any) -> None:
    from ui.panels import build_status_panel, build_stats_panel, build_inventory_panel, build_skills_panel
    console.print(Columns([build_status_panel(player), build_stats_panel(player)]))
    console.print(build_inventory_panel(player, item_registry))
    console.print(build_skills_panel(player, skill_registry))


def print_class_reveal(class_def: "ClassDefinition") -> None:
    from ui.panels import build_class_panel
    console.print()
    console.print(build_class_panel(class_def))
    console.print()


def show_ai_thinking_spinner(message: str = "THE SYSTEM IS CONTEMPLATING...") -> "Live":
    """Return a Rich Live context displaying a spinner. Caller must use as context manager."""
    spinner = Spinner("dots", text=f"[system_anomaly]{message}[/system_anomaly]")
    return Live(Align.center(spinner), console=console, refresh_per_second=10)


def print_combat_header(enemy_name: str, enemy_hp: int, enemy_max_hp: int) -> None:
    bar_width = 20
    ratio = enemy_hp / enemy_max_hp if enemy_max_hp > 0 else 0
    filled = int(ratio * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    console.print(Rule(f"[damage]⚔  {enemy_name}[/damage]  [{bar_color(ratio)}]{bar}[/{bar_color(ratio)}]  [damage]{enemy_hp}/{enemy_max_hp}[/damage]", style="border"))


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
        console.print(f"  [gold]+ {amount} gold[/gold]")
    else:
        console.print(f"  [damage]- {amount} gold[/damage]")


def prompt_any_key() -> None:
    console.print("\n  [dim_text]Press Enter to continue...[/dim_text]")
    input()
