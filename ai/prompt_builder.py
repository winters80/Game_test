from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from entities.player import Player
    from systems.progression_tracker import DivergenceResult


def _schema_hint() -> str:
    return json.dumps({
        "class_id": "snake_case_unique_name",
        "name": "Display Name (max 30 chars, proper noun)",
        "rarity": "RARE | EPIC | LEGENDARY",
        "description": "Lore paragraph, max 120 words",
        "flavor_text": "Short tagline, max 20 words",
        "skills": [
            {
                "skill_id": "snake_case_id",
                "name": "Skill Display Name",
                "description": "What it does, max 40 words",
                "skill_type": "ACTIVE | PASSIVE | TRIGGERED",
                "effect_hint": "e.g. deals INT-scaling damage with LCK bonus"
            }
        ],
        "lore_hook": "One sentence tying this class to the world lore"
    }, indent=2)


def build_system_prompt(lore_data: dict) -> str:
    world_name = lore_data.get("world_name", "Aethoria")
    system_origin = lore_data.get("system_origin", "")
    anomalies = lore_data.get("anomalies", "")
    tone = lore_data.get("tone", "Dark but not hopeless.")

    return f"""You are the System — an ancient, omniscient entity that governs power in the world of {world_name}.
{system_origin}
{anomalies}
Tone: {tone}

You generate structured game content in JSON format. Respond ONLY with valid JSON. No commentary, no markdown fencing.
Match this exact schema:
{_schema_hint()}"""


def build_class_generation_prompt(
    player: "Player",
    divergence: "DivergenceResult",
    standard_combos: list[str],
    lore_data: dict,
) -> str:
    base_classes = [c for c in [player.base_class, player.secondary_class] if c]
    dominant = player.stats.dominant_stat()
    stats = player.stats

    catalyst_names = []
    for item_id in divergence.catalyst_items:
        catalyst_names.append(item_id.replace("_", " ").title())

    unusual_choice_descriptions = []
    for choice in divergence.unusual_choices[:3]:
        unusual_choice_descriptions.append(choice.replace("unexpected:", "").replace("_", " "))

    div_flag_descriptions = [f.replace("_", " ") for f in divergence.divergence_flags]

    return f"""A player has reached a critical moment — their class must be determined, but no standard designation fits.

PLAYER PROFILE:
- Base classes explored: {", ".join(base_classes) if base_classes else "None — unclassified"}
- Stats: STR {stats.STR}, INT {stats.INT}, AGI {stats.AGI}, LCK {stats.LCK}, VIT {stats.VIT}, WIS {stats.WIS}, END {stats.END}
- Dominant stat: {dominant}
- Level: {player.level}
- Catalyst items in possession: {", ".join(catalyst_names) if catalyst_names else "None"}
- Story flags (divergence): {", ".join(div_flag_descriptions) if div_flag_descriptions else "None"}
- Unusual choices made: {", ".join(unusual_choice_descriptions) if unusual_choice_descriptions else "None"}

STANDARD COMBINATIONS for these classes would be: {", ".join(standard_combos) if standard_combos else "None — no base classes to combine"}

This player's path is anomalous. Their divergence score is {divergence.score} (threshold: 30).
Reasons: {", ".join(divergence.reasons)}

Generate a UNIQUE class that:
1. Feels earned by this specific combination of stats, items, and choices
2. Has a name that is a proper noun or title — NOT a compound of two class names (not BattleMage, not SpellSword, not ShadowMage)
3. Includes 3-5 skills that mechanically reflect the dominant stat ({dominant}) and the player's unusual path
4. Has lore that acknowledges this person bent or broke the System's expectations
5. Rarity should be RARE minimum — this is a special designation
6. The class should feel DIFFERENT from: {", ".join(standard_combos) if standard_combos else "all standard classes"}

Do not repeat existing class names. Make something genuinely new."""


def build_narrative_prompt(
    player: "Player",
    scene_id: str,
    choice_description: str,
    scene_tone: str,
    relevant_flags: list[str],
    lore_data: dict,
) -> str:
    flags_text = ", ".join(relevant_flags) if relevant_flags else "none"
    return f"""The player {player.name} ({player.active_class or player.base_class or "Unclassified"}, Level {player.level}) has taken an unexpected action in scene "{scene_id}": they {choice_description}.

This was not a pre-written path. Generate 2-3 paragraphs of narrative (max 200 words total) describing the immediate consequence of this choice.

Tone: {scene_tone}
Established facts about this player: {flags_text}
World: {lore_data.get("world_name", "Aethoria")}
Current threat context: {lore_data.get("current_threat", "")}

End with a line that transitions naturally to continued exploration. Write as plain prose — no JSON, no headers."""
