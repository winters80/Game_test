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
    from systems.economy import quest_reward_bounds, gear_price_hint

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

    # Level-scaled reward bounds + an honest economy hint so the LLM doesn't
    # propose rewards that would bankrupt a village or insult a lord.
    (g_min, g_max), (x_min, x_max) = quest_reward_bounds(player.level, "standard")
    economy_hint = gear_price_hint(player.level)

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

ECONOMY CONTEXT (use these bounds, do not invent your own):
  {economy_hint}
  For this player (level {player.level}) the reward MUST be in this range:
    reward_gold: between {g_min} and {g_max} gold pieces
    reward_xp:   between {x_min} and {x_max} XP
  Pick the LOW end for short courier/errand quests, the MIDDLE for standard
  retrieval / investigation quests, and the HIGH end only for genuinely
  multi-stage and dangerous work. NEVER exceed these bounds — values
  outside the range will be clamped silently.

REQUIREMENTS:
- Create a 2-3 stage quest that fits this player's profile and alignment
- Quest must feel personal to this character's background and choices
- Use existing world lore: faction politics, the dungeon, guild rivalries
- stages must have completion_condition as {{"has_flag": "flag_name"}} or {{"has_item": "item_id"}}
- Flags should be snake_case like "completed_verath_errand"
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


