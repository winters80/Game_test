import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SAVES_DIR = BASE_DIR / "saves"
SAVES_DIR.mkdir(exist_ok=True)

# ── Save versioning ────────────────────────────────────────────────────────────
SAVE_VERSION = 4   # bump when Player schema or world_db schema changes

# ── Ollama ─────────────────────────────────────────────────────────────────────
AI_ENABLED = True
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# Both can be overridden with environment variables of the same name, e.g.
# OLLAMA_MODEL=qwen3:32b, to use a model you already have without editing this file.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or "mistral-nemo"   # Primary: 12B, good JSON + creative writing
OLLAMA_FAST_MODEL = os.environ.get("OLLAMA_FAST_MODEL", "gemma3:4b")  # Small model for interactive calls. "" = use primary.
# gemma3:1b is faster but mostly echoes the prompt back for [?] questions.
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"
OLLAMA_TIMEOUT_JSON = 60               # seconds for structured generation (primary model)
OLLAMA_TIMEOUT_FAST = 15              # seconds for fast-model interactive calls
OLLAMA_TIMEOUT_TEXT = 20               # seconds for narrative generation
OLLAMA_MAX_RETRIES = 3
OLLAMA_KEEP_ALIVE = "30m"              # keep models loaded between calls (Ollama's default is 5m)
OLLAMA_PRELOAD = True                  # load both models into memory at startup (background)
OLLAMA_WARMUP_TIMEOUT = 180            # seconds allowed for the first model load
OLLAMA_TEMP_JSON = 0.4                 # lower = more consistent structured output
OLLAMA_TEMP_TEXT = 0.85                # higher = more creative narrative

# ── Background AI generation ────────────────────────────────────────────────
BG_GEN_ENABLED  = True    # master switch for background content generation
BG_GEN_INTERVAL = 5       # turns between background task submissions
DIRECTOR_INTERVAL = 20    # turns between WorldDirector analysis cycles (turn-based fallback)
AUTONOMOUS_GEN_INTERVAL_MINUTES = 20   # how often background AI generates without player action
GUILD_SIM_INTERVAL = 10    # turns between background guild simulation ticks

# ── Divergence / AI trigger ────────────────────────────────────────────────────
DIVERGENCE_THRESHOLD = 30              # score >= this triggers AI class generation
DIVERGENCE_SCORE_UNEXPECTED_CHOICE = 10
DIVERGENCE_SCORE_NO_COMBO_MATCH = 40
DIVERGENCE_SCORE_WILD_CATALYST = 20
DIVERGENCE_SCORE_FLAG = 25

DIVERGENCE_FLAGS = [
    "refused_system",
    "broke_tutorial_sequence",
    "helped_enemy",
    "destroyed_quest_item",
    "betrayed_faction",
]

# ── AI quest offers ────────────────────────────────────────────────────────────
# Minimum NPC disposition required before they implicitly offer an AI-generated
# quest. Only fires when the NPC has no available pre-written quest seeds.
AI_QUEST_DISPOSITION_MIN = 30.0

# ── AI trigger policy (systems/ai_trigger_policy.py) ───────────────────────────
# Effects the AI attaches to generated options are filtered before use.
# Flags the AI sets are namespaced with AI_FLAG_PREFIX so it can never set an
# authored story flag — except these, which content deliberately lets it grant.
AI_FLAG_PREFIX = "ai_"
AI_GRANTABLE_FLAGS = frozenset({"verath_access"})
AI_MAX_ALIGNMENT_SHIFT = 5.0           # |alignment:±N| clamp for AI options
AI_GIVEABLE_RARITIES = frozenset({"COMMON", "UNCOMMON"})
# No gear, key/quest items or life tokens: those are progression, not flavour.
AI_GIVEABLE_ITEM_TYPES = frozenset({"CONSUMABLE", "MATERIAL"})
AI_MAX_ITEMS_PER_OPTION = 1

# ── Traders (systems/trade_system.py) ──────────────────────────────────────────
TRADER_ROUTE_STAY_TURNS = 15           # turns a travelling NPC stays in each route zone

# ── Bots (systems/bot_system.py, systems/bot_brain.py) ─────────────────────────
BOT_COUNT = 12                         # adventurers per save (authored ones + generated)
BOT_ACT_EVERY_TURNS = 2                # each bot acts once every N player turns (staggered)
BOT_AI_DECISIONS = False               # True = old behaviour: ask the primary model for every
                                       # bot decision (slow; bots freeze without Ollama)
BOT_MAX_EVENT_LINES = 1                # bot activity lines shown per turn in your zone
BOT_ARRIVALS_EVERY_TURNS = 4           # batch "X and Y are about." to once per N turns

