from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from config import STARTING_LIVES, ALIGNMENT_LABELS


class Stats(BaseModel):
    STR: int = 5
    INT: int = 5
    AGI: int = 5
    LCK: int = 5
    VIT: int = 5
    WIS: int = 5
    END: int = 5

    def __add__(self, other: "Stats") -> "Stats":
        return Stats(
            STR=self.STR + other.STR,
            INT=self.INT + other.INT,
            AGI=self.AGI + other.AGI,
            LCK=self.LCK + other.LCK,
            VIT=self.VIT + other.VIT,
            WIS=self.WIS + other.WIS,
            END=self.END + other.END,
        )

    def meets(self, requirements: "Stats") -> bool:
        """Return True if every stat >= the requirement."""
        return (
            self.STR >= requirements.STR
            and self.INT >= requirements.INT
            and self.AGI >= requirements.AGI
            and self.LCK >= requirements.LCK
            and self.VIT >= requirements.VIT
            and self.WIS >= requirements.WIS
            and self.END >= requirements.END
        )

    def dominant_stat(self) -> str:
        d = self.model_dump()
        return max(d, key=lambda k: d[k])


class InventorySlot(BaseModel):
    item_id: str
    quantity: int = 1


class EquipmentSlots(BaseModel):
    weapon: str | None = None
    armor: str | None = None
    accessory: str | None = None


class Player(BaseModel):
    # ── Identity ──────────────────────────────────────────────────────────────
    player_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    gender: str = "unspecified"            # "male" | "female" | "other" | "unspecified"
    species_id: str = "human"              # references species definitions JSON
    background_id: str | None = None       # references background template
    background_narrative: str = ""         # AI-generated or player-written flavor text

    # ── Progression ───────────────────────────────────────────────────────────
    level: int = 1
    experience: int = 0
    experience_to_next: int = 100
    stats: Stats = Field(default_factory=Stats)
    stat_points: int = 0
    perception_bonus: int = 0             # from species/equipment; true perception is derived

    # ── Classes ───────────────────────────────────────────────────────────────
    base_class: str | None = None
    secondary_class: str | None = None
    active_class: str | None = None
    evolution_stage: int = 0              # species evolution tier (0 = base form)

    # ── Skills & Abilities ────────────────────────────────────────────────────
    skills: list[str] = Field(default_factory=list)

    # ── Inventory ─────────────────────────────────────────────────────────────
    inventory: list[InventorySlot] = Field(default_factory=list)
    equipped: EquipmentSlots = Field(default_factory=EquipmentSlots)
    gold: int = 50

    # ── Alignment ─────────────────────────────────────────────────────────────
    # Float -100.0 (Harbinger of Ruin) to +100.0 (Paragon of Light)
    # Never store the label — derive it at render time via alignment_label property
    alignment: float = 0.0

    # ── Lives ─────────────────────────────────────────────────────────────────
    lives_remaining: int = STARTING_LIVES
    lives_used: int = 0

    # ── Social / Faction ──────────────────────────────────────────────────────
    # guild_memberships: {guild_id: rank_id}
    guild_memberships: dict[str, str] = Field(default_factory=dict)
    # faction_standing cached on player for fast reads (authoritative copy in world_db)
    faction_standing_cache: dict[str, float] = Field(default_factory=dict)

    # ── Quests (IDs only — full state in world_db) ────────────────────────────
    active_quest_ids: list[str] = Field(default_factory=list)
    completed_quest_ids: list[str] = Field(default_factory=list)

    # ── HP / MP ───────────────────────────────────────────────────────────────
    current_hp: int = 0
    max_hp: int = 0
    current_mp: int = 0
    max_mp: int = 0

    # ── Tracking ──────────────────────────────────────────────────────────────
    flags: dict[str, Any] = Field(default_factory=dict)
    choice_history: list[str] = Field(default_factory=list)
    turn_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    play_time_seconds: int = 0
    last_safe_zone_id: str = "village_start"

    # ── Derived properties ────────────────────────────────────────────────────

    def model_post_init(self, __context: Any) -> None:
        if self.max_hp == 0:
            self.max_hp = 20 + self.stats.VIT * 5 + self.stats.END * 3
            self.current_hp = self.max_hp
        if self.max_mp == 0:
            self.max_mp = 10 + self.stats.INT * 3 + self.stats.WIS * 2
            self.current_mp = self.max_mp

    @property
    def perception(self) -> int:
        """Derived perception: AGI//2 + WIS//3 + species/equipment bonuses."""
        return self.stats.AGI // 2 + self.stats.WIS // 3 + self.perception_bonus

    @property
    def alignment_label(self) -> str:
        """Human-readable alignment band derived from the float value."""
        for low, high, label in ALIGNMENT_LABELS:
            if low <= self.alignment <= high:
                return label
        return "Neutral"

    @property
    def is_alive(self) -> bool:
        return self.current_hp > 0

    @property
    def has_lives(self) -> bool:
        return self.lives_remaining > 0

    @property
    def hp_percent(self) -> float:
        return self.current_hp / self.max_hp if self.max_hp > 0 else 0.0

    # ── Inventory helpers ─────────────────────────────────────────────────────

    def has_item(self, item_id: str) -> bool:
        return any(slot.item_id == item_id for slot in self.inventory)

    def add_item(self, item_id: str, quantity: int = 1) -> None:
        for slot in self.inventory:
            if slot.item_id == item_id:
                slot.quantity += quantity
                return
        self.inventory.append(InventorySlot(item_id=item_id, quantity=quantity))

    def remove_item(self, item_id: str, quantity: int = 1) -> bool:
        for slot in self.inventory:
            if slot.item_id == item_id:
                if slot.quantity > quantity:
                    slot.quantity -= quantity
                    return True
                elif slot.quantity == quantity:
                    self.inventory.remove(slot)
                    return True
        return False

    # ── State helpers ─────────────────────────────────────────────────────────

    def record_choice(self, choice_key: str) -> None:
        self.choice_history.append(choice_key)

    def set_flag(self, key: str, value: Any = True) -> None:
        self.flags[key] = value

    def has_flag(self, key: str) -> bool:
        return bool(self.flags.get(key, False))

    def shift_alignment(self, delta: float) -> None:
        """Clamp alignment to [-100, +100]."""
        self.alignment = max(-100.0, min(100.0, self.alignment + delta))

    def in_guild(self, guild_id: str) -> bool:
        return guild_id in self.guild_memberships

    def guild_rank(self, guild_id: str) -> str | None:
        return self.guild_memberships.get(guild_id)

    def add_quest(self, quest_instance_id: str) -> None:
        if quest_instance_id not in self.active_quest_ids:
            self.active_quest_ids.append(quest_instance_id)

    def complete_quest(self, quest_instance_id: str) -> None:
        if quest_instance_id in self.active_quest_ids:
            self.active_quest_ids.remove(quest_instance_id)
        if quest_instance_id not in self.completed_quest_ids:
            self.completed_quest_ids.append(quest_instance_id)
