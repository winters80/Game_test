from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import (
    DIVERGENCE_THRESHOLD,
    DIVERGENCE_SCORE_UNEXPECTED_CHOICE,
    DIVERGENCE_SCORE_NO_COMBO_MATCH,
    DIVERGENCE_SCORE_WILD_CATALYST,
    DIVERGENCE_SCORE_FLAG,
    DIVERGENCE_FLAGS,
)

if TYPE_CHECKING:
    from entities.player import Player
    from entities.item import ItemRegistry
    from entities.character_class import ClassRegistry


@dataclass
class DivergenceResult:
    score: int
    trigger_ai: bool
    reasons: list[str] = field(default_factory=list)
    catalyst_items: list[str] = field(default_factory=list)
    unusual_choices: list[str] = field(default_factory=list)
    divergence_flags: list[str] = field(default_factory=list)


def compute_divergence_score(
    player: "Player",
    class_registry: "ClassRegistry",
    item_registry: "ItemRegistry",
) -> DivergenceResult:
    score = 0
    reasons: list[str] = []

    # Signal 1: unexpected choices in history
    unexpected_choices = [c for c in player.choice_history if c.startswith("unexpected:")]
    score += len(unexpected_choices) * DIVERGENCE_SCORE_UNEXPECTED_CHOICE
    if unexpected_choices:
        reasons.append("unexpected_choices_taken")

    # Signal 2: no standard combo matches
    matched = _find_matching_combos(player, class_registry)
    if not matched and (player.base_class or player.secondary_class):
        score += DIVERGENCE_SCORE_NO_COMBO_MATCH
        reasons.append("no_standard_combo_match")

    # Signal 3: wild catalyst items
    wild_catalysts = _get_wild_catalysts(player, item_registry, class_registry)
    score += len(wild_catalysts) * DIVERGENCE_SCORE_WILD_CATALYST
    if wild_catalysts:
        reasons.append("wild_catalyst_items_present")

    # Signal 4: divergence flags
    active_div_flags = [f for f in DIVERGENCE_FLAGS if player.has_flag(f)]
    score += len(active_div_flags) * DIVERGENCE_SCORE_FLAG
    if active_div_flags:
        reasons.append("divergence_flags_set")

    return DivergenceResult(
        score=score,
        trigger_ai=score >= DIVERGENCE_THRESHOLD,
        reasons=reasons,
        catalyst_items=wild_catalysts,
        unusual_choices=unexpected_choices,
        divergence_flags=active_div_flags,
    )


def _find_matching_combos(player: "Player", class_registry: "ClassRegistry") -> list[str]:
    """Return combo class IDs whose required_classes are a subset of what player has."""
    player_classes = {c for c in [player.base_class, player.secondary_class] if c}
    if not player_classes:
        return []
    matches = []
    for cls in class_registry.combo_classes():
        if cls.combo_requirements:
            required = set(cls.combo_requirements.required_classes)
            if required.issubset(player_classes):
                matches.append(cls.class_id)
    return matches


def _get_wild_catalysts(player: "Player", item_registry: "ItemRegistry", class_registry: "ClassRegistry") -> list[str]:
    known_combo_items: set[str] = set()
    for cls in class_registry.combo_classes():
        if cls.combo_requirements:
            known_combo_items.update(cls.combo_requirements.required_items)
    wild = []
    for slot in player.inventory:
        item = item_registry.get(slot.item_id)
        if item and item.combo_catalyst and slot.item_id not in known_combo_items:
            wild.append(slot.item_id)
    return wild