# ── World growth (systems/world_growth.py) ─────────────────────────────────────
WORLD_YIELD_COOLDOWN_TURNS = 10        # turns before repeating an action yields again
WORLD_MAX_TRADERS_PER_LOCATION = 2     # generated traders per zone (and on the road);
                                       # beyond this, new goods join an existing one's stock
WORLD_MAX_EXPANSIONS = 60              # world expansions per save; later new actions are 'capped'
WORLD_RETRY_ON_LOAD = 3                # pending / no_ai actions re-queued per load

# ── Game balance ───────────────────────────────────────────────────────────────
BASE_XP_PER_LEVEL = 100
XP_SCALING_FACTOR = 1.5               # each level needs x1.5 more XP
STAT_POINTS_PER_LEVEL = 3
STARTING_GOLD    = 5000             # copper pieces (50 gold)
COPPER_PER_GOLD  = 100              # 1 gold = 100 copper
COPPER_PER_SILVER = 10              # 1 silver = 10 copper
LOW_HP_TRIGGER_THRESHOLD = 0.30     # on_low_hp skills fire when an enemy hit drops HP below this fraction of max


def format_currency(copper: int) -> str:
    """Format copper amount as 'Xg Ys Zc', dropping zero denominations."""
    if copper <= 0:
        return "0c"
    g = copper // 100
    s = (copper % 100) // 10
    c = copper % 10
    parts = []
    if g:
        parts.append(f"{g}g")
    if s:
        parts.append(f"{s}s")
    if c:
        parts.append(f"{c}c")
    return " ".join(parts) or "0c"

# ── Lives system ───────────────────────────────────────────────────────────────
STARTING_LIVES = 9
MAX_LIVES = 12                         # cap even after buying from auction
DEATH_PENALTY_MODE = "xp"             # "none" | "xp" | "item" | "alignment"
DEATH_XP_PENALTY_PCT = 0.15           # lose 15% of current-level XP on death
DEATH_ALIGNMENT_PENALTY = -5.0        # if mode = "alignment"

# ── Alignment ──────────────────────────────────────────────────────────────────
ALIGNMENT_INERTIA_RATE = -0.2         # passive drift toward 0 per turn (dampens extremes)
ALIGNMENT_INERTIA_INTERVAL = 10       # apply inertia every N turns

ALIGNMENT_LABELS = [
    (+80,  +100, "Paragon of Light"),
    (+50,   +79, "Virtuous"),
    (+20,   +49, "Good-Natured"),
    (-19,   +19, "Neutral"),
    (-49,   -20, "Morally Gray"),
    (-79,   -50, "Corrupt"),
    (-100,  -80, "Harbinger of Ruin"),
]

# ── NPC memory ─────────────────────────────────────────────────────────────────
NPC_MEMORY_MAX_EVENTS = 20            # compress oldest 10 into summary when exceeded
NPC_MEMORY_COMPRESS_AT = 20
NPC_MEMORY_KEEP_RECENT = 10

# ── Auction house ──────────────────────────────────────────────────────────────
AUCTION_TURN_DURATION = 20            # turns before an auction listing expires
AUCTION_LIFE_TOKEN_BASE_PRICE = 500   # base gold cost per life token
AUCTION_LIFE_TOKEN_SCARCITY_MULT = 1.5  # price multiplier per life already lost

# ── UI ─────────────────────────────────────────────────────────────────────────
TERMINAL_WIDTH = 100
GAME_TITLE = "SYSTEM BREAKER"
GAME_SUBTITLE = "The Universe Is Watching"

# ── Feature flags ──────────────────────────────────────────────────────────────
# Set to True to enable a system. False = system is present in code but never called.
# This lets you build and test systems in isolation before wiring them together.
FEATURES: dict[str, bool] = {
    "species_system":     True,    # Species selection + evolution paths
    "alignment_system":   True,    # Alignment float + alignment-gated content
    "npc_system":         True,    # Named NPCs with memory + stat-gated dialogue
    "quest_system":       True,    # Quest state machine + completion tracking
    "guild_system":       True,    # Guild membership, ranks, perks
    "faction_system":     True,    # Faction politics, standing, ascension
    "auction_house":      True,    # Auction listings, competing guilds, life tokens
    "lives_system":       True,    # 9-lives death mechanic (replaces instant game-over)
    "crafting_system":    True,    # Recipe + material crafting
    "stat_gating":        True,    # INT/Perception/LCK/WIS content gates
    "world_db":           True,    # SQLite world state (required by npc/quest systems)
    "synergy_system":     True,    # Stat synergy bonuses in combat
    "bot_system":         True,    # AI-driven autonomous bot agents
}


def feature(name: str) -> bool:
    """Check if a feature flag is enabled. Raises KeyError for unknown flags."""
    return FEATURES[name]
