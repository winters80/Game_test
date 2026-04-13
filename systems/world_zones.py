"""
Zone adjacency helper — loads zones.json once and provides adjacency queries.
Call load_zones() once at startup from GameEngine._load_data().
"""
from __future__ import annotations

import json
from pathlib import Path

_ZONE_MAP: dict[str, list[str]] = {}


def load_zones(data_dir: Path) -> None:
    """Load zone connectivity from zones.json into module-level cache."""
    global _ZONE_MAP
    zones_path = data_dir / "world" / "zones.json"
    if not zones_path.exists():
        return
    zones = json.loads(zones_path.read_text(encoding="utf-8"))
    _ZONE_MAP = {z["zone_id"]: z.get("connected_zones", []) for z in zones}


def is_adjacent(from_zone: str, to_zone: str) -> bool:
    """Return True if to_zone is directly reachable from from_zone."""
    return to_zone in _ZONE_MAP.get(from_zone, [])


def get_connected(zone_id: str) -> list[str]:
    """Return list of zone_ids directly reachable from zone_id."""
    return list(_ZONE_MAP.get(zone_id, []))
