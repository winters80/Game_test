# SYSTEM BREAKER — Developer Reference

> Read this before touching any code. It covers architecture, content authoring, trigger strings, feature flags, and AI integration contracts.

---

## Architecture in One Paragraph

The game is a Python terminal LitRPG. **Static content** (class definitions, skill definitions, item templates, species, guilds, NPCs, quests, scene scripts) lives in **JSON files** under `data/` and is read-only at runtime. **Dynamic world state** (NPC memory, active quests, faction relationships, auction listings, death records) lives in a **SQLite database** at `saves/{slot}.db`. The player's own serializable data (stats, inventory, alignment, lives, etc.) lives in `saves/{slot}.json` as a Pydantic v2 model. The two databases are always opened and closed together by `persistence/save_manager.py`.

**The one rule:** content is always JSON, state is always SQLite. Never mix them.

---

## Directory Map

| Path | Purpose |
|------|---------|
| `config.py` | All constants, feature flags, balance values — change here, never in game logic |
| `entities/` | Pydantic v2 data models + Registry classes (load JSON → in-memory dict) |
| `systems/` | Pure-function game logic — no I/O, no rendering, no direct Ollama calls. Systems that need AI take an `ai_service: AIService` parameter |
| `ai/` | Ollama integration — client, prompt builders, response validators, content generator, `AIService` facade |
| `scenes/data/*.json` | Scene definitions — all game narrative and choice trees |
| `data/` | All static content definitions (JSON) |
| `core/` | Engine (game loop), event bus, game state |
| `ui/` | All Rich rendering — **only files under `ui/` import `rich`** |
| `persistence/` | save_manager.py (JSON + SQLite open/close), world_db.py (SQLite facade), repos/* (per-table query modules) |
| `saves/` | Runtime save files — gitignored except `.gitkeep` |

---

## Adding Content (No Code Required)

### New class
Add an entry to `data/classes/base_classes.json` or `combo_classes.json`. See existing entries for the schema. `ClassRegistry.load_from_file()` picks it up at startup.

### New skill
Add to a `data/skills/*.json` file. `SkillRegistry.load_from_dir()` loads all files in the directory.
Every id in a class's `starting_skills` and `learnable_skills` must exist in the registry. `test_characters.py` fails otherwise. At runtime, auto-learn logs and skips a missing id rather than stalling on it.

### New item
Add to a `data/items/*.json` file. Set `"combo_catalyst": true` if the item should contribute to AI class divergence detection.

### New NPC *(npc_system feature flag must be True)*
Add a template to `data/npcs/*.json`. The NPC is instantiated into `npc_instances` SQLite table on first entry to their home zone.

Optional NPC fields for traders (`systems/trade_system.py`):

| Field | Effect |
|-------|--------|
| `trades.buys` | Item ids or `type:ITEM_TYPE` tags the NPC will buy. Price = `value_gold × 100 × buy_rate` copper |
| `trades.buy_rate` | 0–1, default 0.5 |
| `trades.sells` | `[{"item_id": ..., "price": copper}]` shown under « Browse wares » |
| `ambient` | `true` → an automatic "Talk to …" option appears at the root node of whatever zone the NPC is in. No scene edit needed |
| `route` | Zones a travelling NPC visits in order, `TRADER_ROUTE_STAY_TURNS` (15) turns each. Overrides `schedule` |

Trade menus (« Browse wares » / « Sell items ») are injected at the dialogue root of any NPC with `trades`. The last copy of an equipped item is never sellable. `NPCRegistry.register()` and `alchemy_system.register_recipe()` add NPCs and recipes at runtime; recipes load once into `engine.recipes`.

### New quest template *(quest_system feature flag must be True)*
Add to `data/quests/*.json`. Quest state machine states are defined in the JSON.

### New species *(species_system feature flag must be True)*
Add to `data/species/species_definitions.json`. Define `evolution_path` as an ordered list of stages.

### New scene
Add a new `scenes/data/*.json` file. `SceneRegistry.load_from_dir()` loads all JSON in that directory. Scene ID must match the filename stem.

### New zone
Add to `data/world/zones.json`.

---

## Trigger String Reference

Trigger strings are the `"triggers": [...]` values on scene option nodes. They are processed in two passes:

1. **State triggers**: `scenes/option_logic.process_triggers()`. `Scene.process_triggers()` in `scenes/scene_base.py` is a thin delegate to it.
2. **Engine triggers** (combat, NPCs, quests, menus): `core/choice_handler.ChoiceHandlerMixin._handle_choice()`.

| Trigger | Effect |
|---------|--------|
| `flag:X` | Set `player.flags["X"] = True` |
| `give_item:X` | Add item_id X to player inventory; fires `ITEM_FOUND` event |
| `give_food:X` | Add one food item X to inventory; fires `ITEM_FOUND` event |
| `give_skill:X` | Add skill_id X to player; fires `SKILL_ACQUIRED` event. If X isn't in the skill registry, a skill is AI-generated (or a deterministic default is built) under that id |
| `give_gold:N` | Add N to `player.gold`, which is stored in **copper** (`COPPER_PER_GOLD` = 100), so `give_gold:500` is 5g |
| `spend_gold:N` | Subtract N copper from `player.gold` (never below 0). Gate the option with `requires.min_gold` |
| `set_base_class:X` | Assign class_id X as player's base class via `class_system.assign_base_class()` |
| `set_gender:X` | Set `player.gender` |
| `set_species:X` | *(species_system)* Queue species X; applied after the choice resolves |
| `set_background:X` | Queue background X; applied after the choice resolves (alongside species) |
| `combat:X` | Start encounter X (see the `encounters` table in `combat_system.spawn_encounter()`) |
| `alignment:+N` | Shift player alignment by +N (float) |
| `alignment:-N` | Shift player alignment by -N (float) |
| `rest_camp:full` / `rest_camp:short` | Restore all / half of HP + MP, then run the rest flow (`systems/rest_system.handle_rest`) |
| `rest_inn:N` | Pay N (copper) for a full restore + `well_rested` buff (+1 combat stats, 30 turns) |
| `talk_npc:X` | *(npc_system)* Trigger NPC interaction with NPC id X |
| `start_quest:X` | *(quest_system)* Activate quest template X |
| `advance_quest:X[:STAGE]` | *(quest_system)* Advance the active instance of template X (optionally to STAGE) |
| `complete_quest:X[:OUTCOME]` | *(quest_system)* Complete the active instance of template X; outcome defaults to `success` |
| `join_guild:X` | *(guild_system)* Join guild X |
| `update_faction:X:±N` | *(faction_system)* Shift standing with faction X by N |
| `show_auction` / `buy_life_token` | *(auction_house)* Open the auction house / buy a life token directly |
| `talk_bot:BOT_ID` | *(bot_system)* Talk to / trade with a bot adventurer. Added automatically for bots in the player's zone; no need to author it |
| `world_action:VERB:SUBJECT` | AI-only tag describing what the player did (e.g. `world_action:mine:gold`). No direct effect; feeds world expansion |

### Adding a new trigger type
1. If it only changes player/game state, add it to `process_triggers()` in `scenes/option_logic.py`
2. If it needs engine context (combat, menus, NPCs, quests), handle it in `_handle_choice()` in `core/choice_handler.py` (and add a no-op branch in `option_logic` if it shouldn't fall through)
3. Document it in this table

---

## Scene Option Gate Reference

Option `"requires"` block controls visibility and locking:

```json
"requires": {
  "min_stats": { "INT": 15 },
  "items": ["ancient_tome"],
  "flags": ["found_vault"],
  "flags_any": ["took_left_path", "took_right_path"],
  "flags_absent": ["vault_emptied"],
  "min_gold": 2500,
  "alignment_min": 20,
  "alignment_max": 100,
  "lock_reason": "The vault door won't budge."
}
```

| Field | Behavior |
|-------|---------|
| `min_stats` | Hard lock if any stat below threshold. Shows lock reason. |
| `items` | Hard lock if item not in inventory |
| `flags` | Hard lock if any listed flag is not set on player |
| `flags_any` | Hard lock unless **at least one** listed flag is set |
| `flags_absent` | **Hides** the option if any listed flag is set (use for one-shot options) |
| `min_gold` | Hard lock if `player.gold` (copper) is below the value; pair with a `spend_gold:N` trigger |
| `alignment_min` | *(alignment_system)* Hard lock if `player.alignment < value` |
| `alignment_max` | *(alignment_system)* Hard lock if `player.alignment > value` |
| `lock_reason` | Replaces the generic lock text shown when the option is locked. Write it in-world |

**Verath access:** the capital (`verath_city`) is gated on the `verath_access` flag. It's granted by clearing Floor 1 (both fights, then the survey report), by descending to Floor 2, by one of the divergent routes at `village_start.city_gate` (bribe, INT, LCK, the Gray Wanderer's blind spot), or by the fast AI model when a typed `[?]` action plausibly gets the player past the gate (`ai/content_generator._dynamic_option_rules`; the rule is only sent in scenes that mention Verath).

**Divergence signal:** Set `"expected": false` on any option that represents an unusual player path. This feeds the divergence scorer. The higher the score, the more likely the player gets an AI-generated class at the Class Awakening scene.

---

## Feature Flags

All flags live in `config.FEATURES`. Set to `True` to enable, `False` to disable without removing code.

```python
from config import feature
if feature("npc_system"):
    npc_system.handle_interaction(...)
```

| Flag | Controls |
|------|---------|
| `world_db` | SQLite WorldDatabase open/close in SaveManager |
| `species_system` | Species selection in character creation + evolution checks |
| `alignment_system` | Alignment float tracking + alignment gates in scenes |
| `npc_system` | NPC instances, memory, stat-gated dialogue |
| `quest_system` | Quest state machine + AI quest generation |
| `guild_system` | Guild membership, ranks, perks |
| `faction_system` | Faction standing, political ascension |
| `auction_house` | Auction listings, competing guilds, life tokens |
| `lives_system` | 9-lives death mechanic (replaces instant game over) |
| `crafting_system` | Recipe + material crafting |
| `stat_gating` | INT/Perception/LCK/WIS content gates via `systems/stat_gate.py` |

**Enable order matters.** `world_db` must be True before `npc_system`, `quest_system`, `faction_system`, or `auction_house`. `stat_gating` should be enabled before `npc_system`.

---

## Player Model — Key Fields

```
player.alignment          float -100.0 to +100.0 (never store the label, derive it)
player.alignment_label    property → human-readable string
player.perception         property → derived from AGI//2 + WIS//3 + perception_bonus
player.species_id         string, references species definitions JSON
player.evolution_stage    int, 0 = base form
player.lives_remaining    int, starts at STARTING_LIVES (9)
player.guild_memberships  dict {guild_id: rank_id}
player.active_quest_ids   list of quest instance UUIDs (full state in world_db)
player.flags              dict for arbitrary story state
player.choice_history     list of "unexpected:scene:option_id" strings
player.turn_count         incremented each game loop iteration
player.last_safe_zone_id  where the player respawns after death
```

---

## SQLite World Database (`persistence/world_db.py`)

One `.db` file per save slot. Always opened/closed alongside the `.json` file.

| Table | Stores |
|-------|--------|
| `npc_instances` | Live NPC state: zone, disposition, alive/dead, custom flags |
| `npc_memory` | Per-NPC interaction history (compressed to summary at 20 events) |
| `quest_instances` | Active/completed quest state machine positions |
| `ai_quest_data` | Full JSON definition for AI-generated quests |
| `faction_standing` | Player standing with each faction + current rank |
| `faction_relations` | Faction-to-faction relationship matrix |
| `auction_listings` | Active auction items and life tokens |
| `auction_bids` | Bid history per listing |
| `death_records` | Every death: cause, zone, level, alignment, lives_remaining |
| `world_flags` | Global flags independent of the player |
| `turn_log` | Lightweight event log for quest/NPC trigger processing |
| `world_actions` | *(schema v6)* Every AI-option action the player took (`verb:subject`), status `pending` / `expanded` / `empty` / `no_ai`, yield cooldown |
| `world_expansions` | *(schema v6)* The validated content bundle the world grew for each action; re-registered on load |

**Never query the `.db` from outside `persistence/world_db.py`.** All access is through typed methods on `WorldDatabase`.

---

## AI Integration Contract

### The `AIService` boundary
Non-AI packages **must** depend on `ai.ai_service.AIService`, never on
`ContentGenerator` or an `OllamaClient` directly. AIService:
- Returns `None` on failure (caller supplies its own fallback path)
- Centralises try/except + logging for every generation method
- Exposes `is_available` so systems can skip work when Ollama is offline
- Provides `submit_quest_async()` for callers that can tolerate a queued result

Systems (`class_system`, `quest_system`, `guilds/guild_sim`) all take
`ai_service` as a typed parameter and use `ai_service.is_available` to gate.
Engine code in `core/` uses `self.ai_service` and gates on `self._ai_online()`;
it owns the spinner, AIService owns the call.

`core/bootstrap.setup_ai()` is the only file outside `ai/` that constructs
`ContentGenerator` / `OllamaClient`, and it keeps the generator in a local
variable. The "No package outside ai/ bypasses AIService" check in
`test_characters.py` fails if anything else references them.

**Adding a new AI call:** put the prompt + client call in a
`ContentGenerator` method, add a wrapper on `AIService` that returns `None`
(or `[]`) on failure, and call the wrapper. Purely cosmetic calls
(e.g. follow-up option flavour) log failures at INFO. The console handler
shows WARNING and above from the main thread, so a WARNING would print mid-scene. Records from background threads (world growth, quests, the director) only go to the log files.

### AI trigger policy (`systems/ai_trigger_policy.py`)

Every trigger list the AI authors, whether on `[?]` situation options or on
background NPC dialogue branches, goes through `sanitize_ai_triggers()` before
the engine sees it. Only these survive:

| Trigger | Rule |
|---------|------|
| `flag:X` | Rewritten to `flag:ai_X`, so the AI can never set an authored story flag. Exception: `config.AI_GRANTABLE_FLAGS` (currently `verath_access`) |
| `give_item:X` | X must exist and be a COMMON/UNCOMMON **consumable or material**, not a combo catalyst. Max 1 per option |
| `alignment:±N` | Clamped to `AI_MAX_ALIGNMENT_SHIFT` (5) |
| `combat:X` | X must be in `combat_system.ENCOUNTERS` |
| `world_action:VERB:SUBJECT` | Slugged; one per option |

Everything else (gold, skills, classes, quests, NPC talk, shops, factions, rests) is dropped and logged at INFO. AI option `requires.flags` match either the authored flag or its `ai_` form. To let the AI grant a new story flag, add it to `AI_GRANTABLE_FLAGS` deliberately.

### Economy guard rails

All quest rewards — hand-crafted and AI-generated — are denominated in
**gold pieces** at the template level. `quest_system._apply_rewards`
multiplies by `COPPER_PER_GOLD` (100) when applying to `player.gold`
(which stores copper).

`systems/economy.py` is the single source of truth for "how much gold/XP
should a level-N quest of difficulty D pay?":

- `recommended_quest_reward(level, difficulty) → (gold, xp)`
- `quest_reward_bounds(level, difficulty) → ((g_min, g_max), (x_min, x_max))`
- `clamp_quest_reward(proposed_gold, proposed_xp, level, difficulty)` —
  called on every AI-generated quest before storage so a hallucinating
  model can't drop 5000g on a level-1 player

Tier multipliers: `trivial 0.5×, standard 1.0×, hard 2.0×, epic 4.0×`.
Base per level: `BASE_GOLD_PER_LEVEL = 20g`, `BASE_XP_PER_LEVEL_REWARD = 50`.

The AI quest prompt embeds the level-scaled range and a one-line
gear-price hint (`gear_price_hint()`) so the LLM sees the local economy
before proposing rewards.

### When AI is called
1. **Class generation** — when divergence score ≥ `DIVERGENCE_THRESHOLD` (default 30). Triggered by `systems/class_system.resolve_combo_class()` via `ai_service.generate_class()`.
2. **NPC dialogue** *(npc_system)* — when NPC has no pre-written dialogue for the player's current context.
3. **Quest generation** *(quest_system)* — fired from `dialogue_handler` when the player picks an injected `« Is there any work I could take on? »` option. Triggered by an explicit `"ai_dynamic"` seed on the NPC, **or implicitly** when the NPC has no available seeds left and disposition ≥ `AI_QUEST_DISPOSITION_MIN` (default 30) and `_ai_offered_{npc_id}` flag is unset.
4. **Narrative generation** — when a scene option has no `leads_to` text and is flagged `"ai_narrative": true`.
5. **World growth** — the first time the player takes a new `[?]` action (`world_action:VERB:SUBJECT`), the primary model is asked in the background how the world should respond. See "World Growth" below.

### AI response schemas (validated by `ai/response_validator.py`)
All AI calls must return JSON validated against Pydantic models. If validation fails, retry up to `OLLAMA_MAX_RETRIES` times, then use the fallback.

| Generator method | Returns | Fallback |
|-----------------|---------|---------|
| `generate_class()` | `AIClassResponse` | `fallback_generated` class from combo_classes.json |
| `generate_narrative()` | plain text string | None (scene uses static text) |
| `generate_quest()` *(future)* | `AIQuestResponse` | nearest matching template quest |
| `generate_npc_dialogue()` *(future)* | `AINPCDialogueResponse` | NPC's `dialogue_hooks["default"]` |
| `generate_world_expansion()` | `AIWorldExpansionResponse` (+ optional skill) | None: the action stays logged, nothing is added |

### Lore constraints (always injected into system prompt)
- World name: Aethoria
- The System appeared during the Fracture (3 years before game start)
- Magic = "arcane arts" in formal contexts
- Capital city = Verath
- Classes are assigned by the System, not chosen (unless player diverges)
- See `data/world/lore_fragments.json` for full context

---

## World Growth (typed actions grow the world)

The script is the spine; what the player *types* grows the world around it.

1. **Fast model** (`[?] Ask about this situation`): answers and offers options. Every option gets a `world_action:VERB:SUBJECT` tag, which `world_growth.derive_world_action()` derives from its label ("Mine the gold vein" → `world_action:mine:gold_vein`). Tags the model writes itself are dropped: a 1B model copies prompt examples, so the prompt carries no example actions. Only hands-on work grows the world: the label's verb must be in `world_growth._WORLD_VERBS` (mine, forage, chop, hunt, brew, craft, trade, steal …). Looking, talking, moving, remembering and declining get no tag. Options that repeat an existing choice, and answers that just echo the scene text, are dropped (`core/situation_query`). AI follow-up options are tagged the same way, and each new set replaces the previous AI options at that node.
2. **Player picks the option** → `core/world_growth_flow.on_world_action()`:
   - **New action** (`world_growth.action_key()` normalises "gold veins" and "the gold" to `mine:gold`): recorded in `world_actions`, queued as a background `world_expansion` task, and the player sees a **✦ DISCOVERY** notice.
   - **Already expanded**: the player gathers the bundle's yield item, at most once every `WORLD_YIELD_COOLDOWN_TURNS` (10).
   - **No Ollama**: recorded as `no_ai`; nothing is promised.
3. **Primary model, in the background** (`ContentGenerator.generate_world_expansion`): returns a yield item, up to 3 derived items, 2 recipes, 1 trader, and an optional skill hint (which becomes a real skill via `generate_skill`).
4. **`systems/world_growth.build_bundle()`** turns that untrusted response into a safe bundle:
   - every new id is prefixed `gen_` and can never overwrite authored content
   - only MATERIAL / CONSUMABLE items, with values and heals clamped by `economy.generated_item_value_cap` / `generated_effect_cap`
   - recipes must reference real items
   - traders are ambient, placed at `outer_market` / `verath` / `camp` or travelling the `road`, only trade materials and consumables, and sell at 1–3× value (no buy-low / sell-high loops)
5. **`apply_expansion_result()`** registers the bundle, stores it in `world_expansions`, gives the player the new item (and skill), and announces **✦ NEW ITEM / NEW SKILL / NEW RECIPE / WORD SPREADS**. It also logs to the `[L]` World Log.
6. **Per save:** `reload_world_growth()` (called on new game and load) clears every `gen_` item / NPC / recipe / skill, then re-registers only this save's bundles.

Guard rails (all in `config.py`):

| Setting | Effect |
|---------|--------|
| `WORLD_MAX_TRADERS_PER_LOCATION` (2) | Once a zone (or the road) has this many generated traders, a new bundle's goods are merged into an existing one's stock (`npc_extend`) instead of adding another NPC |
| `WORLD_MAX_EXPANSIONS` (60) | Per save. Later new actions are recorded as `capped` and never queued |
| `WORLD_RETRY_ON_LOAD` (3) | On load, up to this many `pending` / `no_ai` actions are re-queued if the AI is available (the background queue doesn't survive quitting) |
| `WORLD_YIELD_COOLDOWN_TURNS` (10) | Minimum turns between gathering an action's yield |

The admin panel's **World Growth log** lists every recorded action, its status, and what it added.

## Bot Adventurers (other "players" in the world)

`BOT_COUNT` (12) adventurers live alongside the player in every save, like
playerbots: they level, fight, loot, trade and craft on their own.

| Piece | Where |
|-------|-------|
| State: class, level, XP, HP, inventory (`{item_id: qty}`), gold (copper), activity, memory | `systems/bot_system.BotAgent`, stored as JSON in `bot_instances` |
| Cast: 3 authored bots (`data/bots/bot_templates.json`) + generated ones from name / archetype pools (`data/bots/bot_generation.json`), deterministic per save | `bot_system.generate_bots`, `BotManager.populate` |
| Behaviour: rules-based, **no AI calls**. Five archetypes (fighter, explorer, gatherer, trader, crafter) weight the choices | `systems/bot_brain.tick_bot` |
| Engine: per-save setup, per-turn tick (each bot every `BOT_ACT_EVERY_TURNS`), what the player sees, persistence | `core/bot_flow.py` |
| Player ↔ bot trading (bots have a real purse and bag) | `systems/bot_trade.py` |

What a bot does each act:
- **Hurt:** drinks a potion in the dungeon, rests in a safe zone, or walks home.
- **In the dungeon:** fights real encounters from `combat_system.ENCOUNTERS`, getting the XP, gold and loot from `ENEMY_TEMPLATES` (only fights it can survive); forages; or gathers world-growth yields. It heads home when its bag is full.
- **In a safe zone:** sells loot to the traders actually present, crafts from `engine.recipes` (bots brew their own potions from the salve recipe), buys a missing potion ingredient, then walks towards what its archetype wants. Fighters go to the deepest floor their level allows (Floor 2 at level 3, Floor 3 at level 5).
- **Trader bots** never dump stock on NPC traders. They keep up to 6 items to sell to the player.

The player sees bots through:
- a "Talk to Ryn (Lv 4 Warrior), …" option at the zone root (at most 4)
- `[W] Who's around`
- one activity line at most per turn in their zone: level-ups and defeats at once, fights / crafts / trades at most every other turn, and arrivals batched every `BOT_ARRIVALS_EVERY_TURNS`

World Log entries are only written for milestones (level-ups, defeats). Talking uses `AIService.generate_bot_line` (fast model, grounded in the bot's memory) with `bot_system.fallback_line` when offline. `BOT_AI_DECISIONS = True` restores the old "ask the primary model for every bot decision" path. It's slow and freezes bots without Ollama.

## Divergence System (How AI Classes Are Triggered)

The `systems/progression_tracker.compute_divergence_score()` function scores how far the player has deviated from the expected path:

| Signal | Score |
|--------|-------|
| Each `expected: false` option taken | +10 |
| No standard combo class matches player's classes | +40 |
| Each "wild catalyst" item (combo_catalyst=true, not in any known combo) | +20 |
| Each divergence flag set (refused_system, broke_tutorial, etc.) | +25 |

Score ≥ 30 = AI generates a unique class. Threshold configurable in `config.DIVERGENCE_THRESHOLD`.

---

## Save Format Versions

| Version | Changes |
|---------|---------|
| 1 | Initial — Player, scene position, AI cache |
| 2 | Added: gender, species_id, alignment, lives_remaining/used, guild_memberships, faction_standing_cache, active/completed_quest_ids, evolution_stage, background, perception_bonus, turn_count, last_safe_zone_id. Paired SQLite .db file introduced. |
| 3 | Added: identified_items, background_narrative, active_buffs, skill_cooldowns, play_time_seconds |
| 4 | Added: skill_uses, skill_levels (for the use-count-based skill leveling system from F8 of the skill audit) |

The paired SQLite world DB has its own `DB_SCHEMA_VERSION` (`persistence/world_db.py`), currently **6** (v6 added `world_actions` + `world_expansions`). Migrations run automatically on open.

## Skill System

Three skill types with distinct combat roles:

| Type | Combat menu | When fires |
|------|-------------|-----------|
| `ACTIVE` | Yes — player picks each turn | On selection |
| `PASSIVE` | No — contributes via `systems/passive_system.get_passive_modifiers` | Always — see stacking rules below |
| `TRIGGERED` | No — fires via `systems/passive_system.try_fire_trigger` | When `trigger_condition` matches event: `on_attack`, `on_hit`, `on_kill`, `on_low_hp` |

### Triggered events (`systems/combat_system.py`)

| Event | Fired from | When | Value |
|-------|-----------|------|-------|
| `on_attack` | `player_attack` | Every basic attack | bonus damage |
| `on_hit` | `player_attack` | Basic attack lands (always, today — no miss mechanic) | bonus damage |
| `on_kill` | `player_attack` | Basic attack drops the enemy | heals player |
| `on_low_hp` | `enemy_attack` | Hit takes HP from ≥ `LOW_HP_TRIGGER_THRESHOLD` (30%) of max to below it, player still alive | heals player |

`on_low_hp` fires on the threshold *crossing*, so it can't re-fire until the player climbs back above 30%. `trigger_condition` is matched exactly: list several events with commas (`"on_hit, on_kill"`); `"on_attack_or_kill"` matches nothing. A TRIGGERED skill with `cooldown_turns > 0` goes on the normal per-combat cooldown after firing.

`passive_system.fire_triggers()` returns a `TriggerFiring` per skill that fired (`try_fire_trigger()` is the summed-value shorthand). `player_attack` / `enemy_attack` take an optional `fired` list so `core/combat_handler` can log "✦ Arcane Strike triggers — bonus damage 16".

### Passive stacking rules (`systems/passive_system.py`)

| Bonus | Stack | Hard cap |
|-------|-------|---------|
| `attack_bonus` | additive | +20 dmg |
| `defense_bonus` | additive | +15 dmg reduction |
| `magic_defense_bonus` | additive | +10 dmg reduction |
| `bonus_damage_on_hit` | additive | +30 dmg |
| `dodge_chance` | additive | 40% |
| `crit_chance` | additive | 30% |
| `utility_tags` | set union | (presence-only) |

A skill is classified by keyword scan of its `name`/`description`: "iron skin"/"armor"→defense, "evasion"/"acrobat"→dodge, "tracking"/"persuasion"/etc.→utility tags. Unmatched PASSIVE skills with effects fall back to a generic contribution based on `effect_type` (BUFF→attack, SHIELD→defense, DAMAGE→bonus_damage_on_hit).

### Cooldowns

Cooldowns are **per-combat-encounter**:
- `reset_cooldowns_for_combat(player)` is called at the start of every `_run_combat`
- `tick_combat_cooldowns(player)` is called at the end of every combat round (not every game-loop turn)
- This stops the old exploit where a 5-CD skill was "off cooldown" after walking 5 menu steps in town

### Skill leveling (F8 — use-count based)

Skills grow with use. Every `USES_PER_LEVEL` (=5) casts in combat, the skill levels up by +1, capped at `Skill.max_level` (default 10). Each level adds:
- `+LEVEL_BONUS_FLAT` (1.0) to base value
- `+LEVEL_BONUS_COEFF` (10%) to stat scaling

Per-player level + use-count live in `player.skill_levels` and `player.skill_uses` dicts. `calculate_skill_damage` reads them automatically.

### Auto-learn on level-up (F9)

`level_system.add_experience(player, xp, class_registry, skill_registry)` calls `skill_system.grant_next_learnable_skill` after each level-up — auto-grants the next unlearned skill from the player's class `learnable_skills` list. Callers without registries (legacy / test) skip this gracefully.

Migration in `persistence/save_manager._migrate()`. Always backward-compatible (new fields have defaults).

---

## Event Bus Reference (`core/event_bus.py`)

All UI notifications are driven by events. Subscribe in `ui/notifications.py`.

| Event name | Payload keys | Fired by |
|-----------|-------------|---------|
| `LEVEL_UP` | `level`, `stat_points` | `systems/level_system` |
| `SKILL_ACQUIRED` | `skill_id`, `skill_name`, `rarity` | `systems/class_system`, `scenes/scene_base` |
| `CLASS_ASSIGNED` | `class_id`, `class_name`, `rarity` | `systems/class_system` |
| `ANOMALY_DETECTED` | *(none)* | `systems/class_system` (before AI gen) |
| `ITEM_FOUND` | `item_id`, `item_name`, `rarity` | `systems/inventory_system`, `scenes/scene_base` |
| `WARNING` | *(none)* | various |
| `PLAYER_DIED` | `lives_remaining`, `cause` | `systems/lives_system` *(future)* |
| `QUEST_STARTED` | `quest_id`, `title` | `systems/quest_system` *(future)* |
| `QUEST_COMPLETED` | `quest_id`, `outcome` | `systems/quest_system` *(future)* |
| `SPECIES_EVOLVED` | `species_id`, `stage`, `new_name` | `systems/class_system` *(future)* |
| `FACTION_RANK_CHANGED` | `faction_id`, `old_rank`, `new_rank` | `systems/faction_system` *(future)* |

---

## Development Workflow

```bash
# Start a new feature
git checkout -b feature/npc-system

# Enable the feature flag while developing
# config.py: FEATURES["npc_system"] = True

# Run the game
python main.py

# Before committing, disable the flag if not ready for main
# config.py: FEATURES["npc_system"] = False

git add -p   # stage only intentional changes
git commit -m "feat: add NPC template loading + instance creation"
git push origin feature/npc-system
```

### Implementation order for next phases
1. `species_system` + `alignment_system` (self-contained, no world_db needed)
2. `stat_gating` — `systems/stat_gate.py` (needed by NPC system)
3. `npc_system` (requires world_db + stat_gating)
4. `quest_system` (requires npc_system)
5. `guild_system` (requires quest_system)
6. `lives_system` (tiny, enable anytime after world_db)
7. `faction_system` + `auction_house` (requires guild_system)
