from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SAVES_DIR = BASE_DIR / "saves"
SAVES_DIR.mkdir(exist_ok=True)

# ── Save versioning ────────────────────────────────────────────────────────────
SAVE_VERSION = 2   # bump when Player schema or world_db schema changes

# ── Ollama ─────────────────────────────────────────────────────────────────────
AI_ENABLED = True
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral-nemo"          # Primary: 12B, good JSON + creative writing
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"
OLLAMA_TIMEOUT_JSON = 30               # seconds for structured generation
OLLAMA_TIMEOUT_TEXT = 20               # seconds for narrative generation
OLLAMA_MAX_RETRIES = 3
OLLAMA_TEMP_JSON = 0.4                 # lower = more consistent structured output
OLLAMA_TEMP_TEXT = 0.85                # higher = more creative narrative

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

# ── Game balance ───────────────────────────────────────────────────────────────
BASE_XP_PER_LEVEL = 100
XP_SCALING_FACTOR = 1.5               # each level needs x1.5 more XP
STAT_POINTS_PER_LEVEL = 3
STARTING_GOLD = 50

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
    "species_system":     False,   # Species selection + evolution paths
    "alignment_system":   False,   # Alignment float + alignment-gated content
    "npc_system":         False,   # Named NPCs with memory + stat-gated dialogue
    "quest_system":       False,   # Dynamic AI quest generation + state machine
    "guild_system":       False,   # Guild membership, ranks, perks
    "faction_system":     False,   # Faction politics, standing, ascension
    "auction_house":      False,   # Auction listings, competing guilds, life tokens
    "lives_system":       False,   # 9-lives death mechanic (replaces instant game-over)
    "crafting_system":    False,   # Recipe + material crafting
    "stat_gating":        False,   # INT/Perception/LCK/WIS content gates
    "world_db":           False,   # SQLite world state (required by most above)
}


def feature(name: str) -> bool:
    """Check if a feature flag is enabled. Raises KeyError for unknown flags."""
    return FEATURES[name]
