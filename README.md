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
- [Ollama](https://ollama.ai) — **two models** working together (see below) for AI content generation
- SQLite for world state (NPC memory, quests, factions)

---

## Quick Start

### 1. Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.ai)** installed and running locally. Optional, but the game is much richer with it on.

### 2. Pull the Ollama models

The game uses a **dual-model setup**:

| Model | Role | Size | Required? |
|---|---|---|---|
| `mistral-nemo` | **Primary** — heavy generation: classes, quests, NPC dialogue branches, world events | ~7 GB | recommended |
| `gemma3:1b` | **Fast** — interactive calls (the «Ask about this situation» prompt, anything player-facing where latency matters) | ~800 MB | recommended |

```bash
ollama pull mistral-nemo
ollama pull gemma3:1b
```

Both fall back gracefully:
- If only `mistral-nemo` is pulled, the fast model alias points back to the primary (slower interactive prompts, still works)
- If Ollama isn't running at all, every AI call returns a fallback (hand-crafted classes, static dialogue, no dynamic quests). The game stays fully playable.

### 3. Install Python dependencies

```bash
cd Game_Test
pip install -r requirements.txt
```

### 4. Run the game

```bash
python main.py
```

You'll see `AI system online. Ollama connected.` on the title screen if everything is wired correctly. If Ollama isn't reachable you'll see `Ollama not available — AI features disabled.` and the game will continue with the static fallback path.

### 5. (Optional) Install the pre-push test hook

If you plan to push commits, install the local git hook that runs the test suite before every push:

```bash
# macOS / Linux / Git Bash
./scripts/install-hooks.sh

# Windows / PowerShell
.\scripts\install-hooks.ps1
```

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
AI_ENABLED = True                          # master switch — set False to disable all Ollama calls
OLLAMA_BASE_URL = "http://localhost:11434" # default Ollama port
OLLAMA_MODEL = "mistral-nemo"              # PRIMARY — used for heavy generation (classes, quests, NPC branches)
OLLAMA_FAST_MODEL = "gemma3:1b"            # FAST — interactive «Ask…» queries. Set "" to reuse primary.
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"  # used if primary fails to load

OLLAMA_TIMEOUT_JSON = 60   # seconds for structured (JSON) generation on primary
OLLAMA_TIMEOUT_FAST = 15   # seconds for fast-model interactive calls
OLLAMA_TIMEOUT_TEXT = 20   # seconds for free-form narrative generation
OLLAMA_MAX_RETRIES = 3

DIVERGENCE_THRESHOLD = 30          # score that triggers AI class generation
AI_QUEST_DISPOSITION_MIN = 30.0    # NPC disposition needed for an implicit AI-quest offer
```

If Ollama is offline or `AI_ENABLED = False`, every AI call silently falls back to the nearest pre-written class from `data/classes/combo_classes.json`. NPC dialogue uses its `dialogue_hooks["default"]`. The dynamic quest path simply skips its option.

### Verifying the AI is wired up

After a fresh install you can do a non-interactive sanity check without launching the game:

```bash
python -c "from ai.ollama_client import OllamaClient; print(OllamaClient(model='mistral-nemo').is_available())"
```

Prints `True` if Ollama is reachable and the model is pulled.

---

## Development

```bash
# Quick syntax check (no game launch needed)
python -c "import py_compile; py_compile.compile('main.py', doraise=True)"

# Main test suite — runs on every push via the pre-push hook
python -X utf8 test_characters.py     # 111 checks across 23 sections
python -X utf8 test_quests.py         #  60 checks across 15 sections

# Supplementary suites — run manually
python -X utf8 test_runs.py                       # 74 checks (older run suite)
python -X utf8 tests/test_db_migrations.py        # 10 checks (SQLite schema)
python -X utf8 tests/test_background_tick.py      #  9 checks (BG worker)
python -X utf8 tests/test_guild_betrayal.py       # 22 checks
python -X utf8 tests/test_guild_founding.py       # 10 checks

# Headless smoke test — bootstrap the engine without launching the UI
python -c "from core.game_engine import GameEngine; e = GameEngine(); e.bootstrap(); print('OK')"

# Start a feature branch
git checkout -b feature/my-feature
```

**Combined test count: 296 checks across 7 test files.**

`test_characters.py` (111 checks, 23 sections) covers:
- Registry loading (skills, items, classes, NPCs, scenes)
- Scene-graph link / trigger validation
- Three simulated character playthroughs (warrior, divergent, mage) with save/load round-trip
- Player model edge cases, inventory API, combat (auto-resolve + loot, lvl-up)
- Background generator thread lifecycle (start/stop/restart)
- Feature-flag toggling
- Quest lifecycle: start → tick advance → completion → rewards
- Save migration v1 → v2 → v3 (with `SaveMigrationError` for missing steps)
- AIService facade (sync + async class-gen Future, exception handling)
- Quest reward currency cap (validates `MAX_QUEST_REWARD_GOLD = 5000`)
- Class resolver Layer 2 superset-match bug fix regression guard
- Free-function trigger processing (`scenes/option_logic.py`)
- Every extracted module's exports (game_engine, bootstrap, integrator, etc.)
- Bot action handlers all fire (regression guard for the elif-chain bug)
- Hotkey table integrity in `input_handler`
- Soft budget guard: `core/game_engine.py` must stay under 250 lines

`test_quests.py` (60 checks, 15 sections) covers:
- Schema integrity for every quest template (stage chains, terminal stages, conditions)
- NPC giver references resolve; dialogue triggers reference real templates
- Reward references (items, factions, guilds) point to real entities
- Full lifecycles for 2-stage, 3-stage, and 4-stage quests
- Three concurrent quests where partial completion doesn't disturb others
- Failure conditions + time-limit expiration both fail the quest correctly
- AI quest data DB round-trip
- Event payload shapes
- Trigger format resolution
- Dialogue quest-seed injection (concrete seeds, `ai_dynamic` seeds, implicit AI offers)

Both main suites run automatically via `.git/hooks/pre-push` before every push.

See **CLAUDE.md** for the full developer reference: trigger strings, gate syntax, event bus events, SQLite table descriptions, the `AIService` boundary contract, and the directory map.

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
- **`core/game_engine.py` slimmed 1049 → 165 lines (-84%) — now a genuine coordinator.** Extracted to: `core/bootstrap.py` (load_registries + setup_ai), `core/background_integrator.py` (BG result drain), `core/situation_query.py` (the AI «Ask about this situation» handler), `core/menu_flow.py` (main / load / new game menus), `core/game_loop.py` (per-turn tick loop), `core/bg_scheduler.py` (decides which AI content to submit each turn), `core/input_handler.py` (per-turn option building + hotkey dispatch via a data-driven `_HOTKEYS` table), `ui/scene_renderer.py` (per-turn scene rendering), `ui/auction_ui.py` (interactive auction house loop), `systems/rest_system.py` (camp rest with ambush rolls), `systems/faction_endings.py` (political ending path triggers). Each module is independently importable and unit-testable. The integrator extraction also caught and fixed a tangled elif chain that was making bot trade/rest/craft/talk_npc actions silently fall through. The final extraction also fixed a latent crash in `_setup_notifications` (missing import). test_characters.py Section 19 includes a soft budget guard — `game_engine.py` must stay under 250 lines.
