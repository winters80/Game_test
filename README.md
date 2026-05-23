# SYSTEM BREAKER

> A LitRPG terminal RPG where the universe watches your choices.

The System appeared during the Fracture and rewrote the rules of the world. Most survivors got a class, a rank, and an expected path. You didn't — or you refused yours. Now the System is paying attention, and **Ollama** is generating your story in real time.

---

## What Is This?

SYSTEM BREAKER is a Python terminal RPG inspired by progression fantasy. The defining mechanic: when you take unexpected paths — unusual stat combinations, refusing the System's assignments, carrying rare catalyst items — an on-device AI (Ollama) generates a unique class, skills, and narrative tailored to your specific playthrough.

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
Add a template to any file in `data/npcs/`. Enable `"npc_system": true` in `config.FEATURES` if not already on.

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
    "species_system":     True,   # Species selection + evolution paths
    "alignment_system":   True,   # Alignment float + alignment-gated content
    "npc_system":         True,   # Named NPCs with memory + stat-gated dialogue
    "quest_system":       True,   # Quest state machine + completion tracking
    "guild_system":       True,   # Guild membership, ranks, perks
    "faction_system":     True,   # Faction politics, standing, ascension
    "auction_house":      True,   # Auction listings, competing guilds, life tokens
    "lives_system":       True,   # 9-lives death mechanic (replaces instant game-over)
    "crafting_system":    True,   # Recipe + material crafting
    "stat_gating":        True,   # INT/Perception/LCK/WIS content gates
    "world_db":           True,   # SQLite world state (required by npc/quest systems)
    "synergy_system":     True,   # Stat synergy bonuses in combat
    "bot_system":         True,   # AI-driven autonomous bot agents
}
```

Enable order matters: `world_db` must be `True` before `npc_system`, `quest_system`, or `faction_system`. All other systems can be toggled independently.

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

# Run the automated character test suite
# Validates registries, scene graph, triggers, character playthroughs, save/load,
# inventory, combat, BG-thread lifecycle, feature flags, and quest lifecycle
python -X utf8 test_characters.py

# Run the dedicated quest test suite
# 14 sections covering schema integrity, NPC giver references, lifecycles for
# 1-/3-/4-stage quests, concurrent quests, failure conditions, time limits,
# AI quest DB round-trip, and trigger format resolution
python -X utf8 test_quests.py

# Start a feature branch
git checkout -b feature/my-feature
# Enable the flag (if any), build, test, then disable before merging if not ready
```

`test_characters.py` (47 checks across 10 sections) covers:
- All skills, items, classes, and NPC data load without errors
- Every scene `leads_to` reference points to a real scene and node
- Every `give_item` / `give_skill` trigger references a real ID
- Three simulated character playthroughs (warrior, divergent, mage) including save/load round-trip
- Player model edge cases, inventory API, combat (auto-resolve + loot)
- Background generator thread lifecycle (start/stop/restart)
- Feature-flag toggling
- Quest lifecycle: start → tick advance → completion → rewards

`test_quests.py` (53 checks across 14 sections) covers:
- Schema integrity for every quest template (stage chains, terminal stages, conditions)
- NPC giver references resolve and dialogue triggers reference real templates
- Reward references (items, factions, guilds) point to real entities
- Full lifecycles for 2-stage, 3-stage, and 4-stage quests
- Three concurrent quests where partial completion doesn't disturb others
- Failure conditions and time-limit expiration both fail the quest correctly
- AI quest data DB round-trip (`store_ai_quest` → `get_quest` → JSON intact)
- Event payload shapes (`QUEST_STARTED` / `QUEST_ADVANCED` / `QUEST_COMPLETED`)
- Trigger format resolution (`advance_quest:TEMPLATE_ID`, `complete_quest:TEMPLATE_ID`)

Both suites run automatically via the `.git/hooks/pre-push` git hook before every push.

To install the hook locally (collaborators run this once after cloning):

```bash
# macOS / Linux / Git Bash
./scripts/install-hooks.sh

# Windows / PowerShell
.\scripts\install-hooks.ps1
```

See **CLAUDE.md** for the full developer reference: trigger strings, gate syntax, event bus events, SQLite table descriptions, and AI integration contracts.

---

## Roadmap

### Current stage: **Phase 3 — Quest system shipped, polish & content next**

The full feature surface from the original design is now wired and player-facing. The
project moves from "build new systems" into "deepen existing ones" — more quest content,
more NPCs that offer them, and richer AI integration on top of the working backbone.

