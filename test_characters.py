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
    "flag:", "give_item:", "give_skill:", "give_gold:", "spend_gold:", "set_base_class:",
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
                    elif trigger.startswith("combat:"):
                        # Must spawn real enemies (not crash, not silently skip).
                        from systems.combat_system import spawn_encounter
                        encounter_id = trigger.split(":", 1)[1]
                        try:
                            spawned = spawn_encounter(encounter_id)
                        except Exception as exc:
                            fail(f"combat:{encounter_id} crashes on spawn in {scene_id}/{node_id}", exc)
                            continue
                        if not spawned:
                            fail(f"combat:{encounter_id} spawns no enemies in {scene_id}/{node_id}")

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
            description="Mage path — elf, mage class, bribes the gate into the city",
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
                ("village_start", "root", "approach_city_gate"),
                ("village_start", "city_gate", "bribe_gate_guard"),
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
        for skill_id in cls.learnable_skills:
            if not skill_reg.get(skill_id):
                fail(f"Class '{class_id}' learnable skill '{skill_id}' not found")

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

    # TRIGGERED skills: on_hit (Arcane Strike), on_low_hp, exact matching
    import random
    from entities.enums import EffectType, Rarity, SkillType
    from entities.skill import Skill, SkillEffect
    from systems import passive_system

    try:
        arcane = skill_reg.get("arcane_strike")
        assert arcane is not None, "arcane_strike missing from skill registry"
        assert "on_hit" in passive_system.parse_trigger_conditions(arcane.trigger_condition)

        def _swing(with_arcane: bool) -> int:
            p = Player(name="ArcaneTest")
            p.stats.STR = 10
            p.stats.INT = 15
            p.stats.LCK = 0          # no crits → deterministic comparison
            if with_arcane:
                p.skills.append("arcane_strike")
            target = combat_system.spawn_encounter("single_slime")[0]
            target.max_hp = target.current_hp = 1000   # never dies → no on_kill
            random.seed(1234)
            dmg, _ = combat_system.player_attack(p, target, skill_reg)
            return dmg

        base_dmg, arcane_dmg = _swing(False), _swing(True)
        expected_bonus = int(4.0 + 15 * 0.8)
        assert arcane_dmg - base_dmg == expected_bonus, (
            f"Arcane Strike should add {expected_bonus} dmg, got {arcane_dmg - base_dmg} "
            f"({base_dmg} → {arcane_dmg})"
        )
        ok(f"on_hit fires from player_attack — Arcane Strike adds {expected_bonus} dmg "
           f"({base_dmg} → {arcane_dmg})")
    except Exception as e:
        fail("on_hit trigger (arcane_strike) broken", e)

    # Test-only skills go in a scratch registry so later sections never see them
    trig_reg = SkillRegistry()

    try:
        trig_reg.register(Skill(
            skill_id="test_last_stand", name="Last Stand", rarity=Rarity.RARE,
            description="Heals when badly wounded.",
            skill_type=SkillType.TRIGGERED, trigger_condition="on_low_hp",
            effects=[SkillEffect(effect_type=EffectType.HEAL, base_value=25.0)],
        ))

        def _low_hp_player(hp: int) -> Player:
            p = Player(name="LowHPTest")
            p.stats.AGI = 0          # no dodge
            p.stats.END = 0          # no END mitigation
            p.max_hp = 100
            p.current_hp = hp
            p.skills.append("test_last_stand")
            return p

        brute = combat_system.spawn_encounter("single_slime")[0]
        brute.attack = 20            # 19–22 dmg: 35 HP → below 30% but alive

        p = _low_hp_player(35)
        dmg = combat_system.enemy_attack(brute, p, trig_reg)
        assert dmg > 0
        assert p.current_hp == 35 - dmg + 25, (
            f"on_low_hp heal expected {35 - dmg + 25} HP, got {p.current_hp}"
        )
        ok(f"on_low_hp fires when a hit crosses 30% HP — took {dmg}, healed 25 → {p.current_hp}")

        p = _low_hp_player(25)       # already below threshold → no crossing
        brute.attack = 5
        dmg = combat_system.enemy_attack(brute, p, trig_reg)
        assert p.current_hp == 25 - dmg, (
            f"on_low_hp should not re-fire while already low, got {p.current_hp} HP"
        )
        ok("on_low_hp does not fire again while HP is already below the threshold")

        p = _low_hp_player(35)       # lethal hit → no post-mortem heal
        brute.attack = 100
        combat_system.enemy_attack(brute, p, trig_reg)
        assert p.current_hp == 0, f"on_low_hp must not revive a dead player, got {p.current_hp}"
        ok("on_low_hp does not fire on a lethal hit")
    except Exception as e:
        fail("on_low_hp trigger broken", e)

    try:
        for sid, cond, cd in (("test_combo_cond", "on_attack_or_kill", 0),
                              ("test_multi_cond", "on_hit, ON_KILL", 0),
                              ("test_cd_trigger", "on_hit", 3)):
            trig_reg.register(Skill(
                skill_id=sid, name=sid, rarity=Rarity.COMMON, description="test",
                skill_type=SkillType.TRIGGERED, trigger_condition=cond, cooldown_turns=cd,
                effects=[SkillEffect(effect_type=EffectType.DAMAGE, base_value=5.0)],
            ))
        p = Player(name="MatchTest")
        p.skills.append("test_combo_cond")
        assert passive_system.try_fire_trigger(p, "on_attack", trig_reg) == 0
        assert passive_system.try_fire_trigger(p, "on_kill", trig_reg) == 0
        ok("trigger_condition matching is exact — 'on_attack_or_kill' matches neither event")

        p = Player(name="MultiTest")
        p.skills.append("test_multi_cond")
        assert passive_system.try_fire_trigger(p, "on_hit", trig_reg) == 5
        assert passive_system.try_fire_trigger(p, "on_kill", trig_reg) == 5
        assert passive_system.try_fire_trigger(p, "on_attack", trig_reg) == 0
        ok("comma-separated trigger_condition fires on each listed event")

        p = Player(name="CDTrigTest")
        p.skills.append("test_cd_trigger")
        assert passive_system.try_fire_trigger(p, "on_hit", trig_reg) == 5
        assert p.skill_cooldowns.get("test_cd_trigger") == 3
        assert passive_system.try_fire_trigger(p, "on_hit", trig_reg) == 0
        ok("TRIGGERED skill with cooldown_turns goes on cooldown after firing")
    except Exception as e:
        fail("trigger_condition matching / cooldown broken", e)

    try:
        # Combat reports which TRIGGERED skills fired so the UI can log them
        p = Player(name="FiredTest")
        p.skills.append("arcane_strike")
        target = combat_system.spawn_encounter("single_slime")[0]
        target.max_hp = target.current_hp = 1000
        fired = []
        combat_system.player_attack(p, target, skill_reg, fired)
        assert [(f.skill_name, f.event) for f in fired] == [("Arcane Strike", "on_hit")], fired
        assert fired[0].value > 0

        p = _low_hp_player(35)
        brute.attack = 20
        fired = []
        combat_system.enemy_attack(brute, p, trig_reg, fired)
        assert [(f.skill_id, f.value) for f in fired] == [("test_last_stand", 25)], fired
        ok("player_attack / enemy_attack report fired TRIGGERED skills for the combat log")
    except Exception as e:
        fail("fired-trigger reporting broken", e)

    try:
        # Headless auto-combat applies TRIGGERED skills too (on_kill heal)
        trig_reg.register(Skill(
            skill_id="test_kill_heal", name="Bloodrush", rarity=Rarity.RARE,
            description="Heal on kill.", skill_type=SkillType.TRIGGERED,
            trigger_condition="on_kill",
            effects=[SkillEffect(effect_type=EffectType.HEAL, base_value=100.0)],
        ))
        p = Player(name="AutoTrigTest")
        p.stats.STR = 99             # one-shots the slime before it can swing
        p.max_hp, p.current_hp = 200, 50
        p.skills.append("test_kill_heal")
        result = combat_system.resolve_combat_auto(
            p, combat_system.spawn_encounter("single_slime"), trig_reg,
        )
        assert result.victory
        assert p.current_hp == 150, f"on_kill heal should apply in auto combat, HP={p.current_hp}"
        assert all(isinstance(i, str) for i in result.loot), f"loot must be item ids: {result.loot}"
        ok("resolve_combat_auto applies TRIGGERED skills (on_kill healed 50 → 150)")
    except Exception as e:
        fail("resolve_combat_auto trigger wiring broken", e)

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

    # ─────────────────────────────────────────────────────────────────────────
    section("11. Save Migration (v1 → v2 → v3)")
    try:
        import json as _json
        from persistence.save_manager import (
            _migrate, _v1_to_v2, _v2_to_v3, SaveMigrationError, load_game,
        )
        from config import SAVE_VERSION

        # 11a. Synthetic v1 save (minimal player blob, no v2/v3 fields)
        v1_save = {
            "save_version": 1,
            "player": {
                "name": "Legacy",
                "base_class": "warrior",
                "stats": {"STR": 8, "INT": 5, "AGI": 5, "LCK": 5, "VIT": 5, "WIS": 5, "END": 5},
                # Intentionally missing: species_id, alignment, lives, quest IDs, etc.
            },
            "current_scene_id": "village_start",
            "current_node_id": "root",
            "generated_content_cache": {},
        }
        migrated = _migrate(v1_save.copy(), from_version=1, to_version=SAVE_VERSION)
        assert migrated["save_version"] == SAVE_VERSION, \
            f"Migrated save_version wrong: {migrated['save_version']}"
        assert migrated["player"]["species_id"] == "human", "v1→v2 didn't set species_id"
        assert migrated["player"]["lives_remaining"] == 9, "v1→v2 didn't set lives_remaining"
        assert migrated["player"]["identified_items"] == {}, "v2→v3 didn't set identified_items"
        assert migrated["player"]["active_buffs"] == [], "v2→v3 didn't set active_buffs"
        ok("v1 → v3 migration fills all defaulted fields explicitly")

        # 11b. Migrated player blob validates against current Player model
        from entities.player import Player as _Player
        _Player.model_validate(migrated["player"])
        ok("Migrated v1 save validates against current Player schema")

        # 11c. Synthetic v2 save migrates forward
        v2_save = {
            "save_version": 2,
            "player": {
                "name": "Tester",
                "base_class": "mage",
                "stats": {"STR": 5, "INT": 8, "AGI": 5, "LCK": 5, "VIT": 5, "WIS": 5, "END": 5},
                "species_id": "elf",
                "alignment": 10.0,
                "lives_remaining": 5,
                "lives_used": 4,
                # Intentionally missing v3 fields
            },
            "turn_number": 42,
            "current_scene_id": "verath_city",
            "current_node_id": "root",
            "generated_content_cache": {},
        }
        migrated2 = _migrate(v2_save.copy(), from_version=2, to_version=SAVE_VERSION)
        assert migrated2["player"]["alignment"] == 10.0, "v2 data lost"
        assert migrated2["player"]["lives_remaining"] == 5, "v2 data lost"
        assert "identified_items" in migrated2["player"], "v2→v3 didn't add identified_items"
        ok("v2 → v3 migration preserves existing fields + adds v3 defaults")

        # 11d. Each migrator is callable in isolation and idempotent on already-v_n data
        d = {"player": {"species_id": "human", "lives_remaining": 9}, "turn_number": 5}
        _v1_to_v2(d)  # should not crash even though some defaults already set
        _v2_to_v3(d)
        ok("Migrators are safe to re-apply (setdefault, not overwrite)")

        # 11e. Unknown migration step raises clearly
        try:
            _migrate({"player": {"name": "x"}}, from_version=999, to_version=1000)
            fail("Expected SaveMigrationError for unknown migration step")
        except SaveMigrationError:
            ok("Unknown migration step raises SaveMigrationError (no silent no-op)")

        # 11f. End-to-end load_game from a synthetic v1 save on disk
        slot = "_migration_test_"
        v1_path = SAVES_DIR / f"{slot}.json"
        v1_path.write_text(_json.dumps(v1_save), encoding="utf-8")
        loaded = load_game(slot, SAVES_DIR)
        assert loaded is not None, "load_game returned None for v1 save"
        assert loaded.player.species_id == "human", "Loaded player lost species default"
        assert loaded.player.lives_remaining == 9, "Loaded player lost lives default"
        close_game(loaded)
        ok("load_game transparently migrates a v1 file to v3 and loads cleanly")

        # Cleanup
        for ext in (".json", ".db"):
            p = SAVES_DIR / f"{slot}{ext}"
            if p.exists():
                try: p.unlink()
                except OSError: pass

        # 11g. Full v1 → v4 round-trip from disk + save the migrated state +
        #      reload it. Catches problems where the migrator chain produces
        #      a model that loads but then can't be re-serialised.
        slot2 = "_v1_roundtrip_test_"
        v1_disk = {
            "save_version": 1,
            "player": {
                "name": "Disk Tester",
                "base_class": "rogue",
                "stats": {"STR": 5, "INT": 5, "AGI": 8, "LCK": 6, "VIT": 5, "WIS": 5, "END": 5},
            },
            "current_scene_id": "village_start",
            "current_node_id": "root",
            "generated_content_cache": {},
        }
        v1_path = SAVES_DIR / f"{slot2}.json"
        v1_path.write_text(_json.dumps(v1_disk), encoding="utf-8")

        loaded = load_game(slot2, SAVES_DIR)
        assert loaded is not None
        assert loaded.player.skill_uses == {}, "v4 skill_uses missing on migrated player"
        assert loaded.player.skill_levels == {}, "v4 skill_levels missing"

        # Mutate + re-save
        loaded.player.skill_uses["fireball"] = 3
        loaded.player.skill_levels["fireball"] = 1
        loaded.player.gold = 7777
        save_game(loaded, slot2, SAVES_DIR)

        # Re-load the migrated-then-saved file — should be v4 cleanly
        reloaded = load_game(slot2, SAVES_DIR)
        assert reloaded is not None
        assert reloaded.player.gold == 7777, "Gold drift on second load"
        assert reloaded.player.skill_uses == {"fireball": 3}, "skill_uses drift"
        assert reloaded.player.skill_levels == {"fireball": 1}, "skill_levels drift"

        # File on disk now declares v4
        on_disk = _json.loads(v1_path.read_text(encoding="utf-8"))
        assert on_disk["save_version"] == SAVE_VERSION, (
            f"Saved file should be at current version {SAVE_VERSION}, got {on_disk['save_version']}"
        )
        ok(f"v1 disk-fixture → load → migrate → mutate → save → reload all clean "
           f"(file now at v{SAVE_VERSION})")
        close_game(reloaded)
        for ext in (".json", ".db"):
            p = SAVES_DIR / f"{slot2}{ext}"
            if p.exists():
                try: p.unlink()
                except OSError: pass

    except Exception as e:
        fail("Save migration broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("12. AIService Facade")
    try:
        from ai.ai_service import AIService

        # 12a. Empty AIService (no content generator) → is_available False, all
        # generator methods return None without raising.
        svc = AIService()
        assert svc.is_available is False, "Empty service should report unavailable"
        assert svc.has_background is False, "No BG → has_background should be False"
        assert svc.generate_class(None, None, None) is None
        assert svc.generate_quest(None, "", "", "") is None
        assert svc.generate_guild_intent(None, None) is None
        assert svc.generate_guild_template(name="x", archetype="x", zone_id="x") is None
        assert svc.generate_narrative(None, "", "") is None
        ok("Empty AIService is unavailable + all methods return None")

        # 12b. AIService with a fake generator passes calls through unchanged.
        class _FakeGen:
            def __init__(self): self.calls = []
            def generate_class(self, p, d, r, skill_registry=None, rich_skills=False):
                self.calls.append("class"); return "CLASS_OK"
            def generate_quest(self, p, gid, n, r):
                self.calls.append("quest"); return "QUEST_OK"
            def generate_guild_intent(self, g, m):
                self.calls.append("intent"); return "INTENT_OK"
            def generate_guild_template(self, **kw):
                self.calls.append("guild_tpl"); return "GUILD_OK"
            def generate_narrative(self, **kw):
                self.calls.append("narr"); return "NARR_OK"

        fake = _FakeGen()
        svc2 = AIService(content_generator=fake)
        assert svc2.is_available is True
        assert svc2.generate_class(None, None, None) == "CLASS_OK"
        assert svc2.generate_quest(None, "", "", "") == "QUEST_OK"
        assert svc2.generate_guild_intent(None, None) == "INTENT_OK"
        assert svc2.generate_guild_template(name="x", archetype="x", zone_id="x") == "GUILD_OK"
        assert svc2.generate_narrative(player=None, scene_id="x", choice_description="x") == "NARR_OK"
        assert fake.calls == ["class", "quest", "intent", "guild_tpl", "narr"]
        ok("AIService passes all 5 generation methods through to the wrapped generator")

        # 12c. Generator exceptions are caught and converted to None.
        class _BrokenGen:
            def generate_class(self, *a, **kw): raise RuntimeError("ollama exploded")  # noqa: E501
            def generate_quest(self, *a, **kw): raise TimeoutError("timeout")
            def generate_guild_intent(self, *a, **kw): raise ValueError("bad json")
            def generate_guild_template(self, **kw): raise OSError("connection refused")
            def generate_narrative(self, **kw): raise RuntimeError("bad")

        svc3 = AIService(content_generator=_BrokenGen())
        assert svc3.generate_class(None, None, None) is None
        assert svc3.generate_quest(None, "", "", "") is None
        assert svc3.generate_guild_intent(None, None) is None
        assert svc3.generate_guild_template(name="x", archetype="x", zone_id="x") is None
        assert svc3.generate_narrative(player=None, scene_id="x", choice_description="x") is None
        ok("AIService swallows generator exceptions and returns None")

        # 12d. submit_quest_async returns False without a BG generator.
        assert svc2.submit_quest_async("zone", "hint", None) is False
        ok("submit_quest_async returns False when no BG generator wired")

        # 12e. systems/quest_system.generate_ai_quest accepts ai_service and
        # short-circuits when unavailable.
        from systems import quest_system as _qs
        # Reuse the player/state from earlier — fresh isolated save.
        from persistence.save_manager import new_game_state as _ng, close_game as _cg
        _p = Player(name="ServiceTester", base_class="warrior")
        _state = _ng(_p, SAVES_DIR, "_aisvc_test_")
        _state.turn_number = 1
        try:
            result = _qs.generate_ai_quest(
                giver_npc_id="x", npc_name="X", npc_role="x",
                state=_state, ai_service=AIService(),  # empty → unavailable
            )
            assert result is None, "generate_ai_quest must return None when AIService unavailable"
            ok("quest_system.generate_ai_quest short-circuits on empty AIService")
        finally:
            _cg(_state)
            for ext in (".json", ".db"):
                p = SAVES_DIR / f"_aisvc_test_{ext}"
                if p.exists():
                    try: p.unlink()
                    except OSError: pass

        # 12f. Interactive helpers (situation query, follow-ups, shop quip,
        # token usage) follow the same None-on-failure contract.
        assert svc.generate_dynamic_options("q", "t", "x", [], None) is None
        assert svc.generate_action_narrative("t", "x", "act") is None
        assert svc.generate_followup_options("t", "x") is None
        assert svc.generate_shop_refusal("n", "r", "1g", "0g") is None
        assert svc.token_usage() == []

        class _FakeInteractiveGen:
            def generate_dynamic_options(self, **kw): return "DYN_OK"
            def generate_action_narrative(self, *a): return "ACT_OK"
            def generate_followup_options(self, *a): return ["F1", "F2"]
            def generate_shop_refusal(self, *a): return "SHOP_OK"
            def token_usage(self): return [("primary", "m", {})]
        svc4 = AIService(content_generator=_FakeInteractiveGen())
        assert svc4.generate_dynamic_options("q", "t", "x", [], None) == "DYN_OK"
        assert svc4.generate_action_narrative("t", "x", "act") == "ACT_OK"
        assert svc4.generate_followup_options("t", "x") == ["F1", "F2"]
        assert svc4.generate_shop_refusal("n", "r", "1g", "0g") == "SHOP_OK"
        assert svc4.token_usage() == [("primary", "m", {})]

        class _BrokenInteractiveGen:
            def __getattr__(self, name):
                def _boom(*a, **kw): raise RuntimeError(name)
                return _boom
        svc5 = AIService(content_generator=_BrokenInteractiveGen())
        assert svc5.generate_dynamic_options("q", "t", "x", [], None) is None
        assert svc5.generate_action_narrative("t", "x", "act") is None
        assert svc5.generate_followup_options("t", "x") is None
        assert svc5.generate_shop_refusal("n", "r", "1g", "0g") is None
        assert svc5.token_usage() == []
        ok("AIService interactive helpers: None when offline, pass-through, None on failure")

        # 12g. ContentGenerator parsing for the helpers moved out of core/.
        from ai.content_generator import ContentGenerator

        class _FakeClient:
            def __init__(self, model, json_out=None, text_out=None):
                self.model = model
                self._json, self._text = json_out, text_out
            def generate_json(self, **kw):
                if isinstance(self._json, Exception): raise self._json
                return self._json
            def generate_text(self, **kw): return self._text
            def token_summary(self): return {"calls": 0}

        def _gen(client, fast=None):
            return ContentGenerator(client, {}, client.model, fast_client=fast)

        assert _gen(_FakeClient("m", json_out=["a", 1, "b"])).generate_followup_options("t", "x") == ["a", "b"]
        assert _gen(_FakeClient("m", json_out={"choices": ["c"]})).generate_followup_options("t", "x") == ["c"]
        assert _gen(_FakeClient("m", json_out={"nope": 1})).generate_followup_options("t", "x") is None
        assert _gen(_FakeClient("m", json_out=ValueError("bad"))).generate_followup_options("t", "x") is None
        assert _gen(_FakeClient("m", json_out={"narrative": "It lands."})).generate_action_narrative("t", "x", "a") == "It lands."
        assert _gen(_FakeClient("m", json_out=ValueError("bad"))).generate_action_narrative("t", "x", "a") is None
        assert _gen(_FakeClient("m", text_out='  "Not today."  ')).generate_shop_refusal("n", "r", "1g", "0g") == "Not today."
        assert _gen(_FakeClient("m", text_out='  ')).generate_shop_refusal("n", "r", "1g", "0g") is None
        _slow = _FakeClient("slow")
        assert [u[:2] for u in _gen(_slow).token_usage()] == [("primary", "slow")]
        assert [u[:2] for u in _gen(_slow, _FakeClient("fast")).token_usage()] == [("primary", "slow"), ("fast", "fast")]
        ok("ContentGenerator follow-up / narrative / shop / token helpers parse + fail cleanly")

        # 12g2. [?] answers from a small model: the exact malformed shape seen
        # live (a stray "triggers" string in the options list) is salvaged,
        # the structured-output schema is sent, and a bad first try is retried.
        from types import SimpleNamespace as _NS
        from entities.player import Player as _P12
        _p12 = _P12(name="Q")
        class _SeqClient(_FakeClient):
            def __init__(self, outs):
                super().__init__("fast"); self.outs, self.kw = list(outs), []
            def generate_json(self, **kw):
                self.kw.append(kw); out = self.outs.pop(0)
                if isinstance(out, Exception): raise out
                return out
        _bad = {"situation_text": "The air hums.",
                "options": [{"option_id": "look", "label": "Look closer", "triggers": []}, "triggers"]}
        _sc = _SeqClient([_bad])
        _r = _gen(_FakeClient("m"), _sc).generate_dynamic_options("what?", "t", "x", [], _p12)
        assert [o.label for o in _r.options] == ["Look closer"]
        assert _sc.kw[0]["schema"]["properties"]["options"]["items"]["required"][:2] == ["option_id", "label"]
        _sc = _SeqClient([ValueError("bad json"), _bad])
        assert _gen(_FakeClient("m"), _sc).generate_dynamic_options("what?", "t", "x", [], _p12) is not None
        _sc = _SeqClient([ValueError("a"), {"options": []}])   # no situation_text twice → None
        assert _gen(_FakeClient("m"), _sc).generate_dynamic_options("what?", "t", "x", [], _p12) is None
        ok("[?] answers: malformed options salvaged, JSON schema sent, one retry")

        # 12h. Boundary guard: outside ai/, only core/bootstrap.py (the
        # composition root) may touch ContentGenerator or raw Ollama clients.
        import re as _re
        _pat = _re.compile(r"\b(ContentGenerator|OllamaClient|fast_client|ai_generator)\b")
        _offenders = []
        for _pkg in ("core", "systems", "scenes", "ui", "persistence", "entities"):
            for _py in (BASE_DIR / _pkg).rglob("*.py"):
                if _py.relative_to(BASE_DIR).as_posix() == "core/bootstrap.py":
                    continue
                for _n, _line in enumerate(_py.read_text(encoding="utf-8").splitlines(), 1):
                    if _pat.search(_line) and not _line.lstrip().startswith("#"):
                        _offenders.append(f"{_py.relative_to(BASE_DIR).as_posix()}:{_n}")
        assert not _offenders, f"Direct AI access outside AIService: {_offenders}"
        ok("No package outside ai/ bypasses AIService (bootstrap excepted)")

    except Exception as e:
        fail("AIService facade broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("13. Quest Reward Currency Cap")
    try:
        from entities.quest import QuestTemplate, QuestStage, MAX_QUEST_REWARD_GOLD
        from pydantic import ValidationError

        # 13a. Sane reward value passes
        QuestTemplate(
            template_id="t1", title="x", description="x",
            stages=[QuestStage(stage_id="s", description="", objective_text="", completion_condition={"has_flag": "x"})],
            reward_gold=100,
        )
        ok(f"reward_gold ≤ {MAX_QUEST_REWARD_GOLD} (gold pieces) is accepted")

        # 13b. Out-of-range reward fails loudly
        try:
            QuestTemplate(
                template_id="t2", title="x", description="x",
                stages=[QuestStage(stage_id="s", description="", objective_text="", completion_condition={"has_flag": "x"})],
                reward_gold=MAX_QUEST_REWARD_GOLD + 1,
            )
            fail(f"Expected ValidationError for reward_gold > {MAX_QUEST_REWARD_GOLD}")
        except ValidationError:
            ok(f"reward_gold > {MAX_QUEST_REWARD_GOLD} is rejected with ValidationError")

        # 13c. Negative gold rejected too
        try:
            QuestTemplate(
                template_id="t3", title="x", description="x",
                stages=[QuestStage(stage_id="s", description="", objective_text="", completion_condition={"has_flag": "x"})],
                reward_gold=-1,
            )
            fail("Expected ValidationError for negative reward_gold")
        except ValidationError:
            ok("Negative reward_gold rejected")

        # 13d. AIQuestResponse clamps instead of failing (LLM can hallucinate)
        from ai.response_validator import AIQuestResponse, AIQuestStageResponse
        runaway = AIQuestResponse(
            template_id="ai_t", title="x", description="x",
            stages=[AIQuestStageResponse(stage_id="s", objective_text="", completion_condition={"has_flag": "x"})],
            reward_gold=999_999,  # hallucinated jackpot
        )
        assert runaway.reward_gold == MAX_QUEST_REWARD_GOLD, \
            f"AI runaway reward should clamp to {MAX_QUEST_REWARD_GOLD}, got {runaway.reward_gold}"
        ok("AIQuestResponse.reward_gold clamps runaway LLM values instead of failing")
    except Exception as e:
        fail("Quest reward currency guards broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("14. Class Resolver Layer 2 (Superset Match)")
    try:
        from entities.character_class import ClassRegistry, ClassDefinition, ComboRequirements
        from entities.player import Stats
        from entities.enums import Rarity
        from entities.item import ItemRegistry as _ItemReg
        from entities.skill import SkillRegistry as _SkillReg
        from systems.class_system import resolve_combo_class

        # Build minimal registries with one combo that requires an item
        cr = ClassRegistry()
        cr.register(ClassDefinition(
            class_id="warrior", name="Warrior", rarity=Rarity.COMMON, description="",
        ))
        cr.register(ClassDefinition(
            class_id="mage", name="Mage", rarity=Rarity.COMMON, description="",
        ))
        cr.register(ClassDefinition(
            class_id="rogue", name="Rogue", rarity=Rarity.COMMON, description="",
        ))
        cr.register(ClassDefinition(
            class_id="spellblade", name="Spellblade", rarity=Rarity.RARE, description="combo",
            combo_requirements=ComboRequirements(
                required_classes=["warrior", "mage"],
                required_items=["mythic_focus"],  # GATING item
            ),
        ))
        cr.register(ClassDefinition(
            class_id="fallback_generated", name="Fallback", rarity=Rarity.COMMON, description="",
        ))
        ir = _ItemReg()
        sr = _SkillReg()

        # Player has warrior+mage+rogue (superset of spellblade combo) but NOT
        # the required item. Buggy old Layer 2 would return spellblade anyway,
        # short-circuiting Layer 3. Fixed Layer 2 must respect item requirement.
        from entities.player import Player as _Player
        p = _Player(name="Test")
        p.base_class = "warrior"
        p.secondary_class = "mage"
        # Manually add a third class is awkward with the model — simulate
        # superset via the 2-slot fields and assert Layer 2 doesn't grant
        # spellblade when the item is missing.
        result = resolve_combo_class(p, cr, ir, sr, ai_service=None)
        # With the bug, this would be spellblade. After the fix it falls
        # through to fallback (or None if no fallback).
        assert result is None or result.class_id != "spellblade", (
            f"Layer 2 should not grant spellblade without required item; got {result and result.class_id}"
        )
        ok("Layer 2 superset match honours required_items (no silent short-circuit)")

        # Now give the player the item — Layer 1 grants spellblade.
        p.add_item("mythic_focus")
        result2 = resolve_combo_class(p, cr, ir, sr, ai_service=None)
        assert result2 is not None and result2.class_id == "spellblade", (
            f"With item present Layer 1 should grant spellblade, got {result2 and result2.class_id}"
        )
        ok("Layer 1 still grants combo when all conditions met (regression check)")
    except Exception as e:
        fail("Class resolver Layer 2 broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("15. Trigger Processing as Free Function")
    try:
        from scenes.option_logic import process_triggers as pt, build_option
        from persistence.save_manager import new_game_state as _ng2, close_game as _cg2

        _pl = Player(name="TriggerTester", base_class="warrior")
        _st = _ng2(_pl, SAVES_DIR, "_trig_test_")
        try:
            # 15a. flag: trigger sets the player flag without any Scene object
            pt(["flag:my_test_flag"], _st)
            assert _pl.has_flag("my_test_flag"), "free-function process_triggers didn't set flag"
            ok("process_triggers free function sets flag (no Scene instance needed)")

            # 15b. give_gold: trigger applies arithmetic
            before = _pl.gold
            pt(["give_gold:250"], _st)
            assert _pl.gold == before + 250, f"give_gold delta wrong: {_pl.gold - before}"
            ok("process_triggers free function applies give_gold:")

            # 15c. build_option free function gates correctly on missing item
            raw = {"option_id": "x", "label": "lbl", "leads_to": "__stay__",
                   "triggers": [], "requires": {"items": ["nonexistent_item"]}}
            opt = build_option(raw, _st)
            assert opt.locked is True, "build_option should lock when required item missing"
            ok("build_option free function gates on requires.items")

            # 15d. Scene.process_triggers still works as a delegate (back-compat)
            from scenes.scene_base import Scene
            scene = Scene("__test__", {"nodes": {}})
            scene.process_triggers(["flag:scene_delegate_works"], _st)
            assert _pl.has_flag("scene_delegate_works"), "Scene.process_triggers delegate broken"
            ok("Scene.process_triggers delegate still works (back-compat)")
        finally:
            _cg2(_st)
            for ext in (".json", ".db"):
                p = SAVES_DIR / f"_trig_test_{ext}"
                if p.exists():
                    try: p.unlink()
                    except OSError: pass
    except Exception as e:
        fail("Free-function trigger processing broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("16. AIService Async Class Generation")
    try:
        from ai.ai_service import AIService

        # 16a. submit_class_generation_async returns None when AI is unavailable
        empty = AIService()
        assert empty.submit_class_generation_async(None, None, None) is None
        ok("submit_class_generation_async returns None when AI unavailable")

        # 16b. await_class_result returns None for a None future (safe to call)
        assert AIService.await_class_result(None, timeout=1.0) is None
        ok("await_class_result handles None future without raising")

        # 16c. With a fake generator, async submission returns a Future that resolves
        from concurrent.futures import Future
        class _FastGen:
            def generate_class(self, p, d, r, skill_registry=None, rich_skills=False):
                return "MOCK_CLASS_DEF"
        svc = AIService(content_generator=_FastGen())
        fut = svc.submit_class_generation_async(None, None, None)
        assert isinstance(fut, Future), f"Expected Future, got {type(fut)}"
        result = AIService.await_class_result(fut, timeout=5.0)
        assert result == "MOCK_CLASS_DEF", f"Async result wrong: {result}"
        ok("submit_class_generation_async returns Future that resolves via await_class_result")

        # 16d. A generator that raises returns None (caught + logged)
        class _BrokenGen:
            def generate_class(self, p, d, r, skill_registry=None, rich_skills=False):
                raise RuntimeError("Ollama exploded")
        svc2 = AIService(content_generator=_BrokenGen())
        fut2 = svc2.submit_class_generation_async(None, None, None)
        result2 = AIService.await_class_result(fut2, timeout=5.0)
        assert result2 is None, "Broken gen should produce None result"
        ok("Async class gen handles generator exceptions and yields None")

        # 16e. shutdown is safe to call repeatedly
        svc.shutdown()
        svc.shutdown()
        svc2.shutdown()
        ok("AIService.shutdown() is idempotent")
    except Exception as e:
        fail("AIService async class generation broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("17. world_db Repo Split (Round-trip via Public Methods)")
    try:
        from persistence.save_manager import new_game_state as _ngs, close_game as _cgs
        _pl = Player(name="RepoRT", base_class="warrior")
        _st = _ngs(_pl, SAVES_DIR, "_repo_split_test_")
        try:
            wdb = _st.world_db
            assert wdb is not None, "Test save needs a world_db"

            # NPC delegate round-trip
            wdb.upsert_npc("test_npc_1", "tmpl", "village_start", "merchant")
            row = wdb.get_npc("test_npc_1")
            assert row and row["template_id"] == "tmpl", "NPC repo delegate broken"
            ok("NPC repo delegate: upsert + get round-trip")

            # Faction delegate round-trip
            wdb.update_faction_standing("test_fac", 15.0)
            standing, rank = wdb.get_faction_standing("test_fac")
            assert standing == 15.0, f"Faction standing wrong: {standing}"
            ok("Faction repo delegate: update + get round-trip")

            # World flag delegate round-trip
            wdb.set_world_flag("test_repo_flag", "yes")
            assert wdb.get_world_flag("test_repo_flag") == "yes"
            assert wdb.has_world_flag("test_repo_flag") is True
            ok("World-state repo delegate: flag round-trip")

            # Death repo delegate
            wdb.record_death("combat:slime", "dungeon", 5, 1, 0.0, 8)
            assert wdb.get_death_count() == 1, "Death record didn't persist"
            ok("Death repo delegate: record + count round-trip")

            # World event repo delegate
            wdb.store_world_event("rumor", "Test rumor text", zone_id="z1", generated_turn=1)
            events = wdb.get_recent_events(limit=5, event_type="rumor")
            assert any(e["event_text"] == "Test rumor text" for e in events)
            ok("AI-content repo delegate: store_world_event + get_recent round-trip")

            # Verify the repo functions are also callable directly with conn
            from persistence.repos import npc_repo
            direct_row = npc_repo.get_npc(wdb._conn, "test_npc_1")
            assert direct_row and direct_row["template_id"] == "tmpl"
            ok("Repo functions are callable directly (sqlite3.Connection arg)")
        finally:
            _cgs(_st)
            for ext in (".json", ".db"):
                p = SAVES_DIR / f"_repo_split_test_{ext}"
                if p.exists():
                    try: p.unlink()
                    except OSError: pass
    except Exception as e:
        fail("world_db repo split broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("18. game_engine.py Extractions (bootstrap + integrator)")
    try:
        # 18a. Bootstrap module is importable and exposes the two free functions
        from core import bootstrap as _bs
        assert callable(_bs.load_registries), "bootstrap.load_registries missing"
        assert callable(_bs.setup_ai), "bootstrap.setup_ai missing"
        ok("core.bootstrap exposes load_registries + setup_ai")

        # 18b. background_integrator module is importable
        from core import background_integrator as _bi
        assert callable(_bi.integrate_results), "background_integrator.integrate_results missing"
        ok("core.background_integrator exposes integrate_results")

        # 18c. GameEngine still constructs cleanly (no broken imports)
        from core.game_engine import GameEngine as _GE
        eng = _GE()
        assert eng.class_registry is not None
        assert eng.ai_service is None  # not bootstrapped yet
        ok("GameEngine constructs without bootstrap")

        # 18d. integrate_results is a no-op when _bg_generator is None
        class _Stub:
            _bg_generator = None
        _bi.integrate_results(_Stub())  # must not raise
        ok("integrate_results no-ops cleanly when no BG generator")

        # 18e. Bot-action dispatch structural fix:
        # Previously the trade/rest/craft/talk_npc branches accidentally
        # elif-chained off the arrival-announcement guard. Verify each
        # action handler now fires independently.
        seen_actions: list[str] = []

        class _FakeBot:
            def __init__(self):
                self.bot_id = "b1"
                self.name = "TestBot"
                self.current_zone_id = "zone_a"
                self.gold = 100
                self.memory: list[str] = []
                self.current_goal = "explore"
                self.turn_last_acted = 0

        class _FakeBotMgr:
            def __init__(self, bot): self.bot = bot
            def get(self, bid): return self.bot
            def save_to_db(self, *_a, **_k): pass

        class _FakePlayer:
            turn_count = 5

        class _FakeState:
            current_scene_id = "elsewhere"  # NOT the bot's zone
            world_db = None
            player = _FakePlayer()

        class _FakeEngine:
            _bot_manager = _FakeBotMgr(_FakeBot())
            state = _FakeState()

        for act in ["trade", "rest", "craft", "talk_npc"]:
            eng_inst = _FakeEngine()
            eng_inst._bot_manager = _FakeBotMgr(_FakeBot())  # fresh bot per action
            _bi._handle_bot_action(eng_inst, {
                "type": "bot_action",
                "bot_id": "b1",
                "action": act,
                "target": "some_target",
            })
            # Each action should leave a turn_last_acted bump (proves the
            # handler ran, not just that we silently skipped).
            assert eng_inst._bot_manager.bot.turn_last_acted == 5, \
                f"action '{act}' did not run handler (turn_last_acted not updated)"
            seen_actions.append(act)
        assert seen_actions == ["trade", "rest", "craft", "talk_npc"]
        ok("Bot-action handlers all fire (trade/rest/craft/talk_npc no longer dead branches)")

        # 18f. World-event dispatcher handles unknown types gracefully
        _bi._dispatch(_FakeEngine(), {"type": "nonsense_type"})  # must not raise
        ok("Unknown background result types are ignored, not raised")
    except Exception as e:
        fail("game_engine extraction broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("19. game_engine.py Further Extractions (situation/menu/loop)")
    try:
        # 19a. situation_query module exposes its single entry point
        from core import situation_query as _sq
        assert callable(_sq.handle_situation_query)
        assert callable(_sq._convert_ai_option)
        ok("core.situation_query exposes handle_situation_query + _convert_ai_option")

        # 19b. menu_flow module exposes its three entry points
        from core import menu_flow as _mf
        assert callable(_mf.main_menu)
        assert callable(_mf.load_game_menu)
        assert callable(_mf.new_game)
        ok("core.menu_flow exposes main_menu + load_game_menu + new_game")

        # 19c. game_loop module exposes run_game_loop + _per_turn_ticks helper
        from core import game_loop as _gl
        assert callable(_gl.run_game_loop)
        assert callable(_gl._per_turn_ticks)
        ok("core.game_loop exposes run_game_loop + _per_turn_ticks")

        # 19d. situation_query short-circuits cleanly when AI is unavailable
        class _StubState:
            current_scene_id = "x"
            current_node_id = "root"
            player = Player(name="StubP", base_class="warrior")
        class _StubEngine:
            ai_service = None  # AI offline
            state = _StubState()
            scene_registry = None

        # Suppress the "press any key" prompt for the headless test.
        from ui import renderer as _rdr
        _orig_prompt = _rdr.prompt_any_key
        _rdr.prompt_any_key = lambda *a, **kw: None
        try:
            _sq.handle_situation_query(_StubEngine(), [])
        finally:
            _rdr.prompt_any_key = _orig_prompt
        ok("handle_situation_query returns early when ai_service is None")

        # 19e. _convert_ai_option locks options when stats fail
        class _AIOpt:
            option_id = "x"
            label = "test"
            triggers = []
            narrative = ""
            requires = {"min_stats": {"STR": 999}}
        class _StubState2:
            current_node_id = "root"
            player = Player(name="WeakP", base_class="warrior")
        class _StubEngine2:
            state = _StubState2()
        opt = _sq._convert_ai_option(_AIOpt(), _StubEngine2())
        assert opt.locked is True, "Option should be locked when min_stats unmet"
        assert "STR" in opt.lock_reason, f"lock_reason should mention STR: {opt.lock_reason}"
        ok("_convert_ai_option locks options when min_stats requirements unmet")

        # 19f. _convert_ai_option locks on missing flags too
        class _AIOpt2:
            option_id = "y"
            label = "test"
            triggers = []
            narrative = ""
            requires = {"flags": ["unset_flag"]}
        opt2 = _sq._convert_ai_option(_AIOpt2(), _StubEngine2())
        assert opt2.locked is True, "Option should be locked when required flag absent"
        ok("_convert_ai_option locks options when required flag absent")

        # 19g. game_engine.py shrank — soft assertion. After 6 extraction PRs
        # the file is ~165 lines; 250 leaves headroom for the occasional small
        # addition without letting accidental re-bloat slip through.
        from pathlib import Path as _Path
        gepath = _Path(__file__).parent / "core" / "game_engine.py"
        line_count = sum(1 for _ in gepath.open(encoding="utf-8"))
        assert line_count < 250, (
            f"core/game_engine.py grew back to {line_count} lines — "
            f"if you added a method, consider extracting it into a focused module"
        )
        ok(f"core/game_engine.py stays slim ({line_count} lines, <250 budget)")
    except Exception as e:
        fail("Further game_engine extraction broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("20. BG Scheduler Extraction")
    try:
        from core import bg_scheduler as _bs2

        assert callable(_bs2.maybe_submit_tasks)
        assert callable(_bs2._build_context)
        assert callable(_bs2._submit_rotation_slot)
        ok("core.bg_scheduler exposes maybe_submit_tasks + helpers")

        # 20a. maybe_submit_tasks is a no-op when there is no _bg_generator
        class _NoOpEngine:
            _bg_generator = None
            state = None
        _bs2.maybe_submit_tasks(_NoOpEngine())  # must not raise
        ok("maybe_submit_tasks no-ops cleanly when no BG generator")

        # 20b. Rotation cycles deterministically across 5 slots
        from config import BG_GEN_INTERVAL
        calls: list[tuple[str, dict]] = []

        class _FakeBG:
            def __init__(self): self.calls = calls
            def submit_world_event(self, **kw): self.calls.append(("world_event", kw))
            def submit_quest(self, **kw): self.calls.append(("quest", kw))
            def submit_zone_narrative(self, *a, **kw):
                self.calls.append(("zone_narrative", {"args": a}))
            def submit_rumor(self, **kw): self.calls.append(("rumor", kw))
            def submit_lore_entry(self, **kw): self.calls.append(("lore_entry", kw))
            def submit_area_activity(self, **kw): self.calls.append(("area_activity", kw))
            def submit_npc_branch(self, *a, **kw): self.calls.append(("npc_branch", {"args": a}))
            def submit_guild_tick(self, **kw): self.calls.append(("guild_tick", kw))
            def submit_bot_decision(self, *a, **kw): self.calls.append(("bot_decision", {"args": a}))
            def update_context(self, **kw): pass

        class _SchedScene:
            title = "Test Zone"
        class _SchedSceneReg:
            def get(self, _zid): return _SchedScene()
        class _SchedState:
            current_scene_id = "test_zone"
            world_db = None
            def __init__(self, turn):
                self.player = Player(name="SchedTest", base_class="warrior")
                self.player.turn_count = turn
        class _SchedEngine:
            def __init__(self, turn):
                self._bg_generator = _FakeBG()
                self.state = _SchedState(turn)
                self.scene_registry = _SchedSceneReg()
                self.npc_registry = None
                self.guild_registry = None
                self._bot_manager = None

        # Verify each rotation slot fires the corresponding submitter.
        # Rotation formula: (turn // BG_GEN_INTERVAL) % 5
        # → pick turn = (slot + 5) * BG_GEN_INTERVAL so slot 0 → quest, etc.
        seen_slots: list[str] = []
        for slot in range(5):
            turn = (slot + 5) * max(BG_GEN_INTERVAL, 1)
            calls.clear()
            eng_s = _SchedEngine(turn)
            _bs2._submit_rotation_slot(eng_s, _bs2._build_context(eng_s))
            assert calls, f"Slot {slot} (turn {turn}) submitted nothing"
            seen_slots.append(calls[0][0])
        assert seen_slots == [
            "quest", "zone_narrative", "rumor", "lore_entry", "area_activity",
        ], f"Rotation did not cycle through 5 types in order: {seen_slots}"
        ok("Rotation slot 0..4 cycles through quest / narrative / rumor / lore / area")
    except Exception as e:
        fail("BG scheduler extraction broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("21. Input Handler Extraction")
    try:
        from core import input_handler as _ih

        assert callable(_ih.get_current_options)
        assert callable(_ih.prompt_choice)
        assert callable(_ih._build_extras)
        ok("core.input_handler exposes get_current_options + prompt_choice + _build_extras")

        # 21a. Hotkey table has the expected 11 entries
        assert len(_ih._HOTKEYS) == 11, f"Expected 11 hotkeys, got {len(_ih._HOTKEYS)}"
        keys = [k for k, _m, _l in _ih._HOTKEYS]
        for required in ("[?]", "[K]", "[I]", "[J]", "[L]", "[W]", "[C]", "[S]", "[A]", "[G]", "[Q]"):
            assert required in keys, f"Hotkey {required} missing from _HOTKEYS"
        ok("All 11 documented hotkeys present in _HOTKEYS table")

        # 21b. _build_extras filters by feature flags + player flags
        from config import FEATURES as _F
        class _StubReg:
            pass
        class _IHPlayer:
            def __init__(self):
                self._flags: dict = {}
            def has_flag(self, key): return self._flags.get(key, False)
        class _IHState:
            def __init__(self): self.player = _IHPlayer()  # per-instance, not shared
        class _IHEngine:
            def __init__(self):
                self.state = _IHState()
                self.quest_registry = None

        # Save and restore feature flags
        _saved = dict(_F)
        try:
            _F["crafting_system"] = False
            _F["quest_system"]    = False
            _F["guild_system"]    = False
            extras = _ih._build_extras(_IHEngine())
            assert all("[C]" not in e for e in extras), "[C] should be hidden when crafting off"
            assert all("[J]" not in e for e in extras), "[J] should be hidden when quests off / no registry"
            assert all("[G]" not in e for e in extras), "[G] should be hidden when guilds off"
            ok("_build_extras hides crafting / quest / guild hotkeys when flags off")

            _F["crafting_system"] = True
            eng = _IHEngine()
            eng.state.player._flags["alchemist"] = True
            extras2 = _ih._build_extras(eng)
            assert any("[C]" in e for e in extras2), "[C] should appear when crafting_system on AND alchemist flag set"
            ok("[C] hotkey appears when crafting enabled AND player has alchemist/crafter flag")

            # 21c. Crafting on but NO alchemist/crafter → [C] still hidden
            eng2 = _IHEngine()  # fresh player, no flags
            extras3 = _ih._build_extras(eng2)
            assert all("[C]" not in e for e in extras3), (
                "[C] should still be hidden when crafting_system on but player lacks alchemist/crafter flag"
            )
            ok("[C] hotkey hidden when crafting on but player has neither flag")

            # 21d. Quest journal appears when quest_system flag + registry both present
            _F["quest_system"] = True
            class _IHEngine2:
                def __init__(self):
                    self.state = _IHState()
                    self.quest_registry = _StubReg()  # truthy
            extras4 = _ih._build_extras(_IHEngine2())
            assert any("[J]" in e for e in extras4), "[J] should appear with quest_system on + registry set"
            ok("[J] hotkey appears when quest_system on AND quest_registry present")
        finally:
            _F.clear()
            _F.update(_saved)
    except Exception as e:
        fail("Input handler extraction broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("22. Auction UI Extraction")
    try:
        from ui import auction_ui as _au

        assert callable(_au.show_auction_house)
        assert callable(_au._render_listings)
        assert callable(_au._render_status)
        assert callable(_au._buy_life_token)
        assert callable(_au._bid_on_listing)
        ok("ui.auction_ui exposes show_auction_house + render/buy/bid helpers")

        # 22a. _render_listings handles an empty list without raising
        _au._render_listings([])
        ok("_render_listings handles empty listing list without raising")

        # 22b. _render_listings handles a populated listing dict
        _au._render_listings([{
            "listing_id": "abc12345",
            "item": "Test Sword",
            "current_bid": 999,
            "turns_left": 5,
            "guild": "Test Guild",
        }])
        ok("_render_listings renders a populated listing without raising")
    except Exception as e:
        fail("Auction UI extraction broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("23. Final game_engine.py Extractions (render/rest/endings)")
    try:
        from ui import scene_renderer as _sr
        from systems import rest_system as _rs
        from systems import faction_endings as _fe

        assert callable(_sr.render_scene)
        assert callable(_sr._render_bots_present)
        ok("ui.scene_renderer exposes render_scene + _render_bots_present")

        assert callable(_rs.handle_rest)
        ok("systems.rest_system exposes handle_rest")

        assert callable(_fe.update_faction_standing)
        assert callable(_fe.check_ending_paths)
        ok("systems.faction_endings exposes update_faction_standing + check_ending_paths")

        # 23a. _INTRO_SCENES constant exists and contains expected entries
        assert "prologue" in _sr._INTRO_SCENES
        assert "character_creation" in _sr._INTRO_SCENES
        ok("scene_renderer._INTRO_SCENES suppresses status bar on prologue + character_creation")

        # 23b. faction_endings.update_faction_standing no-ops when registry absent
        class _NoFactionEngine:
            faction_registry = None
        _fe.update_faction_standing(_NoFactionEngine(), "any_id", 1.0)  # must not raise
        ok("update_faction_standing no-ops cleanly when faction_registry is None")

        # 23c. check_ending_paths skips early if the notified flag is already set
        class _FlagPlayer:
            def __init__(self): self._flags = {"ending_path_notified": True}
            def has_flag(self, k): return self._flags.get(k, False)
            def set_flag(self, k, v=True): self._flags[k] = v
        class _FlagState:
            def __init__(self): self.player = _FlagPlayer()
        class _NotifiedEngine:
            def __init__(self):
                self.state = _FlagState()
                self.faction_registry = None  # would crash if check_political_path called
        _fe.check_ending_paths(_NotifiedEngine())  # must not raise (early-returns)
        ok("check_ending_paths returns early when ending_path_notified flag set")

        # 23d. game_engine.py is now genuinely tiny — sanity check on the budget
        from pathlib import Path as _Path2
        ge = _Path2(__file__).parent / "core" / "game_engine.py"
        ge_lines = sum(1 for _ in ge.open(encoding="utf-8"))
        assert ge_lines < 200, f"core/game_engine.py at {ge_lines} lines, expected <200"
        ok(f"core/game_engine.py is genuinely a coordinator ({ge_lines} lines)")
    except Exception as e:
        fail("Final game_engine extractions broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("24. Verath Gate + Option Gates (flags_any / min_gold / lock_reason)")
    _vstate = None
    try:
        from scenes.option_logic import build_option, process_triggers

        # 24a. Every scene is reachable from the prologue. Verath used to be
        # an island: nothing linked into it.
        _edges: dict[str, set[str]] = {}
        for _sid, _sc in scene_reg._scenes.items():
            _edges[_sid] = {
                o.get("leads_to") for n in _sc.nodes.values()
                for o in n.get("options", [])
                if o.get("leads_to") not in (None, "__stay__")
            }
        _seen, _todo = {"prologue"}, ["prologue"]
        while _todo:
            for _nxt in _edges.get(_todo.pop(), ()):
                if _nxt in _edges and _nxt not in _seen:
                    _seen.add(_nxt); _todo.append(_nxt)
        _unreachable = sorted(set(_edges) - _seen)
        assert not _unreachable, f"Scenes unreachable from prologue: {_unreachable}"
        ok(f"All {len(_edges)} scenes reachable from the prologue")

        _vp = Player(name="GateTester", base_class="warrior")
        _vstate = new_game_state(_vp, SAVES_DIR, "_verath_gate_test_")

        def _opt(scene_id: str, node_id: str, option_id: str) -> dict:
            node = scene_reg.get(scene_id).get_node(node_id)
            return next(o for o in node["options"] if o["option_id"] == option_id)

        # 24b. Generic gate fields.
        _g = build_option({"option_id": "a", "label": "a",
                           "requires": {"flags_any": ["x_flag", "y_flag"]}}, _vstate)
        assert _g.locked, "flags_any with no flag set should lock"
        _vp.set_flag("y_flag")
        assert not build_option({"option_id": "a", "label": "a",
                                 "requires": {"flags_any": ["x_flag", "y_flag"]}}, _vstate).locked
        _vp.gold = 100
        _g = build_option({"option_id": "b", "label": "b", "requires": {"min_gold": 2500}}, _vstate)
        assert _g.locked and "25" in _g.lock_reason, f"min_gold lock reason: {_g.lock_reason!r}"
        _vp.gold = 2500
        assert not build_option({"option_id": "b", "label": "b",
                                 "requires": {"min_gold": 2500}}, _vstate).locked
        _g = build_option({"option_id": "c", "label": "c",
                           "requires": {"flags": ["nope"], "lock_reason": "Custom why."}}, _vstate)
        assert _g.locked and _g.lock_reason == "Custom why."
        process_triggers(["spend_gold:1000"], _vstate)
        assert _vp.gold == 1500
        process_triggers(["spend_gold:999999"], _vstate)
        assert _vp.gold == 0, "spend_gold must floor at zero"
        ok("flags_any / min_gold / lock_reason gates + spend_gold trigger behave")

        # 24c. The city gate is locked by default, with an in-world reason,
        # and opens once verath_access is set.
        _enter = _opt("village_start", "root", "enter_verath")
        _g = build_option(_enter, _vstate)
        assert _g.locked and "gate guards" in _g.lock_reason, _g.lock_reason
        assert not build_option(_opt("village_start", "root", "approach_city_gate"), _vstate).should_hide
        _vp.set_flag("verath_access")
        assert not build_option(_enter, _vstate).locked
        assert _enter["leads_to"] == "verath_city"
        assert build_option(_opt("village_start", "root", "approach_city_gate"), _vstate).should_hide
        del _vp.flags["verath_access"]
        ok("Verath gate locked with reason until verath_access, then open")

        # 24d. Normal route: both Floor 1 fights → report option → access.
        _rep = _opt("dungeon_floor1", "root", "report_floor1_cleared")
        _vp.set_flag("goblins_defeated")
        assert build_option(_rep, _vstate).locked, "Needs both fights, not one"
        _vp.set_flag("crystal_spiders_cleared")
        assert not build_option(_rep, _vstate).locked
        process_triggers(_rep["triggers"], _vstate)
        assert _vp.has_flag("verath_access") and _vp.has_flag("floor1_cleared")
        assert "flag:verath_access" in _opt("dungeon_floor1", "floor2_warning", "descend_anyway")["triggers"]
        ok("Clearing Floor 1 (or descending to Floor 2) grants verath_access")

        # 24e. Early routes: each is divergent, gated, grants access, lands in Verath.
        _routes = {
            "bribe_gate_guard": {"min_gold": 2500},
            "talk_past_gate_guard": {"min_stats": {"INT": 12}},
            "stow_away_on_cart": {"min_stats": {"LCK": 12}},
            "wanderer_blind_spot_route": {"flags": ["wanderer_route_known"]},
        }
        for _oid, _req in _routes.items():
            _o = _opt("village_start", "city_gate", _oid)
            assert _o["expected"] is False, f"{_oid} should count as divergent"
            for _k, _v in _req.items():
                assert _o["requires"][_k] == _v, f"{_oid} gate {_k}"
            assert "flag:verath_access" in _o["triggers"] and _o["leads_to"] == "verath_city"
        assert "spend_gold:2500" in _opt("village_start", "city_gate", "bribe_gate_guard")["triggers"]
        ok("Bribe / INT / LCK / wanderer routes are divergent, gated, and open Verath")

        # 24f. The wanderer reveals his route once you've offered to help.
        _w = npc_reg.get_by_npc_id("gray_wanderer")
        _wopts = {o.option_id: o for n in _w.dialogue_nodes.values() for o in n.options}
        for _oid in ("ask_verath_blind_spot", "ask_verath_blind_spot_friendly"):
            assert _wopts[_oid].requires.get("flags") == ["wanderer_asked_for_help"]
            assert "flag:wanderer_route_known" in _wopts[_oid].triggers
        ok("Gray Wanderer reveals the blind-spot route after the mapping favour")

        # 24g. The fast model may grant access only while the player lacks it.
        from ai.content_generator import _dynamic_option_rules
        _fresh = Player(name="RuleTester")
        _gate = "Outer Verath. The guarded gate into the city proper."
        assert any("flag:verath_access" in r for r in _dynamic_option_rules(_fresh, _gate))
        # Not at the gate: no Verath rule (it made the model set the Rite in Verath).
        assert not any("verath" in r.lower() for r in _dynamic_option_rules(_fresh, "The Classification Rite"))
        _fresh.set_flag("verath_access")
        assert not any("verath_access" in r for r in _dynamic_option_rules(_fresh, _gate))
        ok("AI situation prompt offers verath_access only near the gate, while locked out")

        # 24h. flags_any now actually gates Floor 3's sealed door.
        _door = _opt("dungeon_floor3", "root", "go_core_chamber")
        _fresh_state_player = Player(name="DoorTester")
        _vstate.player = _fresh_state_player
        assert build_option(_door, _vstate).locked, "Sealed door should need recorded data"
        _fresh_state_player.set_flag("fracture_data_recorded")
        assert not build_option(_door, _vstate).locked
        _vstate.player = _vp
        ok("Floor 3 sealed door honours its flags_any gate")
    except Exception as e:
        fail("Verath gate / option gates broken", e)
        traceback.print_exc()
    finally:
        if _vstate is not None:
            close_game(_vstate)
        for _ext in (".json", ".db"):
            _pp = SAVES_DIR / f"_verath_gate_test_{_ext}"
            if _pp.exists():
                try: _pp.unlink()
                except OSError: pass

    # ─────────────────────────────────────────────────────────────────────────
    section("25. AI Trigger Policy (allow-list for AI-authored effects)")
    try:
        from systems.ai_trigger_policy import (
            ai_required_flag_met, parse_world_action, sanitize_ai_triggers,
        )

        def _san(trigs):
            return sanitize_ai_triggers(trigs, item_reg)

        # 25a. Economy / progression effects are never allowed from AI.
        _banned = [
            "give_gold:999999", "spend_gold:1", "give_skill:god_mode",
            "set_base_class:paladin", "start_quest:crown_ascension",
            "talk_npc:gray_wanderer", "buy_item:iron_sword:1", "rest_inn:0",
            "join_guild:x", "update_faction:verath_crown:+100", "buy_life_token",
            "complete_quest:x", "set_species:void",
        ]
        _v = _san(_banned)
        assert _v.kept == [] and len(_v.dropped) == len(_banned), _v
        ok("Gold / skills / classes / quests / NPCs / shops / factions dropped")

        # 25b. Flags are namespaced, except the deliberately grantable ones.
        _v = _san(["flag:Found Gold Vein", "flag:crown_ruler_candidate_flag",
                   "flag:verath_access", "flag:ai_already", "flag:!!!"])
        assert _v.kept == ["flag:ai_found_gold_vein", "flag:ai_crown_ruler_candidate_flag",
                           "flag:verath_access", "flag:ai_already"], _v.kept
        assert _v.dropped == ["flag:!!!"]
        ok("AI flags namespaced to ai_* (story flags untouchable); verath_access passes")

        # 25c. Items: only existing common/uncommon consumables + materials, max 1.
        assert _san(["give_item:health_potion"]).kept == ["give_item:health_potion"]
        assert _san(["give_item:forest_herb", "give_item:bread"]).kept == ["give_item:forest_herb"]
        for _bad in ("no_such_item", "void_shard", "ancient_tome",
                     "torven_hammer", "room_key", "iron_sword", "chain_shirt"):
            assert _san([f"give_item:{_bad}"]).kept == [], _bad
        ok("give_item limited to one real common consumable/material (no gear, keys, catalysts)")

        # 25d. Alignment clamped, combat must be a real encounter.
        assert _san(["alignment:-50"]).kept == ["alignment:-5"]
        assert _san(["alignment:+2.5"]).kept == ["alignment:+2.5"]
        assert _san(["alignment:0", "alignment:abc"]).kept == []
        assert _san(["combat:wolf_pack", "combat:dragon_army"]).kept == ["combat:wolf_pack"]
        ok("alignment clamped to ±5; combat only for known encounters")

        # 25e. world_action tags: slugged, one per option, malformed dropped.
        _v = _san(["world_action:Mine:Gold Vein", "world_action:mine:silver",
                   "world_action:onlyverb"])
        assert _v.kept == ["world_action:mine:gold_vein"], _v.kept
        assert parse_world_action("world_action:forage:wild herbs") == ("forage", "wild_herbs")
        assert parse_world_action("world_action::gold") is None
        ok("world_action tags normalised, one per option")

        # 25f. AI requires.flags accept the namespaced form of their own flags.
        _fp = Player(name="PolicyP")
        _fp.set_flag("ai_found_gold_vein"); _fp.set_flag("met_the_wanderer")
        assert ai_required_flag_met(_fp.has_flag, "found_gold_vein")
        assert ai_required_flag_met(_fp.has_flag, "met_the_wanderer")
        assert not ai_required_flag_met(_fp.has_flag, "never_set")
        ok("AI option gates match both ai_-namespaced and authored flags")

        # 25g. Both AI entry points apply the policy.
        from core import situation_query as _sq2
        class _AIOptStub:
            option_id = "ai_mine"
            label = "Mine the gold"
            narrative = "You chip at the vein."
            triggers = ["give_gold:5000", "flag:struck_gold", "world_action:mine:gold"]
            requires = {"flags": ["struck_gold"]}
        class _StateStub:
            player = Player(name="SqPolicy")
            current_node_id = "root"
        class _EngineStub:
            item_registry = item_reg
            state = _StateStub()
        _so = _sq2._convert_ai_option(_AIOptStub(), _EngineStub())
        assert _so.triggers == ["flag:ai_struck_gold", "world_action:mine:gold"], _so.triggers
        assert _so.locked, "Gate on an unset AI flag should lock"
        _EngineStub.state.player.set_flag("ai_struck_gold")
        assert not _sq2._convert_ai_option(_AIOptStub(), _EngineStub()).locked
        ok("[?] situation options are sanitised before reaching the engine")

        from core import background_integrator as _bi
        from entities.npc import NPCRegistry as _NR
        _nreg = _NR(); _nreg.load_from_dir(DATA_DIR / "npcs")
        class _BiState:
            world_db = None
            player = Player(name="BiP")
            current_scene_id = "village_start"
        class _BiEngine:
            npc_registry = _nreg
            item_registry = item_reg
            state = _BiState()
        _bi._handle_npc_branch(_BiEngine(), {
            "npc_id": "gray_wanderer",
            "node": {"node_id": "ai_test_branch", "npc_text": "…", "options": [{
                "option_id": "greedy", "label": "Pay me", "npc_response": "…",
                "triggers": ["give_gold:100000", "flag:trusted", "give_skill:x"],
                "leads_to_node": "__exit__",
            }]},
        })
        _branch = _nreg.get("gray_wanderer").dialogue_nodes["ai_test_branch"]
        assert _branch.options[0].triggers == ["flag:ai_trusted"], _branch.options[0].triggers
        ok("Background AI NPC dialogue branches are sanitised before registration")
    except Exception as e:
        fail("AI trigger policy broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("26. Traders: selling, travelling NPCs, runtime NPCs + recipes")
    _tstate = None
    try:
        from config import TRADER_ROUTE_STAY_TURNS
        from entities.npc import NPCTemplate
        from systems import trade_system as _ts
        from systems.npc_system import get_npc_zone
        from systems.alchemy_system import craft_item, load_recipes, register_recipe

        _wren = npc_reg.get("wren_pedlar")
        _torven = npc_reg.get("torven_blacksmith")
        assert _wren and _wren.ambient and _wren.trades and _wren.route
        assert _torven.trades and "type:WEAPON" in _torven.trades.buys

        # 26a. Travelling route: one zone per TRADER_ROUTE_STAY_TURNS.
        _n = TRADER_ROUTE_STAY_TURNS
        assert [get_npc_zone(_wren, t) for t in (0, _n - 1, _n, 2 * _n, 3 * _n)] == \
            ["village_start", "village_start", "camp_rest", "verath_city", "village_start"]
        ok("Travelling trader walks outer market → camp → Verath and loops")

        # 26b. Ambient presence → automatic "Talk to …" option at zone root.
        _tp = Player(name="TraderTester", base_class="warrior")
        _tstate = new_game_state(_tp, SAVES_DIR, "_trader_test_")
        _tstate.current_scene_id = "camp_rest"
        _tp.turn_count = _n
        assert [n.template_id for n in _ts.ambient_npcs_here(npc_reg, _tstate)] == ["wren_pedlar"]
        _tp.turn_count = 0
        assert _ts.ambient_npcs_here(npc_reg, _tstate) == []

        from core import input_handler as _ih
        class _IHEngine:
            npc_registry = npc_reg
            scene_registry = scene_reg
            state = _tstate
        _tstate.current_scene_id = "village_start"; _tstate.current_node_id = "root"
        _ambient = _ih._ambient_npc_options(_IHEngine(), [])
        assert [o.triggers for o in _ambient] == [["talk_npc:wren_pedlar"]], _ambient
        assert "Wren" in _ambient[0].label
        _linked = [_ambient[0]]
        assert _ih._ambient_npc_options(_IHEngine(), _linked) == [], "No duplicate when already linked"
        assert any(o.option_id == "__ambient__wren_pedlar" for o in _ih.get_current_options(_IHEngine()))
        _tstate.current_node_id = "guard_post"
        assert not any(o.option_id.startswith("__ambient__") for o in _ih.get_current_options(_IHEngine()))
        _tstate.current_node_id = "root"
        ok("Ambient NPCs get a Talk-to option at the zone root, no duplicates, not on sub-nodes")

        # 26c. Selling: type/id matching, price, equipped copy kept.
        _herb = item_reg.get("forest_herb"); _sword = item_reg.get("iron_sword")
        assert _ts.trader_buys(_wren, _herb) and not _ts.trader_buys(_wren, _sword)
        assert _ts.trader_buys(_torven, _sword) and not _ts.trader_buys(_torven, _herb)
        assert _ts.sell_price(_wren, _herb) == int(_herb.value_gold * 100 * 0.5)
        _tp.inventory.clear(); _tp.gold = 0
        _tp.add_item("forest_herb", 3); _tp.add_item("iron_sword", 1)
        _tp.equipped.weapon = "iron_sword"
        assert _ts.sellable_items(_tp, _torven, item_reg) == [], "Last equipped copy is not sellable"
        _tp.add_item("iron_sword", 1)
        assert _ts.sellable_items(_tp, _torven, item_reg)[0].quantity == 1
        _ok, _msg, _earned = _ts.sell_item(_tp, _wren, "forest_herb", item_reg, 2)
        assert _ok and _earned == 2 * _ts.sell_price(_wren, _herb) and _tp.gold == _earned
        assert sum(s.quantity for s in _tp.inventory if s.item_id == "forest_herb") == 1
        assert not _ts.sell_item(_tp, _wren, "iron_sword", item_reg)[0], "Wren doesn't buy swords"
        ok("sell_item pays trader rate, removes items, respects equipped + trader interests")

        # 26d. Buying from wares.
        _tp.gold = 400
        assert not _ts.buy_item(_tp, _wren, "rations", item_reg)[0], "500c rations, only 400c"
        _tp.gold = 1000
        _ok, _msg = _ts.buy_item(_tp, _wren, "rations", item_reg)
        assert _ok and _tp.gold == 500 and _tp.has_item("rations")
        assert not _ts.buy_item(_tp, _wren, "iron_sword", item_reg)[0]
        from core.dialogue_handler import DialogueHandlerMixin
        class _DH:
            item_registry = item_reg
        assert [o.option_id for o in DialogueHandlerMixin._build_trade_options(_DH(), _wren)] == \
            ["__trade_buy__", "__trade_sell__"]
        assert [o.option_id for o in DialogueHandlerMixin._build_trade_options(_DH(), _torven)] == \
            ["__trade_sell__"], "Torven only buys"
        ok("buy_item checks gold + wares; dialogue offers Browse/Sell as the trader supports")

        # 26e. Runtime NPC registration.
        _gen = NPCTemplate.model_validate({
            "template_id": "gen_ore_buyer", "npc_id": "gen_ore_buyer", "name": "Brenna",
            "role": "merchant", "zone_id": "village_start", "description": "Buys ore.",
            "ambient": True, "trades": {"buys": ["type:MATERIAL"]}, "is_ai_generated": True,
            "dialogue_nodes": {"root": {"node_id": "root", "npc_text": "Ore?", "options": []}},
        })
        npc_reg.register(_gen)
        assert npc_reg.get("gen_ore_buyer") is _gen
        assert "gen_ore_buyer" in [n.template_id for n in _ts.ambient_npcs_here(npc_reg, _tstate)]
        ok("NPCRegistry.register adds an NPC at runtime; ambient ones appear immediately")

        # 26f. Runtime recipes + the crafting quantity exploit.
        _recipes = load_recipes(DATA_DIR)
        _n0 = len(_recipes)
        assert register_recipe(_recipes, {"recipe_id": "herb_tea", "ingredients": [
            {"item_id": "forest_herb", "qty": 1}], "output_item_id": "health_potion"}, item_reg)[0]
        assert len(_recipes) == _n0 + 1 and _recipes[-1]["name"] == "Herb Tea"
        assert not register_recipe(_recipes, {"recipe_id": "herb_tea", "ingredients": [
            {"item_id": "forest_herb", "qty": 1}], "output_item_id": "health_potion"}, item_reg)[0]
        assert not register_recipe(_recipes, {"recipe_id": "x", "ingredients": [
            {"item_id": "unobtainium", "qty": 1}], "output_item_id": "health_potion"}, item_reg)[0]
        assert not register_recipe(_recipes, {"recipe_id": "y", "ingredients": [
            {"item_id": "forest_herb", "qty": 1}], "output_item_id": "nothing"}, item_reg)[0]
        _tp.inventory.clear(); _tp.add_item("forest_herb", 1); _tp.add_item("empty_vial", 1)
        _ok, _msg = craft_item(_tp, "basic_health_salve", item_reg, _recipes)
        assert not _ok and not _tp.has_item("health_potion"), "1 herb must not craft a 2-herb recipe"
        _tp.add_item("forest_herb", 1)
        assert craft_item(_tp, "basic_health_salve", item_reg, _recipes)[0]
        assert _tp.has_item("health_potion") and not _tp.has_item("forest_herb")
        ok("register_recipe validates; crafting now checks ingredient quantities")
    except Exception as e:
        fail("Traders / runtime registration broken", e)
        traceback.print_exc()
    finally:
        if _tstate is not None:
            close_game(_tstate)
        for _ext in (".json", ".db"):
            _pp = SAVES_DIR / f"_trader_test_{_ext}"
            if _pp.exists():
                try: _pp.unlink()
                except OSError: pass
        # Leave the shared NPC registry as the rest of the suite expects it.
        npc_reg.remove("gen_ore_buyer")

    # ─────────────────────────────────────────────────────────────────────────
    section("27. World Growth (typed action → primary model → new world content)")
    _wg_slot = "_world_growth_test_"
    _wstate = None
    try:
        from ai.ai_service import AIService
        from ai.background_generator import BackgroundGenerator
        from ai.content_generator import _dynamic_option_rules
        from ai.response_validator import AIWorldExpansionResponse
        from config import WORLD_YIELD_COOLDOWN_TURNS
        from core import background_integrator as _bi2
        from core import world_growth_flow as _wgf
        from core.game_engine import GameEngine
        from core.menu_flow import _attach_engine_handles
        from core.situation_query import _convert_ai_option
        from systems import world_growth as _wg
        from ui import renderer as _rdr2

        # 27a. Action keys + fallback tags.
        assert _wg.action_key("Mine", "gold veins") == "mine:gold"
        assert _wg.action_key("mine", "the gold") == "mine:gold"
        assert _wg.normalize_subject("wild berries") == "wild_berry"
        assert _wg.derive_world_action("[AI] Mine the gold vein") == "world_action:mine:gold_vein"
        assert _wg.derive_world_action("Go") is None
        # No example action in the [?] prompt: the 1B model copied "mine gold".
        assert not any("mine" in r or "world_action" in r for r in _dynamic_option_rules(Player(name="R")))
        class _NoTagOpt:
            option_id = "ai_x"; label = "Forage for wild berries"; narrative = ""
            triggers = ["flag:x"]; requires = {}
        class _NoTagEngine:
            item_registry = item_reg
            class state:
                player = Player(name="T"); current_node_id = "root"
        assert "world_action:forage:wild_berries" in _convert_ai_option(_NoTagOpt(), _NoTagEngine()).triggers
        # The model's own tag is ignored: the label says what the player does.
        class _WrongTagOpt(_NoTagOpt):
            label = "Carve the glowing sigils"; triggers = ["world_action:mine:gold"]
        _wt = _convert_ai_option(_WrongTagOpt(), _NoTagEngine()).triggers
        assert "world_action:carve:glowing_sigils" in _wt and "world_action:mine:gold" not in _wt, _wt
        # The [?] prompt answers the question from the scene and lists the
        # existing choices; it carries no example action to copy.
        from ai.prompt_builder import build_dynamic_options_prompt
        _pr = build_dynamic_options_prompt("Whats happening", "The Day the Sky Shattered",
                                           "The Classification Rite is beginning.",
                                           ["Step forward."], {"STR": 5}, [], {},
                                           _dynamic_option_rules(Player(name="R")))
        assert "Step forward." in _pr and "Classification Rite" in _pr and "Whats happening" in _pr
        assert "gold" not in _pr.lower() and "Short label" not in _pr
        from ai.response_validator import AIDynamicOptionsResponse as _ADR
        assert _ADR.model_validate({"situation_text": "x", "options": [{"label": "Short label"}]}).options == []
        # Looking, waiting and moving don't grow the world (seen live: "Take
        # a moment" announced a DISCOVERY); helper verbs are skipped.
        for _lbl in ("Take a moment.", "Examine your surroundings.", "Attempt to move forward.",
                     "Observe the damage.", "Search for clues near."):
            assert _wg.derive_world_action(_lbl) is None, _lbl
        assert _wg.derive_world_action("Try to pry open the crate") == "world_action:pry:open_crate"
        # Seen live with mistral-nemo + gemma3:1b: talking, remembering and
        # scanning announced discoveries. Only hands-on work grows the world.
        for _lbl in ("Politely decline further interaction.", "Recall your memories.",
                     "Scan for allies.", "Attempt to track them.", "Dash away.", "Catch it"):
            assert _wg.derive_world_action(_lbl) is None, _lbl
        assert _wg.derive_world_action("Fish the stream") == "world_action:fish:stream"
        # "<AI: …>" placeholder wrappers are stripped from narratives.
        from core.choice_handler import _sanitise_narrative
        assert _sanitise_narrative("<AI: A nimble rogue spots a masked figure.>") == "A nimble rogue spots a masked figure."
        # AI NPC dialogue nodes: "text" is accepted for npc_text; unusable
        # nodes are dropped quietly (INFO) instead of a WARNING traceback.
        from core.background_integrator import _handle_npc_branch
        class _NpcStub:
            dialogue_nodes = {}
        class _RegStub:
            def get(self, _id): return _npc_stub
        _npc_stub = _NpcStub()
        class _EngStub:
            npc_registry = _RegStub(); item_registry = item_reg
            class state:
                world_db = None
        import unittest.mock as _mk
        with _mk.patch("core.background_integrator.feature", lambda n: n == "npc_system"):
            _handle_npc_branch(_EngStub(), {"npc_id": "torven", "node": {
                "node_id": "t1", "text": "Steel doesn't lie.", "options": []}})
            _handle_npc_branch(_EngStub(), {"npc_id": "torven", "node": {"node_id": "t2", "options": []}})
        assert _npc_stub.dialogue_nodes["t1"].npc_text == "Steel doesn't lie." and "t2" not in _npc_stub.dialogue_nodes
        # Echoed answers and split-up existing choices are dropped.
        from core.situation_query import _repeats_choice
        _have = ["Step forward. Accept what comes.", "Take a moment. Examine your surroundings."]
        assert _repeats_choice("[AI] Take a moment.", _have) and _repeats_choice("Examine your surroundings", _have)
        assert not _repeats_choice("Touch the glowing crystal", _have)
        # Invented skill effect types map onto real ones instead of failing.
        from ai.response_validator import AISkillEffectResponse as _SE
        assert _SE(effect_type="reveal").effect_type == "buff"
        assert _SE(effect_type="stun").effect_type == "debuff" and _SE(effect_type="heal").effect_type == "heal"
        # Warnings from background threads stay out of the terminal.
        import logging as _lg, threading as _th
        from utils.logging_setup import _MainThreadOnly
        _rec = _lg.LogRecord("ai", _lg.WARNING, "", 0, "x", None, None)
        assert _MainThreadOnly().filter(_rec)
        _res = []
        _t = _th.Thread(target=lambda: _res.append(_MainThreadOnly().filter(
            _lg.LogRecord("ai", _lg.WARNING, "", 0, "x", None, None))), name="bg-gen")
        _t.start(); _t.join()
        assert _res == [False]
        ok("Action keys normalise; options are tagged from their label, never the model's tag; "
           "the [?] prompt is grounded in the scene with nothing to parrot")

        import unittest.mock as _mock_fu
        # 27b. A real engine with a fake primary model + a real BG generator
        # (not started: the test drives the worker step by step).
        _response = AIWorldExpansionResponse.model_validate({
            "summary": "Word spreads: Brenna is buying ore in the outer market.",
            "yield_item": {"item_id": "gold_ore", "name": "Gold Ore", "value_gold": 500},
            "items": [{"item_id": "gold_ingot", "name": "Gold Ingot", "value_gold": 9}],
            "recipes": [{"recipe_id": "smelt_gold", "ingredients": [{"item_id": "gold_ore", "qty": 3}],
                         "output_item_id": "gold_ingot"}],
            "trader": {"name": "Brenna", "location": "outer_market", "greeting": "'Ore?'",
                       "buys": ["gold_ore", "gold_ingot"], "sells": [{"item_id": "gold_ingot", "price_gold": 20}]},
        })
        class _FakePrimary:
            calls = 0
            def generate_world_expansion(self, **kw):
                _FakePrimary.calls += 1
                return {"response": _response, "skill": None}
        _fake = _FakePrimary()
        _bg = BackgroundGenerator(_fake)
        _eng = GameEngine()
        _orig_print = _rdr2.console.print
        _rdr2.console.print = lambda *a, **k: None
        try:
            _eng.bootstrap()
        finally:
            _rdr2.console.print = _orig_print
        _eng.ai_service = AIService(content_generator=_fake, background_generator=_bg)
        _wp = Player(name="Miner", base_class="warrior")
        _wstate = new_game_state(_wp, SAVES_DIR, _wg_slot)
        _eng.state = _wstate
        _attach_engine_handles(_eng)
        _wstate.current_scene_id = "dungeon_floor1"

        # 27c. First sighting → recorded, queued, no content yet.
        _wgf.on_world_action(_eng, "mine", "gold vein", "You chip at a glinting seam.")
        _row = _wstate.world_db.get_world_action("mine:gold")
        assert _row and _row["status"] == "pending" and _row["subject"] == "gold"
        _task = _bg._task_queue.get_nowait()
        assert _task["type"] == "world_expansion" and _task["action_key"] == "mine:gold"
        assert "forest_herb" in _task["known_items"]
        assert _eng.item_registry.get("gen_gold_ore") is None
        ok("First 'mine gold' logs a discovery and queues a background expansion")

        # 27d. Worker produces the result; the integrator applies it.
        _result = _bg._process_task(_task)
        assert _result["type"] == "world_expansion" and _FakePrimary.calls == 1
        _bi2._dispatch(_eng, _result)
        _ore = _eng.item_registry.get("gen_gold_ore")
        assert _ore is not None and _ore.value_gold < 500, "Value clamped by economy"
        assert _wp.has_item("gen_gold_ore"), "Discovery reward: the new item"
        assert any(r["recipe_id"] == "gen_smelt_gold" for r in _eng.recipes)
        _brenna = _eng.npc_registry.get("gen_npc_brenna")
        assert _brenna and _brenna.ambient and _brenna.zone_id == "village_start"
        assert _wstate.world_db.get_world_action("mine:gold")["status"] == "expanded"
        assert any(e["event_type"] == "world_growth" for e in _wstate.world_db.get_recent_events())
        _bi2._dispatch(_eng, _result)
        assert sum(s.quantity for s in _wp.inventory if s.item_id == "gen_gold_ore") == 1, \
            "A duplicate result must not apply twice"
        ok("Expansion applied: clamped item, recipe, trader, reward, world log; idempotent")

        # 27e. Repeating the action yields ore, on cooldown.
        _wgf.on_world_action(_eng, "mine", "gold", "")
        assert sum(s.quantity for s in _wp.inventory if s.item_id == "gen_gold_ore") == 1, "Cooldown"
        _wp.turn_count += WORLD_YIELD_COOLDOWN_TURNS
        _wgf.on_world_action(_eng, "Mine", "gold veins", "")
        assert sum(s.quantity for s in _wp.inventory if s.item_id == "gen_gold_ore") == 2
        assert _bg._task_queue.empty(), "Known actions never re-query the model"
        ok("Repeating an expanded action gathers its yield after the cooldown")

        # 27f. The trader is really there and really trades.
        from core.input_handler import get_current_options
        _wstate.current_scene_id = "village_start"; _wstate.current_node_id = "root"
        assert any(o.option_id == "__ambient__gen_npc_brenna" for o in get_current_options(_eng))
        assert _ts.sell_item(_wp, _brenna, "gen_gold_ore", _eng.item_registry)[0]
        ok("Generated trader appears in the outer market and buys the new ore")

        # 27f2. Follow-ups replace the earlier AI options instead of piling up
        # (live: the menu grew to 11 entries), with ids unique per turn.
        from scenes.scene_base import SceneOption as _SO
        _key = f"{_wstate.current_scene_id}:{_wstate.current_node_id}"
        _mk_opt = lambda oid, lbl: _SO(option_id=oid, label=lbl, leads_to="__stay__",
                                       leads_to_node="root", narrative="You look closer.")
        _wstate._dynamic_options[_key] = [_mk_opt("ai_old_1", "[AI] Old one"), _mk_opt("ai_old_2", "[AI] Old two")]
        with _mock_fu.patch.object(type(_eng), "_ai_online", lambda self: True), \
             _mock_fu.patch.object(type(_eng), "_generate_ai_followup", lambda self, n: ["Next A", "Next B"]), \
             _mock_fu.patch.object(_rdr2, "prompt_any_key", lambda: None), \
             _mock_fu.patch.object(_rdr2.console, "print", lambda *a, **k: None):
            _eng._handle_choice(_mk_opt("ai_old_1", "[AI] Old one"))
        _now = _wstate._dynamic_options[_key]
        assert [o.label for o in _now] == ["[AI] Next A", "[AI] Next B"], [o.label for o in _now]
        assert len({o.option_id for o in _now}) == 2 and all(o.option_id.startswith("ai_followup_") for o in _now)
        ok("AI follow-up options replace the previous ones (no pile-up)")

        # 27g. Per-save: reload restores it; another save never sees it.
        from persistence.save_manager import load_game as _lg, save_game as _sg
        _sg(_wstate, _wg_slot, SAVES_DIR)
        close_game(_wstate); _wstate = None
        _other = new_game_state(Player(name="Other"), SAVES_DIR, _wg_slot + "b")
        _eng.state = _other
        _attach_engine_handles(_eng)
        assert _eng.item_registry.get("gen_gold_ore") is None
        assert _eng.npc_registry.get("gen_npc_brenna") is None
        assert not any(r["recipe_id"].startswith("gen_") for r in _eng.recipes)
        close_game(_other)
        _wstate = _lg(_wg_slot, SAVES_DIR)
        _eng.state = _wstate
        _attach_engine_handles(_eng)
        assert _eng.item_registry.get("gen_gold_ore") is not None
        assert _eng.npc_registry.get("gen_npc_brenna") is not None
        assert any(r["recipe_id"] == "gen_smelt_gold" for r in _eng.recipes)
        ok("Generated world is per save: restored on load, absent from other saves")

        # 27h. No AI: actions are still recorded, nothing is promised.
        _eng.ai_service = AIService()
        _wgf.on_world_action(_eng, "forage", "mushrooms", "")
        assert _wstate.world_db.get_world_action("forage:mushroom")["status"] == "no_ai"
        ok("Without Ollama the action is logged as no_ai and play continues")
    except Exception as e:
        fail("World growth broken", e)
        traceback.print_exc()
    finally:
        if _wstate is not None:
            close_game(_wstate)
        for _slot in (_wg_slot, _wg_slot + "b"):
            for _ext in (".json", ".db"):
                _pp = SAVES_DIR / f"{_slot}{_ext}"
                if _pp.exists():
                    try: _pp.unlink()
                    except OSError: pass

    # ─────────────────────────────────────────────────────────────────────────
    section("28. World Growth guard rails (trader cap, expansion cap, retry, log)")
    _d_slot = "_world_growth_d_test_"
    _dstate = None
    try:
        from ai.ai_service import AIService
        from ai.background_generator import BackgroundGenerator
        from ai.response_validator import AIWorldExpansionResponse
        from config import WORLD_MAX_TRADERS_PER_LOCATION
        from core import world_growth_flow as _wgf2
        from core.game_engine import GameEngine
        from core.menu_flow import _attach_engine_handles
        from systems import world_growth as _wg2
        from ui import renderer as _rdr3

        def _resp(n: int, location: str = "outer_market"):
            return AIWorldExpansionResponse.model_validate({
                "yield_item": {"item_id": f"thing_{n}", "name": f"Thing {n}", "value_gold": 2},
                "trader": {"name": f"Trader {n}", "location": location,
                           "buys": [f"thing_{n}"], "sells": [{"item_id": f"thing_{n}", "price_gold": 3}]},
            })

        _deng = GameEngine()
        _orig_print2 = _rdr3.console.print
        _rdr3.console.print = lambda *a, **k: None
        try:
            _deng.bootstrap()
            _dp = Player(name="Guard", base_class="warrior")
            _dstate = new_game_state(_dp, SAVES_DIR, _d_slot)
            _deng.state = _dstate
            _attach_engine_handles(_deng)

            # 28a. Trader cap: once a location is full, goods join an existing trader.
            for _i in range(WORLD_MAX_TRADERS_PER_LOCATION + 1):
                _dstate.world_db.record_world_action(f"find:thing_{_i}", "find", f"thing_{_i}", "village_start", 0)
                _wgf2.apply_expansion_result(_deng, {
                    "type": "world_expansion", "action_key": f"find:thing_{_i}",
                    "zone_id": "village_start", "response": _resp(_i), "skill": None,
                })
            _gen_traders = [n for n in _deng.npc_registry.all()
                            if n.is_ai_generated and n.zone_id == "village_start" and not n.route]
            assert len(_gen_traders) == WORLD_MAX_TRADERS_PER_LOCATION, [n.name for n in _gen_traders]
            _last = _dstate.world_db.get_world_expansion(f"find:thing_{WORLD_MAX_TRADERS_PER_LOCATION}")
            assert _last["bundle"]["npc"] is None and _last["bundle"]["npc_extend"]
            _host = _deng.npc_registry.get(_last["bundle"]["npc_extend"]["template_id"])
            _n = WORLD_MAX_TRADERS_PER_LOCATION
            assert f"gen_thing_{_n}" in _host.trades.buys
            assert any(o.item_id == f"gen_thing_{_n}" for o in _host.trades.sells)
            # Road traders have their own cap.
            _dstate.world_db.record_world_action("find:road_thing", "find", "road_thing", "camp_rest", 0)
            _wgf2.apply_expansion_result(_deng, {
                "type": "world_expansion", "action_key": "find:road_thing", "zone_id": "camp_rest",
                "response": _resp(99, "road"), "skill": None,
            })
            assert _deng.npc_registry.get("gen_npc_trader_99").route
            ok(f"Max {WORLD_MAX_TRADERS_PER_LOCATION} generated traders per location; extras extend stock")

            # 28b. Merged stock survives a reload, without duplicating offers.
            _attach_engine_handles(_deng)
            _host = _deng.npc_registry.get(_last["bundle"]["npc_extend"]["template_id"])
            assert _host.trades.buys.count(f"gen_thing_{_n}") == 1
            assert [o.item_id for o in _host.trades.sells].count(f"gen_thing_{_n}") == 1
            ok("Merged trader stock is restored once on reload")

            # 28c. Expansion cap: new actions past the cap are logged, not queued.
            class _Gen:
                def generate_world_expansion(self, **kw): return None
            _dbg = BackgroundGenerator(_Gen())
            _deng.ai_service = AIService(content_generator=_Gen(), background_generator=_dbg)
            _saved_cap = _wgf2.WORLD_MAX_EXPANSIONS
            _wgf2.WORLD_MAX_EXPANSIONS = _dstate.world_db.count_world_expansions()
            try:
                _wgf2.on_world_action(_deng, "carve", "statue", "")
            finally:
                _wgf2.WORLD_MAX_EXPANSIONS = _saved_cap
            assert _dstate.world_db.get_world_action("carve:statue")["status"] == "capped"
            assert _dbg._task_queue.empty()
            ok("Past WORLD_MAX_EXPANSIONS new actions are 'capped' and never queued")

            # 28d. Retry on load: offline actions are re-queued once AI is back.
            _deng.ai_service = AIService()
            _wgf2.on_world_action(_deng, "fish", "river", "")
            assert _dstate.world_db.get_world_action("fish:river")["status"] == "no_ai"
            _deng.ai_service = AIService(content_generator=_Gen(), background_generator=_dbg)
            _attach_engine_handles(_deng)
            _queued = [_dbg._task_queue.get_nowait()["action_key"] for _ in range(_dbg._task_queue.qsize())]
            assert "fish:river" in _queued, _queued
            assert "carve:statue" not in _queued, "Capped actions stay capped"
            assert _dstate.world_db.get_world_action("fish:river")["status"] == "pending"
            ok("Pending / no_ai actions are re-queued on load when the AI is available")

            # 28e. Admin World Growth log.
            _log = "\n".join(_wgf2.world_growth_report(_deng))
            assert "find thing 0" in _log and "trader  Trader 0 @ village_start" in _log
            assert "joined gen_npc_trader_" in _log and "fish river  ·  pending" in _log
            ok("Admin 'World Growth log' lists actions, statuses and what each added")
        finally:
            _rdr3.console.print = _orig_print2
    except Exception as e:
        fail("World growth guard rails broken", e)
        traceback.print_exc()
    finally:
        if _dstate is not None:
            close_game(_dstate)
        for _ext in (".json", ".db"):
            _pp = SAVES_DIR / f"{_d_slot}{_ext}"
            if _pp.exists():
                try: _pp.unlink()
                except OSError: pass

    # ─────────────────────────────────────────────────────────────────────────
    section("29. Ollama startup checks (server, models, preload)")
    try:
        import time as _time
        from unittest import mock as _mock
        import ai.ollama_client as _oc
        from core.game_engine import GameEngine
        from ui import renderer as _rdr4

        assert _oc.model_in("mistral-nemo", {"mistral-nemo:latest"})
        assert _oc.model_in("gemma3:1b", {"gemma3:1b"})
        assert not _oc.model_in("gemma3:1b", {"gemma3:4b"})

        import core.bootstrap as _bs

        def _boot(available: bool, models: set[str], accept_download: bool = False):
            said, warmed, pulled = [], [], []

            def _pull(self, model=None, on_progress=None):
                pulled.append(model)
                if on_progress:
                    on_progress("pulling", 5, 10); on_progress("pulling", 10, 10)
                models.add(model)
                return True

            with _mock.patch.object(_oc.OllamaClient, "is_available", lambda self: available), \
                 _mock.patch.object(_oc.OllamaClient, "installed_models", lambda self: set(models)), \
                 _mock.patch.object(_oc.OllamaClient, "pull", _pull), \
                 _mock.patch.object(_bs, "_confirm_download", lambda missing: accept_download), \
                 _mock.patch.object(_oc.OllamaClient, "warm_up",
                                    lambda self: warmed.append(self.model) or True), \
                 _mock.patch.object(_rdr4.console, "print", lambda *a, **k: said.append(" ".join(map(str, a)))), \
                 _mock.patch.object(_rdr4, "print_success", lambda m: said.append(m)):
                _e = GameEngine(); _e.bootstrap()
                for _ in range(20):
                    if len(warmed) >= 2 or not _e.ai_service.is_available:
                        break
                    _time.sleep(0.05)
            _boot.pulled = pulled
            return _e, "\n".join(said), sorted(warmed)

        _e, _said, _w = _boot(False, set())
        assert not _e.ai_service.is_available and "ollama serve" in _said and "start.ps1" in _said
        assert "Nothing is answering" in _e.ai_offline_reason  # kept for the admin panel
        _e, _said, _w = _boot(True, {"gemma3:4b"})
        assert not _e.ai_service.is_available and "ollama pull mistral-nemo" in _said
        assert "ollama pull mistral-nemo" in _e.ai_offline_reason and "gemma3:4b" in _e.ai_offline_reason
        _e, _said, _w = _boot(True, {"mistral-nemo:latest"})
        assert _e.ai_service.is_available and "ollama pull gemma3:4b" in _said and _w == ["mistral-nemo"]
        _e, _said, _w = _boot(True, {"mistral-nemo:latest", "gemma3:4b"})
        assert _e.ai_service.is_available and _w == ["gemma3:4b", "mistral-nemo"]
        assert _e.ai_offline_reason == ""
        # Pre-check: missing models are named, other installed models suggested,
        # and accepting the offer downloads them so the AI comes online.
        _e, _said, _w = _boot(True, {"qwen3:32b"})
        assert not _e.ai_service.is_available and _boot.pulled == []
        assert "mistral-nemo, gemma3:4b" in _said and "qwen3:32b" in _said and "OLLAMA_MODEL" in _said
        _e, _said, _w = _boot(True, {"qwen3:32b"}, accept_download=True)
        assert _boot.pulled == ["mistral-nemo", "gemma3:4b"] and _e.ai_service.is_available
        assert "Downloaded mistral-nemo" in _said and _e.ai_offline_reason == ""
        # Thinking models (qwen3, deepseek-r1) are asked not to think.
        _c = _oc.OllamaClient(model="qwen3:32b")
        with _mock.patch.object(_c, "_get_client", lambda timeout=None: type("C", (), {
                "show": lambda self, m: {"capabilities": ["completion", "thinking"]}})()):
            assert _c._think_kwargs() == {"think": False}
        _c = _oc.OllamaClient(model="mistral-nemo")
        with _mock.patch.object(_c, "_get_client", lambda timeout=None: type("C", (), {
                "show": lambda self, m: {"capabilities": ["completion"]}})()):
            assert _c._think_kwargs() == {}
        ok("Startup says exactly what's wrong (server / model), falls back to primary, preloads models; "
           "the reason is kept for the admin panel; missing models can be downloaded at startup; "
           "thinking models are asked not to think")
    except Exception as e:
        fail("Ollama startup checks broken", e)
        traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    section("30. Bot adventurers (generation, rules brain, presence, talk, trade)")
    _b_slot = "_bots_test_"
    _bstate = None
    try:
        import random as _rnd
        from unittest import mock as _mock2
        from config import BOT_COUNT
        from core import bot_flow as _bf
        from core.game_engine import GameEngine
        from core.input_handler import get_current_options as _gco
        from core.menu_flow import _attach_engine_handles
        from persistence.save_manager import load_game as _lg2, save_game as _sg2
        from systems import bot_brain as _bb, bot_trade as _bt
        from systems.bot_system import BotAgent, fallback_line
        from ui import renderer as _rdr5

        # 30a. A save gets BOT_COUNT bots, persisted; reload restores them exactly.
        _beng = GameEngine()
        with _mock2.patch.object(_rdr5.console, "print", lambda *a, **k: None):
            _beng.bootstrap()
        _bp = Player(name="BotTester", base_class="warrior")
        _bstate = new_game_state(_bp, SAVES_DIR, _b_slot)
        _beng.state = _bstate
        _attach_engine_handles(_beng)
        _bots = _beng._bot_manager.all()
        assert len(_bots) == BOT_COUNT and len(_bstate.world_db.load_bot_instances()) == BOT_COUNT
        assert {"Kael", "Dova", "Ryn"} <= {b.name for b in _bots}, "Authored bots included"
        _npc_first = {n.name.split()[0] for n in npc_reg.all()}
        assert not ({b.name for b in _bots if b.is_generated} & _npc_first), "No NPC name clashes"
        assert BotAgent.model_validate({"bot_id": "x", "name": "X", "inventory": ["a", "a"]}).inventory == {"a": 2}
        ok(f"New save gets {BOT_COUNT} bots (authored + generated), stored in world_db")

        # 30b. The brain uses the real world: fights, levels, loot, trade, craft.
        from collections import Counter as _C
        _kinds = _C()
        with _mock2.patch.object(_rdr5.console, "print", lambda *a, **k: None):
            for _ in range(120):
                _bp.turn_count += 1
                for ev in _bf.tick_bots(_beng):
                    _kinds[ev.kind] += 1
        for _k in ("move", "fight", "gather", "trade", "craft"):
            assert _kinds[_k] > 0, f"No '{_k}' events in 120 turns: {_kinds}"
        assert any(b.level > 1 or b.xp > 0 for b in _beng._bot_manager.all())
        assert all(b.current_zone_id in _bb.BOT_MAP for b in _beng._bot_manager.all())
        assert all(0 < b.hp <= b.max_hp for b in _beng._bot_manager.all())
        ok(f"Rules brain drives real fights / loot / trading / crafting ({dict(_kinds)})")

        # 30c. Brain rules: retreat when hurt, level gates, no NPC-trader money loop.
        _t = BotAgent(bot_id="t", name="T", archetype="fighter", current_zone_id="dungeon_floor1")
        _t.hp = 3
        _w = _bf.bot_world(_beng)
        _ev = _bb.tick_bot(_t, _w, _rnd.Random(1))
        assert _t.current_zone_id == "village_start" and _ev and _ev[0].kind == "move"
        _t.hp = _t.max_hp
        assert _bb.next_step(_t, "dungeon_floor3") is None, "Level 1 can't path to Floor 3"
        _t.level = 5
        assert _bb.next_step(_t, "dungeon_floor3") == "dungeon_floor1"
        _tr = BotAgent(bot_id="tr", name="Tr", archetype="trader", current_zone_id="village_start", gold=5000)
        _tr.add_item("firewood", 3)
        _g0 = _tr.gold
        for _i in range(30):
            _bb.tick_bot(_tr, _w, _rnd.Random(_i))
            _tr.current_zone_id = "village_start"
        assert _tr.gold >= _g0, "Trader bots must not lose money trading with NPC traders"
        ok("Hurt bots retreat, floors are level-gated, trader bots don't bleed gold")

        # 30d. Presence: Talk-to options at the zone root; [W] list.
        _here = [b for b in _beng._bot_manager.all()][:2]
        for b in _here:
            b.current_zone_id = "village_start"
        _bstate.current_scene_id = "village_start"; _bstate.current_node_id = "root"
        _opts = [o for o in _gco(_beng) if o.option_id.startswith("__bot__")]
        assert _opts and all(o.triggers[0].startswith("talk_bot:") for o in _opts)
        assert len(_opts) <= 4 and "Lv " in _opts[0].label
        _wl = _bf.whos_around_lines(_beng)
        assert len(_wl) == BOT_COUNT and "HERE" in _wl[0]
        from core.input_handler import _build_extras
        assert any(e.startswith("[W]") for e in _build_extras(_beng))
        ok("Bots in your zone get 'Talk to …' options; [W] lists everyone, here first")

        # 30e. Trading with a bot moves real items and real gold both ways.
        _seller = _here[0]
        _seller.inventory = {"herb_bundle": 3, "health_potion": 1}
        _seller.gold = 1000
        _offers = {o.item.item_id: o for o in _bt.bot_sells(_seller, item_reg)}
        assert "herb_bundle" in _offers and "health_potion" not in _offers, "Keeps its own potion"
        _bp.gold = 10_000
        _price = _offers["herb_bundle"].price
        assert _bt.buy_from_bot(_bp, _seller, "herb_bundle", item_reg)[0]
        assert _bp.gold == 10_000 - _price and _seller.gold == 1000 + _price
        assert _seller.inventory["herb_bundle"] == 2 and _bp.has_item("herb_bundle")
        _bp.add_item("forest_herb", 5)
        _ok, _msg, _earned = _bt.sell_to_bot(_bp, _seller, "forest_herb", item_reg, 5)
        assert _ok and _seller.inventory["forest_herb"] == 5 and _earned == 5 * _bt.bot_buy_price(item_reg.get("forest_herb"))
        _seller.gold = 0
        assert _bt.player_sellables(_bp, _seller, item_reg) == [], "A broke bot can't buy"
        assert not _bt.bot_buys(_seller, item_reg.get("torven_hammer")), "Key items never traded"
        ok("Buying from / selling to bots transfers items and gold; bots respect their purse")

        # 30f. Talk: AI line when online, grounded fallback otherwise.
        _seller.remember("Defeated a goblin patrol on Floor 1 (turn 3)")
        assert "defeated a goblin patrol" in fallback_line(_seller)
        from ai.ai_service import AIService
        class _Talky:
            def generate_bot_line(self, profile, player_name):
                assert profile["recent"] and profile["name"] == _seller.name
                return "Watch the wolves on Floor 1."
        assert AIService(content_generator=_Talky()).generate_bot_line(
            {"name": _seller.name, "recent": ["x"]}, "P") == "Watch the wolves on Floor 1."
        assert AIService().generate_bot_line({"name": "x"}, "P") is None, "Offline → fallback"
        _beng.ai_service = AIService(content_generator=_Talky())
        import questionary as _q
        _said = []
        with _mock2.patch.object(_q, "select", lambda *a, **k: type("A", (), {"ask": lambda s: "Farewell."})()), \
             _mock2.patch.object(_rdr5, "print_npc_dialogue", lambda n, d, t: _said.append(t)), \
             _mock2.patch.object(_rdr5, "clear", lambda: None), \
             _mock2.patch.object(_rdr5, "print_title", lambda: None):
            _bf.talk_to_bot(_beng, _seller.bot_id)
        assert _said == ["Watch the wolves on Floor 1."], _said
        ok("Talking to a bot uses the AI line (grounded in its memory) or a fallback")

        # 30g. Bots survive save + load unchanged (they used to reset every load).
        _snap = {b.bot_id: (b.level, b.current_zone_id, b.gold, dict(b.inventory)) for b in _beng._bot_manager.all()}
        _bf._persist(_beng, _beng._bot_manager.all(), [])
        _sg2(_bstate, _b_slot, SAVES_DIR)
        close_game(_bstate); _bstate = None
        _bstate = _lg2(_b_slot, SAVES_DIR)
        _beng.state = _bstate
        _attach_engine_handles(_beng)
        _snap2 = {b.bot_id: (b.level, b.current_zone_id, b.gold, dict(b.inventory)) for b in _beng._bot_manager.all()}
        assert _snap == _snap2
        ok("Bots are restored exactly on load (per save)")

        # 30h. Saves from before the bot update stored 3 bare bots (no
        # archetype, all "warrior"): they get their identity back from the
        # templates and the cast is topped up to BOT_COUNT.
        import json as _json30
        from systems.bot_system import BotManager as _BM
        class _OldDB:
            def __init__(self):
                self.rows = {b["bot_id"]: {"definition": _json30.dumps(
                    {"bot_id": b["bot_id"], "name": b["name"], "current_zone_id": "village_start"}),
                    "current_zone_id": "village_start"} for b in _beng._bot_registry.all_templates()}
            def load_bot_instances(self): return list(self.rows.values())
            def upsert_bot_instance(self, bot_id, definition, current_zone_id, last_active_turn):
                self.rows[bot_id] = {"definition": _json30.dumps(definition), "current_zone_id": current_zone_id}
        _odb = _OldDB()
        _bm = _BM(); _bm.populate(_beng._bot_registry, _odb, BOT_COUNT, seed="old-save")
        _kael = _bm.get("wanderer_kael")
        assert _bm.active_count() == BOT_COUNT and len(_odb.rows) == BOT_COUNT
        assert (_kael.archetype, _kael.class_id) == ("explorer", "ranger")
        assert _bm.get("merchant_dova").archetype == "trader"
        _bm2 = _BM(); _bm2.populate(_beng._bot_registry, _odb, BOT_COUNT, seed="old-save")
        assert _bm2.active_count() == BOT_COUNT, "No duplicates on the next load"
        ok("Older saves: stored bots regain their archetype/class and are topped up to BOT_COUNT")

        # 30h. The legacy per-bot AI decision path is off by default.
        from core import bg_scheduler as _bgs
        _submitted = []
        class _BG:
            def submit_bot_decision(self, *a, **k): _submitted.append(a)
        _beng._bg_generator = _BG()
        _bgs._maybe_submit_bot_decisions(_beng, {"zone_id": "village_start", "turn": 1})
        _beng._bg_generator = None
        assert _submitted == [], "BOT_AI_DECISIONS is False: no model call per bot"
        ok("No per-bot AI calls by default (BOT_AI_DECISIONS = False)")
    except Exception as e:
        fail("Bot adventurers broken", e)
        traceback.print_exc()
    finally:
        if _bstate is not None:
            close_game(_bstate)
        for _ext in (".json", ".db"):
            _pp = SAVES_DIR / f"{_b_slot}{_ext}"
            if _pp.exists():
                try: _pp.unlink()
                except OSError: pass

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
