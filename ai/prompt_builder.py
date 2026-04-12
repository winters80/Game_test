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


def build_quest_generation_prompt(
    player: "Player",
    giver_npc_id: str,
    npc_name: str,
    npc_role: str,
    lore_data: dict,
    ai_context: dict | None = None,
) -> str:
    """Build a prompt for dynamic quest generation based on player profile."""
    from config import ALIGNMENT_LABELS

    # Derive alignment label
    alignment_label = "Neutral"
    for low, high, label in ALIGNMENT_LABELS:
        if low <= player.alignment <= high:
            alignment_label = label
            break

    skills_str = ", ".join(player.skills[:5]) if player.skills else "none"
    inventory_str = ", ".join(s.item_id for s in player.inventory[:5]) if player.inventory else "empty"

    world_name = lore_data.get("world_name", "Aethoria")
    capital = lore_data.get("capital", "Verath")

    return f"""Generate a quest for a LitRPG game set in the world of {world_name}.
Capital city: {capital}. The System appeared 3 years ago during the Fracture.

NPC QUEST GIVER: {npc_name} ({npc_role}, id: {giver_npc_id})

PLAYER PROFILE:
  Name: {player.name}
  Level: {player.level}
  Class: {player.active_class or player.base_class or 'Unclassified'}
  Species: {player.species_id or 'human'}
  Gender: {player.gender}
  Alignment: {alignment_label} ({player.alignment:+.0f})
  Top stats: STR={player.stats.STR}, INT={player.stats.INT}, AGI={player.stats.AGI}
  Skills: {skills_str}
  Inventory items: {inventory_str}
  Flags set: {list(player.flags.keys())[:8]}

REQUIREMENTS:
- Create a 2-3 stage quest that fits this player's profile and alignment
- Quest must feel personal to this character's background and choices
- Use existing world lore: faction politics, the dungeon, guild rivalries
- stages must have completion_condition as {{"has_flag": "flag_name"}} or {{"has_item": "item_id"}}
- Flags should be snake_case like "completed_verath_errand"
- reward_gold should be 50-300 based on difficulty
- reward_xp should be 100-500 based on difficulty
- If player is evil-aligned, quest can have morally gray objectives
- faction_rewards and guild_rewards: use faction/guild IDs like "iron_vanguard", "shadow_network", "mages_conclave", "silver_fangs"
- Return ONLY valid JSON matching the schema exactly

Return JSON in this exact format:
{{
  "template_id": "ai_quest_<short_unique_id>",
  "title": "Quest title",
  "description": "Brief description",
  "stages": [
    {{
      "stage_id": "stage_1",
      "objective_text": "Player-facing objective",
      "completion_condition": {{"has_flag": "some_flag"}},
      "next_stage_id": "stage_2"
    }},
    {{
      "stage_id": "stage_2",
      "objective_text": "Final objective",
      "completion_condition": {{"has_flag": "quest_done_flag"}},
      "next_stage_id": null
    }}
  ],
  "reward_gold": 100,
  "reward_xp": 200,
  "reward_items": [],
  "alignment_reward": 5.0,
  "faction_rewards": {{"iron_vanguard": 10.0}},
  "guild_rewards": {{}},
  "flavor_text": "Atmospheric closing line"
}}"""


def build_dynamic_options_prompt(
    question: str,
    scene_title: str,
    scene_text: str,
    current_options: list[str],
    player_stats: dict,
    player_flags: list[str],
    lore_data: dict,
) -> str:
    """
    Build a prompt for generating dynamic situational options based on player question.
    """
    stats_str = ", ".join(f"{k}:{v}" for k, v in player_stats.items())
    options_str = "\n".join(f"- {o}" for o in current_options)
    flags_str = ", ".join(player_flags[:12]) or "none"
    world_name = lore_data.get("world_name", "Aethoria")

    return f"""
The player is in scene: {scene_title}
Scene text: {scene_text[:300]}

Current choices available:
{options_str}

Player stats: {stats_str}
Active flags: {flags_str}

The player asks: "{question}"

Generate 1-4 new situational options that address this question. These options should:
- Be creative and grounded in the scene context
- Use realistic stat checks if the action requires ability
- Include specific triggers (e.g. "combat:enemy_id", "flag:action_done", "give_gold:50")
- NOT duplicate existing options
- Feel like a skilled GM adding depth to the encounter

Available trigger types:
- flag:FLAG_NAME — sets a player flag
- combat:ENEMY_ID — starts combat (use existing IDs: goblin_scout, goblin_looter, dungeon_slime)
- give_gold:N — gives N copper (100 = 1 gold)
- give_item:ITEM_ID — gives item
- give_skill:SKILL_ID — grants a skill
- alignment:+N or alignment:-N — shifts alignment

Return ONLY valid JSON matching this schema:
{{
  "situation_text": "A narrative paragraph (2-4 sentences) explaining what the player can see or do given their question. Be specific about what's possible.",
  "options": [
    {{
      "option_id": "unique_snake_case_id",
      "label": "Short action label (max 60 chars)",
      "narrative": "What happens if they choose this (1 sentence)",
      "triggers": ["flag:example_flag"],
      "requires": {{}}
    }}
  ]
}}
"""
