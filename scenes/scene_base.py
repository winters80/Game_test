from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.state_manager import GameState


@dataclass
class SceneOption:
    option_id: str
    label: str
    leads_to: str                          # Next scene_id or "__stay__"
    leads_to_node: str = "root"            # Node within the next scene
    expected: bool = True                  # False = divergence signal
    triggers: list[str] = field(default_factory=list)   # "flag:X", "assign_class:X", "give_item:X"
    requires_min_stats: dict[str, int] = field(default_factory=dict)
    requires_items: list[str] = field(default_factory=list)
    requires_flags: list[str] = field(default_factory=list)
    locked: bool = False
    lock_reason: str = ""


class Scene:
    """Base class for all scenes. Subclasses can override for custom logic."""

    def __init__(self, scene_id: str, data: dict[str, Any]) -> None:
        self.scene_id = scene_id
        self.title: str = data.get("title", scene_id)
        self.nodes: dict[str, Any] = data.get("nodes", {})
        self.entrance_text: list[str] = data.get("entrance_text", [])

    def enter(self, state: "GameState") -> None:
        """Called when transitioning into this scene."""
        pass

    def exit(self, state: "GameState") -> None:
        """Called when leaving this scene."""
        pass

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self.nodes.get(node_id, self.nodes.get("root", {}))

    def get_options(self, state: "GameState", node_id: str = "root") -> list[SceneOption]:
        node = self.get_node(node_id)
        raw_options = node.get("options", [])
        options = []
        for raw in raw_options:
            opt = self._build_option(raw, state)
            options.append(opt)
        return options

    def _build_option(self, raw: dict[str, Any], state: "GameState") -> SceneOption:
        player = state.player
        min_stats = raw.get("requires", {}).get("min_stats", {}) if raw.get("requires") else {}
        required_items = raw.get("requires", {}).get("items", []) if raw.get("requires") else []
        required_flags = raw.get("requires", {}).get("flags", []) if raw.get("requires") else []

        locked = False
        lock_reason = ""

        # Check stat requirements
        for stat, value in min_stats.items():
            if getattr(player.stats, stat, 0) < value:
                locked = True
                lock_reason = f"Requires {stat} ≥ {value}"
                break

        # Check item requirements
        if not locked:
            for item_id in required_items:
                if not player.has_item(item_id):
                    locked = True
                    lock_reason = f"Missing required item"
                    break

        # Check flag requirements
        if not locked:
            for flag in required_flags:
                if not player.has_flag(flag):
                    locked = True
                    lock_reason = f"Condition not met"
                    break

        return SceneOption(
            option_id=raw.get("option_id", "unknown"),
            label=raw.get("label", "???"),
            leads_to=raw.get("leads_to", "__stay__"),
            leads_to_node=raw.get("leads_to_node", "root"),
            expected=raw.get("expected", True),
            triggers=raw.get("triggers", []),
            requires_min_stats=min_stats,
            requires_items=required_items,
            requires_flags=required_flags,
            locked=locked,
            lock_reason=lock_reason,
        )

    def process_triggers(self, triggers: list[str], state: "GameState") -> None:
        """Process option triggers: flags, item grants, class assignments, etc."""
        for trigger in triggers:
            if trigger.startswith("flag:"):
                flag_name = trigger[5:]
                state.player.set_flag(flag_name)
            elif trigger.startswith("give_item:"):
                item_id = trigger[10:]
                state.player.add_item(item_id)
                from core.event_bus import Event, bus
                bus.publish(Event("ITEM_FOUND", {"item_id": item_id}))
            elif trigger.startswith("give_skill:"):
                skill_id = trigger[11:]
                if skill_id not in state.player.skills:
                    state.player.skills.append(skill_id)
                    from core.event_bus import Event, bus
                    bus.publish(Event("SKILL_ACQUIRED", {"skill_id": skill_id}))
            elif trigger.startswith("give_gold:"):
                amount = int(trigger[10:])
                state.player.gold += amount
            elif trigger.startswith("scene_transition:"):
                # handled by engine
                pass
        state.mark_dirty()
