"""
NPC entities — templates loaded from JSON, instances live in SQLite.

NPCTemplate     — static definition (name, role, dialogue tree, quest seeds)
NPCDialogueNode — one "screen" of NPC speech + options
NPCDialogueOption — a player choice within a dialogue node
NPCQuestSeed    — which quests an NPC can offer and under what conditions
NPCTrades       — what a trader buys / sells
NPCRegistry     — loads templates from JSON; ``register`` adds them at runtime
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class NPCDialogueOption(BaseModel):
    option_id: str
    label: str
    # Same shape as scene option requires blocks:
    # min_stats, items, flags, alignment_min, alignment_max, stat_gates, min_disposition
    requires: dict[str, Any] = Field(default_factory=dict)
    npc_response: str = ""              # NPC reply text shown after player picks this
    disposition_change: float = 0.0    # delta applied to npc_instances.disposition
    triggers: list[str] = Field(default_factory=list)  # flag:X, give_item:X, give_gold:N, start_quest:X, alignment:N
    leads_to_node: str = "__exit__"    # next dialogue node, or "__exit__"
    memory_event_type: str = ""        # if non-empty, record memory event of this type
    memory_summary: str = ""           # summary text stored in npc_memory


class NPCDialogueNode(BaseModel):
    node_id: str
    npc_text: str
    # Appended if player INT >= smart_threshold (player asks smarter questions)
    smart_observation: str = ""
    smart_threshold: int = 0
    # Appended if player Perception >= perception_threshold (player detects lies/hidden info)
    perception_reveal: str = ""
    perception_threshold: int = 0
    options: list[NPCDialogueOption] = Field(default_factory=list)


class NPCQuestSeed(BaseModel):
    quest_template_id: str          # "ai_dynamic" triggers AI generation
    trigger_flag: str = ""          # player must have this flag (or empty = always available)
    trigger_disposition_min: float = 0.0   # NPC must be at least this disposed toward player
    already_given_flag: str = ""    # flag set after quest offered; prevents re-offering


class NPCScheduleEntry(BaseModel):
    phase: Literal["day", "night", "always"] = "always"
    zone_id: str


class NPCTradeOffer(BaseModel):
    """Something a trader sells. ``price`` is in copper, like player.gold."""
    item_id: str
    price: int = Field(ge=1)


class NPCTrades(BaseModel):
    """What a trader NPC buys and sells (systems/trade_system.py).

    ``buys`` entries are item ids or ``type:ITEM_TYPE`` tags (e.g.
    ``type:MATERIAL``). Sale price to the trader = item.value_gold × 100 ×
    ``buy_rate``.
    """
    buys: list[str] = Field(default_factory=list)
    sells: list[NPCTradeOffer] = Field(default_factory=list)
    buy_rate: float = Field(default=0.5, gt=0.0, le=1.0)


class NPCTemplate(BaseModel):
    template_id: str                # stable ID, used in talk_npc: triggers
    npc_id: str                     # instance ID written to npc_instances SQLite table
    name: str
    role: str                       # matches NPCRole enum values
    zone_id: str                    # home zone — NPC is available when player is here
    description: str                # shown in dialogue header
    flavor_text: str = ""
    is_essential: bool = True       # if True, cannot be killed
    starting_disposition: float = 0.0
    # Requires block for NPC visibility — supports "any_of_flags", "any_of_items" in addition
    # to standard "flags" and "items" keys. "any_of_X" = player needs at least one.
    appears_requires: dict[str, Any] = Field(default_factory=dict)
    # Maps disposition bracket to entry node_id:
    # keys: "hostile" (< -30), "friendly" (> +40), "default" (everything else)
    disposition_hooks: dict[str, str] = Field(default_factory=lambda: {"default": "root"})
    # All dialogue nodes for this NPC, keyed by node_id
    dialogue_nodes: dict[str, NPCDialogueNode] = Field(default_factory=dict)
    quest_seeds: list[NPCQuestSeed] = Field(default_factory=list)
    schedule: list[NPCScheduleEntry] = Field(default_factory=list)
    # Travelling NPCs: zones visited in order, TRADER_ROUTE_STAY_TURNS each.
    # Takes precedence over ``schedule`` when non-empty.
    route: list[str] = Field(default_factory=list)
    # Ambient NPCs get an automatic "Talk to …" option at the root node of
    # whatever zone they're currently in, with no scene authoring needed.
    ambient: bool = False
    trades: NPCTrades | None = None
    is_ai_generated: bool = False


class NPCRegistry:
    def __init__(self) -> None:
        self._npcs: dict[str, NPCTemplate] = {}

    def load_from_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            npc = NPCTemplate.model_validate(entry)
            self._npcs[npc.template_id] = npc

    def load_from_dir(self, path: Path) -> None:
        """Load all *.json files in a directory. Each file is a JSON array of NPC templates."""
        for json_file in sorted(path.glob("*.json")):
            self.load_from_file(json_file)

    def register(self, npc: NPCTemplate) -> None:
        """Add or replace a template at runtime (e.g. AI world expansion)."""
        self._npcs[npc.template_id] = npc

    def get(self, template_id: str) -> NPCTemplate | None:
        return self._npcs.get(template_id)

    def get_by_npc_id(self, npc_id: str) -> NPCTemplate | None:
        for npc in self._npcs.values():
            if npc.npc_id == npc_id:
                return npc
        return None

    def all(self) -> list[NPCTemplate]:
        return list(self._npcs.values())

    def for_zone(self, zone_id: str) -> list[NPCTemplate]:
        return [n for n in self._npcs.values() if n.zone_id == zone_id]
