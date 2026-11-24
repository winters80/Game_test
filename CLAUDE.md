# SYSTEM BREAKER — Developer Reference

> Read this before touching any code. It covers architecture, content authoring, trigger strings, feature flags, and AI integration contracts.

---

## Architecture in One Paragraph

The game is a Python text RPG (terminal, panel and browser front ends). **Static content** (class definitions, skill definitions, item templates, species, guilds, NPCs, quests, scene scripts) lives in **JSON files** under `data/` and is read-only at runtime. **Dynamic world state** (NPC memory, active quests, faction relationships, auction listings, death records) lives in a **SQLite database** at `saves/{slot}.db`. The player's own serializable data (stats, inventory, alignment, lives, etc.) lives in `saves/{slot}.json` as a Pydantic v2 model. The two databases are always opened and closed together by `persistence/save_manager.py`.

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
| `ui/` | All Rich rendering — **only files under `ui/` import `rich`** (and `textual`). `ui/prompt.py` is the only place that asks the player anything; `ui/tui_app.py` is the Textual front end |
| `utils/` | `logging_setup.py` (rotating logs) and `session_log.py` (the diagnostics log) |
| `assets/` | Optional pictures and sound, found by file name (`ui/media.py`). `ASSET_LIST.md` (from `scripts/asset_list.py`) lists every usable file with a prompt |
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
Add to any `data/quests/*.json` (`QuestRegistry.load_from_dir` loads them all). Quest state machine states are defined in the JSON. Offer it by adding a seed to the giver NPC's `quest_seeds`.

Stage `completion_condition` keys (`entities/quest.CONDITION_KEYS`):

| Key | Met when |
|-----|----------|
| `has_item` / `has_items` | the player holds the item / every `{item: qty}` |
| `has_flag` / `world_flag` | the player / world flag is set. Useful flags: `cleared_<gate_id>`, `slew_<occupant_id>`, `done_<template_id>` |
| `min_level` | player level ≥ N |
| `turn_in` | the player picks « Report: <title> » at that NPC. Inside `all`, the option only appears once the rest is met |
| `all` / `any` | every / at least one sub-condition |

**Chains:** give each quest `reward_flags: ["done_<id>"]` and the next quest's seed `trigger_flag: "done_<id>"`. Seeds also take `min_level` (hidden until then), `min_faction` (`{faction_id: standing}`, e.g. Lady Vos's `crown_ascension` at `verath_crown` 20) and `already_given_flag`. `chain_id` / `chain_step` / `chain_kind` (`skill` / `item` / `story`) are metadata for tests and tools. `reward_skills` grants real skill ids; `reward_items` grants items. Hand-ins use `take_item:X:N` in `on_advance_triggers`.

### New gate (instanced dungeon) *(boss_succession)*
Add to `data/dungeons/gates/*.json` (see "Gates" below). No scene edit needed: the Gate Registry lists every gate.

### New species *(species_system feature flag must be True)*
Add to `data/species/species_definitions.json`. Define `evolution_path` as an ordered list of stages.

### New scene
Add a new `scenes/data/*.json` file. `SceneRegistry.load_from_dir()` loads all JSON in that directory. Scene ID must match the filename stem.

### New zone
Add to `data/world/zones.json`.

### New boss lair or successor *(boss_succession)*
Add to `data/dungeons/*.json` (see "Boss Succession" below). Enemies go under `enemy_templates`; a lair lists its `occupants` and each occupant's `successors`. Put the lair in a scene with an option whose trigger is `boss_lair:LAIR_ID`. `test_characters.py` fails if an occupant is unreachable, has no successor, or uses an unknown enemy or item.

---

## Asking the Player (`ui/prompt.py`) and the Front Ends

Never call `questionary` directly (`tests/test_ui.py` fails if anything outside `ui/prompt.py` does). Use:

```python
from ui import prompt as uip
pick = uip.select("Choose:", choices=["Fight", uip.Choice("Flee", value="flee", disabled="Rooted")]).ask()
name = uip.text("Name:", default="Ash", instruction="(blank to cancel)").ask()
ok   = uip.confirm("Sure?", default=True).ask()
```

Give Back / Cancel entries the value `""`, never `None`: a `Choice` with value `None` answers with its label (that crashed Admin → Art & audio; `tests/test_ui.py` checks). The call shape is questionary's (build, then `.ask()`), and the terminal backend still calls `questionary.select(...)` at call time, so tests can keep patching it. `uip.set_backend(...)` swaps the front end; a backend may also provide renderer hooks (`clear`, `status`, `spinner`, `any_key_hook`, `hides_title`, `set_busy_text`) that `ui/renderer.py` checks via `_ui_hook`. Every answer is written to the diagnostics log.

