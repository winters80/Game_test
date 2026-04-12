from __future__ import annotations

from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassDefinition


RARITY_COLORS = {
    "COMMON": "white",
    "UNCOMMON": "green",
    "RARE": "bright_blue",
    "EPIC": "magenta",
    "LEGENDARY": "bright_yellow",
}


def _hp_color(current: int, max_hp: int) -> str:
    ratio = current / max_hp if max_hp > 0 else 0
    if ratio > 0.6:
        return "hp_high"
    elif ratio > 0.3:
        return "hp_mid"
    return "hp_low"


def build_status_panel(player: "Player") -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="stat_name", width=8)
    table.add_column(style="stat_value")

    hp_color = _hp_color(player.current_hp, player.max_hp)
    hp_text = Text(f"{player.current_hp}/{player.max_hp}", style=hp_color)
    mp_text = Text(f"{player.current_mp}/{player.max_mp}", style="mp")

    class_display = player.active_class or player.base_class or "Unclassified"
    table.add_row("Name", player.name)
    table.add_row("Class", class_display)
    table.add_row("Level", f"[level]{player.level}[/level]  XP: [xp]{player.experience}/{player.experience_to_next}[/xp]")
    table.add_row("HP", hp_text)
    table.add_row("MP", mp_text)
    table.add_row("Gold", f"[gold]{player.gold}[/gold]")

    return Panel(table, title="[system_msg][ STATUS ][/system_msg]", border_style="border", width=40)


def build_stats_panel(player: "Player") -> Panel:
    stats = player.stats
    table = Table.grid(padding=(0, 2))
    table.add_column(style="stat_name", width=4)
    table.add_column(style="stat_value", width=4)
    table.add_column(style="stat_name", width=4)
    table.add_column(style="stat_value")

    table.add_row("STR", str(stats.STR), "WIS", str(stats.WIS))
    table.add_row("INT", str(stats.INT), "END", str(stats.END))
    table.add_row("AGI", str(stats.AGI), "", "")
    table.add_row("LCK", str(stats.LCK), "", "")
    table.add_row("VIT", str(stats.VIT), "", "")

    return Panel(table, title="[system_msg][ STATS ][/system_msg]", border_style="border", width=30)


def build_inventory_panel(player: "Player", item_registry: object) -> Panel:
    table = Table(show_header=True, header_style="bold bright_white", border_style="border")
    table.add_column("Item", style="white")
    table.add_column("Qty", justify="right", style="dim_text", width=4)
    table.add_column("Type", style="dim_text", width=10)

    if not player.inventory:
        table.add_row("[dim_text]Empty[/dim_text]", "", "")
    else:
        for slot in player.inventory:
            item = item_registry.get(slot.item_id) if item_registry else None
            if item:
                rarity_color = RARITY_COLORS.get(item.rarity.value, "white")
                table.add_row(
                    f"[{rarity_color}]{item.name}[/{rarity_color}]",
                    str(slot.quantity),
                    item.item_type.value.capitalize(),
                )
            else:
                table.add_row(slot.item_id, str(slot.quantity), "?")

    return Panel(table, title="[system_msg][ INVENTORY ][/system_msg]", border_style="border")


def build_skills_panel(player: "Player", skill_registry: object) -> Panel:
    lines = Text()
    if not player.skills:
        lines.append("No skills learned yet.", style="dim_text")
    else:
        for skill_id in player.skills:
            skill = skill_registry.get(skill_id) if skill_registry else None
            if skill:
                rarity_color = RARITY_COLORS.get(skill.rarity.value, "white")
                lines.append(f"  {skill.name}", style=rarity_color)
                lines.append(f"  {skill.description}\n", style="dim_text")
            else:
                lines.append(f"  {skill_id}\n", style="dim_text")

    return Panel(lines, title="[system_msg][ SKILLS ][/system_msg]", border_style="border")


def build_class_panel(class_def: "ClassDefinition") -> Panel:
    rarity_color = RARITY_COLORS.get(class_def.rarity.value, "white")
    lines = Text()
    lines.append(f"{class_def.name}\n", style=f"bold {rarity_color}")
    lines.append(f"{class_def.rarity.value}\n\n", style=rarity_color)
    lines.append(class_def.description + "\n\n", style="scene_text")
    if class_def.flavor_text:
        lines.append(f'"{class_def.flavor_text}"', style="italic dim_text")

    return Panel(lines, title="[system_msg][ CLASS DESIGNATION ][/system_msg]", border_style=rarity_color)
