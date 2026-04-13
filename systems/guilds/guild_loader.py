"""
Guild loader — loads templates from JSON files (authored + AI-generated).
Also provides save_generated_template() for persisting AI-created guilds.
"""
from __future__ import annotations

import json
from pathlib import Path

from entities.guild import GuildRegistry, GuildDefinition, GuildRank


def load_guild_registry(data_dir: Path) -> GuildRegistry:
    """
    Load all guild templates: authored definitions + AI-generated ones.
    """
    reg = GuildRegistry()
    authored = data_dir / "guilds" / "guild_definitions.json"
    if authored.exists():
        reg.load_from_file(authored)

    generated_dir = data_dir / "guilds" / "generated"
    if generated_dir.exists():
        for f in sorted(generated_dir.glob("*.json")):
            try:
                _load_single_guild(reg, f)
            except Exception:
                pass  # skip malformed generated files

    return reg


def _load_single_guild(reg: GuildRegistry, path: Path) -> None:
    """Load a single guild JSON that contains either a list or a single object."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for entry in data:
            g = GuildDefinition.model_validate(entry)
            reg._guilds[g.guild_id] = g
    elif isinstance(data, dict):
        g = GuildDefinition.model_validate(data)
        reg._guilds[g.guild_id] = g


def save_generated_template(template: GuildDefinition, generated_dir: Path) -> None:
    """Persist an AI-generated GuildDefinition to disk as JSON."""
    generated_dir.mkdir(parents=True, exist_ok=True)
    out = generated_dir / f"{template.guild_id}.json"
    out.write_text(template.model_dump_json(indent=2), encoding="utf-8")


def make_default_template(
    guild_id: str,
    name: str,
    archetype: str,
    zone_id: str,
    founding_reason: str = "",
) -> GuildDefinition:
    """
    Minimal fallback template when AI is unavailable.
    Uses archetype-appropriate rank names.
    """
    archetype_ranks: dict[str, list[tuple[str, str, int]]] = {
        "combat":   [("recruit", "Recruit", 0), ("soldier", "Soldier", 25), ("captain", "Captain", 70)],
        "stealth":  [("initiate", "Initiate", 0), ("shadow", "Shadow", 25), ("phantom", "Phantom", 70)],
        "arcane":   [("apprentice", "Apprentice", 0), ("adept", "Adept", 25), ("arcanist", "Arcanist", 70)],
        "merchant": [("trader", "Trader", 0), ("broker", "Broker", 25), ("magnate", "Magnate", 70)],
    }
    ranks = [
        GuildRank(rank_id=r, name=n, standing_required=float(s))
        for r, n, s in archetype_ranks.get(archetype, archetype_ranks["combat"])
    ]
    return GuildDefinition(
        guild_id=guild_id,
        name=name,
        description=founding_reason or f"A {archetype} guild founded in {zone_id}.",
        archetype=archetype,
        zone_id=zone_id,
        ranks=ranks,
        perks=[],
    )
