"""
Automated test runs - 5 character scenarios through character creation,
combat, level-up, alignment, and save/load.

Run with: python test_runs.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

results: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    msg = f"  {tag} {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((label, condition, detail))
    return condition


def section(title: str) -> None:
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


# ── Bootstrap engine once ─────────────────────────────────────────────────────

section("Bootstrap")
from core.game_engine import GameEngine
from entities.player import Player
from core.state_manager import GameState
from persistence.save_manager import new_game_state, save_game, load_game
from config import SAVES_DIR, DATA_DIR, feature
from systems import level_system, combat_system

engine = GameEngine()
engine.bootstrap()

check("Engine bootstrap", engine.species_registry is not None, "SpeciesRegistry loaded")
check("Species count", len(engine.species_registry._species) == 5, f"{len(engine.species_registry._species)} species")
check("Backgrounds count", len(engine.backgrounds) == 8, f"{len(engine.backgrounds)} backgrounds")
check("feature species_system", feature("species_system"))
check("feature alignment_system", feature("alignment_system"))
check("feature stat_gating", feature("stat_gating"))


# ── Utility ───────────────────────────────────────────────────────────────────

def make_player(name: str) -> Player:
    return Player(name=name)


def apply_species_bg(player: Player, species_id: str, bg_id: str) -> None:
    player.set_flag(f"_pending_species:{species_id}")
    player.set_flag(f"_pending_background:{bg_id}")
    engine._apply_pending_species(player)
    engine._apply_pending_background(player)


def run_options(state: GameState, scene_id: str, node_id: str) -> list:
    scene = engine.scene_registry.get(scene_id)
    state.current_scene_id = scene_id
    state.current_node_id = node_id
    return scene.get_options(state, node_id) if scene else []


def pick(options, option_id: str):
    for opt in options:
        if opt.option_id == option_id:
            return opt
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  RUN 1 — Standard human scholar, expected path, class awakening
# ══════════════════════════════════════════════════════════════════════════════
section("Run 1 — Human Scholar (standard path)")
try:
    p = make_player("Aldric")
    apply_species_bg(p, "human", "scholars_apprentice")

    check("R1 species applied", p.species_id == "human")
    check("R1 background applied", p.background_id == "scholars_apprentice")
    check("R1 INT boosted (5+3=8)", p.stats.INT == 8, f"INT={p.stats.INT}")
    check("R1 WIS boosted (5+2=7)", p.stats.WIS == 7, f"WIS={p.stats.WIS}")
    check("R1 alignment positive", p.alignment == 10.0, f"align={p.alignment}")
    check("R1 mana_potion in inventory", p.has_item("mana_potion"))
    check("R1 academic_training flag", p.has_flag("academic_training"))

    # Assign warrior class
    state = new_game_state(p, SAVES_DIR, "test_aldric")
    ok, msg = engine._assign_class.__func__(engine, "warrior") if False else (None, None)
    from systems.class_system import assign_base_class
    ok, msg = assign_base_class(p, "warrior", engine.class_registry, engine.skill_registry)
    check("R1 class assigned", ok, msg)
    check("R1 base_class set", p.base_class == "warrior", f"class={p.base_class}")

    # Level up — give enough XP for level 2
    leveled = level_system.add_experience(p, 100)
    check("R1 leveled up", len(leveled) > 0, f"new levels: {leveled}")
    check("R1 stat points granted", p.stat_points >= 3)

    # Spend a stat point
    before_str = p.stats.STR
    level_system.spend_stat_point(p, "STR")
    check("R1 STR increased", p.stats.STR == before_str + 1)

    # Perception derived stat
    check("R1 perception derived", p.perception == p.stats.AGI // 2 + p.stats.WIS // 3 + p.perception_bonus,
          f"perception={p.perception}")

    # Save / load round-trip
    save_path = save_game(state, "test_aldric", SAVES_DIR)
    loaded = load_game("test_aldric", SAVES_DIR)
    check("R1 save/load round-trip", loaded is not None)
    check("R1 species survives save", loaded.player.species_id == "human")
    check("R1 alignment survives save", loaded.player.alignment == 10.0)

except Exception:
    print(f"  {FAIL} Run 1 crashed:\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════════════════════
#  RUN 2 — Void-Touched street thief, divergence path + alignment shift
# ══════════════════════════════════════════════════════════════════════════════
section("Run 2 — Void-Touched Street Thief (divergence path)")
try:
    p = make_player("Sable")
    apply_species_bg(p, "void_touched", "street_thief")

    check("R2 species applied", p.species_id == "void_touched")
    # void_touched: LCK+4, INT+2, AGI+1 | street_thief: LCK+1, INT+1, AGI+3
    check("R2 LCK boosted (5+4+1=10)", p.stats.LCK == 10, f"LCK={p.stats.LCK}")
    check("R2 INT (5+2+1=8)", p.stats.INT == 8, f"INT={p.stats.INT}")
    check("R2 perception_bonus +4", p.perception_bonus == 4)
    check("R2 alignment negative", p.alignment < 0, f"align={p.alignment}")
    check("R2 street_knowledge flag", p.has_flag("street_knowledge"))

    # Alignment shift via trigger string
    from systems.alignment_system import apply_alignment_shift
    old_align = p.alignment
    apply_alignment_shift(p, -20.0, reason="test:dark_choice")
    check("R2 alignment shift applied", p.alignment == max(-100.0, old_align - 20.0),
          f"align={p.alignment:.1f}")
    check("R2 alignment clamped >= -100", p.alignment >= -100.0)

    # Check alignment label
    from systems.alignment_system import get_alignment_label, alignment_band
    label = get_alignment_label(p.alignment)
    band = alignment_band(p.alignment)
    check("R2 alignment label non-empty", bool(label), f"label='{label}'")
    check("R2 alignment band evil", band == "evil", f"band={band}")

    # Divergence scoring
    from systems.progression_tracker import compute_divergence_score
    p.record_choice("unexpected:character_creation:choose_void_touched")
    p.set_flag("refused_system")
    result_div = compute_divergence_score(p, engine.class_registry, engine.item_registry)
    check("R2 divergence score > 0", result_div.score > 0, f"score={result_div.score}")

    # Assign rogue class
    ok, msg = assign_base_class(p, "rogue", engine.class_registry, engine.skill_registry)
    check("R2 rogue class assigned", ok)

    # Stat gate: HIDDEN gate for Perception
    from systems.stat_gate import StatGate, evaluate_gate, build_gate_from_dict
    from entities.enums import GateType
    gate_data = {"stat": "PERCEPTION", "threshold": 8, "gate_type": "hidden",
                 "hidden_hint_threshold": 3, "hint_text": "Something moves in the shadows..."}
    gate = build_gate_from_dict(gate_data)
    result = evaluate_gate(p, gate)
    check("R2 HIDDEN gate evaluated", result is not None)
    # void_touched has perception_bonus +4, base perception = AGI//2 + WIS//3 + 4
    # AGI=6, WIS=7, so perception = 3 + 2 + 4 = 9 >= 8, should pass
    check("R2 HIDDEN gate passed (perception >= 8)", result.passed, f"perception={p.perception}")

except Exception:
    print(f"  {FAIL} Run 2 crashed:\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════════════════════
#  RUN 3 — Beast-Kin Soldier, combat test
# ══════════════════════════════════════════════════════════════════════════════
section("Run 3 — Beast-Kin Soldier (combat flow)")
try:
    p = make_player("Krag")
    apply_species_bg(p, "beast_kin", "soldier")

    check("R3 species applied", p.species_id == "beast_kin")
    check("R3 STR (5+2+2=9)", p.stats.STR == 9, f"STR={p.stats.STR}")
    check("R3 END (5+1+2=8)", p.stats.END == 8, f"END={p.stats.END}")
    check("R3 iron_sword in inventory", p.has_item("iron_sword"))

    assign_base_class(p, "warrior", engine.class_registry, engine.skill_registry)

    # Combat round (encounter IDs are groups, not individual enemy templates)
    enemies = combat_system.spawn_encounter("goblin_patrol")
    check("R3 enemies spawned", len(enemies) > 0, f"{len(enemies)} enemies")

    enemy = enemies[0]
    original_hp = enemy.current_hp
    dmg, is_crit = combat_system.player_attack(p, enemy)
    check("R3 player deals damage > 0", dmg > 0, f"dmg={dmg} crit={is_crit}")
    check("R3 enemy HP reduced", enemy.current_hp < original_hp)

    # Enemy attacks back
    player_hp_before = p.current_hp
    enemy_dmg = combat_system.enemy_attack(enemy, p)
    check("R3 enemy attack resolves", enemy_dmg >= 0, f"enemy_dmg={enemy_dmg}")

    # Flee attempt
    can_flee = combat_system.try_flee(p, enemy)
    check("R3 flee attempt resolves (bool)", isinstance(can_flee, bool))

    # Kill enemy, get XP
    enemy.current_hp = 0
    alive = [e for e in enemies if e.is_alive]
    check("R3 enemy marked dead", not enemy.is_alive)

    total_xp = sum(e.xp_reward for e in enemies)
    leveled = level_system.add_experience(p, total_xp)
    check("R3 XP applied", p.experience > 0 or p.level > 1, f"xp={p.experience} lvl={p.level}")

except Exception:
    print(f"  {FAIL} Run 3 crashed:\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════════════════════
#  RUN 4 — Aetherian Elf Scholar, evolution trigger test
# ══════════════════════════════════════════════════════════════════════════════
section("Run 4 — Aetherian Elf Scholar (evolution path)")
try:
    p = make_player("Lyriael")
    apply_species_bg(p, "aetherian_elf", "scholars_apprentice")

    check("R4 species applied", p.species_id == "aetherian_elf")
    check("R4 INT (5+3+3=11)", p.stats.INT == 11, f"INT={p.stats.INT}")
    check("R4 WIS (5+2+2=9)", p.stats.WIS == 9, f"WIS={p.stats.WIS}")
    check("R4 perception_bonus +3", p.perception_bonus == 3)

    # Check stage 1 evolution requires INT ≥ 20 — should NOT trigger yet
    species = engine.species_registry.get("aetherian_elf")
    from systems.species_system import check_evolution, trigger_evolution
    stage = check_evolution(p, species, engine.skill_registry)
    check("R4 no evolution yet (INT < 20)", stage is None, f"INT={p.stats.INT}")

    # Boost INT to 20 to trigger
    p.stats.INT = 20
    stage = check_evolution(p, species, engine.skill_registry)
    check("R4 evolution stage detected at INT=20", stage is not None)
    if stage:
        check("R4 stage name set", bool(stage.name), f"name='{stage.name}'")
        trigger_evolution(p, stage, species, engine.skill_registry)
        check("R4 evolution_stage advanced", p.evolution_stage == 1, f"evo={p.evolution_stage}")
        check("R4 passive skill granted", len(p.skills) > 0, f"skills={p.skills}")
        # No further evolution yet (stage 2 requires level 35)
        stage2 = check_evolution(p, species, engine.skill_registry)
        check("R4 no second evolution yet", stage2 is None)

    # Alignment gate: elven ancient trial requires positive alignment
    from systems.alignment_system import check_alignment_gate
    p.alignment = 30.0
    passed, reason = check_alignment_gate(p, 20.0, None)
    check("R4 alignment gate passes at +30", passed)
    passed2, reason2 = check_alignment_gate(p, 50.0, None)
    check("R4 alignment gate blocks at +30 (need 50)", not passed2, reason2[:40] if reason2 else "")

except Exception:
    print(f"  {FAIL} Run 4 crashed:\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════════════════════
#  RUN 5 — Stone Dwarf Noble, scene option gate evaluation
# ══════════════════════════════════════════════════════════════════════════════
section("Run 5 — Stone Dwarf Merchant's Ward (stat gates in scenes)")
try:
    p = make_player("Durgin")
    apply_species_bg(p, "stone_dwarf", "merchants_ward")

    check("R5 species applied", p.species_id == "stone_dwarf")
    check("R5 END (5+4+0=9)", p.stats.END == 9, f"END={p.stats.END}")
    check("R5 VIT (5+2+0=7)", p.stats.VIT == 7, f"VIT={p.stats.VIT}")
    check("R5 gold 5000 base + 7500 bg = 12500 copper", p.gold == 12500, f"gold={p.gold}")
    check("R5 alignment (0+5=5.0)", p.alignment == 5.0, f"align={p.alignment}")

    assign_base_class(p, "warrior", engine.class_registry, engine.skill_registry)
    state = new_game_state(p, SAVES_DIR, "test_durgin")

    # Test HARD stat gate via scene option build
    from scenes.scene_base import Scene
    from systems.stat_gate import build_gate_from_dict, evaluate_gate, GateResult
    from entities.enums import GateType

    # HARD gate — END ≥ 15 (dwarf has 9, should fail)
    raw_option = {
        "option_id": "deep_mine_shortcut",
        "label": "Take the deep mine shortcut",
        "leads_to": "dungeon_floor1",
        "requires": {
            "stat_gates": [
                {"stat": "END", "threshold": 15, "gate_type": "hard",
                 "fail_message": "You lack the endurance to survive the deep mines."}
            ]
        }
    }
    scene_data = {"nodes": {"root": {"options": [raw_option]}}}
    scene = Scene("test_scene", scene_data)
    options = scene.get_options(state, "root")
    check("R5 scene option built", len(options) == 1)
    opt = options[0]
    check("R5 HARD gate locks option (END 9 < 15)", opt.locked, f"locked={opt.locked}")
    check("R5 lock_reason populated", bool(opt.lock_reason), f"reason='{opt.lock_reason}'")

    # Boost END to 15, gate should open
    p.stats.END = 15
    options2 = scene.get_options(state, "root")
    opt2 = options2[0]
    check("R5 HARD gate unlocks at END=15", not opt2.locked)

    # SOFT gate — always visible, success probability varies
    raw_soft = {
        "option_id": "persuade_guard",
        "label": "Persuade the guard",
        "leads_to": "__stay__",
        "requires": {
            "stat_gates": [
                {"stat": "INT", "threshold": 10, "gate_type": "soft",
                 "soft_base_chance": 0.3, "soft_coefficient": 0.05}
            ]
        }
    }
    scene_data2 = {"nodes": {"root": {"options": [raw_soft]}}}
    scene2 = Scene("test_scene2", scene_data2)
    options3 = scene2.get_options(state, "root")
    check("R5 SOFT gate option visible", len(options3) == 1)
    check("R5 SOFT gate not locked", not options3[0].locked)

    # SOFT gate: roll_soft_gate works
    from systems.stat_gate import StatGate, evaluate_gate, roll_soft_gate
    gate = StatGate(stat="INT", threshold=10, gate_type=GateType.SOFT,
                    soft_base_chance=1.0, soft_coefficient=0.0)
    result = evaluate_gate(p, gate)
    check("R5 SOFT gate always passes with 100% chance", roll_soft_gate(result))

    gate_zero = StatGate(stat="INT", threshold=10, gate_type=GateType.SOFT,
                         soft_base_chance=0.0, soft_coefficient=0.0)
    result_zero = evaluate_gate(p, gate_zero)
    check("R5 SOFT gate always fails with 0% chance", not roll_soft_gate(result_zero))

    # Inertia: alignment nudges toward 0 over time
    from systems.alignment_system import apply_inertia
    p.alignment = 80.0
    apply_inertia(p)
    check("R5 inertia nudges positive alignment down", p.alignment < 80.0, f"align={p.alignment}")

    p.alignment = -60.0
    apply_inertia(p)
    check("R5 inertia nudges negative alignment up", p.alignment > -60.0, f"align={p.alignment}")

except Exception:
    print(f"  {FAIL} Run 5 crashed:\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════════════
section("Summary")
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print(f"\n  Total checks : {total}")
print(f"  {PASS} Passed    : {passed}")
if failed:
    print(f"  {FAIL} Failed    : {failed}")
    print("\n  Failed checks:")
    for label, ok, detail in results:
        if not ok:
            print(f"    • {label}  ({detail})")
else:
    print(f"\n  All checks passed.")

# Cleanup test saves — close any open DBs first
from persistence.save_manager import close_game as _close
for _state in [s for s in [locals().get("state"), locals().get("loaded")] if s is not None]:
    try:
        _close(_state)
    except Exception:
        pass

import time; time.sleep(0.2)
for name in ("test_aldric", "test_durgin"):
    for suffix in (".json", ".db", ".db-shm", ".db-wal"):
        p_file = SAVES_DIR / f"{name}{suffix}"
        if p_file.exists():
            try:
                p_file.unlink()
            except PermissionError:
                pass  # DB may still be held; leave it

sys.exit(0 if failed == 0 else 1)
