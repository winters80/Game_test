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
    def __init__(self, ollama_client: OllamaClient, lore_data: dict, model: str,
                 fast_client: OllamaClient | None = None) -> None:
        self.client = ollama_client
        self.fast_client = fast_client or ollama_client  # smaller model for interactive calls
        self.lore_data = lore_data
        self.system_prompt = build_system_prompt(lore_data)
        self.model = model

    def generate_class(
        self,
        player: "Player",
        divergence: "DivergenceResult",
        class_registry: "ClassRegistry",
        skill_registry: "SkillRegistry | None" = None,
    ) -> ClassDefinition | None:
        """Generate a unique class for a divergent player. When
        ``skill_registry`` is provided, every AI-proposed skill is also
        materialised as a real Skill object and registered — otherwise the
        class's starting_skills point to IDs that combat will silently drop.
        """
        standard_combos = self._get_standard_combo_names(player, class_registry)
        prompt = build_class_generation_prompt(player, divergence, standard_combos, self.lore_data)

        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.6,
            )
            ai_response = AIClassResponse.model_validate(raw)
            return self._convert_to_class_definition(
                ai_response, player, divergence, skill_registry=skill_registry,
            )
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

        player_stats = {k: v for k, v in player.stats.model_dump().items() if v}
        player_stats["level"] = player.level
        player_flags = [k for k in player.flags.keys() if not k.startswith("_")][:6]

        prompt = build_dynamic_options_prompt(
            question=question,
            scene_title=scene_title,
            scene_text=scene_text,
            current_options=current_options,
            player_stats=player_stats,
            player_flags=player_flags,
            lore_data=self.lore_data,
        )

        _sys = "You write player action options for a fantasy text RPG. Return ONLY valid JSON. No commentary."
        try:
            raw = self.fast_client.generate_json(
                prompt=prompt,
                system_prompt=_sys,
                temperature=0.8,
                num_predict=300,
                max_retries=1,
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
        skill_registry: "SkillRegistry | None" = None,
    ) -> ClassDefinition:
        rarity = Rarity(ai_response.rarity)

        # Stat bonuses based on player's dominant stat
        dominant = player.stats.dominant_stat()
        bonus_stats = {s: 0 for s in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END")}
        bonus_stats[dominant] = 2
        bonus_stats["LCK"] = max(bonus_stats.get("LCK", 0), 1)

        # Materialise every AI skill stub into a real Skill object and
        # register it. Previously this method only stored skill IDs that
        # weren't backed by any registered Skill — combat silently dropped
        # them and the class's "unique skills" were invisible to the player.
        for stub in ai_response.skills:
            built = self._build_skill_from_stub(stub, rarity, dominant_stat=dominant)
            if skill_registry is not None and skill_registry.get(built.skill_id) is None:
                skill_registry.register(built)

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

    def _build_skill_from_stub(
        self, stub: Any, rarity: Rarity, dominant_stat: str = "LCK",
    ) -> Skill:
        """Convert an AI skill stub into a proper Skill object.

        Parses the LLM's ``effect_hint`` (a free-text field on AISkillStub)
        for stat names and effect kind so each generated skill is actually
        mechanically distinct. Previously every AI skill was identical:
        8 base damage + LCK x1.5. Now a stub with effect_hint "heals based
        on WIS" actually produces a heal skill that scales off WIS.
        """
        hint = (stub.effect_hint or "").lower()

        # Default effect type
        eff_type = EffectType.DAMAGE
        if any(k in hint for k in ("heal", "restore", "mend", "regenerat")):
            eff_type = EffectType.HEAL
        elif any(k in hint for k in ("shield", "ward", "barrier")):
            eff_type = EffectType.SHIELD
        elif any(k in hint for k in ("buff", "empower", "strengthen")):
            eff_type = EffectType.BUFF
        elif any(k in hint for k in ("debuff", "weaken", "slow", "stun")):
            eff_type = EffectType.DEBUFF

        # Pick a scaling stat from the hint, falling back to the player's
        # dominant stat so the class feels tailored.
        scaling_stat = dominant_stat
        for candidate in ("STR", "INT", "AGI", "LCK", "VIT", "WIS", "END"):
            if candidate.lower() in hint:
                scaling_stat = candidate
                break

        # Rarity-tiered base values — higher rarity skills hit harder.
        base_by_rarity = {
            Rarity.COMMON: 6.0, Rarity.UNCOMMON: 8.0, Rarity.RARE: 12.0,
            Rarity.EPIC: 18.0, Rarity.LEGENDARY: 25.0,
        }
        coeff_by_rarity = {
            Rarity.COMMON: 1.0, Rarity.UNCOMMON: 1.3, Rarity.RARE: 1.7,
            Rarity.EPIC: 2.2, Rarity.LEGENDARY: 2.8,
        }
        base = base_by_rarity.get(rarity, 8.0)
        coeff = coeff_by_rarity.get(rarity, 1.5)

        # MP cost + cooldown scale with rarity too. ACTIVE skills cost MP,
        # PASSIVE/TRIGGERED skills don't.
        try:
            stype = SkillType(stub.skill_type)
        except ValueError:
            stype = SkillType.ACTIVE
        mp_cost = 0 if stype != SkillType.ACTIVE else {
            Rarity.COMMON: 5, Rarity.UNCOMMON: 8, Rarity.RARE: 12,
            Rarity.EPIC: 18, Rarity.LEGENDARY: 25,
        }.get(rarity, 8)
        cooldown = 0 if stype != SkillType.ACTIVE else {
            Rarity.COMMON: 1, Rarity.UNCOMMON: 2, Rarity.RARE: 3,
            Rarity.EPIC: 5, Rarity.LEGENDARY: 6,
        }.get(rarity, 2)

        return Skill(
            skill_id=stub.skill_id,
            name=stub.name,
            rarity=rarity,
            description=stub.description,
            skill_type=stype,
            mp_cost=mp_cost,
            cooldown_turns=cooldown,
            effects=[SkillEffect(
                effect_type=eff_type,
                scaling_stat=scaling_stat,
                base_value=base,
                scaling_coefficient=coeff,
            )],
            is_ai_generated=True,
        )
