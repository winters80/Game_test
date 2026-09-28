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


# Flag the fast model may grant from a typed action while the player is
# still locked out of the capital. Scene options gate the city on it.
VERATH_ACCESS_FLAG = "verath_access"


def _dynamic_option_rules(player: "Player") -> list[str]:
    """Situational rules for the "Ask about this situation" prompt."""
    # No worked example actions here: a 1B model copies them verbatim (it
    # offered "Mine Gold" at the Classification Rite). The world_action tag
    # is derived from each option's label in code instead
    # (core/situation_query._convert_ai_option).
    rules: list[str] = [
        'Allowed triggers: "flag:<snake_name>", "alignment:+N" or "alignment:-N" (max 5), or none. '
        "Never give gold, items, skills or quests.",
    ]
    if not player.has_flag(VERATH_ACCESS_FLAG):
        rules.append(
            "If an option would realistically get the player past Verath's "
            "city gate (a forged pass, hiding in a cart, a convincing lie), add "
            f'"flag:{VERATH_ACCESS_FLAG}" to that option\'s triggers. Otherwise never add it.'
        )
    return rules


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
        rich_skills: bool = False,
    ) -> ClassDefinition | None:
        """Generate a unique class for a divergent player. When
        ``skill_registry`` is provided, every AI-proposed skill is also
        materialised as a real Skill object and registered — otherwise the
        class's starting_skills point to IDs that combat will silently drop.

        ``rich_skills`` toggles whether each skill stub gets its own
        contextual ``generate_skill`` AI pass. Adds significant latency
        (3-5 extra AI calls per class). Use only when the caller can
        absorb the wait — typically the BG-queue submission path.
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
                ai_response, player, divergence,
                skill_registry=skill_registry, rich_skills=rich_skills,
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

    def generate_skill(
        self,
        player: "Player",
        name_hint: str,
        source: str,
        context: dict | None = None,
        has_inspect: bool = False,
    ) -> "Skill | None":
        """Generate a single skill in context.

        Used by give_skill: triggers, quest reward_skill_hints, future
        inspect / trainer flows. The LLM gets the player's profile, the
        source (quest / scene / etc.), and any narrative context the
        caller can provide (quest_title, scene_text, npc_name, ...).
        ``has_inspect`` is True when the player has the Inspect utility
        passive — the description gets exposed mechanical numbers.

        Returns a fully-built Skill, or None on failure. The caller is
        responsible for registering the result in the SkillRegistry.
        """
        from ai.prompt_builder import build_skill_generation_prompt
        from ai.response_validator import AISkillResponse

        prompt = build_skill_generation_prompt(
            player=player,
            name_hint=name_hint,
            source=source,
            context=context or {},
            has_inspect=has_inspect,
            lore_data=self.lore_data,
        )
        try:
            raw = self.client.generate_json(
                prompt=prompt,
                system_prompt=self.system_prompt,
                temperature=0.7,
            )
            validated = AISkillResponse.model_validate(raw)
        except (OllamaParseError, OllamaTimeoutError, Exception) as e:
            logger.warning(f"AI skill generation failed: {e}")
            return None

        # PASSIVE / TRIGGERED skills shouldn't have MP cost or cooldown
        # (the AISkillResponse validator clamps the values; this enforces
        # the type-specific rule).
        if validated.skill_type != "ACTIVE":
            validated.mp_cost = 0
            validated.cooldown_turns = 0

        # Convert to the engine Skill model. The Skill model has its own
        # Pydantic validators (mp_cost ≤ 200, scaling_coefficient ≤ 10),
        # which act as a second-layer guard.
        from entities.enums import EffectType as _ET, Rarity as _R, SkillType as _ST
        # Rarity scales with player level — early skills are COMMON/UNCOMMON,
        # later ones can reach RARE/EPIC. Caller can override after.
        if player.level >= 12:
            rarity = _R.EPIC
        elif player.level >= 7:
            rarity = _R.RARE
        elif player.level >= 3:
            rarity = _R.UNCOMMON
        else:
            rarity = _R.COMMON

        engine_effects = [
            SkillEffect(
                effect_type=_ET(eff.effect_type),
                scaling_stat=eff.scaling_stat,
                base_value=eff.base_value,
                scaling_coefficient=eff.scaling_coefficient,
            )
            for eff in validated.effects
        ]

        try:
            skill = Skill(
                skill_id=validated.skill_id,
                name=validated.name,
                rarity=rarity,
                description=validated.description,
                flavor_text=validated.flavor_text,
                spell_type=validated.spell_type,
                skill_type=_ST(validated.skill_type),
                trigger_condition=validated.trigger_condition,
                mp_cost=validated.mp_cost,
                cooldown_turns=validated.cooldown_turns,
                effects=engine_effects,
                max_level=validated.max_level,
                is_ai_generated=True,
            )
        except Exception as e:
            logger.warning(f"AI skill failed engine validation: {e}")
            return None

        return skill

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
                # AI may propose 0-2 short skill name hints; each becomes a
                # fully-generated Skill at quest completion (themed to the
                # quest narrative). See AIQuestResponse + _grant_quest_skill_rewards.
                reward_skill_hints=ai_response.reward_skill_hints,
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
        from ai.response_validator import DYNAMIC_OPTIONS_SCHEMA, AIDynamicOptionsResponse

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
            extra_rules=_dynamic_option_rules(player),
        )

        _sys = "You write player action options for a fantasy text RPG. Return ONLY valid JSON. No commentary."
        # Two tries: a malformed answer from a small model is usually a one-off.
        # INFO, not WARNING: the console shows WARNING+ mid-scene, and the
        # caller already tells the player when there's no answer.
        for attempt in (1, 2):
            try:
                raw = self.fast_client.generate_json(
                    prompt=prompt,
                    system_prompt=_sys,
                    temperature=0.8,
                    num_predict=400,
                    max_retries=1,
                    schema=DYNAMIC_OPTIONS_SCHEMA,
                )
                return AIDynamicOptionsResponse.model_validate(raw)
            except Exception as e:
                logger.info("Dynamic options generation failed (attempt %d): %s", attempt, e)
        return None

    def generate_action_narrative(
        self,
        scene_title: str,
        scene_text: str,
        action_label: str,
    ) -> str | None:
        """
        One-sentence outcome for a follow-up AI option that has no narrative.
        Uses the fast client with a tiny token budget. Returns the raw
        (unsanitised) narrative string, or None on failure.
        """
        prompt = (
            f'Scene: {scene_title}. {scene_text}\n'
            f'Player action: "{action_label}"\n'
            'Describe the outcome in one vivid sentence (max 25 words). '
            'Return ONLY a JSON object: {"narrative": "..."}'
        )
        try:
            raw = self.fast_client.generate_json(
                prompt=prompt,
                system_prompt="You write one-sentence action outcomes for a fantasy RPG. Return only valid JSON.",
                temperature=0.8,
                num_predict=80,
                max_retries=1,
            )
            return str(raw.get("narrative", ""))
        except Exception as e:
            # INFO not WARNING — best-effort cosmetic call; the console
            # handler shows WARNING+ and the player doesn't need to see it.
            logger.info(f"Action narrative generation failed: {e}")
            return None

    def generate_followup_options(
        self,
        scene_title: str,
        narrative_text: str,
    ) -> list[str] | None:
        """
        2-3 short follow-up option labels for a narrative outcome.
        Returns None on failure.
        """
        prompt = (
            f"Scene: {scene_title}\n"
            f"What just happened: {narrative_text}\n\n"
            "Given this outcome, list 2-3 immediate short options the player could choose next. "
            "Each option must be a single short sentence (under 12 words). "
            "Return ONLY a JSON array of strings, e.g.: "
            '[\"Press the advantage.\", \"Step back and assess.\", \"Call out to the others.\"]'
        )
        system_prompt = (
            "You write concise player action options for a fantasy LitRPG text game set in Aethoria. "
            "Return ONLY a valid JSON array of 2-3 short option strings. No explanation."
        )
        try:
            result = self.fast_client.generate_json(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.75,
                num_predict=200,
                max_retries=1,
            )
        except Exception as e:
            # INFO not WARNING — best-effort cosmetic, same as above.
            logger.info(f"AI follow-up generation failed: {e}")
            return None
        if isinstance(result, list):
            return [str(r) for r in result if isinstance(r, str)]
        # Some models return {"options": [...]}
        if isinstance(result, dict):
            for key in ("options", "choices", "actions"):
                if key in result and isinstance(result[key], list):
                    return [str(r) for r in result[key] if isinstance(r, str)]
        return None

    def generate_shop_refusal(
        self,
        npc_name: str,
        npc_role: str,
        price_text: str,
        gold_text: str,
    ) -> str | None:
        """
        In-character line from a shopkeeper when the player can't afford an
        item. Returns the stripped line, or None on failure / empty output.
        """
        try:
            raw = self.client.generate_text(
                prompt=(
                    f"You are {npc_name}, a {npc_role} in a fantasy city. "
                    f"A customer wants to buy something costing {price_text} "
                    f"but only has {gold_text}. "
                    f"Reply in character, 1-2 short sentences. "
                    f"You may offer them a small errand or job to earn coin, "
                    f"or make a dry but not cruel remark."
                ),
                system_prompt=(
                    "You write brief, flavourful NPC dialogue for a fantasy RPG. "
                    "Stay in character. No quotation marks around the response."
                ),
                temperature=0.85,
                max_tokens=80,
            )
        except Exception as e:
            logger.warning(f"NPC shop-refusal generation failed: {e}")
            return None
        return (raw or "").strip().strip('"') or None

    def generate_world_expansion(
        self,
        verb: str,
        subject: str,
        zone_name: str,
        scene_title: str,
        narrative: str,
        player: "Player",
        known_items: list[str],
    ) -> dict | None:
        """
        Ask the primary model how the world should grow around a player
        action. Returns ``{"response": AIWorldExpansionResponse,
        "skill": Skill | None}``, or None on failure. Values are NOT yet
        clamped: systems/world_growth does that before anything is used.
        Runs in the background worker; never on the main thread.
        """
        from ai.prompt_builder import build_world_expansion_prompt
        from ai.response_validator import AIWorldExpansionResponse

        prompt = build_world_expansion_prompt(
            verb=verb, subject=subject, zone_name=zone_name,
            scene_title=scene_title, narrative=narrative,
            player_level=player.level, known_items=known_items,
        )
        try:
            raw = self.client.generate_json(
                prompt=prompt, system_prompt=self.system_prompt, temperature=0.6,
            )
            response = AIWorldExpansionResponse.model_validate(raw)
        except Exception as e:
            logger.info(f"World expansion generation failed: {e}")
            return None

        skill = None
        if response.skill_hint:
            skill = self.generate_skill(
                player=player,
                name_hint=response.skill_hint,
                source="world_action",
                context={"scene_title": scene_title,
                         "scene_text": f"The player chose to {verb} {subject}. {narrative[:160]}"},
            )
        return {"response": response, "skill": skill}

    def generate_bot_line(self, profile: dict, player_name: str) -> str | None:
        """One or two in-character sentences from a bot adventurer, grounded in
        what it has actually been doing (``profile["recent"]``). Fast client."""
        recent = "; ".join(profile.get("recent") or []) or "nothing notable"
        prompt = (
            f"You are {profile['name']}, a level {profile['level']} {profile['class']} adventurer in "
            f"Aethoria ({profile['personality']}). Right now you are {profile['doing']}. "
            f"Recently: {recent}. {player_name}, another adventurer, walks up to you. "
            "Reply in character, 1-2 short sentences. Mention something you've really been doing."
        )
        try:
            raw = self.fast_client.generate_text(
                prompt=prompt,
                system_prompt=("You write brief, grounded dialogue for adventurers in a gritty fantasy "
                               "RPG. No quotation marks around the reply."),
                temperature=0.85, max_tokens=80,
            )
        except Exception as e:
            logger.info(f"Bot line generation failed: {e}")
            return None
        text = (raw or "").strip().strip('"').strip()
        return text or None

    def token_usage(self) -> list[tuple[str, str, dict]]:
        """
        Per-client token stats as ``(role, model, stats)`` tuples. The fast
        client is only listed separately when it is a distinct instance.
        """
        usage = [("primary", self.client.model, self.client.token_summary())]
        if self.fast_client is not self.client:
            usage.append(("fast", self.fast_client.model, self.fast_client.token_summary()))
        return usage

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
        rich_skills: bool = False,
    ) -> ClassDefinition:
        """Build a ClassDefinition from an AI class response.

        ``rich_skills=False`` (default): each AI skill stub is converted
        deterministically via _build_skill_from_stub (keyword-parsed
        effect_hint). Fast — one AI call total for the class.

        ``rich_skills=True``: each stub is ALSO fed back through the full
        ``generate_skill`` pipeline using the class context. Adds 3-5
        sequential AI calls per class (~30-60s of latency) but every
        skill gets the same level of mechanical care a quest-reward or
        inspect-flow skill does. Used by the BG-queue path so the slow
        generation happens off the main thread.
        """
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
        ctx = {
            "extra": f"Part of the AI-generated class '{ai_response.name}'. "
                     f"Lore hook: {ai_response.lore_hook[:120]}",
        }
        for stub in ai_response.skills:
            built: Skill | None = None
            if rich_skills:
                # Each stub goes through the full generate_skill pipeline
                # using the class's lore as context. Falls back to the
                # deterministic path if any single AI call fails.
                try:
                    built = self.generate_skill(
                        player=player,
                        name_hint=stub.name or stub.skill_id,
                        source="class_grant",
                        context=ctx,
                        has_inspect=False,
                    )
                    if built is not None:
                        # Force the AI to keep the stub's id so the
                        # ClassDefinition's starting_skills list aligns.
                        built.skill_id = stub.skill_id
                except Exception:
                    built = None
            if built is None:
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
