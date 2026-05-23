from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import feature

if TYPE_CHECKING:
    from core.state_manager import GameState


@dataclass
class SceneOption:
    option_id: str
    label: str
    leads_to: str                          # Next scene_id or "__stay__"
    leads_to_node: str = "root"            # Node within the next scene
    expected: bool = True                  # False = divergence signal
    triggers: list[str] = field(default_factory=list)
    requires_min_stats: dict[str, int] = field(default_factory=dict)
    requires_items: list[str] = field(default_factory=list)
    requires_flags: list[str] = field(default_factory=list)
    requires_alignment_min: float | None = None
    requires_alignment_max: float | None = None
    stat_gates: list[dict] = field(default_factory=list)  # advanced gate dicts
    locked: bool = False
    lock_reason: str = ""
    hint_text: str = ""   # shown for near-miss HIDDEN gates
    should_hide: bool = False  # True = don't render this option at all
    narrative: str = ""   # feedback text shown after this option is chosen (AI options)


class Scene:
    """Base class for all scenes. Subclasses can override for custom logic."""

    def __init__(self, scene_id: str, data: dict[str, Any]) -> None:
        self.scene_id = scene_id
        self.title: str = data.get("title", scene_id)
        self.nodes: dict[str, Any] = data.get("nodes", {})
        self.entrance_text: list[str] = data.get("entrance_text", [])

    def enter(self, state: "GameState") -> None:
        pass

    def exit(self, state: "GameState") -> None:
        pass

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self.nodes.get(node_id, self.nodes.get("root", {}))

    def get_options(self, state: "GameState", node_id: str = "root") -> list[SceneOption]:
        node = self.get_node(node_id)
        raw_options = node.get("options", [])
        options = []
        for raw in raw_options:
            opt = self._build_option(raw, state)
            if not opt.should_hide:
                options.append(opt)
        return options

    def _build_option(self, raw: dict[str, Any], state: "GameState") -> SceneOption:
        """Thin delegate — real logic lives in scenes/option_logic.build_option()."""
        from scenes.option_logic import build_option
        return build_option(raw, state)

    def process_triggers(self, triggers: list[str], state: "GameState") -> None:
        """Thin delegate — real logic lives in scenes/option_logic.process_triggers()."""
        from scenes.option_logic import process_triggers as _pt
        _pt(triggers, state)

    # ── End of class — legacy bodies removed (see scenes/option_logic.py) ────

