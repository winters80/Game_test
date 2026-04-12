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
