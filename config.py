from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SAVES_DIR = BASE_DIR / "saves"
SAVES_DIR.mkdir(exist_ok=True)

# ── Save versioning ────────────────────────────────────────────────────────────
SAVE_VERSION = 3   # bump when Player schema or world_db schema changes

# ── Ollama ─────────────────────────────────────────────────────────────────────
AI_ENABLED = True
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral-nemo"          # Primary: 12B, good JSON + creative writing
OLLAMA_FAST_MODEL = "gemma3:1b"        # Small model for interactive (dynamic options). Set "" to use primary.
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"
OLLAMA_TIMEOUT_JSON = 60               # seconds for structured generation (primary model)
OLLAMA_TIMEOUT_FAST = 15              # seconds for fast-model interactive calls
OLLAMA_TIMEOUT_TEXT = 20               # seconds for narrative generation
OLLAMA_MAX_RETRIES = 3
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

# ── Game balance ───────────────────────────────────────────────────────────────
BASE_XP_PER_LEVEL = 100
XP_SCALING_FACTOR = 1.5               # each level needs x1.5 more XP
STAT_POINTS_PER_LEVEL = 3
STARTING_GOLD    = 5000             # copper pieces (50 gold)
COPPER_PER_GOLD  = 100              # 1 gold = 100 copper
COPPER_PER_SILVER = 10              # 1 silver = 10 copper


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
