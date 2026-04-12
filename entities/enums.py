from enum import Enum


class Rarity(str, Enum):
    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    EPIC = "EPIC"
    LEGENDARY = "LEGENDARY"


class StatType(str, Enum):
    STR = "STR"
    INT = "INT"
    AGI = "AGI"
    LCK = "LCK"
    VIT = "VIT"
    WIS = "WIS"
    END = "END"


class SkillType(str, Enum):
    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"
    TRIGGERED = "TRIGGERED"


class ItemType(str, Enum):
    WEAPON = "WEAPON"
    ARMOR = "ARMOR"
    ACCESSORY = "ACCESSORY"
    CONSUMABLE = "CONSUMABLE"
    KEY_ITEM = "KEY_ITEM"
    LIFE_TOKEN = "LIFE_TOKEN"          # purchasable from auction house


class EffectType(str, Enum):
    DAMAGE = "damage"
    HEAL = "heal"
    BUFF = "buff"
    DEBUFF = "debuff"
    SUMMON = "summon"
    SHIELD = "shield"
    DRAIN = "drain"


# ── New enums for Phase 1 expansion ───────────────────────────────────────────

class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


class AlignmentBand(str, Enum):
    """Derived display label for the alignment float. Never stored directly."""
    PARAGON = "Paragon of Light"
    VIRTUOUS = "Virtuous"
    GOOD = "Good-Natured"
    NEUTRAL = "Neutral"
    GRAY = "Morally Gray"
    CORRUPT = "Corrupt"
    HARBINGER = "Harbinger of Ruin"


class GuildType(str, Enum):
    COMBAT = "combat"
    TRADE = "trade"
    AUCTION = "auction"
    CRIMINAL = "criminal"
    POLITICAL = "political"
    ARCANE = "arcane"
    RELIGIOUS = "religious"


class FactionType(str, Enum):
    POLITICAL = "political"
    CRIMINAL = "criminal"
    MILITARY = "military"
    MERCANTILE = "mercantile"
    ANCIENT = "ancient"
    SYSTEM = "system"                  # factions that ARE the System or oppose it


class QuestStatus(str, Enum):
    AVAILABLE = "available"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class QuestType(str, Enum):
    MAIN = "main"
    GUILD = "guild"
    NPC_PERSONAL = "npc_personal"
    FACTION = "faction"
    AI_GENERATED = "ai_generated"
    DYNAMIC = "dynamic"                # triggered by player actions mid-scene


class NPCRole(str, Enum):
    MERCHANT = "merchant"
    SHOP_ASSISTANT = "shop_assistant"
    GUILD_MASTER = "guild_master"
    GUILD_OFFICER = "guild_officer"
    GENERAL = "general"
    KING = "king"
    NOBLE = "noble"
    CRIMINAL_BOSS = "criminal_boss"
    GUARD = "guard"
    VILLAGER = "villager"
    INNKEEPER = "innkeeper"
    BLACKSMITH = "blacksmith"
    SCHOLAR = "scholar"
    PROPHET = "prophet"
    AUCTION_HOST = "auction_host"


class GateType(str, Enum):
    """How a stat gate behaves when the player falls short."""
    HARD = "hard"           # visible, locked with reason shown
    HIDDEN = "hidden"       # invisible until threshold met (near-miss shows hint)
    SOFT = "soft"           # available with reduced success probability
    PROBABILITY = "probability"  # LCK-driven random outcome


class SpeciesEvolutionTrigger(str, Enum):
    LEVEL = "level"
    QUEST = "quest"
    ITEM = "item"
    ALIGNMENT = "alignment"
    STAT = "stat"