| Front end | Start | How |
|---|---|---|
| Classic terminal | `python main.py --classic` (also when Textual is missing or stdio isn't a tty) | Rich + questionary |
| Panels (Textual), the default | `python main.py` / `--tui` | `ui/tui_app.GameApp`: the engine runs unchanged on a worker thread; `TuiBackend` turns prompts into widgets and blocks the engine on a queue; a Rich render hook on `renderer.console` sends everything printed to the story log, `tui_app.StoryLog` (the app's console carries `GAME_THEME`). `StoryLog` renders each entry at its own width (RichLog would measure against the app console, which under the web driver can be ~20 columns) and re-writes its last 800 entries when its width changes (`tests/test_ui.py` checks both) |
| Browser | `python main.py --web` | `ui/web_server.make_server` (a textual-serve `Server` subclass with the template `ui/web/app_index.html`) runs `main.py --tui` per browser tab. Each page load gets an art id: websocket `/ws/<id>`, env `SB_WEB_ART=<id>` for the game, which `GameApp.set_art` turns into `web_server.publish` (`saves/web_art/<id>.json`) instead of drawing in the sidebar; the page polls `/art/<id>` and shows `/art-file/<id>/<v>` full size in `#art-panel`. `safe_asset` serves only image files under `assets/`. Nothing may write to the real stdout in this mode (it is the protocol channel). After the game quits, the page's own keydown handler turns F5 / Ctrl+R / Enter into a fresh start (the dead xterm otherwise keeps focus and swallows them) |

`core/input_handler.prompt_choice` passes locked scene options as disabled `Choice`s with their `lock_reason` (+ `hint_text`), so the numbers match and the player sees what unlocks them. Hotkey entries (`[K] Skills` …) become the Textual hotkey bar.

## Art and Audio (`ui/media.py`, `assets/`)

Everything optional and found by file name, in `assets_local/` (git-ignored: files whose license forbids publishing them raw, e.g. Pixabay / Sonniss) then `assets/` (CC0 / own work only; `media.asset_dirs()`); a missing file, library (`pygame-ce`, `textual-image`) or sound device means silence / no picture, never an error. Settings in `saves/settings/media.json` (Admin → Art & audio).

| Hook | Called from | Uses |
|---|---|---|
| `on_scene(scene, node, title)` | `ui/scene_renderer.render_scene` | `images/scenes/<scene>.<node>` → `ART_ALIASES[node]` → `<scene>`, `audio/ambience/…` (looped), music by `MUSIC_FOR_NODE` / `MUSIC_FOR_SCENE` (fallback `explore`) |
| `on_gate(gate_id, name)` | `core/gate_flow.run_gate` | `images/scenes/gate.<id>` → `gate`, sfx `gate_enter` (else `rift_open`), ambience `gate.<id>` → `gate` |
| `bot_voice(archetype)` | `core/bot_flow.talk_to_bot` | `audio/voice/bots/<archetype>/greeting` → `bots/trader` (`media.BOT_VOICES`) |
| `on_npc(npc_id, name)` | `core/dialogue_handler._run_dialogue` | `images/npcs/<npc_id>`, `audio/voice/<npc_id>/greeting` |
| `on_boss(occupant, enemies, title)` | `core/lair_flow.enter_lair` | `images/bosses/<occupant_id>` or an enemy template id |
| `on_item(item_id, name)` | Items → Inspect | `images/items/<item_id>` |
| `on_combat_start(boss)` / `on_combat_end(result)` | `core/combat_handler._run_combat` | music `combat` / `boss`, sfx `victory` / `defeat` |
| `sfx(name)` | `renderer.print_combat_action` (`hit` / `spell`), enemy hits, rifts, the Ledger, `register_event_sounds` | `audio/sfx/<name>` (names in `media.SFX`), 0.25 s throttle per name |
| `system_voice(key)` | events (level up, class, divergence, milestone, sedition), new game, lives | `audio/voice/system/<key>` (lines in `media.SYSTEM_LINES`) |

Effects go through `media.find_variant` too: `sfx(name)` picks one of `<name>`, `<name>_2` … at random, pooled from both folders by `media.variants` (an `assets_local/` take replaces its namesake in `assets/`). Combat cues: `combat_effects.EnemyTurn.cues` (`enrage`, `summon`, `boss_warning` when a heavy blow is announced) are played by `combat_handler._combat_sounds`; `on_combat_start(boss=True)` plays `boss_reveal`; a lost life plays `life_lost`. Purchased packs: `scripts/stage_packs.py --zips DIR --out STAGED` (`PACK_SOUNDS`: pack file → game sound, trimmed) then `master_audio.py --src STAGED --out assets_local/audio`; that gives every Gate its own `ambience/gate.<id>` plus scene / spot ambience, the five new effects and extra takes, all git-ignored.

`media.audio_status()` → (ok, sentence) says why sound is off (no files, switched off, `AudioPlayer.error`: library missing / device failed); `startup_notice()` prints it under the title when there are sounds but no audio, Admin → Art & audio shows it with **Test sound** (`test_sound()`), and the launchers warn when `pygame-ce` didn't install. NPC and bot voices go through `media.find_variant(kind, key)`: `<key>` or any `<key>_<n>` take, chosen at random. Music: a mood without a file borrows one through `media.MUSIC_FALLBACK` (verath / undermarket → ledger, fracture_core → boss, …), then `explore`. 83 sounds ship (every `media.SFX` effect, ten ambience loops, four music tracks (no `explore`: it was dropped, so explore-mood places play ambience only), System lines, three takes per cast member and bot type), made by the owner from `assets/audio_prompts/`.

Pictures go to the front end's `art(path, caption)` hook (ui/prompt backend). A scene node with no picture of its own (`show_art(..., clear=True)`) sends `art("", "")`, which hides the panel instead of leaving the last place's (or boss's) picture up; `on_combat_end` resets the state so the next scene always re-sends: the Textual app shows them at the top of the sidebar (`#art`), with `AutoImage` in a terminal (`HalfcellImage` under the web driver if `SB_WEB_ART` is missing; normally the browser page shows the real file, see the Browser row above); `tui_app._art_size` sizes the widget to the picture's aspect, at most `ART_MAX_ROWS` (22) rows; the classic terminal shows none. Newer hooks: `ui/prompt` select answers → `media.ui_answer` (`ui_confirm` / `ui_cancel`, setting `ui_sounds`), the Textual `#choices` highlight → `ui_hover` (silent for 0.3 s after a menu appears), `renderer.print_error` → `denied`, `[I]` / `[J]` → `menu_open`, basic attack → `swing`, flee → `dodge`, `combat_handler._combat_sounds` after each enemy turn (`block` when a ward / evade charge was spent, `dodge` on a clean miss, `player_hurt` for hits ≥ 15% max HP else `enemy_hit`, and `media.heartbeat` looping `sfx/heartbeat` on reserved channel 2 below `HEARTBEAT_BELOW`, stopped by `on_combat_end`), Gate trap / treasure rooms → `trap` / `chest_open`, `enter_lair` → `lever`, scene change → `footstep_<FOOTSTEP_FOR_SCENE>`. Channels 0-2 (ambience, voice, loop) are reserved with `set_reserved(3)`. Settings `ui_hover` (default off) and `muted` (list of `kind/name` such as `music/camp` or `voice/mira_innkeeper/greeting_2`): `is_muted` makes `_find_any` / `find_variant` skip them; Admin → Art & audio → listen and mute (`menus._sound_listen_menu`, `media.all_sounds` / `preview` / `set_muted`). `scripts/master_audio.py --src FOLDER` masters originals into `assets/audio` (`KINDS`: target RMS per kind, -3 dBFS peak ceiling, equal-power loop crossfade for music / ambience / the heartbeat, trimmed and faded one-shots, softened UI); always master from originals since a crossfade shortens the loop. `--report` measures what's there. Other sounds: `crystal_shatter` (`renderer.print_combat_action` on a crystal/shard/golem defeat), `ledger_seal` (fines in `core/law_flow`, unseen crimes), `potion` (Items → Use of a heal), warden voice `voice/ledger_warden/greeting` on a confrontation. The 20 shipped pictures are WebP. `assets/audio_prompts/manifest.json` is the audio prompt pack; `scripts/asset_list.PACK_TARGETS` maps each spec id to game files and its prompt replaces the generated one (`tests/test_media.py` checks every id is mapped and every sfx target is in `media.SFX`). `scripts/fetch_sounds.py` (run by the user, not the game) downloads Kenney's CC0 packs, adds `--extra FOLDER:LICENSE` folders, picks a file per `TARGETS` entry by file-name keywords (`KIND_AVOID` keeps crowds out of music), trims to `.ogg` with ffmpeg and zips it with `sounds_manifest.json` (license per file) and `CREDITS.md`; when integrating such a zip, `cc0` files go to `assets/`, everything else to `assets_local/`. Add a `TARGETS` row for a new sound name (`tests/test_media.py` checks every `media.SFX` has one). When you add a new place, NPC, boss, item, sound name or System line, `scripts/asset_list.py` picks it up; keep `media.SFX` / `SYSTEM_LINES` / the prompt tables in `scripts/asset_list.py` in step. `asset_list.MOST_WANTED` is the ordered "make these first" list shown at the top of `ASSET_LIST.md` (missing ones only; `tests/test_media.py` checks each is a real file name). `.claude/launch.json` has a `game-web` preview config (browser version on port 8765).

## Diagnostics Log (`utils/session_log.py`)

On by default (Admin → Diagnostics log; remembered in `logs/diagnostics_settings.json`). `logs/sessions/session_<time>.log` gets plain-text screen output (a render hook), `>> CHOICE` lines from `ui/prompt`, `## EVENT` lines (`session_log.event("combat_start", ...)` — the choice handler and combat already emit them), INFO+ log records from every thread, and `!! CRASH` tracebacks (`sys.excepthook` / `threading.excepthook`). Admin → Export diagnostics zips logs + the last 3 sessions + a setup summary into `logs/exports/`. Add `session_log.event(...)` at any new milestone worth seeing in a bug report.

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
| `boss_lair:X` | *(boss_succession)* Enter boss lair X: meet / study / fight its current occupant, or cross it while empty (`core/lair_flow.enter_lair`) |
| `gate_registry` | *(boss_succession)* Open the Gate Registry: every gate, its rated level and current master (`core/gate_flow.open_gate_registry`) |
| `enter_gate:X` | *(boss_succession)* Run gate X directly: rooms, then its lair (`core/gate_flow.run_gate`) |
| `take_item:X[:N]` | Remove up to N (default 1) of item X, never below zero. Quest hand-ins use it |
| `class_awakening` | Run the Class Awakening (`core/class_awakening.awaken`): divergence ≥ threshold + AI online → AI class on a background Future (spinner); else nearest authored class, else `fallback_generated`. Once per save (`class_awakened`) |
| `open_rift:N` / `open_rift:+N` | *(boss_succession)* Open a new Gate at level N, or the player's level + N, on the next turn (AI-made if online, else a Deep Echo). Special quest lines use it |
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
| `world_action:VERB:SUBJECT` | AI-only tag describing what the player did (e.g. `world_action:mine:gold`). No direct effect; feeds world expansion. Crime verbs (steal, pickpocket, burgle, smuggle, bribe…) also commit the crime |
| `crime:KIND` | *(law_system)* Commit a crime (`theft`, `burglary`, `bribery`, `smuggling`, `assault`, `trespass`, `sedition`): `law_system.commit_crime` rolls witnesses; publishes `CRIME_COMMITTED`. Counts as a cost in content health's farmable-gold check |
| `heat:±N` | *(law_system)* Change the player's heat (0-100), e.g. Silas's `heat:-30` |
| `ledger_settle` / `ledger_surrender` / `ledger_warrants` | *(law_system)* The Ledger House counter, the cells, the warrant board (`core/law_flow`) |

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
| `boss_succession` | Boss lairs whose occupant changes after each kill, Gates, and new rifts (`data/dungeons/`). State needs `world_db` to persist |
| `law_system` | The Ledger: crimes, heat, warden encounters, bot crimes and arrests |

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
| `dungeon_lairs` | *(schema v7)* Who holds each boss lair now, as a `LairState` JSON blob (occupant, lineage name, generation, vacant/occupied, who's coming and when) |
| `lair_history` | *(schema v7)* Every boss kill: occupant, killer (player / bot, name, level), killing blow, aftermath, successor |
| `generated_dungeons` | *(schema v8)* Gates and lair successors the world grew (`kind` gate / occupant), re-registered on load by `core/gate_growth.reload_generated_dungeons` |

**Never query the `.db` from outside `persistence/world_db.py`.** All access is through typed methods on `WorldDatabase`.

**Threads:** the connection is opened with `check_same_thread=False` and a `_LockedConnection` factory (an `RLock` around execute / commit / rollback; `transaction()` holds it for the whole block), because the background guild simulation and world growth write from worker threads. Before v9 every background guild tick failed with SQLite's same-thread error.

**Maintenance (schema v9):** pragmas on every open (WAL, `synchronous=NORMAL`, `busy_timeout=5000`); indexes in `_INDEXES_SQL` (created `IF NOT EXISTS` on every open, so old saves get them); `prune()` trims `turn_log` (5,000 rows) and shown `world_events` (1,000) on save; `integrity_ok()`, `backup_to(path)`, `stats()`, `insights()` (aggregates for `scripts/playtest_report.py`). `save_manager.save_game` writes the JSON atomically and keeps 3 rotating backups in `saves/backups/` (at most one per 5 minutes); `load_game` restores the newest good backup if the `.db` fails `quick_check`.

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

**AI skill prompts** get `context["known_skills"]` (`prompt_builder.describe_known_skills`: "Power Strike (ACTIVE: damage)") and a `skill_gap_hint` so the model fills a gap in the kit instead of duplicating it. The three callers (give_skill, quest rewards, Inspect) add it to the context dict; `AIService.generate_skill(..., skill_registry=)` can also build it.

**Adding a new AI call:** put the prompt + client call in a
`ContentGenerator` method, add a wrapper on `AIService` that returns `None`
(or `[]`) on failure, and call the wrapper. Purely cosmetic calls
(e.g. follow-up option flavour) log failures at INFO. The console handler
shows WARNING and above from the main thread, so a WARNING would print mid-scene. Records from background threads (world growth, quests, the director) only go to the log files.

### Model roles and review (`ai/model_registry.py`, `core/model_admin.py`)

Roles `primary` / `fast` / `fallback` resolve env var → `saves/settings/ai_models.json` (Admin → AI models) → `config.py`. `bootstrap.setup_ai` reads `active_models()`, falls back to the fallback model when the primary isn't installed, records `engine.ai_models`, and can be re-run live (it stops the old background generator). `data/ai/model_catalog.json` lists known models (roles, size, RAM, a relative quality score) and RAM `tiers`; `load_catalog(remote=True)` also fetches `config.MODEL_CATALOG_URL` (this repo's `main`) and caches it, so **updating recommendations is a JSON edit on GitHub**. `review()` suggests a better model per role for the machine's RAM (a recorded benchmark beats the catalog estimate) and offers unknown installed models for benchmarking; `benchmark()` scores JSON validity (45), option picking (35) and speed (20). `installed_models()` / `pull_model()` exist so `core/` never builds an `OllamaClient`.

### Decisions (`ai/decision.py`)

`AIService.decide(question, options, context) → Decision(index, confidence, source) | None` picks one of N declared options with a schema-constrained call (`{"choice": enum, "confidence": 0-100}`) instead of generating text. Providers (`config.DECISION_PROVIDER`): `ollama` (fast client, default), `openai_compatible` (`DECISION_ENDPOINT`, `DECISION_MODEL`, key in the `DECISION_API_KEY` env var; opt-in because it sends scene text out), `off`. Used by `[?]`: unless the text ends in `?`, `situation_query._match_existing` asks whether it means an on-screen choice; at ≥ `DECISION_MATCH_CONFIDENCE` the engine takes it (`engine._pending_option`, returned by `prompt_choice`). Use it wherever code would otherwise generate text and then parse a choice out of it.

`AIService.decide_many(questions, context)` / `submit_decide_many_async(...)` ask several independent questions in **one** call (schema `{"q0": enum, "q1": enum, …}`; `Decision.confidence` is a flat 0.6 for batches). Two users, both off when `DECISION_PROVIDER == "off"` or the AI is offline:

| Use | Where | How |
|---|---|---|
| Bot goals | `core/bot_flow.plan_bot_goals` (from `tick_bots`) | Every `BOT_GOAL_EVERY_TURNS` (36) one background call; options per bot from `bot_brain.goal_options` (zones its level allows, `ZONE_HINT` labels). The answer sets `BotAgent.goal_zone` until `goal_until` (`BOT_GOAL_HOLD_TURNS`); `bot_brain._destination` honours it, the rules do the rest. Bots waiting for the player are skipped |
| Boss tactics | `core/combat_handler._plan_tactics` / `_tactic_for` | Submitted at the start of each round (while the player chooses), read at the enemy's turn with `BOSS_TACTIC_WAIT` (1.5 s). Options from `combat_effects.tactic_options`: basic + non-heavy abilities off cooldown (`Enemy.ability_last`), heal only below 70% HP, summon only with room. `enemy_turn(..., chosen=)` uses it, except on an announced heavy turn (`heavy_due`). `BOSS_TACTICS = False` turns it off |

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
1. **Class generation** — when divergence score ≥ `DIVERGENCE_THRESHOLD` (default 30), at the Class Awakening (`class_awakening` trigger, `core/class_awakening.py`) via `AIService.submit_class_generation_async` (a background Future; `await_class_result` with the spinner running). `class_system.resolve_combo_class()` still does the synchronous layered resolution for other callers.
2. **NPC dialogue** *(npc_system)* — when NPC has no pre-written dialogue for the player's current context.
3. **Quest generation** *(quest_system)* — fired from `dialogue_handler` when the player picks an injected `« Is there any work I could take on? »` option. Triggered by an explicit `"ai_dynamic"` seed on the NPC, **or implicitly** when the NPC has no available seeds left and disposition ≥ `AI_QUEST_DISPOSITION_MIN` (default 30) and `_ai_offered_{npc_id}` flag is unset.
4. **Narrative generation** — when a scene option has no `leads_to` text and is flagged `"ai_narrative": true`.
5. **World growth** — the first time the player takes a new `[?]` action (`world_action:VERB:SUBJECT`), the primary model is asked in the background how the world should respond. See "World Growth" below.
6. **New Gates** — on a rift surge or `open_rift:` (background `new_gate` task, `AIService.submit_gate_async`).
7. **Lair successors** — after a boss kill, with chance `AI_SUCCESSOR_CHANCE` (background `lair_successor` task, `AIService.submit_lair_successor_async`). See "Growing dungeons" below.

### AI response schemas (validated by `ai/response_validator.py`)
All AI calls must return JSON validated against Pydantic models. If validation fails, retry up to `OLLAMA_MAX_RETRIES` times, then use the fallback.

| Generator method | Returns | Fallback |
|-----------------|---------|---------|
| `generate_class()` | `AIClassResponse` | `fallback_generated` class from combo_classes.json |
| `generate_narrative()` | plain text string | None (scene uses static text) |
| `generate_quest()` *(future)* | `AIQuestResponse` | nearest matching template quest |
| `generate_npc_dialogue()` *(future)* | `AINPCDialogueResponse` | NPC's `dialogue_hooks["default"]` |
| `generate_world_expansion()` | `AIWorldExpansionResponse` (+ optional skill) | None: the action stays logged, nothing is added |
| `generate_gate()` | `AIGateResponse` (names and texts only) | A Deep Echo opens instead |
| `generate_lair_successor()` | `AISuccessorResponse` | The authored successor moves in |

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

`BOT_COUNT` (24) adventurers live alongside the player in every save, like
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
- **In the dungeon:** a healthy fighter or explorer pushes on to the deepest floor its level allows, if one of that floor's fights looks comfortable (`bot_brain._can_handle`). Otherwise it fights real encounters from `combat_system.ENCOUNTERS`, getting the XP, gold and loot from `ENEMY_TEMPLATES` (only fights it can survive); forages; or gathers world-growth yields. It heads home when its bag is full.
- **Boss lairs:** fighters and explorers on a lair's floor sometimes take on its boss (`bot_brain._try_boss`, see "Boss Succession").
- **In a safe zone:** sells loot to the traders actually present, crafts from `engine.recipes` (bots brew their own potions from the salve recipe), buys a missing potion ingredient, then walks towards what its archetype wants. Fighters go to the deepest floor their level allows (Floor 2 at level 3, Floor 3 at level 5).
- **Trader bots** never dump stock on NPC traders. They keep up to 6 items to sell to the player.
- **Guilds:** an unaffiliated bot of level `JOIN_GUILD_LEVEL` (3)+ joins a guild that suits its archetype (`bot_brain.GUILD_FOR`), or the player's founded guild (then also a real `guild_members` row). `BotAgent.guild_id` / `guild_rank`; rank rises every 4 levels. Shown in `[W]`.
- **Bidding:** every 5 turns bots in a town may bid (`core/bot_flow.bot_auction_bids`, `BOT_BID_CHANCE`) on a listing they want, priced by `bot_trade.auction_interest` (potions when short, materials for crafters/gatherers/traders, gear for fighters/explorers; ≤ 40% of their purse). Bidder `bot:<id>`; `settle_bot_auctions` charges the winner and gives it the item.
- **Auction:** traders, gatherers and fighters in a town consign their most valuable item (≥ `AUCTION_MIN_VALUE` 15g) to the auction house (`seller_npc_id = "bot:<id>"`, a day long, on `state.turn_number`). `auction_system.tick_auction` returns the settlements; `bot_flow.settle_bot_auctions` pays the seller (minus a 10% house fee) or returns unsold items.

The player sees bots through:
- a "Talk to Ryn (Lv 4 Warrior), …" option at the zone root (at most 4)
- `[W] Who's around`
- one activity line at most per turn in their zone: level-ups and defeats at once, fights / crafts / trades at most every other turn, and arrivals batched every `BOT_ARRIVALS_EVERY_TURNS`

World Log entries are only written for milestones (level-ups, defeats). Talking (`core/bot_flow.talk_to_bot`) is player-style chat: topics `greeting` / `catch_up` / `tips` / `build` / `guild` / `team_up` (`systems/bot_chat.TOPICS`). `bot_chat.summarize_memory` folds the raw memory log into first-person phrases ("been grinding Floor 1: four fights, mostly crystal spiders"), prioritising fights and Gates over trading; `reply()` gives the deterministic per-archetype fallback, varied by `ChatContext.seed`. Online, `AIService.generate_bot_reply` (fast model) gets the summary plus facts (Gates for the player's level, a changed lair, party yes/no) and lines that `looks_robotic()` (recite the log) are replaced by the fallback. `team_up` sets `BotAgent.waiting_until` (30 turns): the bot stays in town (`bot_brain.tick_bot` step 1b) and sorts first in `gate_flow.companion_candidates`; a Gate run clears it. Memory lines a new bot action writes should match a `bot_chat._PATTERNS` entry, or they're shown as-is. `BOT_AI_DECISIONS = True` restores the old "ask the primary model for every bot decision" path. It's slow and freezes bots without Ollama.

## The Ledger (`systems/law_system.py`, `core/law_flow.py`)

The System's own law: faction `the_ledger` (neutral, alignment −100…100, rival to `system_breakers` and `shadow_network_faction`). It enforces everyday law on players and bots, and treats work towards the reset as sedition.

| Piece | Where |
|---|---|
| Rules (pure): `CRIMES` (heat, bounty, standing), `WITNESS` per zone, `stealth()` (AGI+LCK, ≤ 45%), `commit_crime`, `cool_down`, `check_sedition`, `fine_for`, `jail_turns`, `enforcement_due` | `systems/law_system.py` |
| Player record: `heat` (0-100, cools 1/hour), `bounty` (copper), `crimes` {kind: n} | `entities/player.py` (save v5) |
| Encounters: `tick_law` (per turn, from `game_loop`), `confront` (pay / jail / talk / run / fight), `settle_at_counter`, `surrender`, `warrant_board`, `arrest_bot`. Warden fights use `_run_combat(..., nonlethal=True)`: losing means the cells, not a life | `core/law_flow.py` |
| Content: the Ledger House (`verath_city.ledger_house`), Auditor Castell + Silas the Fence (`data/npcs/npcs_ledger.json`), chain `ledger_order_1..3` (`data/quests/ledger_quests.json`), thief options (`village_start.root.lift_purse`, `verath_city.market_district.palm_ring` / `burgle_townhouse`), the courier choice in the Undermarket, items in `data/items/law_items.json` | data |
| The tribunal: lair `ledger_tribunal` (`data/dungeons/ledger_tribunal.json`, Floor 3, `min_level` 99 so bots never fight it). `dungeon_floor3.core_chamber.destroy_the_system` needs `ledger_tribunal_broken` | data |

Rules: `PATROL_HEAT` 25 (wardens in `LAWFUL_ZONES`, chance heat/400 per turn), `HUNTER_HEAT` 60 (anywhere but Gates, heat/900), Ledger standing ≤ `WANTED_STANDING` (−40) counts +20 heat. `SEDITION_FLAGS` are reported once each (always witnessed). The Thief background (`criminal_record`) costs −10 standing once and +10% witness chance. Deputies (standing ≥ 40 in `fine_for`; rank `deputy` at 20) pay half fines.

Bots: `BotAgent.allegiance` (`ledger` / `breakers` / `outlaw` / "", fixed per id by `bot_system.allegiance_for`), `heat`, `jailed_until`. In town, outlaws and Breakers commit crimes (`bot_brain._bot_crime`, `BOT_CRIME_CHANCE`); Ledger bots arrest the most wanted bot in their zone (`_bot_patrol`, `BOT_PATROL_CHANCE`, deputies get `LEDGER_BACKUP`); a jailed bot does nothing until released. Events `crime` / `arrest` (milestones), `arrest_fail` (routine). `bot_chat` mentions allegiance in the guild topic, and a Ledger bot refuses to team up with a player at heat ≥ 25.

## Boss Succession (the dungeon remembers who you killed)

A boss room is a **lair** whose occupant changes after each kill. A wizard dies and comes back as a lich; priests come to cleanse the lich's den and are corrupted by it; a new scholar moves in and the cycle repeats.

| Piece | Where |
|-------|-------|
| Content: enemies, lairs, occupants, texts, successor rules | `data/dungeons/*.json`, loaded by `entities/lair.LairRegistry` (references validated on load) |
| Logic: initial state, scaled spawn, successor choice, arrival | `systems/lair_system.py` (pure) |
| Engine: `boss_lair:X` trigger, per-turn arrivals + rumours, bot kills, bot view | `core/lair_flow.py` |
| State per save | `dungeon_lairs` / `lair_history` (world DB v7), mirrored in `engine._lair_states` |

Lairs today: **the Crystal Heart** (Floor 2: Crystal Golem → Brood Matriarch or, if killed by magic, the Shardmind → goblin warband → Reforged Golem → …) and **the Drowned Sanctum** (Floor 3: Archmage → Hollow Lich, or the Ashen Cabal if the phylactery is smashed → Order of the Pale Lamp → Lampless Saint or, if the lamp is relit, the Lampwarden → a new Archmage).

Occupant fields:

| Field | Effect |
|-------|--------|
| `title`, `intro`, `victory`, `omen`, `rumour` | Texts; `{name}` is the lineage name. `omen` shows in the empty lair while the successor moves in; `rumour` spreads when it arrives |
| `arrival_omen`, `arrival_rumour` | The same, written by the newcomer. They win over the dead occupant's `omen` / `rumour`, so a branching chain always hints at what actually comes |
| `name_pool` | A fresh name, drawn in order each time this occupant takes the lair. Empty → inherit the previous occupant's name (the lich keeps the wizard's) |
| `enemies` | Enemy template ids (lair `enemy_templates` or `combat_system.ENEMY_TEMPLATES`) |
| `study` | `{stat, min, text}`: with the stat, studying first gives a surprise round |
| `aftermath` | Choices over the body (`id`, `label`, `text`, `triggers`, the same state triggers as scene options). The **first** is the default, used for bot kills |
| `successors` | `{to, weight, when}`. `when` can match `killed_by` (`magic` / `physical`), `killer` (`player` / `bot`), `aftermath`, `alignment_min` / `alignment_max` (player kills). Matching rules are weighted; none match → the lair's `fallback` |

Lair fields: `zone_id`, `min_level` (bots), `respawn_turns` (how long it stays empty), `first_occupant`, `clear_flags` (set when the player kills any occupant **or** crosses the empty lair; `boss_golem_defeated` opens Floor 3), `fallback`.

Rules:
- The killing blow is `magic` for a spell (spell_type, MP cost or INT/WIS scaling, `lair_system.killing_blow_kind`), else `physical`. `_run_combat` returns `"victory" / "fled" / "defeat" / "none"` and leaves it in `engine._last_killing_blow`.
- A returning occupant gets `+LAIR_SCALING_PER_RETURN` (10%) HP / ATK / rewards per previous stay, capped at `LAIR_MAX_SCALING` (40%).
- Bots challenge a lair with chance `LAIR_BOT_CHALLENGE_CHANCE` when its level allows and expected damage < `LAIR_BOT_SAFETY` × HP. They leave each lair's first occupant alone for `LAIR_BOT_GRACE_TURNS` (250), so the player gets the first shot. Bot kills advance the chain and go to the World Log.
- `[L]` World Log shows who holds each lair now; kills and arrivals are logged as `lair` events.

## Gates (instanced dungeons with succession)

Since the Fracture, rifts ("Gates") open all over Aethoria. Each gate is a short run: 3 rooms, then a boss lair that follows the Boss Succession rules above. There are **32 gates** at levels 2–18 (mythical, technology, paranormal, elemental, and otherworldly: cosmic, insect, time, fungal, celestial, eldritch, outlaw).

| Piece | Where |
|-------|-------|
| Content | `data/dungeons/gates/<group>.json`: `enemy_templates` + `gates`. Gate materials are in `data/items/gate_materials.json` |
| Models | `entities/gate.py`: `GateDefinition` builds its `lair` as a `LairDefinition` (`lair_id` = gate id, `zone_id` = `gate:<id>`) and `GateRegistry` registers it into the `LairRegistry` |
| Engine | `core/gate_flow.py`: registry menu, `run_gate`, `bot_gate_view` |
| Where players find them | The **Gate Hall** node in `village_start` and `verath_city` (Registry + quest givers), and the wardens' fire at `camp_rest` |

Gate fields: `gate_id`, `name`, `theme`, `min_level` (enterable from `gate_flow.entry_level`: `min_level - GATE_LEVEL_GRACE`, but only for Gates rated `GATE_GRACE_FROM_LEVEL` (5)+; below that one level is too big a step, and quest seeds for those Gates open at the Gate's own level), `region`, `description`, `entry_text`, `material`, `rooms`, `lair`. Rooms:

| `kind` | Fields | Effect |
|--------|--------|--------|
| `fight` | `enemies` | A real combat; fleeing or dying ends the run |
| `trap` | `check {stat, min, success, fail, damage_pct}`, `loot` | Pass the check for the loot; fail and lose `damage_pct` of max HP (never below 1) |
| `treasure` | `loot`, optional `check` | Loot; with a check, failing still gives one item |

Every gate master's chain branches at the first master (on a magic kill, or on the first vs. second aftermath choice) and loops back, so a gate never runs out of bosses. Occupant ids are globally unique (`<gate>_<occupant>`) because quests check `slew_<occupant_id>`. Clearing a gate sets `cleared_<gate_id>` and counts `gate_clears_<gate_id>`.

Bots: fighters and explorers in town run a gate with chance `BOT_GATE_CHANCE` when its level fits (`bot_brain._gate_run`): they clear the rooms if they can survive them and may fight the master (same rules as lairs, including the grace period). For a group Gate they recruit allies in the same town (`BotWorld.allies`) and run it as a party (`_run_gate_as_party`: damage per member falls as n^1.5, everyone gets the XP, the leader carries the loot; logged as `party`).

**Player runs** (`core/gate_flow.run_gate`): optional companions from the bots in your zone (`companion_candidates`: fighter / explorer / gatherer, level ≥ gate level − 3, HP > 50%). Group Gates (`party_size` 2-5) need `party_size − 1` companions unless the player is `SOLO_OVERRIDE_LEVELS` (6) over the Gate's level; otherwise `run_gate` returns `"no_party"`. Companions strike each round and draw up to 60% of enemy attacks (`core/combat_handler`), are knocked out at 1 HP rather than killed, and take `COMPANION_SHARE` (25%) of the run's gold each (max 60% in all) plus XP. Between rooms the player recovers `GATE_BREATHER` (15%) of HP and MP. Each room and the master's chamber pass an hour (`TIME_COSTS`).

**Rewards:** each occupant has a `trophy` item (`data/items/gate_trophies.json`), given on the player's first kill of it; Lady Vos buys them. FIRST CLEAR pays a standard quest reward for the Gate's level once; SWIFT CLEAR (all fights within `swift_par` = 3 rounds per fight room + 6) pays half that again, and records the best round count in `swift_<gate_id>`.

**Crafting:** `data/crafting/gate_recipes.json` uses all 32 Gate materials (24 consumables in `data/items/gate_crafts.json`, 8 pieces of gear). `alchemy_system.load_recipes` loads every file in `data/crafting/`. `[C]` appears for alchemists or anyone holding a recipe ingredient other than basic reagents.

**Balance:** `python scripts/balance_sim.py [runs] [--class warrior|mage|rogue] [--offset N]` builds a character at each Gate's level the way play does (class skills learned on level-up, class milestones), plus level-matched companions for group Gates, runs every room and the first master with the real rules, and flags Gates that are too hard (< 40%) or trivial. Run it after changing Gate stats, enemy formulas, class skills or the XP curve. Difficulty knob: `GATE_ENEMY_HP_SCALE` (1.4) / `GATE_ENEMY_ATK_SCALE` (1.1), applied to every lair and Gate enemy in `lair_system.register_enemy_templates` (authored and generated alike). Current result: warrior 58% / mage 49% / rogue 54% HP left on average, ≥ 90% wins at every Gate, no Gate flagged for any class. Group Gates scale only their master (`group_scale`), so the 3-4 person Gates (Hoard, Dawnspire, Thaumic Reactor, Hollow of Unmaking, Dreamer's Abyss) field one more enemy in every fight room, or companions (who draw up to 60% of attacks) carried mages and rogues through them; the Golem Assembly Line and the Tidecall Grotto got the same for mages. Earlier, the Silent Archive, the Clocktower and the Dreamer's Abyss each got one more enemy for warriors. With `--offset -1` the sim skips Gates the Registry wouldn't open and flags none. `tests/test_balance.py` guards a sample of it.

**Class milestones:** every `CLASS_MILESTONE_EVERY` (4) levels `level_system.apply_class_milestone` adds the class's `stat_growth` (and HP/MP for VIT/END/INT/WIS), publishing `CLASS_MILESTONE`. Before this, `stat_growth` was defined on every class but never used. It only fires when `add_experience` gets the class registry.

**Player auctions:** `auction_system.consignable` / `consign_player_item` (Auction house → « Consign an item »): starting bid = value, a day long, `seller_npc_id = "player"`; `_settle_listing` pays the player minus `AUCTION_HOUSE_FEE` or returns the item; the player can't bid on their own listing. The game loop prints "Your … sold" lines.

**Content health:** `core/content_health.check(engine)` (Admin → Content health, `scripts/content_health.py`, and `tests/test_balance.py` requires 0 problems): scenes and nodes unreachable from the prologue, dead `leads_to` links, quests no seed or `start_quest:` offers, items mentioned nowhere outside their definition, materials nothing uses, **repeatable gold** (an option paying `give_gold` with no `take_item` / `spend_gold` and no `flags_absent` on a flag it sets) and **price mismatches** (text says "5g", trigger moves ≠ 500 copper). When you write a paid option: pay in copper, take what's sold, and hide it with `flags_absent` once done.

**Autoplay:** `python scripts/autoplay.py --runs N --steps M --seed S` plays the real engine offline through a scripted `ui/prompt` backend (scene choices, fights, peeks at menus, backs out of loops), saves `autoplay_<seed>`, reports crashes with tracebacks, and writes `logs/playtest_report.md`. It found the journal crash (`reward_alignment`). Run it after big changes.

### Quest content

`data/quests/gate_quests.json` has **213 quests**: 53 chains (10 skill chains whose last step grants a skill from `data/skills/quest_skills.json`, 10 item chains whose last step grants an item from `data/items/quest_rewards.json`, 32 story chains, and the `rift` chain "Uncharted Rifts" whose steps fire `open_rift:+N`), 32 gate bounties and 32 material orders.

The `[J]` journal shows chain progress (`quest_system.chain_progress`), report-ready quests and who to report to, and **leads** (`quest_system.available_work`: seeds the player can take now, by NPC and place). Givers are the 13 NPCs in `data/npcs/npcs_questgivers.json`, reached from the Gate Halls. Rewards come from `economy.recommended_quest_reward` (the last step of a chain is `hard`). Every objective is a gate clear, a specific master kill, materials that gates drop, or a level, and every step ends with a report to the giver.

## Growing dungeons (`core/gate_growth.py`, `systems/gate_generation.py`)

New Gates open three ways, all stored in `generated_dungeons` and re-registered per save:

| Source | When | What |
|--------|------|------|
| Rift surge | Every `RIFT_SURGE_EVERY_TURNS` (288 = 2 days), up to `RIFT_MAX_GENERATED` (16) | AI online: the primary model's `AIGateResponse`. Offline: a **Deep Echo** (an authored Gate 6-10 levels below, reopened at the new level with its texts, new ids and its original material) |
| `open_rift:` trigger | Next turn (queued on `state._pending_rifts`) | The same, at a fixed or relative level. Petra's "Uncharted Rifts" chain uses it |
| Lair successor | After a kill, `AI_SUCCESSOR_CHANCE` | An AI-invented master that moves in first, then hands over to the planned successor |

`systems/gate_generation` is the safety layer: the model supplies names and texts only; ids are new `gen_*`; enemy stats, XP and gold come from `mob_stats` / `boss_stats` for the level (the same formulas as the authored Gates, so `build_gates`-style content and generated content match); the material's value is capped; the chain always loops. `group_scale` gives group masters party-sized HP and rewards but only +20% attack per extra member.

Clearing any generated Gate sets `charted_new_rift`.

## Combat depth (`systems/combat_effects.py`)

| Mechanic | Rule |
|----------|------|
| Statuses | `combat_system.STATUS_EFFECTS`. Enemies roll `status_on_hit` on every hit; spells by `spell_type` (fire → burn, ice → frozen, arcane → stun 15%), DEBUFF effects → stun, TRIGGERED skills named venom / poison → poison. Player statuses are combat-local |
| Stun | Loses the next action; then `STUN_GUARD_ROUNDS` (2) of immunity (stored as `_stun_guard`, hidden from labels). Rank C+ enemies resist half of all stuns |
| Boss abilities | `abilities: [{name, kind, every, power, status, summon}]` on enemy templates (`LairAbility` in lair / gate files). Kinds: `heavy` (announced one turn ahead), `status`, `heal`, `summon` (max 2 summons alive), `drain`. `enrage_below` → +25% attack once |
| MP regen | `combat_system.combat_mp_regen`: 1 + WIS // 5 per round |
| Defensive actives | `combat_effects.is_defensive` (SHIELD / BUFF effects only): SHIELD → `_ward` that absorbs that much damage this fight; BUFF → 1-2 `_evade` charges. `absorb()` spends them in `enemy_attack` |
| Basic attack | Uses `max(STR, AGI)` |

`resolve_combat_auto(player, enemies, skill_registry, companions=None, use_skills=False)` runs the same rules headlessly; `use_skills` casts the best affordable damage skill each round (the balance sim uses it).

## World clock (`systems/world_clock.py`, `core/time_flow.py`)

One turn = `MINUTES_PER_TURN` (10) minutes; turn 0 = Day 1, 08:00. `world_clock.phase(turn)` (day 06:00-20:00) drives NPC schedules. `time_flow.pass_time(engine, turns)` advances extra turns and runs the world ticks quietly (bots, lairs, buffs, quests). `TIME_COSTS`: Gate room / master 6, full rest 47, short rest 11, inn 47, travel between `village_start` / `camp_rest` / `verath_city` / dungeon floors 2 (on top of the choice's own turn).

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
| 5 | Added: heat, bounty, crimes (the Ledger's record) |

The paired SQLite world DB has its own `DB_SCHEMA_VERSION` (`persistence/world_db.py`), currently **9** (v6 added `world_actions` + `world_expansions`, v7 added `dungeon_lairs` + `lair_history`, v8 added `generated_dungeons`, v9 added indexes, pragmas and the shareable connection). Migrations run automatically on open.

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

### XP curve

`level_system.xp_for_level(L)`: `BASE_XP_PER_LEVEL` × 1.5^(L−1) up to `XP_SOFT_CAP_LEVEL` (8), then × `XP_LATE_SCALING` (1.12) per level, so level 15 → 16 costs 3,777 XP instead of 29,192 and the level-18 Gates are reachable. Unchanged below the cap.

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
| `CLASS_MILESTONE` | `class_name`, `level`, `gained` | `systems/level_system.apply_class_milestone` |
| `CRIME_COMMITTED` | `kind`, `witnessed`, `heat`, `bounty` | `scenes/option_logic` (`crime:`), `core/world_growth_flow` (crime verbs) |
| `ANOMALY_DETECTED` | *(none)* | `systems/class_system` (before AI gen) |
| `ITEM_FOUND` | `item_id`, `item_name`, `rarity` | `systems/inventory_system`, `scenes/scene_base` |
| `WARNING` | *(none)* | various |
| `PLAYER_DIED` | `lives_remaining`, `lives_used`, `cause`, `zone_id` | `systems/lives_system` |
| `QUEST_STARTED` | `quest_id`, `title` | `systems/quest_system` |
| `QUEST_COMPLETED` | `quest_id`, `outcome` | `systems/quest_system` |
| `SPECIES_EVOLVED` | `species_id`, `stage`, `new_name` | `systems/species_system` |
| `FACTION_RANK_CHANGED` | `faction_id`, `faction_name`, `old_rank`, `new_rank`, `new_rank_name` | `systems/faction_system` |

`ui/media.register_event_sounds` gives events their sound and System line (quest started → `system_chime`; quest completed, faction rank → `class_milestone` + `quest_complete` / `rank_changed`; evolution → `class_assigned` + `evolution`). Deaths are voiced by `combat_handler._combat_defeat`.

---

## Development Workflow

```bash
# Start a new feature
git checkout -b feature/npc-system

# Enable the feature flag while developing
# config.py: FEATURES["npc_system"] = True

# Run the game
python main.py

# Tests (the pre-push hook sets SB_AI_OFFLINE=1: Ollama is treated as absent,
# so a running Ollama doesn't make the suites slow or machine-dependent)
SB_AI_OFFLINE=1 python -X utf8 test_characters.py

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
