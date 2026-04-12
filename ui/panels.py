from __future__ import annotations

from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import format_currency

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassDefinition


RARITY_COLORS: dict[str, str] = {
    "COMMON":       "white",
    "UNCOMMON":     "green",
    "RARE":         "bright_blue",
    "EPIC":         "magenta",
    "LEGENDARY":    "bright_yellow",
    "AI_GENERATED": "cyan",
}


def _stat_bracket(stats_bonus: object) -> str:
    """Return ' [+2 STR +1 END]' string for non-zero stats in the bonus object."""
    parts = []
    for stat in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
        val = getattr(stats_bonus, stat, 0)
        if val and val != 0:
            sign = "+" if val > 0 else ""
            parts.append(f"{sign}{val} {stat}")
    return f" [{' '.join(parts)}]" if parts else ""


def _hp_color(current: int, max_hp: int) -> str:
    ratio = current / max_hp if max_hp > 0 else 0
    if ratio > 0.6:
        return "hp_high"
    elif ratio > 0.3:
        return "hp_mid"
    return "hp_low"


_ALIGNMENT_COLORS = [
    (+50,  +100, "bright_yellow"),
    (+20,   +49, "green"),
    (-19,   +19, "white"),
    (-49,   -20, "yellow"),
    (-100,  -49, "red"),
]


def _alignment_color(alignment: float) -> str:
    for low, high, color in _ALIGNMENT_COLORS:
        if low <= alignment <= high:
            return color
    return "white"


def build_status_panel(player: "Player") -> Panel:
    from config import feature
    table = Table.grid(padding=(0, 1))
    table.add_column(style="stat_name", width=10)
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
    table.add_row("Gold", f"[gold]{format_currency(player.gold)}[/gold]")

    if feature("species_system") and player.species_id:
        species_str = player.species_id.replace("_", " ").title()
        if player.evolution_stage > 0:
            species_str += f" (Evo {player.evolution_stage})"
        table.add_row("Species", species_str)

    if feature("alignment_system"):
        label = player.alignment_label
        color = _alignment_color(player.alignment)
        table.add_row("Alignment", f"[{color}]{label} ({player.alignment:+.0f})[/{color}]")

    if player.active_buffs:
        from systems.buff_system import summarize_buffs
        buff_lines = summarize_buffs(player)
        if buff_lines:
            table.add_row("Buffs", f"[bright_green]{', '.join(buff_lines[:2])}[/bright_green]")

    if feature("lives_system"):
        table.add_row("Lives", f"[bright_white]{'◆' * player.lives_remaining}[/bright_white] {player.lives_remaining}/{player.lives_remaining + player.lives_used}")

    return Panel(table, title="[system_msg][ STATUS ][/system_msg]", border_style="border", width=44)


def build_stats_panel(player: "Player") -> Panel:
    stats = player.stats
    table = Table.grid(padding=(0, 2))
    table.add_column(style="stat_name", width=5)
    table.add_column(style="stat_value", width=4)
    table.add_column(style="stat_name", width=5)
    table.add_column(style="stat_value")

    table.add_row("STR", str(stats.STR), "WIS", str(stats.WIS))
    table.add_row("INT", str(stats.INT), "END", str(stats.END))
    table.add_row("AGI", str(stats.AGI), "PER", str(player.perception))
    table.add_row("LCK", str(stats.LCK), "", "")
    table.add_row("VIT", str(stats.VIT), "", "")

    return Panel(table, title="[system_msg][ STATS ][/system_msg]", border_style="border", width=30)


def build_compact_inventory_panel(player: "Player", item_registry: object) -> Panel:
    """Narrow inventory panel for the always-visible status bar."""
    lines = Text()
    if not player.inventory:
        lines.append("  Empty", style="dim_text")
    else:
        for slot in player.inventory:
            item = item_registry.get(slot.item_id) if item_registry else None
            if item:
                rarity_color = RARITY_COLORS.get(item.rarity.value, "white")
                qty = f" x{slot.quantity}" if slot.quantity > 1 else ""
                lines.append(f"  {item.name}{qty}\n", style=rarity_color)
            else:
                lines.append(f"  {slot.item_id}\n", style="dim_text")
    return Panel(lines, title="[system_msg][ ITEMS ][/system_msg]", border_style="border", width=26)


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


def build_guild_panel(player: "Player") -> Panel:
    """Panel listing guild memberships and current ranks."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="stat_name", width=24)
    table.add_column(style="stat_value")

    if not player.guild_memberships:
        table.add_row("[dim_text]No guild memberships[/dim_text]", "")
    else:
        for guild_id, rank_id in player.guild_memberships.items():
            name = guild_id.replace("_", " ").title()
            table.add_row(name, rank_id.replace("_", " ").title())

    return Panel(table, title="[system_msg][ GUILDS ][/system_msg]", border_style="border")


def build_faction_panel(player: "Player") -> Panel:
    """Panel listing known faction standings."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="stat_name", width=24)
    table.add_column(style="stat_value", width=8)

    if not player.faction_standing_cache:
        table.add_row("[dim_text]No faction contacts[/dim_text]", "")
    else:
        for faction_id, standing in player.faction_standing_cache.items():
            name = faction_id.replace("_", " ").title()
            color = "green" if standing >= 25 else ("red" if standing <= -25 else "white")
            table.add_row(name, f"[{color}]{standing:+.0f}[/{color}]")

    return Panel(table, title="[system_msg][ FACTIONS ][/system_msg]", border_style="border")


def build_class_panel(class_def: "ClassDefinition") -> Panel:
    rarity_color = RARITY_COLORS.get(class_def.rarity.value, "white")
    lines = Text()
    lines.append(f"{class_def.name}\n", style=f"bold {rarity_color}")
    lines.append(f"{class_def.rarity.value}\n\n", style=rarity_color)
    lines.append(class_def.description + "\n\n", style="scene_text")
    if class_def.flavor_text:
        lines.append(f'"{class_def.flavor_text}"', style="italic dim_text")

    return Panel(lines, title="[system_msg][ CLASS DESIGNATION ][/system_msg]", border_style=rarity_color)


def build_equipment_panel(player: "Player", item_registry: object) -> Panel:
    """Compact panel showing currently equipped items in all three slots."""
    table = Table.grid(padding=(0, 1))
    table.add_column(style="stat_name", width=10)
    table.add_column(style="stat_value")

    slots = [
        ("Weapon", player.equipped.weapon),
        ("Armor",  player.equipped.armor),
        ("Access", player.equipped.accessory),
    ]

    for label, item_id in slots:
        if item_id:
            item = item_registry.get(item_id) if item_registry else None
            if item:
                color = RARITY_COLORS.get(item.rarity.value, "white")
                bracket = _stat_bracket(item.stats_bonus) if item.stats_bonus else ""
                table.add_row(label, f"[{color}]{item.name}[/{color}][dim_text]{bracket}[/dim_text]")
            else:
                table.add_row(label, item_id)
        else:
            table.add_row(label, "[dim_text][empty][/dim_text]")

    return Panel(table, title="[system_msg][ EQUIP ][/system_msg]", border_style="border", width=26)
