"""
Headless character test runner for System Breaker.
Simulates three characters through core game paths without UI or Ollama.
Checks for crashes, missing data, broken triggers, and bad scene links.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Bootstrap ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import DATA_DIR, SAVES_DIR, feature
from entities.player import Player
from entities.skill import SkillRegistry
from entities.item import ItemRegistry
from entities.character_class import ClassRegistry
from entities.npc import NPCRegistry
from scenes.scene_registry import SceneRegistry
from scenes.scene_base import Scene
from core.state_manager import GameState
from persistence.save_manager import new_game_state, close_game, save_game, load_game

ISSUES: list[str] = []
PASSES: list[str] = []

def ok(msg: str) -> None:
    PASSES.append(msg)
    print(f"  ✓  {msg}")

def fail(msg: str, exc: Exception | None = None) -> None:
    detail = f" — {exc}" if exc else ""
    ISSUES.append(f"{msg}{detail}")
    print(f"  ✗  {msg}{detail}")

def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

# ── Registry loaders ───────────────────────────────────────────────────────────

def load_registries():
    skill_reg = SkillRegistry()
    skill_reg.load_from_dir(DATA_DIR / "skills")
    ok(f"Skills loaded: {len(skill_reg._skills)}")

    item_reg = ItemRegistry()
    item_reg.load_from_dir(DATA_DIR / "items")
    ok(f"Items loaded: {len(item_reg._items)}")

    class_reg = ClassRegistry()
    class_reg.load_from_file(DATA_DIR / "classes" / "base_classes.json")
    class_reg.load_from_file(DATA_DIR / "classes" / "combo_classes.json")
    ok(f"Classes loaded: {len(class_reg._classes)}")

    npc_reg = NPCRegistry()
    npc_reg.load_from_dir(DATA_DIR / "npcs")
    ok(f"NPCs loaded: {len(npc_reg._npcs)}")

    scene_reg = SceneRegistry()
    scene_reg.load_from_dir(BASE_DIR / "scenes" / "data")
    ok(f"Scenes loaded: {len(scene_reg._scenes)}")

    return skill_reg, item_reg, class_reg, npc_reg, scene_reg

# ── Scene graph validator ──────────────────────────────────────────────────────

def validate_scene_links(scene_reg: SceneRegistry) -> None:
    """Check all leads_to references point to real scenes and nodes."""
    all_scene_ids = set(scene_reg._scenes.keys())
    for scene_id, scene in scene_reg._scenes.items():
        for node_id, node in scene.nodes.items():
            for opt in node.get("options", []):
                leads_to = opt.get("leads_to", "__stay__")
                leads_to_node = opt.get("leads_to_node", "root")
                if leads_to == "__stay__":
                    continue
                if leads_to not in all_scene_ids:
                    fail(f"Scene '{scene_id}' node '{node_id}' option '{opt.get('option_id')}' → missing scene '{leads_to}'")
                else:
                    target_scene = scene_reg._scenes[leads_to]
                    if leads_to_node not in target_scene.nodes:
                        fail(f"Scene '{scene_id}' node '{node_id}' option '{opt.get('option_id')}' → scene '{leads_to}' missing node '{leads_to_node}'")

# ── Trigger validator ──────────────────────────────────────────────────────────

VALID_TRIGGER_PREFIXES = {
    "flag:", "give_item:", "give_skill:", "give_gold:", "set_base_class:",
    "combat:", "alignment:", "talk_npc:", "start_quest:", "advance_quest:",
    "set_gender:", "set_species:", "set_background:",
    "rest_camp:", "give_food:",
}

def validate_triggers(scene_reg: SceneRegistry, item_reg: ItemRegistry,
                      skill_reg: SkillRegistry) -> None:
    """Check all trigger strings are well-formed and reference real IDs."""
    for scene_id, scene in scene_reg._scenes.items():
        for node_id, node in scene.nodes.items():
            for opt in node.get("options", []):
                for trigger in opt.get("triggers", []):
                    if not any(trigger.startswith(p) for p in VALID_TRIGGER_PREFIXES):
                        fail(f"Unknown trigger '{trigger}' in {scene_id}/{node_id}/{opt.get('option_id')}")
                        continue
                    if trigger.startswith("give_item:"):
                        item_id = trigger.split(":", 1)[1]
                        if not item_reg.get(item_id):
                            fail(f"give_item references unknown item '{item_id}' in {scene_id}/{node_id}")
                    elif trigger.startswith("give_skill:"):
                        skill_id = trigger.split(":", 1)[1]
                        if not skill_reg.get(skill_id):
                            fail(f"give_skill references unknown skill '{skill_id}' in {scene_id}/{node_id}")

# ── Simulated playthrough ──────────────────────────────────────────────────────

@dataclass
class SimChar:
    name: str
    description: str
    # List of (scene_id, node_id, option_id) steps to take
    path: list[tuple[str, str, str]] = field(default_factory=list)

def simulate_playthrough(char: SimChar, scene_reg: SceneRegistry,
                         skill_reg: SkillRegistry) -> None:
    """Walk a character through a scripted path, applying triggers, checking for errors."""
    player = Player(name=char.name)
    state = new_game_state(player, SAVES_DIR, f"_test_{char.name.lower()}")
    state.skill_registry = skill_reg
    state.current_scene_id = "prologue"
    state.current_node_id = "root"

    try:
        for step_num, (scene_id, node_id, option_id) in enumerate(char.path):
            # Navigate to correct scene/node
            state.current_scene_id = scene_id
            state.current_node_id = node_id

            scene = scene_reg.get(scene_id)
            if scene is None:
                fail(f"[{char.name}] step {step_num}: scene '{scene_id}' not found")
                break

            node = scene.get_node(node_id)
            if not node:
                fail(f"[{char.name}] step {step_num}: node '{node_id}' not found in '{scene_id}'")
                break

            # Find the option
            opt_data = next((o for o in node.get("options", []) if o.get("option_id") == option_id), None)
            if opt_data is None:
                fail(f"[{char.name}] step {step_num}: option '{option_id}' not found in {scene_id}/{node_id}")
                break

            # Apply triggers
            try:
                scene.process_triggers(opt_data.get("triggers", []), state)
            except Exception as e:
                fail(f"[{char.name}] step {step_num}: trigger error in {scene_id}/{node_id}/{option_id}", e)
                break

            # Move to next scene/node
            next_scene = opt_data.get("leads_to", "__stay__")
            next_node = opt_data.get("leads_to_node", "root")
            if next_scene != "__stay__":
                state.current_scene_id = next_scene
                state.current_node_id = next_node

        else:
            ok(f"[{char.name}] completed all {len(char.path)} steps — {char.description}")

        # Test save/load round-trip
        try:
            save_path = save_game(state, f"_test_{char.name.lower()}", SAVES_DIR)
            loaded = load_game(f"_test_{char.name.lower()}", SAVES_DIR)
            assert loaded.player.name == char.name
            assert loaded.player.level == state.player.level
            ok(f"[{char.name}] save/load round-trip OK (level={state.player.level}, flags={len(state.player.flags)})")
            close_game(loaded)
        except Exception as e:
            fail(f"[{char.name}] save/load failed", e)

    finally:
        close_game(state)
        # Clean up test save files
        for ext in (".json", ".db"):
            p = SAVES_DIR / f"_test_{char.name.lower()}{ext}"
            if p.exists():
                p.unlink()

# ── Define test characters ─────────────────────────────────────────────────────

def define_characters() -> list[SimChar]:
    return [
        SimChar(
            name="Aldric",
            description="Standard warrior path — human male, fighter class",
            path=[
                # Prologue
                ("prologue", "root", "begin_rite"),
                # Character creation
                ("character_creation", "root", "begin_identity"),
                ("character_creation", "gender", "gender_male"),
                ("character_creation", "species", "species_human"),
                ("character_creation", "species_human_confirm", "confirm_human"),
                ("character_creation", "background", "bg_soldier"),
                ("character_creation", "bg_soldier_confirm", "confirm_bg_soldier"),
                ("character_creation", "affinity", "born_fighter"),
                ("character_creation", "confirm_warrior", "accept_warrior"),
                # Village
                ("village_start", "root", "visit_blacksmith"),
                ("village_start", "root", "enter_guild"),
                ("village_start", "guild_entrance", "talk_to_nara"),
                # Dungeon
                ("village_start", "root", "head_to_dungeon"),
            ],
        ),
        SimChar(
            name="Lyra",
            description="Divergent path — void-touched female, refuses system, unexpected choices",
            path=[
                # Prologue — divergent
                ("prologue", "root", "examine_self"),
                ("prologue", "examine", "flee_attempt"),
                # Character creation — resist everything
                ("character_creation", "root", "resist_reading"),
                ("character_creation", "resist_scan", "relent_to_scan"),
                ("character_creation", "gender", "gender_decline"),
                ("character_creation", "species", "species_void"),
                # Continue through void confirm
                ("character_creation", "species_void_confirm", "confirm_void"),
                ("character_creation", "background", "bg_orphan"),
                ("character_creation", "bg_orphan_confirm", "confirm_bg_orphan"),
                ("character_creation", "affinity", "born_refuse"),
            ],
        ),
        SimChar(
            name="Bram",
            description="Mage path — elf, mage class, visits city",
            path=[
                ("prologue", "root", "begin_rite"),
                ("character_creation", "root", "begin_identity"),
                ("character_creation", "gender", "gender_other"),
                ("character_creation", "species", "species_elf"),
                ("character_creation", "species_elf_confirm", "confirm_elf"),
                ("character_creation", "background", "bg_scholar"),
                ("character_creation", "bg_scholar_confirm", "confirm_bg_scholar"),
                ("character_creation", "affinity", "born_scholar"),
                ("character_creation", "confirm_mage", "accept_mage"),
                ("village_start", "root", "head_to_dungeon"),
                ("verath_city", "root", "go_mages_spire"),
            ],
        ),
    ]

# ── Item / skill data validators ───────────────────────────────────────────────

def validate_items(item_reg: ItemRegistry) -> None:
    """Check all items have required fields."""
    for item_id, item in item_reg._items.items():
        if not item.name:
            fail(f"Item '{item_id}' has no name")
        if item.value_gold < 0:
            fail(f"Item '{item_id}' has negative value_gold")

def validate_skills(skill_reg: SkillRegistry) -> None:
    """Check all skills have required fields."""
    for skill_id, skill in skill_reg._skills.items():
        if not skill.name:
            fail(f"Skill '{skill_id}' has no name")

def validate_classes(class_reg: ClassRegistry) -> None:
    """Check all classes have required fields and skill refs exist."""
    skill_reg = SkillRegistry()
    skill_reg.load_from_dir(DATA_DIR / "skills")
    for class_id, cls in class_reg._classes.items():
        if not cls.name:
            fail(f"Class '{class_id}' has no name")
        for skill_id in cls.starting_skills:
            if not skill_reg.get(skill_id):
                fail(f"Class '{class_id}' starting skill '{skill_id}' not found")

def validate_npcs(npc_reg: NPCRegistry, scene_reg: SceneRegistry) -> None:
    """Check NPC home zones exist in scenes."""
    scene_ids = set(scene_reg._scenes.keys())
    for npc_id, npc in npc_reg._npcs.items():
        if npc.zone_id and npc.zone_id not in scene_ids:
            fail(f"NPC '{npc_id}' zone_id '{npc.zone_id}' is not a known scene")

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "═"*60)
    print("  SYSTEM BREAKER — Automated Character Test Suite")
    print("═"*60)

    # ── 1. Load all registries ─────────────────────────────────────────────────
    section("1. Loading Registries")
    try:
        skill_reg, item_reg, class_reg, npc_reg, scene_reg = load_registries()
    except Exception as e:
        fail("Registry load failed — cannot continue", e)
        traceback.print_exc()
        _report()
        return

    # ── 2. Data validation ────────────────────────────────────────────────────
    section("2. Validating Static Data")
    validate_items(item_reg)
    validate_skills(skill_reg)
    validate_classes(class_reg)
    validate_npcs(npc_reg, scene_reg)
    ok("Item/skill/class/NPC data validation complete")

    # ── 3. Scene graph validation ─────────────────────────────────────────────
    section("3. Validating Scene Graph (links + triggers)")
    validate_scene_links(scene_reg)
    validate_triggers(scene_reg, item_reg, skill_reg)
    ok("Scene link/trigger validation complete")

    # ── 4. Character playthroughs ─────────────────────────────────────────────
    section("4. Simulated Character Playthroughs")
    characters = define_characters()
    for char in characters:
        print(f"\n  → {char.name}: {char.description}")
        try:
            simulate_playthrough(char, scene_reg, skill_reg)
        except Exception as e:
            fail(f"[{char.name}] unhandled exception", e)
            traceback.print_exc()

    # ── 5. Player model edge cases ────────────────────────────────────────────
    section("5. Player Model Edge Cases")
    try:
        p = Player(name="Edge")
        p.flags["test_flag"] = True
        assert p.has_flag("test_flag")
        assert not p.has_flag("missing_flag")
        ok("Player flag system OK")
    except Exception as e:
        fail("Player flag system broken", e)

    try:
        p = Player(name="StatTest")
        p.stats.STR = 20
        assert p.stats.STR == 20
        ok("Player stat assignment OK")
    except Exception as e:
        fail("Player stat assignment broken", e)

    try:
        p = Player(name="AlignTest")
        p.alignment = 50.0
        assert p.alignment_label != ""
        ok(f"Alignment label OK: 50.0 → '{p.alignment_label}'")
        p.alignment = -50.0
        ok(f"Alignment label OK: -50.0 → '{p.alignment_label}'")
    except Exception as e:
        fail("Alignment system broken", e)

    try:
        p = Player(name="InventTest")
        from systems.inventory_system import pick_up_item
        pick_up_item(p, "iron_sword", item_reg)
        assert p.has_item("iron_sword"), "Item not found after add"
        p.remove_item("iron_sword")
        assert not p.has_item("iron_sword"), "Item still present after remove"
        ok("Inventory add/remove OK")
    except Exception as e:
        fail("Inventory system broken", e)

    # ── 6. Inventory system API ───────────────────────────────────────────────
    section("6. Inventory System API")
    from systems.inventory_system import pick_up_item, equip_item, unequip_item, use_item

    try:
        p = Player(name="InvTest")
        ok_flag = pick_up_item(p, "iron_sword", item_reg)
        assert ok_flag, "pick_up_item returned False"
        assert p.has_item("iron_sword"), "Item not in inventory after pick_up"
        ok("pick_up_item OK")
    except Exception as e:
        fail("pick_up_item broken", e)

    try:
        p = Player(name="InvTest2")
        pick_up_item(p, "iron_sword", item_reg)
        success, msg = equip_item(p, "iron_sword", item_reg)
        assert success, f"equip_item failed: {msg}"
        assert p.equipped.weapon == "iron_sword", "Weapon slot not set after equip"
        ok("equip_item OK")
        success2, msg2 = unequip_item(p, "weapon", item_reg)
        assert success2, f"unequip_item failed: {msg2}"
        assert p.equipped.weapon is None, "Weapon slot not cleared after unequip"
        ok("unequip_item OK")
    except Exception as e:
        fail("equip/unequip broken", e)

    try:
        p = Player(name="InvTest3")
        pick_up_item(p, "health_potion", item_reg)
        p.current_hp = 10
        p.max_hp = 100
        success, msg = use_item(p, "health_potion", item_reg)
        assert success, f"use_item failed: {msg}"
        assert p.current_hp > 10, "HP did not increase after using health potion"
        ok(f"use_item (health_potion) OK — HP restored to {p.current_hp}")
    except Exception as e:
        fail("use_item broken", e)

    try:
        p = Player(name="InvTest4")
        pick_up_item(p, "iron_sword", item_reg)
        p.remove_item("iron_sword")
        assert not p.has_item("iron_sword"), "Item still present after remove"
        ok("remove_item OK")
    except Exception as e:
        fail("remove_item broken", e)

    # ── 7. Combat system ──────────────────────────────────────────────────────
    section("7. Combat System")
    from systems import combat_system, level_system

    try:
        # spawn_encounter uses named encounter IDs, not enemy IDs directly
        enemies = combat_system.spawn_encounter("single_slime")
        assert len(enemies) == 1, f"Expected 1 enemy, got {len(enemies)}"
        assert enemies[0].is_alive
        ok(f"spawn_encounter (single_slime) OK — {enemies[0].name} HP={enemies[0].current_hp}")
    except Exception as e:
        fail("spawn_encounter broken", e)

    try:
        enemies = combat_system.spawn_encounter("goblin_patrol")
        assert len(enemies) == 3, f"Expected 3 enemies in patrol, got {len(enemies)}"
        ok(f"spawn_encounter (goblin_patrol) OK — {len(enemies)} enemies")
    except Exception as e:
        fail("spawn_encounter (multi) broken", e)

    try:
        # Verify unknown encounter IDs return empty list gracefully
        enemies = combat_system.spawn_encounter("nonexistent_encounter")
        assert enemies == [], f"Expected [] for unknown encounter, got {enemies}"
        ok("spawn_encounter (unknown id) returns [] gracefully")
    except Exception as e:
        fail("spawn_encounter unknown id handling broken", e)

    try:
        p = Player(name="FightTest")
        p.stats.STR = 15
        p.current_hp = 100
        p.max_hp = 100
        enemies = combat_system.spawn_encounter("single_slime")
        dmg, crit = combat_system.player_attack(p, enemies[0])
        assert dmg >= 0, "Negative damage"
        assert isinstance(crit, bool)
        ok(f"player_attack OK — dealt {dmg} dmg (crit={crit})")
    except Exception as e:
        fail("player_attack broken", e)

    try:
        p = Player(name="LootTest")
        p.stats.STR = 99
        p.current_hp = 200
        p.max_hp = 200
        enemies = combat_system.spawn_encounter("single_slime")
        result = combat_system.resolve_combat_auto(p, enemies, skill_reg)
        assert result.victory, "Should win against slime with STR 99"
        # CombatResult fields are xp_gained / gold_gained (not total_xp/total_gold)
        ok(f"resolve_combat_auto OK — victory={result.victory} xp={result.xp_gained} gold={result.gold_gained}")
    except Exception as e:
        fail("resolve_combat_auto broken", e)

    try:
        # Spawn and manually kill enemy to guarantee loot roll
        enemies = combat_system.spawn_encounter("goblin_patrol")
        for e in enemies:
            e.current_hp = 0
        loot = combat_system.roll_loot(enemies)
        assert isinstance(loot, list), "roll_loot should return a list"
        p = Player(name="LootDropTest")
        for item_id in loot:
            if item_reg.get(item_id):
                pick_up_item(p, item_id, item_reg)
        ok(f"roll_loot + pick_up_item OK — dropped {len(loot)} item(s): {loot}")
    except Exception as e:
        fail("roll_loot / loot pickup broken", e)

    try:
        p = Player(name="XPTest")
        assert p.level == 1
        leveled = level_system.add_experience(p, 150)
        assert p.level == 2, f"Expected level 2, got {p.level}"
        ok(f"add_experience + level up OK — leveled at xp 150: {leveled}")
    except Exception as e:
        fail("level_system broken", e)

    # ── 8. Background generator lifecycle ────────────────────────────────────
    section("8. Background Generator Lifecycle")
    try:
        import threading, time
        from ai.content_generator import ContentGenerator
        from ai.ollama_client import OllamaClient
        from ai.background_generator import BackgroundGenerator

        # Build a minimal ContentGenerator with a non-connecting client
        client = OllamaClient(model="test", base_url="http://localhost:9")  # port 9 = never connects
        gen = ContentGenerator(client, {}, "test")
        bg = BackgroundGenerator(gen)

        assert not bg._running, "Should not be running before start()"
        bg.start()
        assert bg._running, "Should be running after start()"
        assert bg._thread.is_alive(), "Thread should be alive after start()"
        ok("BackgroundGenerator start() OK")

        bg.stop()
        time.sleep(0.1)  # let sentinel propagate
        assert not bg._running, "Should not be running after stop()"
        ok("BackgroundGenerator stop() OK")

        # The critical test — restart after stop (was crashing before fix)
        bg.start()
        assert bg._running, "Should be running after second start()"
        assert bg._thread.is_alive(), "Thread should be alive after restart"
        ok("BackgroundGenerator restart after stop() OK — thread restart bug fixed")

        bg.stop()
    except Exception as e:
        fail("BackgroundGenerator lifecycle broken", e)
        traceback.print_exc()

    # ── 9. Feature flag toggle ────────────────────────────────────────────────
    section("9. Feature Flag Toggle")
    from config import FEATURES

    try:
        # Check all expected flags exist
        expected_flags = [
            "world_db", "species_system", "alignment_system", "npc_system",
            "quest_system", "guild_system", "lives_system", "stat_gating",
            "faction_system", "auction_house", "crafting_system", "bot_system",
        ]
        missing = [f for f in expected_flags if f not in FEATURES]
        if missing:
            fail(f"Missing feature flags: {missing}")
        else:
            ok(f"All {len(expected_flags)} expected feature flags present")
    except Exception as e:
        fail("Feature flag check broken", e)

    try:
        # Toggle each flag off and back on — must not crash
        for flag_name in list(FEATURES.keys()):
            original = FEATURES[flag_name]
            FEATURES[flag_name] = not original
            assert FEATURES[flag_name] != original
            FEATURES[flag_name] = original
            assert FEATURES[flag_name] == original
        ok(f"All {len(FEATURES)} feature flags toggle without error")
    except Exception as e:
        fail("Feature flag toggle broken", e)

    try:
        # Verify feature() helper reads from FEATURES correctly
        for flag_name, val in FEATURES.items():
            assert feature(flag_name) == val, f"feature('{flag_name}') mismatch"
        ok("feature() helper reads FEATURES correctly")
    except Exception as e:
        fail("feature() helper broken", e)

    # ── 10. Quest System Lifecycle ────────────────────────────────────────────
    section("10. Quest System Lifecycle")
    try:
        from entities.quest import QuestRegistry
        from systems import quest_system as qs
        from core.event_bus import bus, Event

        quest_reg = QuestRegistry()
        quest_reg.load_from_file(DATA_DIR / "quests" / "quest_templates.json")
        ok(f"Quest templates loaded: {len(quest_reg._quests)}")

        # Sanity: blacksmith_hammer is the canonical wired quest
        hammer = quest_reg.get("blacksmith_hammer")
        assert hammer is not None, "blacksmith_hammer template missing"
        assert len(hammer.stages) == 2, "blacksmith_hammer should have 2 stages"
        ok("blacksmith_hammer template has expected 2-stage shape")

        # Build an isolated test save
        test_player = Player(name="QuestTester", base_class="warrior")
        test_player.gold = 0
        test_player.experience = 0

        test_state = new_game_state(test_player, SAVES_DIR, "_quest_test_")
        test_state.current_scene_id = "village_start"
        test_state.turn_number = 1

        # ─ Lifecycle 1: start_quest ───────────────────────────────────────────
        starting_active = list(test_player.active_quest_ids)
        instance_id = qs.start_quest("blacksmith_hammer", "torven_blacksmith", test_state, quest_reg)
        assert instance_id, "start_quest returned no instance_id"
        assert instance_id in test_player.active_quest_ids, "instance_id not added to player.active_quest_ids"
        row = test_state.world_db.get_quest(instance_id)
        assert row and row["current_state"] == "find_hammer", f"expected stage 'find_hammer', got {row}"
        ok(f"start_quest created instance at stage 'find_hammer' (id={instance_id[:8]}...)")

        # ─ Lifecycle 2: stage 1 completion via tick_quests (has_item) ────────
        test_player.add_item("torven_hammer", 1)
        # Capture any QUEST_ADVANCED event
        advanced_events: list[Event] = []
        def _capture_advanced(e: Event) -> None: advanced_events.append(e)
        bus.subscribe("QUEST_ADVANCED", _capture_advanced)

        qs.tick_quests(test_state, quest_reg)
        bus.flush()

        row = test_state.world_db.get_quest(instance_id)
        assert row and row["current_state"] == "return_hammer", \
            f"expected advance to 'return_hammer', got {row['current_state'] if row else 'None'}"
        assert any(e.data.get("new_stage") == "return_hammer" for e in advanced_events), \
            "QUEST_ADVANCED event with new_stage='return_hammer' not fired"
        ok("tick_quests advanced quest to 'return_hammer' on has_item condition")

        # The on_advance_triggers should have set 'hammer_found' flag
        assert test_player.has_flag("hammer_found"), "stage on_advance_triggers did not fire flag:hammer_found"
        ok("Stage on_advance_triggers fired (flag:hammer_found set)")

        # ─ Lifecycle 3: terminal stage completion → quest_complete ───────────
        completed_events: list[Event] = []
        def _capture_completed(e: Event) -> None: completed_events.append(e)
        bus.subscribe("QUEST_COMPLETED", _capture_completed)

        gold_before = test_player.gold
        xp_before = test_player.experience
        test_player.flags["torven_hammer_returned"] = True
        qs.tick_quests(test_state, quest_reg)
        bus.flush()

        assert instance_id not in test_player.active_quest_ids, "Completed quest still in active_quest_ids"
        assert instance_id in test_player.completed_quest_ids, "Completed quest not in completed_quest_ids"
        ok("tick_quests completed quest on terminal-stage flag condition")

        assert any(e.data.get("title") == hammer.title for e in completed_events), \
            "QUEST_COMPLETED event not fired with correct title"
        ok(f"QUEST_COMPLETED event fired ({len(completed_events)} event(s))")

        # ─ Lifecycle 4: rewards applied ──────────────────────────────────────
        gold_gained = test_player.gold - gold_before
        xp_gained = test_player.experience - xp_before
        # Quest reward_gold is in "gold units"; player.gold is in copper (×100).
        expected_copper = hammer.reward_gold * 100
        assert gold_gained == expected_copper, \
            f"Expected {expected_copper} copper ({hammer.reward_gold}g) reward, got {gold_gained}"
        assert xp_gained == hammer.reward_xp, \
            f"Expected {hammer.reward_xp} xp reward, got {xp_gained}"
        ok(f"Rewards applied — gold +{gold_gained} copper ({hammer.reward_gold}g), xp +{xp_gained}")

        for reward_flag in hammer.reward_flags:
            assert test_player.has_flag(reward_flag), f"Reward flag '{reward_flag}' not set"
        if hammer.reward_flags:
            ok(f"Reward flags set: {hammer.reward_flags}")

        # ─ Lifecycle 5: NPC dialogue path is wired (start_quest trigger) ─────
        npc_reg2 = NPCRegistry()
        npc_reg2.load_from_dir(DATA_DIR / "npcs")
        torven = npc_reg2.get("torven_blacksmith")
        assert torven, "torven_blacksmith NPC template missing"
        # Find the accept option in hammer_quest_offer node
        accept_node = torven.dialogue_nodes.get("hammer_quest_offer", {})
        accept_opts = accept_node.get("options", []) if isinstance(accept_node, dict) else accept_node.options
        found_trigger = False
        for opt in accept_opts:
            triggers = opt.get("triggers", []) if isinstance(opt, dict) else opt.triggers
            if any("start_quest:blacksmith_hammer" in t for t in triggers):
                found_trigger = True
                break
        assert found_trigger, "Torven dialogue missing start_quest:blacksmith_hammer trigger"
        ok("NPC dialogue wires start_quest:blacksmith_hammer (Torven hammer_quest_offer)")

        # ─ Lifecycle 6: get_active_quest_summaries shape ─────────────────────
        # Start a fresh quest to test summaries on an active one
        new_id = qs.start_quest("blacksmith_hammer", None, test_state, quest_reg)
        summaries = qs.get_active_quest_summaries(test_state, quest_reg)
        assert summaries, "get_active_quest_summaries returned empty list with active quest"
        s = summaries[0]
        for key in ("instance_id", "title", "objective", "turns_active"):
            assert key in s, f"summary missing key '{key}'"
        ok(f"get_active_quest_summaries returns proper shape ({len(summaries)} entry)")

        # ─ Cleanup ────────────────────────────────────────────────────────────
        close_game(test_state)
        # Remove the test save artefacts
        for ext in (".db", ".json"):
            p = SAVES_DIR / f"_quest_test_{ext}"
            if p.exists():
                p.unlink()
        # Also handle the case where slot file exists without underscore prefix
        for p in SAVES_DIR.glob("_quest_test_*"):
            try: p.unlink()
            except OSError: pass

    except Exception as e:
        fail("Quest system lifecycle broken", e)
        traceback.print_exc()

    _report()


def _report() -> None:
    print("\n" + "═"*60)
    print(f"  RESULTS: {len(PASSES)} passed, {len(ISSUES)} issue(s) found")
    print("═"*60)
    if ISSUES:
        print("\n  ISSUES:")
        for i, issue in enumerate(ISSUES, 1):
            print(f"    {i}. {issue}")
    else:
        print("\n  All checks passed!")
    print()


if __name__ == "__main__":
    main()
