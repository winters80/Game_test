from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ai.ollama_client import OllamaClient, OllamaParseError, OllamaTimeoutError
from ai.response_validator import AIClassResponse
from ai.prompt_builder import build_system_prompt, build_class_generation_prompt, build_narrative_prompt
from entities.character_class import ClassDefinition, ComboRequirements
from entities.player import Stats
from entities.skill import Skill, SkillEffect
from entities.enums import Rarity, SkillType, EffectType

if TYPE_CHECKING:
    from entities.player import Player
    from entities.character_class import ClassRegistry
    from systems.progression_tracker import DivergenceResult

logger = logging.getLogger(__name__)


class ContentGenerator:
    def __init__(self, ollama_client: OllamaClient, lore_data: dict, model: str) -> None:
        self.client = ollama_client
        self.lore_data = lore_data
        self.system_prompt = build_system_prompt(lore_data)
        self.model = model

    def generate_class(
        self,
        player: "Player",
        divergence: "DivergenceResult",
        class_registry: "ClassRegistry",
    ) -> ClassDefinition | None:
        standard_combos = self._get_standard_combo_names(player, class_registry)
        prompt = build_class_generation_prompt(player, divergence, standard_combos, self.lore_data)

        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.6,
            )
            ai_response = AIClassResponse.model_validate(raw)
            return self._convert_to_class_definition(ai_response, player, divergence)
        except (OllamaParseError, OllamaTimeoutError, Exception) as e:
            logger.error(f"AI class generation failed: {e}")
            return None

    def generate_narrative(
        self,
        player: "Player",
        scene_id: str,
        choice_description: str,
        scene_tone: str = "mysterious",
        relevant_flags: list[str] | None = None,
    ) -> str | None:
        prompt = build_narrative_prompt(
            player, scene_id, choice_description, scene_tone,
            relevant_flags or [], self.lore_data,
        )
        try:
            return self.client.generate_text(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.85,
                max_tokens=300,
            )
        except Exception as e:
            logger.error(f"AI narrative generation failed: {e}")
            return None

    def generate_quest(
        self,
        player: "Player",
        giver_npc_id: str,
        npc_name: str,
        npc_role: str,
    ) -> "QuestTemplate | None":
        """Generate a dynamic quest tailored to the player's profile. Returns None on failure."""
        from ai.prompt_builder import build_quest_generation_prompt
        from ai.response_validator import AIQuestResponse
        from entities.quest import QuestTemplate, QuestStage

        prompt = build_quest_generation_prompt(
            player, giver_npc_id, npc_name, npc_role, self.lore_data
        )
        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.75,
            )
            ai_response = AIQuestResponse.model_validate(raw)

            stages = []
            for s in ai_response.stages:
                stages.append(QuestStage(
                    stage_id=s.stage_id,
                    description=s.objective_text,
                    objective_text=s.objective_text,
                    completion_condition=s.completion_condition,
                    next_stage_id=s.next_stage_id,
                ))

            return QuestTemplate(
                template_id=ai_response.template_id,
                title=ai_response.title,
                description=ai_response.description,
                giver_npc_id=giver_npc_id,
                stages=stages,
                reward_gold=ai_response.reward_gold,
                reward_xp=ai_response.reward_xp,
                reward_items=ai_response.reward_items,
                alignment_reward=ai_response.alignment_reward,
                reward_flags=[],
                failure_conditions=[],
                faction_rewards=ai_response.faction_rewards,
                guild_rewards=ai_response.guild_rewards,
            )
        except Exception as e:
            logger.error(f"AI quest generation failed: {e}")
            return None

    def generate_dynamic_options(
        self,
        question: str,
        scene_title: str,
        scene_text: str,
        current_options: list[str],
        player: "Player",
    ) -> "AIDynamicOptionsResponse | None":
        """
        Generate situational options based on a player's natural-language question.
        Returns None on failure.
        """
        from ai.prompt_builder import build_dynamic_options_prompt
        from ai.response_validator import AIDynamicOptionsResponse

        player_stats = player.stats.model_dump()
        player_stats["level"] = player.level
        player_stats["perception"] = player.perception
        player_flags = [k for k in player.flags.keys() if not k.startswith("_")][:15]

        prompt = build_dynamic_options_prompt(
            question=question,
            scene_title=scene_title,
            scene_text=scene_text,
            current_options=current_options,
            player_stats=player_stats,
            player_flags=player_flags,
            lore_data=self.lore_data,
        )

        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.8,
            )
            return AIDynamicOptionsResponse.model_validate(raw)
        except Exception as e:
            logger.warning(f"Dynamic options generation failed: {e}")
            return None

    def generate_guild_intent(
        self,
        guild: object,   # GuildState
        member: object,  # GuildMember
    ) -> object | None:
        """
        Ask AI to propose a structured guild intent for an NPC member.
        The engine adjudicates; AI must not set success/failure.
        Returns a GuildIntent or None on failure.
        """
        from ai.prompt_builder import build_guild_intent_prompt
        from systems.guilds.guild_engine import get_eligible_actions, compute_betrayal_risk
        from systems.guilds.guild_models import GuildIntent

        eligible = get_eligible_actions(compute_betrayal_risk(member, guild))
        if not eligible:
            return None

        prompt = build_guild_intent_prompt(guild, member, eligible)
        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.5,
            )
            if not raw:
                return None
            return GuildIntent.model_validate(raw)
        except Exception as e:
            logger.warning(f"Guild intent generation failed: {e}")
            return None

    def generate_guild_template(
        self,
        name: str,
        archetype: str,
        zone_id: str,
        founding_reason: str = "",
        seed_traits: list[str] | None = None,
    ) -> object | None:
        """
        Generate a full GuildDefinition template (ranks + perks) for a newly founded guild.
        Returns a GuildDefinition or None on failure.
        """
        from ai.prompt_builder import build_guild_template_prompt
        from ai.response_validator import AIGuildTemplateResponse
        from entities.guild import GuildDefinition, GuildRank, GuildPerk

        prompt = build_guild_template_prompt(
            name=name, archetype=archetype, zone_id=zone_id,
            founding_reason=founding_reason, seed_traits=seed_traits or [],
        )
        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.7,
            )
            if not raw:
                return None
            validated = AIGuildTemplateResponse.model_validate(raw)
            ranks = [
                GuildRank(
                    rank_id=r.rank_id,
                    name=r.name,
                    standing_required=float(r.standing_required),
                    title=r.title,
                    description=r.description,
                )
                for r in validated.ranks
            ]
            perks = [
                GuildPerk(
                    perk_id=p.perk_id,
                    name=p.name,
                    description=p.description,
                    rank_required=p.rank_required,
                    stat_bonuses=p.stat_bonuses,
                    skill_unlocks=p.skill_unlocks,
                )
                for p in validated.perks
            ]
            return GuildDefinition(
                guild_id=validated.guild_id,
                name=validated.name,
                description=validated.description,
                flavor_text=validated.flavor_text,
                archetype=validated.archetype,
                zone_id=zone_id,
                ranks=ranks,
                perks=perks,
            )
        except Exception as e:
            logger.error(f"Guild template generation failed: {e}")
            return None

    def _get_standard_combo_names(self, player: "Player", class_registry: "ClassRegistry") -> list[str]:
        player_classes = {c for c in [player.base_class, player.secondary_class] if c}
        names = []
        for cls in class_registry.combo_classes():
            if cls.combo_requirements:
                req_set = set(cls.combo_requirements.required_classes)
                if req_set.issubset(player_classes) or req_set == player_classes:
                    names.append(cls.name)
        return names

    def _convert_to_class_definition(
        self,
        ai_response: AIClassResponse,
        player: "Player",
        divergence: "DivergenceResult",
    ) -> ClassDefinition:
        rarity = Rarity(ai_response.rarity)

        # Stat bonuses based on player's dominant stat
        dominant = player.stats.dominant_stat()
        bonus_stats = {s: 0 for s in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END")}
        bonus_stats[dominant] = 2
        bonus_stats["LCK"] = max(bonus_stats.get("LCK", 0), 1)

        # Generate skill IDs for the starting skills
        starting_skill_ids = [s.skill_id for s in ai_response.skills[:2]]
        learnable_skill_ids = [s.skill_id for s in ai_response.skills[2:]]

        generation_context = {
            "divergence_score": divergence.score,
            "reasons": divergence.reasons,
            "catalyst_items": divergence.catalyst_items,
            "divergence_flags": divergence.divergence_flags,
            "player_stats": player.stats.model_dump(),
            "ai_response": ai_response.model_dump(),
        }

        return ClassDefinition(
            class_id=ai_response.class_id,
            name=ai_response.name,
            rarity=rarity,
            description=ai_response.description,
            flavor_text=ai_response.flavor_text,
            base_stats_bonus=Stats(**{k: bonus_stats.get(k, 0) for k in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END")}),
            stat_growth=Stats(LCK=1, **{dominant: 1}),
            starting_skills=starting_skill_ids,
            learnable_skills=learnable_skill_ids,
            is_ai_generated=True,
            generation_context=generation_context,
        )

    def _build_skill_from_stub(self, stub: Any, rarity: Rarity) -> Skill:
        """Convert an AI skill stub into a proper Skill object."""
        return Skill(
            skill_id=stub.skill_id,
            name=stub.name,
            rarity=rarity,
            description=stub.description,
            skill_type=SkillType(stub.skill_type),
            effects=[SkillEffect(
                effect_type=EffectType.DAMAGE,
                scaling_stat="LCK",
                base_value=8.0,
                scaling_coefficient=1.5,
            )],
            is_ai_generated=True,
        )