def build_skill_generation_prompt(
    player: "Player",
    name_hint: str,
    source: str,
    context: dict,
    has_inspect: bool,
    lore_data: dict,
) -> str:
    """Build the AI prompt for a single contextual skill generation.

    The AI is told who the player is, where the skill is coming from
    (quest_reward / give_skill_trigger / inspect / npc_teach / class_grant),
    what hint to anchor on, and whether the player has the Inspect passive
    (which means we can expose mechanical detail in the description).

    Returns a prompt string. The generator wraps the response with
    AISkillResponse → Skill model validation, so absurd output is clamped.
    """
    from systems.economy import gear_price_hint  # for tier-relative context

    # Tier-scaled mp/cd bounds. Lower levels get cheap fast skills; higher
    # levels can afford bigger MP costs and longer cooldowns. Caps mirror
    # the AISkillResponse Pydantic validators (mp_cost ≤ 30, cd ≤ 10).
    level = max(1, player.level)
    mp_cap = min(30, 4 + level * 2)         # L1 → 6, L5 → 14, L10 → 24, L13+ → 30
    cd_cap = min(10, 1 + level // 2)        # L1 → 1, L5 → 3, L10 → 6, L18+ → 10
    base_cap = min(40, 5 + level * 2)       # base damage ceiling per effect
    coeff_cap = min(3.5, 1.0 + level * 0.2) # scaling-coefficient ceiling

    skills_owned = ", ".join(player.skills[:8]) if player.skills else "none"
    active_class = player.active_class or player.base_class or "Unclassified"
    dominant = player.stats.dominant_stat()

    # Source-specific framing — different paths feel different in-fiction.
    source_blurbs = {
        "quest_reward":         "The skill was earned as a quest reward.",
        "give_skill_trigger":   "The skill emerged through a scene event.",
        "inspect":              "The player is studying an unfamiliar ability they've witnessed.",
        "npc_teach":            "An NPC trainer is teaching this skill.",
        "class_grant":          "The class itself manifests this ability for the player.",
    }
    source_blurb = source_blurbs.get(source, "The player acquired this skill.")

    # Pull narrative context the caller provides — quest title, scene
    # description, NPC name etc. Render only the bits the LLM can use.
    ctx_lines: list[str] = []
    for key in ("quest_title", "quest_description", "scene_title",
                "scene_text", "npc_name", "extra"):
        val = context.get(key)
        if val:
            ctx_lines.append(f"  {key.replace('_', ' ').title()}: {str(val)[:200]}")
    ctx_block = "\n".join(ctx_lines) if ctx_lines else "  (no extra context)"

    inspect_block = (
        "The player HAS the Inspect passive — they can see exact numbers.\n"
        "  Be precise in the description: 'deals 12 + INT*1.5 fire damage, "
        "8 MP, 2-turn cooldown'. The description should read like a System "
        "tooltip a power-gamer would trust."
        if has_inspect else
        "The player does NOT have Inspect — keep the description in-fiction\n"
        "  and slightly vague about exact numbers ('a searing arc of pale fire',\n"
        "  'costs effort to channel', 'rarely usable in quick succession')."
    )

    economy = gear_price_hint(level)
    world_name = lore_data.get("world_name", "Aethoria")

    return f"""Generate a SINGLE skill for a player in {world_name}.

PLAYER PROFILE:
  Level: {level}
  Class: {active_class}
  Dominant stat: {dominant}
  Skills already known: {skills_owned}
  Alignment: {player.alignment:+.0f}

SOURCE:
  {source_blurb}
  Name hint (anchor on this — refine if needed, do not invent something unrelated):
    "{name_hint}"
{ctx_block}

PLAYER POWER TIER:
  {economy}

INSPECT CONTEXT:
  {inspect_block}

CONSTRAINTS (HARD — values outside these bounds will be clamped):
  - skill_type: ACTIVE (most common) | PASSIVE (always-on bonus) | TRIGGERED (fires on event)
  - PASSIVE skills MUST have mp_cost=0 and cooldown_turns=0
  - TRIGGERED skills MUST set trigger_condition to one of:
      "on_attack", "on_hit", "on_kill", "on_low_hp"
  - ACTIVE skill mp_cost: 0 to {mp_cap}
  - ACTIVE skill cooldown_turns: 0 to {cd_cap}
  - effects[].base_value: 0 to {base_cap}
  - effects[].scaling_coefficient: 0 to {coeff_cap}
  - effects[].scaling_stat: STR / INT / AGI / LCK / VIT / WIS / END
  - effects[].effect_type: damage / heal / buff / debuff / shield / drain
  - 1-3 effects per skill (most have 1)
  - The skill should feel coherent with the player's CLASS and SOURCE.
    A blacksmith giving a quest skill produces something forge-themed.
    A quest about shadow thieves produces stealth-flavoured skills.

Return JSON ONLY in this schema:
{{
  "skill_id": "snake_case_id",
  "name": "Display Name",
  "description": "Player-facing description",
  "flavor_text": "One-line evocative tag",
  "skill_type": "ACTIVE",
  "spell_type": "",
  "trigger_condition": null,
  "mp_cost": 8,
  "cooldown_turns": 2,
  "max_level": 10,
  "effects": [
    {{"effect_type": "damage", "scaling_stat": "INT",
      "base_value": 12, "scaling_coefficient": 1.5}}
  ]
}}"""


def build_guild_intent_prompt(
    guild: object,  # GuildState
    member: object,  # GuildMember
    eligible_actions: list[str],
) -> str:
    """Build a prompt asking AI to propose a guild intent for an NPC member."""
    return (
        f"You are {member.entity_id}, a member of the guild '{guild.name}' "
        f"(archetype: {guild.archetype}, stability: {guild.stability}/100, "
        f"morale: {guild.morale}/100, wealth: {guild.wealth}). "
        f"Your loyalty is {member.loyalty}/100 and ambition is {member.ambition}/100. "
        f"Your current rank is '{member.rank_id}'. "
        f"\n\nChoose ONE action from this list: {eligible_actions}. "
        f"\n\nRespond with a JSON object matching one of these schemas:\n"
        f'- attempt_coup: {{"intent": "attempt_coup", "actor_id": "{member.entity_id}", "guild_id": "{guild.guild_id}"}}\n'
        f'- found_splinter_guild: {{"intent": "found_splinter_guild", "actor_id": "{member.entity_id}", "parent_guild_id": "{guild.guild_id}", "name": "New Guild Name", "initial_supporters": [], "new_focus": "idle"}}\n'
        f'- leak_secrets: {{"intent": "leak_secrets", "actor_id": "{member.entity_id}", "guild_id": "{guild.guild_id}", "target_guild_id": "TARGET_ID", "severity": 20}}\n'
        f'- steal_resources: {{"intent": "steal_resources", "actor_id": "{member.entity_id}", "guild_id": "{guild.guild_id}", "amount": 50}}\n'
        f'- sabotage_project: {{"intent": "sabotage_project", "actor_id": "{member.entity_id}", "guild_id": "{guild.guild_id}", "project_id": "PROJECT_ID"}}\n'
        f"\nDo NOT include 'success', 'outcome', or 'result' fields. The engine adjudicates."
    )


def build_guild_template_prompt(
    name: str,
    archetype: str,
    zone_id: str,
    founding_reason: str,
    seed_traits: list[str],
) -> str:
    """Build a prompt to generate a full guild template (ranks + perks)."""
    traits_str = ", ".join(seed_traits) if seed_traits else "none specified"
    return (
        f"Create a guild template for a new {archetype} guild called '{name}'. "
        f"Founded in the zone '{zone_id}'. Reason: {founding_reason or 'unspecified'}. "
        f"Defining traits: {traits_str}.\n\n"
        f"Provide 3-5 ranks (with increasing standing_required values: 0, 25, 50, 75, 95) "
        f"and 2-4 perks. "
        f"Perks may only grant stat_bonuses (max +3 per stat) or skill_unlocks referencing real skill IDs. "
        f"Do not invent item IDs or non-existent skills.\n\n"
        f"Respond with JSON matching this exact schema:\n"
        f'{{"guild_id": "unique_snake_case_id", '
        f'"name": "{name}", '
        f'"description": "lore description max 80 words", '
        f'"flavor_text": "tagline max 15 words", '
        f'"archetype": "{archetype}", '
        f'"ranks": [{{"rank_id": "id", "name": "Name", "standing_required": 0, "title": "Title", "description": "desc"}}], '
        f'"perks": [{{"perk_id": "id", "name": "Name", "description": "desc", "rank_required": "rank_id", "stat_bonuses": {{}}, "skill_unlocks": []}}]}}'
    )


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

    return f"""Scene: {scene_title}
Context: {scene_text[:200]}
Player stats: {stats_str}
Flags: {flags_str}
Question: "{question}"

Return ONLY JSON with 1-2 options:
{{"situation_text":"1-2 sentence description","options":[{{"option_id":"snake_id","label":"Short label","narrative":"1 sentence outcome","triggers":[]}}]}}"""
