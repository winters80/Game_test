# SYSTEM BREAKER

> A LitRPG terminal RPG where the universe watches your choices.

The System appeared during the Fracture and rewrote the rules of the world. Most survivors got a class, a rank, and an expected path. You didn't — or you refused yours. Now the System is paying attention, and **Ollama** is generating your story in real time.

---

## What Is This?

SYSTEM BREAKER is a Python terminal RPG inspired by progression fantasy (LitRPG / "Apocalypse Breaker" style). The defining mechanic: when you take unexpected paths — unusual stat combinations, refusing the System's assignments, carrying rare catalyst items — an on-device AI (Ollama) generates a unique class, skills, and narrative tailored to your specific playthrough.

**No two divergent paths produce the same class.**

Built with:
- Python 3.11 + Pydantic v2 (all data models)
- [Rich](https://github.com/Textualize/rich) for terminal UI
- [questionary](https://github.com/tmbo/questionary) for arrow-key menus
- [Ollama](https://ollama.ai) (`mistral-nemo` 12B) for AI content generation
- SQLite for world state (NPC memory, quests, factions)

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) installed and running (optional but recommended)

```bash
# Install Ollama model (optional — game works without it)
ollama pull mistral-nemo
```

### 2. Install dependencies

```bash
cd Game_Test
pip install -r requirements.txt
```

### 3. Run the game

```bash
python main.py
```

That's it. The game detects Ollama automatically on startup. If it's not running, all content falls back to hand-crafted static dialogue and pre-written classes.

---

## Project Layout

```
Game_Test/
├── main.py                 # Entry point
├── config.py               # All constants and feature flags — change here only
├── CLAUDE.md               # Developer reference (architecture, triggers, schemas)
│
├── core/                   # Game engine, event bus, state
├── entities/               # Pydantic models (Player, Item, Skill, Class, NPC, Quest)
├── systems/                # Pure game logic (class resolution, combat, quests, levels)
├── ai/                     # Ollama integration (client, prompts, validators)
├── scenes/data/            # All narrative content as JSON (scene scripts)
├── data/                   # All static content (classes, skills, items, NPCs, quests)
├── ui/                     # All Rich terminal rendering
├── persistence/            # Save/load (JSON player data + SQLite world state)
└── saves/                  # Runtime save files (gitignored)
```

---

## Gameplay

- **Arrow keys** to navigate menus, **Enter** to confirm
- Your class is assigned by the System after the opening rite — unless you diverge
- The AI triggers when your divergence score ≥ 30 (see below)
- **`[S]`** in any choice menu saves your game
- **`[A]`** in any choice menu opens the admin panel (AI token usage)
- **`[Q]`** quits to the main menu

### What triggers AI generation?

| Player action | Divergence score |
|--------------|-----------------|
| Each "unexpected" choice taken | +10 |
| No standard combo class matches your build | +40 |
| Each "wild catalyst" item in inventory | +20 |
| Each divergence flag (refused System, betrayed faction, etc.) | +25 |

**Score ≥ 30 → Ollama generates a unique class.** The AI receives your stats, class history, catalyst items, and story flags. It returns a class name, rarity, description, and 3–5 custom skills. The result is cached so the same build always produces the same class.

---

## Adding Content (No Code Required)

All game content lives in JSON files. Restart the game to pick up changes.

### New dialogue scene
Create `scenes/data/my_scene.json`. The filename stem becomes the scene ID. See `scenes/data/village_start.json` for the full node/option schema.

### New NPC
Add a template to `data/npcs/npcs.json`. Enable `"npc_system": true` in `config.FEATURES` if not already on.

### New quest
Add a template to `data/quests/quest_templates.json`. Trigger it from a scene or NPC with `"start_quest:template_id"`.

### New item
Add to `data/items/*.json`. Set `"combo_catalyst": true` if it should contribute to AI divergence scoring.

### New class (hand-crafted)
Add to `data/classes/base_classes.json` (basic) or `combo_classes.json` (requires specific class combination).

### New skill
Add to any file in `data/skills/`. The skill registry loads everything in that directory.

### New species
Add to `data/species/species_definitions.json`. Define `evolution_path` as ordered stages.

For full trigger string and gate syntax reference, see **CLAUDE.md**.

---

## Feature Flags

In `config.py`, the `FEATURES` dict lets you enable/disable systems without removing code:

```python
FEATURES = {
    "species_system":   True,   # Species + evolution
    "alignment_system": True,   # Moral alignment tracking
    "npc_system":       True,   # NPCs with memory and stat-gated dialogue
    "quest_system":     True,   # Quest state machine
    "stat_gating":      True,   # INT/Perception-gated options
    "world_db":         True,   # SQLite world state (required by NPC/quest)
    "guild_system":     False,  # Not yet implemented
    "faction_system":   False,  # Not yet implemented
    "auction_house":    False,  # Not yet implemented
    "lives_system":     False,  # 9-lives death mechanic
    "crafting_system":  False,  # Not yet implemented
}
```

Enable order matters: `world_db` must be `True` before `npc_system`, `quest_system`, or `faction_system`.

---

## Saves

Save files live in `saves/`. Each slot creates two files:
- `<slot>.json` — player data (Pydantic model)
- `<slot>.db` — world state (SQLite: NPC memory, quest instances, etc.)

Both files must be present to load a save. The game handles migration automatically when the save format version changes.

---

## AI Configuration

All AI settings are in `config.py`:

```python
AI_ENABLED = True                  # Set False to disable all Ollama calls
OLLAMA_MODEL = "mistral-nemo"      # Primary model (12B)
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"
DIVERGENCE_THRESHOLD = 30          # Score needed to trigger AI class generation
```

If Ollama is offline or `AI_ENABLED = False`, every AI call silently falls back to the nearest pre-written class from `data/classes/combo_classes.json`.

---

## Development

```bash
# Run syntax checks (no game launch needed)
python -c "import py_compile; py_compile.compile('main.py', doraise=True)"

# Run integration tests
python test_runs.py

# Start a feature branch
git checkout -b feature/guild-system
# Enable the flag, build, test, then disable before merging if not ready
```

See **CLAUDE.md** for the full developer reference: trigger strings, gate syntax, event bus events, SQLite table descriptions, and AI integration contracts.