| Feature | Status | Notes |
|---------|--------|-------|
| Core game loop, scenes, classes, skills | ✅ Shipped | |
| AI-generated unique classes (Ollama) | ✅ Shipped | Divergence ≥ 30 triggers generation |
| Background AI world generation | ✅ Shipped | Worker thread; non-blocking |
| Dual-model Ollama (slow + fast) | ✅ Shipped | `mistral-nemo` + `gemma3:1b` |
| NPC system (memory, stat-gated dialogue) | ✅ Shipped | |
| Species + alignment systems | ✅ Shipped | |
| Stat gating | ✅ Shipped | |
| Interactive AI situational options (`[?]`) | ✅ Shipped | Uses fast model |
| WorldDirector (off-thread) | ✅ Shipped | Submits to BG queue, never blocks |
| **Quest system (state machine + UI + tests)** | ✅ **Shipped** | 9 templates, `[J] Quest Journal`, `start_quest:` / `advance_quest:` / `complete_quest:` triggers |
| Guild system | ✅ Shipped | Membership, ranks, perks, found-a-guild flow |
| Faction system | ✅ Shipped | Standing, rank changes, `update_faction:` trigger |
| Auction house + life tokens | ✅ Shipped | Listings, bids, life-token purchase |
| 9-lives death mechanic | ✅ Shipped | Replaces instant game over |
| Crafting system | ✅ Shipped | Recipes + materials, `[C] Craft` menu |
| Bot agents (autonomous AI players) | ✅ Shipped | Inspectable via admin panel |
| Test suite (`test_characters` + `test_quests`) | ✅ Shipped | 100 checks, runs on every push |

### Next up — content & polish

| Item | Why |
|------|-----|
| Wire more NPCs to existing quest templates | Six of the nine templates still have no fixed NPC giver — `dungeon_survey`, `verath_courier`, `fracture_investigation` and the `null`-giver faction quests would benefit from explicit dialogue hooks (the implicit AI-quest path also covers them now, but hand-crafted offers play better) |
| Move class-generation off the main thread | `class_system.resolve_combo_class` now uses `AIService` but still calls synchronously. Switch to `submit_class_generation_async` once the awakening scene can show "the System is revealing your fate..." while it polls |
| Pre-push hook installer | The hook is local-only; collaborators need a `scripts/install-hooks.sh` |
| Refactor `core/game_engine.py` (1000+ lines) | God-object even after the mixin split — every mixin freely reads `self.state`/`self.ai_service`/`self.quest_registry`. Composition over inheritance would help |

### Recently shipped (this iteration)

- **AI quest generation wired into NPC dialogue.** Picks up `quest_seeds` with `ai_dynamic` templates AND implicitly offers an AI quest when an NPC has no seeds left and disposition ≥ `AI_QUEST_DISPOSITION_MIN`. `« Is there any work I could take on? »` option injects dynamically.
- **Save migration v2 → v3 is no longer a silent no-op.** `_migrate` now runs explicit per-version functions, validates the result against the Player model, and raises `SaveMigrationError` when a step is missing.
- **`AIService` facade.** Single boundary `systems/` imports for AI generation — centralises try/except, fallback paths, and `is_available` gating. Systems no longer call `ContentGenerator` directly.
- **Quest reward currency cap** — `reward_gold` is now hard-bounded to ≤ `MAX_QUEST_REWARD_GOLD` (5000). Hallucinating LLMs get clamped; content authors typing copper by mistake get a loud `ValidationError`.
- **Class resolver Layer 2 short-circuit fixed** — no longer grants combos when item/flag requirements are missing.
- **Trigger processing extracted from `Scene`** — `scenes/option_logic.py` holds the free functions; `Scene` shrank from 295 → 72 lines.
- **`world_db.py` split into per-table repos** — `persistence/repos/{npc,quest,faction,auction,death,world_state,ai_content,bot,guild_db}_repo.py`. `WorldDatabase` shrank from 1100 → 787 lines and is now a thin facade.
- **AI class generation off the main thread** — `AIService.submit_class_generation_async()` returns a `Future`; the Class Awakening scene can poll it while the Rich spinner animates.
- **Bots surface in-world** — arrivals/departures and same-zone actions print to the player's view, not just the admin panel.
- **Pre-push hook installer** — `scripts/install-hooks.sh` (POSIX) and `.ps1` (Windows) for collaborators.
- **`core/game_engine.py` slimmed 1049 → 237 lines (-77%).** Extracted to: `core/bootstrap.py` (load_registries + setup_ai), `core/background_integrator.py` (BG result drain), `core/situation_query.py` (the AI «Ask about this situation» handler), `core/menu_flow.py` (main / load / new game menus), `core/game_loop.py` (per-turn tick loop), `core/bg_scheduler.py` (decides which AI content to submit each turn), `core/input_handler.py` (per-turn option building + hotkey dispatch via a data-driven `_HOTKEYS` table), `ui/auction_ui.py` (interactive auction house loop). Each module is independently importable and unit-testable. The integrator extraction also caught and fixed a tangled elif chain that was making bot trade/rest/craft/talk_npc actions silently fall through. test_characters.py Section 19 includes a soft budget guard (game_engine.py stays under 600 lines).
