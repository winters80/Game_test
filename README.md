# SYSTEM BREAKER

> A LitRPG terminal RPG where the universe watches your choices.

The System appeared during the Fracture and rewrote the rules of the world. Most survivors got a class, a rank, and an expected path. You didn't — or you refused yours. Now the System is paying attention, and **Ollama** is generating your story in real time.

---

## What Is This?

SYSTEM BREAKER is a Python terminal RPG inspired by progression fantasy. The defining mechanic: when you take unexpected paths — unusual stat combinations, refusing the System's assignments, carrying rare catalyst items — an on-device AI (Ollama) generates a unique class, skills, and narrative tailored to your specific playthrough.

**No two divergent paths produce the same class.**

**The world grows around what you do.** Follow the script, or type your own questions with `[?]`. The fast model answers on the spot. When you *act* on something new (mine a gold seam, forage strange mushrooms, carve a trail marker), the primary model quietly builds the world around it: new items and recipes, a trader who buys what you gather, sometimes a new skill. Every save grows its own world.

Built with:
- Python 3.11 + Pydantic v2 (all data models)
- [Rich](https://github.com/Textualize/rich) for terminal UI
- [questionary](https://github.com/tmbo/questionary) for arrow-key menus
- [Ollama](https://ollama.ai) — **two models** working together (see below) for AI content generation
- SQLite for world state (NPC memory, quests, factions)

---

## Quick Start

### 1. Install the prerequisites (once)

- **Python 3.11+**: https://www.python.org/downloads/
- **Ollama**: https://ollama.com/download. It's optional, but without it there's no AI: no unique classes, no `[?]` answers, no world growth.

### 2. Start the game (recommended)

The launcher makes sure everything is ready every time you play:

```bash
# Windows: double-click start.bat, or from PowerShell:
.\scripts\start.ps1

# macOS / Linux / Git Bash
./scripts/start.sh
```

It will:
1. install the Python dependencies if any are missing
2. **start Ollama** if it isn't already running
3. **download the two models** from `config.py` if they aren't there yet (a one-time download of about 8 GB)
4. launch the game, which then **preloads both models into memory** in the background, so your first `[?]` question doesn't time out on a cold model

Add `--check` (`-Check` on Windows) to do steps 1–3 without launching the game.

On the title screen you should see **`AI system online. Ollama connected.`** The admin panel (`[A]`) shows **`AI: online`**.

### 3. Or start it by hand

```bash
pip install -r requirements.txt

ollama serve                # or open the Ollama app (Windows/macOS start it in the tray)
ollama pull mistral-nemo    # primary model (~7 GB)
ollama pull gemma3:1b       # fast model (~800 MB)

python -X utf8 main.py
```

The game uses a **dual-model setup**:

| Model | Role | Size | Required? |
|---|---|---|---|
| `mistral-nemo` | **Primary**: heavy generation in the background (classes, quests, NPC dialogue branches, world events, and **world growth**: the items, recipes, traders and skills that appear in response to your actions) | ~7 GB | recommended |
| `gemma3:1b` | **Fast**: interactive calls (the `[?]` «Ask about this situation» prompt, anything player-facing where latency matters) | ~800 MB | recommended |

### If the AI shows as offline

The game tells you what's wrong at startup:

| Message | Fix |
|---|---|
| `Ollama not available … Nothing is answering at http://localhost:11434` | Ollama isn't running. Open the Ollama app, run `ollama serve`, or use the launcher |
| `Ollama is running, but the model 'mistral-nemo' isn't downloaded` | `ollama pull mistral-nemo` (or run the launcher) |
| `Fast model 'gemma3:1b' isn't downloaded; using 'mistral-nemo'…` | AI still works, but `[?]` is slower. `ollama pull gemma3:1b` |

Without Ollama the game stays fully playable, with hand-crafted classes, static dialogue, no dynamic quests and no world growth. Actions you take are still logged, and the world catches up the next time you load that save with Ollama running. Ollama on another machine or port? Set the `OLLAMA_BASE_URL` environment variable (the launchers and the game both read it).

### 4. (Optional) Install the pre-push test hook

If you plan to push commits, install the local git hook that runs the test suite before every push:

```bash
# macOS / Linux / Git Bash
./scripts/install-hooks.sh

# Windows / PowerShell
.\scripts\install-hooks.ps1
```

### Updating

```bash
git pull
```

Your saves upgrade automatically when you load them.

---

## Project Layout

```
Game_Test/
├── main.py                 # Entry point
├── start.bat               # Windows: double-click to play (runs scripts/start.ps1)
├── scripts/                # start.ps1 / start.sh launchers, git hook installers
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

### Controls

- **Arrow keys** to navigate menus, **Enter** to confirm
- **`[?]`** Ask about this situation: type a question or an action in your own words
- **`[K]`** Skills · **`[I]`** Items & equipment · **`[J]`** Quest journal · **`[L]`** World log
- **`[C]`** Craft · **`[G]`** Found a guild
- **`[S]`** Save · **`[A]`** Admin panel (AI token usage, feature flags, **World Growth log**) · **`[Q]`** Quit to menu

### The world

- **Prologue and the Classification Rite:** choose gender, species and background, then the System assigns your class, unless you diverge (see below).
- **Verath's outer market** is your hub: the Adventurers' Guild, Torven's forge, the Iron Compass Inn, a cloaked wanderer, and the road to the dungeon.
- **The Old Dungeon** has three floors, down to the Fracture Core, where you can seize the System (**The Throne**), destroy it (**The Break**), or walk away. Which endings are open depends on your standing with the Verath Crown and the System Breakers, or on completing their quest lines.
- **The City of Verath** sits behind a guarded gate. It opens when you clear the dungeon's first floor (both fights, then report to the Guild survey post) or reach Floor 2. There are also unconventional ways in, which count as divergent: bribe the guard, talk your way past (INT 12), stow away on a cart (LCK 12), follow the Gray Wanderer through a gap in the System's sight, or find your own way with `[?]`.

### Asking the System, and how the world grows

1. Press **`[?]`** and type anything: *"What do I see?"*, *"Can I climb that wall?"*, *"Mine the gold."*
2. The **fast model** describes the situation and offers 1–2 new options.
3. Pick one. If it's something you haven't done before, you'll see **✦ DISCOVERY**: the System has logged it.
4. In the background, the **primary model** decides how the world responds. A few moments later you might see:
   - **✦ NEW ITEM DISCOVERED**: Gold Ore, added to your pack
   - **✦ NEW RECIPE**: Gold Ingot (craft it with `[C]`)
   - **✦ WORD SPREADS**: Brenna now trades in the outer market
   - **✦ NEW SKILL**, when the action is a technique worth learning
5. Doing the same thing again later gathers what it produces (at most once every 10 turns).

Everything the AI adds is validated and price-capped. It can't hand out gold, gear, quest items, classes or story flags, and it can't overwrite anything hand-written. Generated traders only appear in safe places: the outer market, Verath, the roadside camp, or travelling the road between them. The admin panel's **World Growth log** shows every action you've taken and what it added.

### Trading

Traders offer **« Browse wares »** and **« Sell items »** in conversation. **Wren**, a travelling pedlar, walks between the outer market, the camp and Verath, buying materials and consumables. **Torven** buys weapons and armour. AI-generated traders appear as the world grows. You can never sell the last copy of something you have equipped.

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

Both files must be present to load a save. The game handles migration automatically when either format changes: the player JSON has its own save version, and the world database has its own schema version (currently 6), both upgraded on load.

Each save has its **own grown world**. Items, recipes, traders and skills the AI created for one save are stored in that save's `.db`, restored when you load it, and never appear in another save.

---

## AI Configuration

All AI settings are in `config.py`:

```python
AI_ENABLED = True                          # master switch — set False to disable all Ollama calls
OLLAMA_BASE_URL = "http://localhost:11434" # default Ollama port (override with the OLLAMA_BASE_URL env var)
OLLAMA_MODEL = "mistral-nemo"              # PRIMARY — used for heavy generation (classes, quests, NPC branches)
OLLAMA_FAST_MODEL = "gemma3:1b"            # FAST — interactive «Ask…» queries. Set "" to reuse primary.
OLLAMA_FALLBACK_MODEL = "mistral:7b-instruct"  # used if primary fails to load

OLLAMA_TIMEOUT_JSON = 60   # seconds for structured (JSON) generation on primary
OLLAMA_TIMEOUT_FAST = 15   # seconds for fast-model interactive calls
OLLAMA_TIMEOUT_TEXT = 20   # seconds for free-form narrative generation
OLLAMA_MAX_RETRIES = 3
OLLAMA_KEEP_ALIVE = "30m"   # keep models loaded between calls (Ollama's default unloads after 5m)
OLLAMA_PRELOAD = True       # load both models into memory at startup, in the background

DIVERGENCE_THRESHOLD = 30          # score that triggers AI class generation
AI_QUEST_DISPOSITION_MIN = 30.0    # NPC disposition needed for an implicit AI-quest offer

# World growth (typed actions → new world content)
WORLD_YIELD_COOLDOWN_TURNS = 10        # turns before repeating an action yields again
WORLD_MAX_TRADERS_PER_LOCATION = 2     # extra goods join an existing trader's stock
WORLD_MAX_EXPANSIONS = 60              # per save
WORLD_RETRY_ON_LOAD = 3                # unfinished actions re-queued per load

# What AI-generated options may do (everything else is dropped)
AI_GRANTABLE_FLAGS = frozenset({"verath_access"})
AI_MAX_ALIGNMENT_SHIFT = 5.0
AI_GIVEABLE_ITEM_TYPES = frozenset({"CONSUMABLE", "MATERIAL"})
```

If Ollama is offline or `AI_ENABLED = False`, every AI call silently falls back to the nearest pre-written class from `data/classes/combo_classes.json`. NPC dialogue uses its `dialogue_hooks["default"]`. The dynamic quest path simply skips its option. World growth records your actions as `no_ai` and retries them the next time you load with Ollama available.

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

# Every push runs ALL 7 test files via the pre-push hook
python -X utf8 test_characters.py                  # 150 checks across 29 sections
python -X utf8 test_quests.py                      # 132 checks across 23 sections
python -X utf8 test_runs.py                        #  74 checks (older run suite)
python -X utf8 tests/test_db_migrations.py         #  12 checks (SQLite schema, v6)
python -X utf8 tests/test_background_tick.py       #   9 checks (BG worker)
python -X utf8 tests/test_guild_betrayal.py        #  22 checks
python -X utf8 tests/test_guild_founding.py        #  10 checks

# Headless smoke test — bootstrap the engine without launching the UI
python -c "from core.game_engine import GameEngine; e = GameEngine(); e.bootstrap(); print('OK')"

# Live playthrough — drives the engine the way a player would, asserts
# quest rewards / skill generation / save round-trip end-to-end. Requires Ollama.
python -X utf8 playthrough_smoke.py                #  24 checks

# Start a feature branch
git checkout -b feature/my-feature
```

**Combined test count: 409 checks across 7 test files (all gated by the pre-push hook).**

`test_characters.py` (150 checks, 29 sections) covers:
- Registry loading (skills, items, classes, NPCs, scenes)
- Scene-graph link / trigger validation
- Three simulated character playthroughs (warrior, divergent, mage) with save/load round-trip
- Player model edge cases, inventory API, combat (auto-resolve + loot, lvl-up)
- Background generator thread lifecycle (start/stop/restart)
- Feature-flag toggling
- Quest lifecycle: start → tick advance → completion → rewards
- Save migration v1 → v2 → v3 → v4 (with `SaveMigrationError` for missing steps + disk-fixture round-trip)
- AIService facade (sync + async class-gen Future, exception handling)
- Quest reward currency cap (validates `MAX_QUEST_REWARD_GOLD = 5000`)
- Class resolver Layer 2 superset-match bug fix regression guard
- Free-function trigger processing (`scenes/option_logic.py`)
- Every extracted module's exports (game_engine, bootstrap, integrator, etc.)
- Bot action handlers all fire (regression guard for the elif-chain bug)
- Hotkey table integrity in `input_handler`
- Soft budget guard: `core/game_engine.py` must stay under 200 lines
- Every class's starting **and learnable** skills exist in the registry
- `AIService` boundary guard: nothing outside `ai/` (bar `core/bootstrap.py`) touches `ContentGenerator` or raw Ollama clients
- **Verath gate:** every scene reachable from the prologue; `flags_any` / `min_gold` / `lock_reason` gates; every normal and divergent route in
- **AI trigger allow-list:** gold, skills, classes, quests and gear dropped; flags namespaced to `ai_*`; items, alignment and combat limited
- **Traders:** travelling routes, ambient "Talk to …" options, buying and selling, equipped-item protection, runtime NPCs and recipes, the crafting-quantity fix
- **World growth end-to-end** (fake primary model through the real background worker, integrator, registries and DB): discovery, clamped bundle, reward, trader presence, yield cooldown, per-save isolation, offline behaviour
- **World growth guard rails:** trader cap with stock merging, expansion cap, retry on load, admin World Growth log
- **Ollama startup:** the right message for "server down" / "model missing", fallback to the primary model, background preload

`test_quests.py` (132 checks, 23 sections) covers:
- Schema integrity for every quest template
- NPC giver references resolve; dialogue triggers reference real templates
- Reward references (items, factions, guilds) point to real entities
- Full lifecycles for 2-stage, 3-stage, and 4-stage quests
- Concurrent quests, failure conditions, time-limit expiration
- AI quest data DB round-trip + event payload shapes
- Trigger format resolution
- Dialogue quest-seed injection (concrete seeds, `ai_dynamic`, implicit AI offers)
- **Economy formula** — `recommended_quest_reward(level, difficulty)` tier ladder + clamp regression
- **Skill audit fixes** — validators on mp/cd/scaling_coefficient, dup-id resolution, `give_skill:` auto-create has effects, `_build_skill_from_stub` parses `effect_hint`
- **Skill design (F3–F9)** — passive aggregator stacking + caps, TRIGGERED `on_attack`/`on_kill` firing, combat-turn cooldowns, skill leveling via use count, `grant_next_learnable_skill`
- **Contextual AI skill generation** — `AISkillResponse` clamping, `_generate_or_default_skill` AI/fallback, quest `reward_skill_hints` registered + granted
- **AI quest skill rewards** — AIQuestResponse parses/clamps/coerces `reward_skill_hints`; hand-crafted demos (`blacksmith_hammer` → 'Forge-Born Strike', `theft_investigation` → 'Hushed Step')
- **Polish items** — `SKILL_LEVELED_UP` listener wired, stat-points menu present, `_inspect_existing_skill` helper, trainer NPC option on Torven, NPC-dialogue start paths for `dungeon_survey` / `fracture_investigation` / `verath_courier`, v1 → v4 chained migration

`playthrough_smoke.py` (24 checks) — headless live playthrough with real Ollama:
- Engine bootstrap (registries, AIService, BG generator, WorldDirector, bot manager)
- Character creation
- 2-stage quest lifecycle (`blacksmith_hammer`) with reward gold/XP/flag/**AI-generated skill** assertions
- 3-stage quest lifecycle (`theft_investigation`) + level-up cascade
- Quest failure path
- Save → close → load → verify gold + completed quests + flags + skill_uses persistence
- BG task submission + scheduler tick
- Live AI quest generation through `AIService` with level-scaled reward clamping

All 7 test files run automatically via `.git/hooks/pre-push` before every push (`playthrough_smoke.py` is optional — needs Ollama).

See **CLAUDE.md** for the full developer reference: trigger strings, gate syntax, event bus events, SQLite table descriptions, the `AIService` boundary contract, and the directory map.

---

## Roadmap

### Current stage: **Phase 4: the world grows around the player**

The AI-as-co-author premise now reaches the world itself. Classes, quests and
skills all flow through one `AIService` boundary with contextual prompts,
Pydantic-validated outputs and level-scaled clamping. On top of that, the
player's own typed actions make the primary model add items, recipes, traders
and skills, per save, behind a strict allow-list. The next step is tuning the
world-growth prompts against real Ollama playthroughs.

| Feature | Status | Notes |
|---------|--------|-------|
| Core game loop, scenes, classes, skills | ✅ Shipped | |
| AI-generated unique classes (Ollama) | ✅ Shipped | Divergence ≥ 30 triggers generation; opt-in `rich_skills=True` for per-skill AI gen |
| Background AI world generation | ✅ Shipped | Worker thread; non-blocking |
| Dual-model Ollama (slow + fast) | ✅ Shipped | `mistral-nemo` + `gemma3:1b` |
| NPC system (memory, stat-gated dialogue) | ✅ Shipped | |
| Species + alignment systems | ✅ Shipped | |
| Stat gating | ✅ Shipped | |
| Interactive AI situational options (`[?]`) | ✅ Shipped | Uses fast model |
| WorldDirector (off-thread) | ✅ Shipped | Submits to BG queue, never blocks |
| Quest system (state machine + UI + tests) | ✅ Shipped | 9 templates, `[J] Quest Journal`, `start_quest:` / `advance_quest:` / `complete_quest:` triggers |
| **Contextual AI skill generation** | ✅ **Shipped** | `give_skill:` / quest `reward_skill_hints` / Inspect — all flow through `AIService.generate_skill` with player + source + scene context |
| **Skill leveling via use count + auto-learn on level-up** | ✅ **Shipped** | F8/F9. Lvl 1-10 per skill, `+1 base / +10% scaling` per level |
| **Passive skill aggregator (F3)** | ✅ **Shipped** | 9 previously-decorative passives now contribute defense / dodge / crit / utility tags with stack caps |
| **Level-scaled quest reward economy** | ✅ **Shipped** | `systems/economy.py` — `gold = 20 × level × tier_mult`, clamped per level |
| Guild system | ✅ Shipped | Membership, ranks, perks, found-a-guild flow |
| Faction system | ✅ Shipped | Standing, rank changes, `update_faction:` trigger |
| Auction house + life tokens | ✅ Shipped | Listings, bids, life-token purchase |
| 9-lives death mechanic | ✅ Shipped | Replaces instant game over |
| Crafting system | ✅ Shipped | Recipes + materials, `[C] Craft` menu |
| Bot agents (autonomous AI players) | ✅ Shipped | Arrivals/departures + in-zone actions surface in-world |
| **Trainer NPC pattern** | ✅ **Shipped** | Torven offers `give_skill:torven_forge_lesson` after the hammer quest — AI generates the skill contextually |
| **Inspect UI flow** | ✅ **Shipped** | `[K]` menu → "✦ Inspect an unknown skill" (gated by Inspect passive) → player describes a skill → AI materialises it |
| Pre-push hook (all 7 test files) | ✅ Shipped | 409 checks gated; `scripts/install-hooks.sh`/`.ps1` for collaborators |
| Test suite | ✅ Shipped | 409 checks across 7 files + 24-check live playthrough |
| **World growth** | ✅ **Shipped** | Typed `[?]` actions are tagged; the primary model grows the world around new ones (items, recipes, traders, skills), validated + economy-clamped, per save. See CLAUDE.md "World Growth" |
| **AI trigger allow-list** | ✅ **Shipped** | AI-authored options can't give gold, gear, classes, quests or story flags (`systems/ai_trigger_policy.py`) |
| **Traders** | ✅ **Shipped** | Buy/sell menus, travelling traders (Wren), AI-generated traders in the outer market / Verath / camp / road |
| **Verath gate** | ✅ **Shipped** | Capital unlocks by clearing Floor 1 or via divergent routes (bribe, INT, LCK, the Wanderer, AI) |

### Next up — long-tail

| Item | Why |
|------|-----|
| Tune world-growth prompts on live Ollama | The loop is tested end-to-end with a fake model; real `mistral-nemo` / `gemma3:1b` output quality still needs a playthrough |
| Fire `on_hit` / `on_low_hp` TRIGGERED skills | Combat only dispatches `on_attack` / `on_kill`, so Battle Mage's starting skill Arcane Strike never fires |
| Wire the three `null`-giver faction quests | `shadow_errand`, `crown_ascension`, `system_break_mission` only reachable via implicit AI offer or scene triggers — would benefit from faction-standing-driven unlock |
| Move class-generation off the main thread | The `Future`-based submission path exists (`AIService.submit_class_generation_async`); the Class Awakening scene still calls it synchronously |
| Cross-skill awareness in the AI prompt | LLM sees `player.skills` but doesn't reason about which new skill would synergize best |
| `GameEngine` mixin → composition refactor | Low priority — 169 lines, budget-guarded. Working fine. |
| `world_db.py` further split | Per-table repos already cover queries; further splitting the facade is mechanical with low payoff |

### Recently shipped (PRs #30 and #32)

**The world grows around the player:**
- **World growth**: every `[?]` option carries a `world_action:VERB:SUBJECT` tag. New actions queue a background expansion. The primary model's bundle (yield item, derived items, recipes, trader, optional skill) is made safe: `gen_` ids, materials and consumables only, values clamped by `economy.py`, recipes checked, and traders who sell at 1–3× value. It's stored per save (world DB v6), and repeating an action gathers its yield.
- **Guard rails**: 2 generated traders per location (extras merge into an existing trader's stock), 60 expansions per save, retry on load, and an admin **World Growth log**.
- **Traders**: `trades` / `ambient` / `route` NPC fields, « Browse wares » / « Sell items », Wren the travelling pedlar, and Torven buying gear.
- **AI trigger allow-list**: AI options and AI NPC dialogue can only set `ai_*` flags, give one cheap consumable or material, nudge alignment ±5, or start a known fight.
- **Verath gate**: the capital was unreachable. It now opens by clearing Floor 1 or through five divergent routes. The start is reworded as "Verath's outer market".

**Content and systems:**
- **28 missing class skills added**: Priest, Ranger and every combo class were stuck on their starting skills. Auto-learn now skips unknown ids instead of stalling.
- **`AIService` boundary enforced in `core/`**: 13 call sites moved behind the facade, with a guard test.

**Bug fixes:**
- `flags_any` was never evaluated, so the Floor 3 door to both endings was always open.
- Crafting checked that ingredients were present, not their quantity, so a 2-herb recipe worked with 1.
- 66 double-encoded em dashes (`â€”`) in NPC dialogue.
- CLAUDE.md trigger reference was out of date (11 undocumented triggers; `give_gold` is copper).

### Earlier (audit arc — 18 PRs from #9 to #26)

**AI as backbone:**
- **Contextual AI skill generation** — `give_skill:X` (when ID unknown), quest `reward_skill_hints`, and the player-facing Inspect flow all funnel through `ai_service.generate_skill(player, name_hint, source, context, has_inspect)`. Pydantic-clamps mp/cd/scaling so a hallucinating LLM can't break the economy. Same hint + same player = different output based on `has_inspect` (rich tooltip vs in-fiction prose).
- **AI quests propose `reward_skill_hints`** — the LLM is taught about the optional field in its prompt; the validator coerces every output shape (`["A"]`, `[{"name":"A"}]`, `"A"`).
- **AI class generation contextual** — opt-in `rich_skills=True` puts every class skill stub through `generate_skill` instead of the deterministic builder.
- **AI quest generation wired into NPC dialogue** — `quest_seeds` injection, plus implicit "I might have work for you" offer when disposition ≥ `AI_QUEST_DISPOSITION_MIN`.
- **`AIService` facade** — single boundary `systems/` imports; centralises try/except, fallback paths, `is_available` gating.
- **Async class-gen Future** — `AIService.submit_class_generation_async` returns a `Future` the awakening scene can poll while the Rich spinner animates from its own thread.

**Skill system (audit + design):**
- **Passive aggregator (F3)** — 9 previously-decorative passives (Iron Skin, Evasion, etc.) now contribute attack / defense / dodge / crit / utility-tags with hard caps (dodge 40%, crit 30%, etc.).
- **Combat-turn cooldowns (F5)** — `reset_cooldowns_for_combat` at fight start, `tick_combat_cooldowns` per round. No more "5-CD spell refreshed by walking 5 menu steps in town".
- **TRIGGERED skills fire (F6)** — `try_fire_trigger(player, "on_attack" / "on_kill", sr)` from `combat_system`. Filtered out of the active menu.
- **Skill leveling (F8)** — `skill_uses` + `skill_levels` per player; every 5 uses → +1 level (capped at `max_level=10`); each level adds `+1 base` and `+10% scaling`.
- **Auto-learn on level-up (F9)** — `grant_next_learnable_skill` walks the class's `learnable_skills` list.
- **Skill validators (F10)** — `mp_cost`, `cooldown_turns`, `scaling_coefficient` hard-clamped against LLM hallucination.
- **Dup skill IDs renamed** — `fireball` / `backstab` / `berserker_rage` no longer silently shadow each other across files.
- **Inspect UI** — `[K]` menu adds "✦ Inspect an unknown skill" (when Inspect passive owned + AI online); skill detail view adds "✦ Inspect mechanics" for owned skills.
- **`SKILL_LEVELED_UP` notification** — fires a "SKILL MASTERY DEEPENS" panel when a skill grows via use-count.
- **Stat-points menu** — spendable from `[K]` anywhere, not just post-combat.

**Quest + economy:**
- **Level-scaled quest economy** (`systems/economy.py`) — `gold = 20 × level × tier_multiplier`; clamped both sides. AI quest reward of "250g for a level-1 player" became 30g (within L1 standard bounds).
- **Quest reward currency cap** — `reward_gold ≤ MAX_QUEST_REWARD_GOLD` (5000) hard-bound on `QuestTemplate`.
- **Hand-crafted demo skill rewards** — `blacksmith_hammer` rewards "Forge-Born Strike", `theft_investigation` rewards "Hushed Step".
- **Trainer NPC pattern** — Torven offers `give_skill:torven_forge_lesson` after the hammer quest (unknown ID → AI generates contextually).
- **NPC dialogue start paths** — Sylara now offers `dungeon_survey` + `fracture_investigation`; Captain Aldis offers `verath_courier`.

**Bug fixes caught along the way:**
- Save migration v2→v3 was a silent no-op — now explicit per-version functions + `SaveMigrationError`.
- Class resolver Layer 2 was granting combos without checking item/flag requirements.
- Bot action elif chain — `trade`/`rest`/`craft`/`talk_npc` were dead branches.
- `_setup_notifications` had a missing import (latent crash).
- `test_background_tick.py` was using stale `ai_generator=` kwarg for a month (pre-push hook only ran 2 of 7 files).

**Refactors:**
- **`core/game_engine.py` slimmed 1049 → 165 lines (-84%)** across 6 PRs. Extracted to: `core/{bootstrap,background_integrator,situation_query,menu_flow,game_loop,bg_scheduler,input_handler}.py`, `ui/{scene_renderer,auction_ui}.py`, `systems/{rest_system,faction_endings}.py`. Soft budget guard: must stay under 200 lines.
- **`scenes/scene_base.py`: 295 → 72 lines** — trigger processing extracted to `scenes/option_logic.py`.
- **`persistence/world_db.py`: 1103 → 787 lines** + 9 per-table repos in `persistence/repos/`.
- **Pre-push hook gates all 7 test files** — was 2; one test sat broken for a month before this was tightened.
