# SYSTEM BREAKER

> A LitRPG terminal RPG where the universe watches your choices.

The System appeared during the Fracture and rewrote the rules of the world. Most survivors got a class, a rank, and an expected path. You didn't — or you refused yours. Now the System is paying attention, and **Ollama** is generating your story in real time.

---

## What Is This?

SYSTEM BREAKER is a Python terminal RPG inspired by progression fantasy. The defining mechanic: when you take unexpected paths — unusual stat combinations, refusing the System's assignments, carrying rare catalyst items — an on-device AI (Ollama) generates a unique class, skills, and narrative tailored to your specific playthrough.

**No two divergent paths produce the same class.**

**The world grows around what you do.** Follow the script, or type your own questions with `[?]`. The fast model answers on the spot. When you *act* on something new (mine a gold seam, forage strange mushrooms, carve a trail marker), the primary model quietly builds the world around it: new items and recipes, a trader who buys what you gather, sometimes a new skill. Every save grows its own world.

**The dungeons are alive.** Kill a boss and something else moves in: a wizard rises as a lich, the priests who come to cleanse it are corrupted. Two dozen other adventurers level up, run Gates, form parties and kill bosses while you're away. New rifts keep opening, invented by the AI or echoing old ones deeper.

## What's new

- **Dungeons that remember.** Kill a boss and something else moves in, decided by how it died and what you did with the body. A wizard rises as a lich; the priests sent to cleanse its den are corrupted.
- **32 Gates** (rift dungeons, levels 2–18) with branching boss chains, trophies, First Clear and Swift Clear bonuses, and **9 group Gates** that need a party of bot companions.
- **The world grows by itself.** New rifts open every couple of in-game days (AI-made, or Deep Echoes of old Gates when offline), quest lines chart new ones, and the AI can invent new bosses.
- **213 quests in 53 chains**, including 10 that teach a skill and 10 that end with a forged item, plus faction quest lines for the Crown and the System Breakers.
- **24 bot adventurers** who level, run Gates, form parties, kill bosses, join guilds and sell at auction while you're away.
- **Deeper combat:** status effects, bosses that telegraph their big moves and enrage, working shields and evasion.
- **A world clock:** resting, travelling and dungeon runs take time, and the world moves on meanwhile.
- **Class Awakening:** refuse the System and it will eventually write a class just for you.
- **New default AI models:** `gemma4:12b` and `qwen3.5:4b`.

