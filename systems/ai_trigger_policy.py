"""
Allow-list for effects the AI attaches to generated options.

The fast model's "[?] Ask about this situation" options and the background
model's NPC dialogue branches both carry trigger strings that the engine
executes verbatim. Without a filter, a hallucinating model could hand out
``give_gold:999999``, set an ending flag, or assign a class. Every AI-authored
trigger list goes through ``sanitize_ai_triggers`` before it reaches the
engine.

Allowed, after normalisation:

| Trigger                     | Rule                                                        |
|-----------------------------|-------------------------------------------------------------|
| ``flag:X``                  | Rewritten to ``flag:ai_X`` unless X is in AI_GRANTABLE_FLAGS |
| ``give_item:X``             | X must exist, be a COMMON/UNCOMMON consumable or material, not a catalyst; max 1 |
| ``alignment:±N``            | Clamped to ±AI_MAX_ALIGNMENT_SHIFT                           |
| ``combat:X``                | X must be a known encounter                                  |
| ``world_action:VERB:SUBJECT`` | Slugged; one per option (feeds world expansion)            |

Everything else (gold, skills, classes, quests, NPC talk, shops, rests…) is
dropped. Pure logic: no I/O beyond logging.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import (
    AI_FLAG_PREFIX,
    AI_GIVEABLE_ITEM_TYPES,
    AI_GIVEABLE_RARITIES,
    AI_GRANTABLE_FLAGS,
    AI_MAX_ALIGNMENT_SHIFT,
    AI_MAX_ITEMS_PER_OPTION,
)

if TYPE_CHECKING:
    from entities.item import ItemRegistry

logger = logging.getLogger(__name__)

WORLD_ACTION_PREFIX = "world_action:"
_SLUG_MAX = 40


@dataclass
class TriggerVerdict:
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def slugify(text: str, max_len: int = _SLUG_MAX) -> str:
    """Lower-case snake slug: letters, digits and underscores only."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return slug[:max_len].rstrip("_")


def ai_flag_name(raw: str) -> str:
    """The flag name an AI-authored ``flag:raw`` actually sets ('' if unusable)."""
    name = slugify(raw)
    if not name:
        return ""
    if name in AI_GRANTABLE_FLAGS:
        return name
    if name.startswith(AI_FLAG_PREFIX):
        return name
    return f"{AI_FLAG_PREFIX}{name}"[:_SLUG_MAX]


def parse_world_action(trigger: str) -> tuple[str, str] | None:
    """``world_action:mine:gold`` → ("mine", "gold"); None if malformed."""
    if not trigger.startswith(WORLD_ACTION_PREFIX):
        return None
    parts = trigger[len(WORLD_ACTION_PREFIX):].split(":", 1)
    if len(parts) != 2:
        return None
    verb, subject = slugify(parts[0], 20), slugify(parts[1], 30)
    if not verb or not subject:
        return None
    return verb, subject


def sanitize_ai_triggers(
    triggers: list[str],
    item_registry: "ItemRegistry | None",
) -> TriggerVerdict:
    """Filter + normalise an AI-authored trigger list. See module docstring."""
    from systems.combat_system import ENCOUNTERS

    verdict = TriggerVerdict()
    items_given = 0
    has_world_action = False

    for raw in triggers or []:
        if not isinstance(raw, str):
            continue
        trigger = raw.strip()
        kind, _, value = trigger.partition(":")
        kind = kind.lower()
        kept: str | None = None

        if kind == "flag":
            name = ai_flag_name(value)
            if name:
                kept = f"flag:{name}"

        elif kind == "give_item" and item_registry is not None:
            item = item_registry.get(value.strip())
            if (
                item is not None
                and items_given < AI_MAX_ITEMS_PER_OPTION
                and item.rarity.value in AI_GIVEABLE_RARITIES
                and item.item_type.value in AI_GIVEABLE_ITEM_TYPES
                and not item.combo_catalyst
            ):
                items_given += 1
                kept = f"give_item:{item.item_id}"

        elif kind == "alignment":
            try:
                delta = float(value)
            except ValueError:
                delta = 0.0
            delta = max(-AI_MAX_ALIGNMENT_SHIFT, min(AI_MAX_ALIGNMENT_SHIFT, delta))
            if delta:
                kept = f"alignment:{delta:+g}"

        elif kind == "combat":
            if value.strip() in ENCOUNTERS:
                kept = f"combat:{value.strip()}"

        elif kind == "world_action" and not has_world_action:
            parsed = parse_world_action(trigger)
            if parsed:
                has_world_action = True
                kept = f"{WORLD_ACTION_PREFIX}{parsed[0]}:{parsed[1]}"

        if kept:
            verdict.kept.append(kept)
        else:
            verdict.dropped.append(trigger)

    if verdict.dropped:
        # INFO, not WARNING: a model over-reaching is expected and handled;
        # the console shows WARNING+ and this must not interrupt play.
        logger.info("AI trigger policy dropped %s (kept %s)", verdict.dropped, verdict.kept)
    return verdict


def ai_required_flag_met(has_flag, flag: str) -> bool:
    """AI ``requires.flags`` entries may name a flag the AI itself set earlier
    (stored under the ai_ namespace) or an authored flag. Either counts."""
    name = slugify(flag)
    if not name:
        return True
    return bool(has_flag(name) or has_flag(ai_flag_name(name)))