Built with:
- Python 3.11 + Pydantic v2 (all data models)
- [Rich](https://github.com/Textualize/rich) for terminal UI
- [questionary](https://github.com/tmbo/questionary) for arrow-key menus
- [Ollama](https://ollama.ai) — **two models** working together (see below) for AI content generation
- SQLite for world state (NPC memory, quests, factions)

---

## Quick Start

### 1. Get the code

```bash
git clone https://github.com/winters80/Game_test.git
cd Game_test
```

This needs **git** (https://git-scm.com/downloads). Without it, choose **Code → Download ZIP** on the repository page and unzip it; you'd then update by downloading again instead of `git pull`.

### 2. Install the prerequisites (once)

- **Python 3.11+**: https://www.python.org/downloads/
- **Ollama**: https://ollama.com/download. It's optional, but without it there's no AI: no unique classes, no `[?]` answers, no world growth, no AI-made Gates.
- **Disk:** about 11 GB for the two default models (less with smaller ones, see below).

### 3. Start the game (recommended)

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
3. **download the two models** from `config.py` if they aren't there yet (a one-time download of about 11 GB)
4. launch the game, which then **preloads both models into memory** in the background, so your first `[?]` question doesn't time out on a cold model

Add `--check` (`-Check` on Windows) to do steps 1–3 without launching the game.

On the title screen you should see **`AI system online. Ollama connected.`** The admin panel (`[A]`) shows **`AI: online`**.

### 4. Or start it by hand

```bash
pip install -r requirements.txt

ollama serve                # or open the Ollama app (Windows/macOS start it in the tray)
ollama pull gemma4:12b      # primary model (~8 GB)
ollama pull qwen3.5:4b      # fast model (~3.4 GB)

python -X utf8 main.py
```

The game uses a **dual-model setup**:

| Model | Role | Size | Required? |
|---|---|---|---|
| `gemma4:12b` | **Primary**: heavy generation in the background (classes, quests, NPC dialogue branches, world events, AI-made Gates and bosses, and **world growth**: the items, recipes, traders and skills that appear in response to your actions). 256K context, tool calling | ~8 GB | recommended |
| `qwen3.5:4b` | **Fast**: interactive calls (the `[?]` «Ask about this situation» prompt, anything player-facing where latency matters). The 0.8b / 2b sizes are quicker but mostly repeat the scene back | ~3.4 GB | recommended |

If a model is missing when the game starts, it names it and offers to **download it right there** (with a progress bar), so running `python main.py` directly works too.

#### Using a different model

Both models can be swapped without editing code, via environment variables the launchers and the game both read:

```powershell
# PowerShell (this window only)
$env:OLLAMA_MODEL = "qwen3.5:27b"    # primary
$env:OLLAMA_FAST_MODEL = "qwen3.5:4b" # fast; "" = use the primary for everything
.\start.bat
```

```bash
OLLAMA_MODEL=qwen3.5:27b OLLAMA_FAST_MODEL=qwen3.5:4b ./scripts/start.sh
```

Models that "think" before answering (Gemma 4, Qwen 3.5, deepseek-r1) are asked not to, so they answer at normal speed; any empty thinking block they still emit is stripped before the game reads the answer. Pick a primary that fits your GPU memory **alongside** the fast model: both stay loaded for 30 minutes.

| Your GPU memory | Primary | Fast |
|---|---|---|
| 8 GB | `qwen3.5:9b` (~6.6 GB, partly on CPU) | `qwen3.5:2b`, or `""` to use the primary for everything |
| 12–16 GB | `gemma4:12b` (default) | `qwen3.5:4b` (default) |
| 24 GB | `qwen3.5:27b` (~17 GB) | `qwen3.5:4b` (default) |
| 32 GB+ | `qwen3.5:27b` | `gemma4:e4b` (~6.6 GB, stronger answers) |

### If the AI shows as offline

The game tells you what's wrong at startup:

| Message | Fix |
|---|---|
| `Ollama not available … Nothing is answering at http://127.0.0.1:11434` | Ollama isn't running. Open the Ollama app, run `ollama serve`, or use the launcher. The same reason shows under Admin → AI Token Usage, which can retry the connection without restarting |
| `The AI needs … model(s) that aren't downloaded yet` | Accept the download offer, or `ollama pull <model>`, or run the launcher |
| `Ollama is running, but the model 'gemma4:12b' isn't downloaded` | You declined the download. `ollama pull gemma4:12b`, or point `OLLAMA_MODEL` at a model you have |
| `Fast model 'qwen3.5:4b' isn't downloaded; using 'gemma4:12b'…` | AI still works, but `[?]` is slower. `ollama pull qwen3.5:4b` |

Without Ollama the game stays fully playable, with hand-crafted classes (the Class Awakening gives refusers the Wayward Soul), static dialogue, no dynamic quests and no world growth. New rifts still open as Deep Echoes, and bosses follow their authored succession chains. Actions you take are still logged, and the world catches up the next time you load that save with Ollama running. Ollama on another machine or port? Set the `OLLAMA_BASE_URL` environment variable (the launchers and the game both read it).

### 5. (Optional) Install the pre-push test hook

If you plan to push commits, install the local git hook that runs the test suite before every push:

```bash
# macOS / Linux / Git Bash
./scripts/install-hooks.sh

# Windows / PowerShell
.\scripts\install-hooks.ps1
```

### Updating

From the game's folder:

```bash
git pull
```

Then start the game with the launcher as usual (it installs any new Python dependencies), or run `pip install -r requirements.txt` yourself. If you installed the pre-push hook, run the install script again so you get its latest version.

Your saves upgrade automatically when you load them: the world database migrates to the current schema, older saves are topped up to the full cast of bots, and new content (Gates, boss lairs, quests and quest givers) is there the next time you visit the Gate Hall.

---

## Project Layout

```
Game_Test/
├── main.py                 # Entry point
├── start.bat               # Windows: double-click to play (runs scripts/start.ps1)
├── scripts/                # start.ps1 / start.sh launchers, git hook installers, balance_sim.py
├── config.py               # All constants and feature flags — change here only
├── CLAUDE.md               # Developer reference (architecture, triggers, schemas)
│
├── core/                   # Game engine, event bus, state
├── entities/               # Pydantic models (Player, Item, Skill, Class, NPC, Quest)
├── systems/                # Pure game logic (class resolution, combat, quests, levels)
├── ai/                     # Ollama integration (client, prompts, validators)
├── scenes/data/            # All narrative content as JSON (scene scripts)
├── data/                   # All static content (classes, skills, items, NPCs, quests, dungeons/Gates, recipes)
├── ui/                     # All Rich terminal rendering
├── persistence/            # Save/load (JSON player data + SQLite world state)
└── saves/                  # Runtime save files (gitignored)
```

---

## Gameplay

### Controls

- **Arrow keys** to navigate menus, **Enter** to confirm
- **`[?]`** Ask about this situation: type a question or an action in your own words
- **`[K]`** Skills · **`[I]`** Items & equipment · **`[J]`** Quest journal · **`[L]`** World log · **`[W]`** Who's around
- **`[C]`** Craft · **`[G]`** Found a guild
- **`[S]`** Save · **`[A]`** Admin panel (AI token usage, feature flags, **World Growth log**) · **`[Q]`** Quit to menu

### The world

- **Prologue and the Classification Rite:** choose gender, species and background, then the System assigns your class, unless you diverge (see below). Refuse the System outright and you arrive unclassed; when you're ready, **answer its summons** in the village for a **Class Awakening**: divergent enough and the AI invents a class for you (in the background, with a spinner, never freezing the game); otherwise you become a Wayward Soul.
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

### Time

The clock in your status panel runs on game time: **Day 1, 08:00** at the start, and every ordinary choice takes about ten minutes. Bigger things take longer, and the world keeps moving while they happen:
- a Gate room takes an hour, and so does its master's chamber
- a full rest takes 8 hours, a short one 2, and a night at the inn 8
- walking between the outer market, the camp, Verath and the dungeon takes half an hour

NPCs keep day hours (06:00–20:00) and night hours. Bosses come back in hours or days, not menu clicks. Bots act, rumours spread and buffs wear off while you sleep.

### Combat

- **Statuses:** poison, bleed and burn hurt every round; **stun** costs you a turn (then you can't be stunned again for two rounds); **frozen** makes you slower to dodge. Many monsters inflict them on a hit. Your spells do too: fire burns, ice freezes, arcane and curse effects can stun. Bosses (rank C and up) shrug off half of all stuns.
- **Boss moves:** every boss has signature moves, such as a heavy blow it **announces a turn ahead** (*"⚠ Asterion is gathering itself for Horned Charge!"*), a heal, a curse, a summon, or a life drain. Below a third of its health it becomes **ENRAGED** and hits 25% harder.
- **Study** a boss first (if your INT or WIS is high enough) for a free first strike.
- Casters regain a little MP every round (1 + WIS/5). Basic attacks use the better of STR and AGI.
- **Defensive skills:** Mana Shield puts up a ward that absorbs damage, and Shadow Step lets you evade the next attack or two.

### Gates

There's more to Aethoria than the three dungeon floors. **32 Gates** (rifts the System registers, rated from level 2 to 18) open across the world: a Minotaur's bronze labyrinth, a fae court under a hill, a dragon's hoard, a clockwork foundry, a derelict skyship, a haunted manor, a plague asylum, a hall of ten thousand mirrors, forges and tombs of the elements, a fallen star, a clocktower stuck at the moment of the Fracture, and a rift you can only reach in your dreams.

Find them at the **Gate Hall** in the village and in Verath. The Registry there lists every Gate's level and who rules it this week, who's in the hall, and the latest news from the rifts.

- **A run:** three rooms (fights, traps and treasure, some with stat checks), then the Gate's master. You catch your breath between rooms (15% HP and MP back) and can withdraw at any point.
- **Rewards:** a **FIRST CLEAR** bonus the first time, a **SWIFT CLEAR** bonus if you win every fight fast, and a **trophy** the first time you kill each master. Lady Vos in Verath buys trophies.
- **Companions:** an adventurer in town can come along. They fight beside you, take some hits, and keep a share of the gold.
- **Group Gates** (`[party of 2–4]` in the Registry) need companions: the Hydra Fen, the Drowned Bell, the Phoenix Ashery and Bellfall Cathedral take two; the Dragon's Hoard, the Thaumic Reactor, the Dawnspire Observatory and the Hollow of Unmaking take three; the Dreamer's Abyss takes four. Their masters are built for a party. Six levels over a group Gate, you may go in alone.
- **Every Gate master follows the succession rules above.** Each Gate has a chain of four that branches on how you killed the first one or what you did with the body.

### New rifts open by themselves

The world keeps growing:
- **Rift surges:** every two in-game days a new Gate opens (up to 16 per save). With Ollama running, the AI invents it: its name, its rooms and a chain of masters. Its stats, rewards and structure come from the Gate's level, never from the AI. With the AI offline, a **Deep Echo** opens instead: an old Gate reopened 6–10 levels deeper, with the same story and stronger masters. That's where the content past level 18 lives.
- **Quest lines open rifts too:** Petra's **Uncharted Rifts** chain charts a brand-new Gate at every step.
- **New masters:** when a boss dies, the AI may invent who moves in next ("a wizard killed by fire rises as a burned lich"). The authored chain carries on after it.

Everything new is saved with your world and comes back when you load.

### Other adventurers

You're not alone. **24 other adventurers** (bots, like playerbots in an MMO) live in every save:
- **What they do:** they fight on the dungeon floors (pushing deeper as they level), run Gates from town, form **parties** for group Gates, kill bosses, loot, sell their haul to traders, brew potions and craft, and wander between the outer market, the camp and Verath.
- **When they're near you:** you'll see the occasional line, like *"Ryn took down a goblin patrol."*, *"Kael and Dova came back from the Hydra Fen together."* or *"✦ Dova and Kael are about."* Each bot in your zone gets a "Talk to …" option.
- **Talking to them:** they tell you what they've really been doing, and you can **buy their spare loot** or **sell them** materials and consumables.
- **`[W]`** shows who's around: everyone's level, class, location and what they're up to.
- **Guilds and the auction house:** bots join guilds that suit them (and your own guild, if you've founded one), rise through the ranks as they level, and consign their best loot to the Verath auction. Their listings show who consigned them, and they're paid when they sell.
- **They change the world without you:** come back from Verath and Ryn may have killed the Golem. The World Log (`[L]`) shows who holds every lair. Bots leave each lair's first boss alone for 250 turns, so you get the first shot.

Their decisions are rules-based, so they keep living even with Ollama off. The AI only writes what they say.

### Quests

The Gate Halls (and the wardens' fire at the camp) are full of people with work: **213 quests** from 13 patrons, including **53 quest chains**:
- **10 skill chains** end with a new skill (Bull Rush, Stormcall, Ember Rebirth, Void Step, Gorgon's Gaze…)
- **10 item chains** end with a forged item (Asterion's Horn Axe, the Stormcaller Staff, the Wyrmscale Bulwark, the Dreamthread Ring…)
- **33 story chains**, including the Drowned Sanctum's wizard → lich → priests cycle, Petra's **Uncharted Rifts** (each step opens a new Gate) and Registrar Ostra's **Wardens of the Breach** (the group Gates)
- plus 32 Gate bounties and 32 material orders

**Faction quest lines:** with enough standing, Lady Vos sponsors **The Throne Petition** (Verath Crown) and the Gray Wanderer offers **Break the Machine** (System Breakers). Their stages are now real deeds: finish a patron's commission, clear a Gate, then report. Finishing a patron's chain raises your standing with their faction.

Finish a step, go back and choose **« Report: … »**, and the next step opens. The **`[J]` journal** shows each quest's chain progress (*skill chain 2/3*), which ones are ready to report and to whom, and **leads**: work you could pick up now, and where.

### Crafting

`[C] Craft` appears once you carry something worth crafting with. Every Gate material has a use. There are 32 Gate recipes: stronger potions, stat tonics (Bronze Whetstone Tonic: STR +2 for 30 turns), and eight pieces of gear such as a Star-Metal Dagger, a Chrono Pendant and the Voidglass Blade. Recipes you can make right now are marked ✓.

### Trading

Traders offer **« Browse wares »** and **« Sell items »** in conversation. **Wren**, a travelling pedlar, walks between the outer market, the camp and Verath, buying materials and consumables. **Torven** buys weapons and armour. **Lady Vos** buys Gate trophies. AI-generated traders appear as the world grows. You can never sell the last copy of something you have equipped.

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

### New quest or quest chain
Add a template to any file in `data/quests/`. Offer it from an NPC's `quest_seeds` (chain steps unlock each other with `done_<id>` flags), or trigger it from a scene with `"start_quest:template_id"`. Objectives can check items, flags, Gate clears, boss kills, level, and « Report » hand-ins.

### New dungeon (Gate) or boss chain
Add a Gate to `data/dungeons/gates/*.json`, or a fixed boss lair to `data/dungeons/lairs.json`. The Registry lists new Gates automatically. Run `python scripts/balance_sim.py` afterwards to check it's beatable at its level.

### New recipe
Add to any file in `data/crafting/`.

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
    "boss_succession":    True,   # Boss lairs whose occupant changes after each kill
}
```

Enable order matters: `world_db` must be `True` before `npc_system`, `quest_system`, or `faction_system`. All other systems can be toggled independently.

---

## Saves

Save files live in `saves/`. Each slot creates two files:
- `<slot>.json` — player data (Pydantic model)
- `<slot>.db` — world state (SQLite: NPC memory, quest instances, etc.)

Both files must be present to load a save. The game handles migration automatically when either format changes: the player JSON has its own save version, and the world database has its own schema version (currently 8), both upgraded on load.

Each save has its **own grown world**. Items, recipes, traders, skills, new Gates and new bosses created for one save are stored in that save's `.db`, restored when you load it, and never appear in another save. So are who rules each lair, the history of every boss kill, and the bots.

---

## AI Configuration

All AI settings are in `config.py`:

```python
AI_ENABLED = True                          # master switch — set False to disable all Ollama calls
OLLAMA_BASE_URL = "http://127.0.0.1:11434" # default Ollama port (override with the OLLAMA_BASE_URL env var)
OLLAMA_MODEL = "gemma4:12b"                # PRIMARY (env var OLLAMA_MODEL overrides) — used for heavy generation (classes, quests, NPC branches)
OLLAMA_FAST_MODEL = "qwen3.5:4b"           # FAST — interactive «Ask…» queries (env var OLLAMA_FAST_MODEL overrides). "" reuses primary.
OLLAMA_FALLBACK_MODEL = "qwen3.5:9b"       # used if primary fails to load

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

# Bot adventurers
BOT_COUNT = 24                    # adventurers per save
BOT_ACT_EVERY_TURNS = 2           # each bot acts once every N turns
BOT_AI_DECISIONS = False          # True = ask the primary model for every bot decision (slow)

# Gates, bosses and the world clock
MINUTES_PER_TURN = 10             # an ordinary choice; TIME_COSTS sets rests, Gate rooms, travel
RIFT_SURGE_EVERY_TURNS = 288      # a new rift every 2 in-game days (AI-made, or a Deep Echo offline)
RIFT_MAX_GENERATED = 16           # generated Gates per save
AI_SUCCESSOR_CHANCE = 0.25        # chance the AI invents who moves into a lair after a kill
XP_SOFT_CAP_LEVEL = 8             # the XP curve flattens past this level (x1.12 per level)

# What AI-generated options may do (everything else is dropped)
AI_GRANTABLE_FLAGS = frozenset({"verath_access"})
AI_MAX_ALIGNMENT_SHIFT = 5.0
AI_GIVEABLE_ITEM_TYPES = frozenset({"CONSUMABLE", "MATERIAL"})
```

If Ollama is offline or `AI_ENABLED = False`, every AI call silently falls back to the nearest pre-written class from `data/classes/combo_classes.json`. NPC dialogue uses its `dialogue_hooks["default"]`. The dynamic quest path simply skips its option. World growth records your actions as `no_ai` and retries them the next time you load with Ollama available. Rift surges open Deep Echoes instead of AI-made Gates, and bosses follow their authored chains.

### Verifying the AI is wired up

After a fresh install you can do a non-interactive sanity check without launching the game:

```bash
python -c "from ai.ollama_client import OllamaClient; print(OllamaClient(model='gemma4:12b').is_available())"
```

Prints `True` if Ollama is reachable and the model is pulled.

---

## Development

```bash
# Quick syntax check (no game launch needed)
python -c "import py_compile; py_compile.compile('main.py', doraise=True)"

# Every push runs ALL 7 test files via the pre-push hook
python -X utf8 test_characters.py                  # 192 checks across 34 sections
python -X utf8 test_quests.py                      # 771 checks across 23 sections (every quest file)
python -X utf8 test_runs.py                        #  74 checks (older run suite)
python -X utf8 tests/test_db_migrations.py         #  15 checks (SQLite schema, v8)
python -X utf8 tests/test_background_tick.py       #   9 checks (BG worker)
python -X utf8 tests/test_guild_betrayal.py        #  22 checks
python -X utf8 tests/test_guild_founding.py        #  10 checks

# Balance: can each build clear every Gate at its rated level? (real combat rules)
python -X utf8 scripts/balance_sim.py 20 --class mage

# Headless smoke test — bootstrap the engine without launching the UI
python -c "from core.game_engine import GameEngine; e = GameEngine(); e.bootstrap(); print('OK')"

# Live playthrough — drives the engine the way a player would, asserts
# quest rewards / skill generation / save round-trip end-to-end. Requires Ollama.
python -X utf8 playthrough_smoke.py                #  23 checks

# Start a feature branch
git checkout -b feature/my-feature
```

**1,093 checks across 7 test files**, all run by the pre-push hook. They cover content integrity (every scene link, trigger, quest objective, Gate chain and item reference), the game systems end to end (combat, quests, Gates, boss succession, bots, world growth, saves and migrations), and the AI boundary with fake model output. `playthrough_smoke.py` is optional and needs Ollama.

See **CLAUDE.md** for the full developer reference: trigger strings, gate syntax, event bus events, SQLite table descriptions, the `AIService` boundary contract, and the directory map.

---

## Roadmap

What's next:

| Item | Why |
|------|-----|
| Tune AI prompts on live Ollama | World growth, AI Gates, AI bosses and the Class Awakening are tested with fake model output; real `gemma4:12b` / `qwen3.5:4b` output needs a playthrough |
| Class identity | Warriors are the tankiest class (63% HP left on an average Gate run, vs 40–49% for mages and rogues in `scripts/balance_sim.py`); worth a design pass on class skill kits |
| Bots bid at auction | Bots consign loot and get paid; bidding on the player's listings would complete the market |
| `GameEngine` mixin → composition refactor | Low priority, budget-guarded |
| `world_db.py` further split | `auction_system` still reads the database connection directly in two places |
