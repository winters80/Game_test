"""
The Ledger's law: crimes, heat, bounties and what the wardens do about them.

The Ledger is the System's own order. It doesn't care whether you are good
or evil, only whether the record is kept. Its wardens enforce everyday law
(theft, burglary, bribery, assault, smuggling) on everyone, players and bot
adventurers alike, and hunt anyone working to break the System (sedition).

Pure rules here (no prompts, no rendering); ``core/law_flow.py`` runs the
encounters.

- ``commit_crime(state, kind)`` (the ``crime:KIND`` trigger) rolls whether
  anyone saw it. Seen: heat, a bounty, and lower Ledger standing. Unseen:
  only a little suspicion. Sedition is always seen: the System reports it.
- Heat (0-100) cools by ``HEAT_DECAY_PER_HOUR``; a bounty stays until paid.
- ``enforcement_due(...)`` says whether a warden patrol stops the player in
  town, or Ledger hunters track them down elsewhere.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.state_manager import GameState
    from entities.player import Player

LEDGER = "the_ledger"


@dataclass(frozen=True)
class Crime:
    kind: str
    heat: int            # added when witnessed
    bounty: int          # copper added to the fine when witnessed
    standing: int        # Ledger standing lost when witnessed
    label: str


CRIMES: dict[str, Crime] = {c.kind: c for c in (
    Crime("trespass",  6,   300,  2, "trespass"),
    Crime("bribery",  10,  1000,  4, "bribing an officer"),
    Crime("theft",    15,  1500,  5, "theft"),
    Crime("smuggling", 15, 2000,  5, "smuggling"),
    Crime("burglary", 25,  4000,  8, "burglary"),
    Crime("assault",  25,  3000, 10, "assaulting a warden"),
    Crime("sedition", 30,  8000, 15, "sedition against the System"),
)}

# How likely a crime here is to be seen (the dungeon has no wardens).
WITNESS = {"verath_city": 0.75, "village_start": 0.6, "camp_rest": 0.3}
LAWFUL_ZONES = ("verath_city", "village_start")
HEAT_DECAY_PER_HOUR = 1.0        # 6 turns
SUSPICION = 2                    # heat from a crime nobody saw
PATROL_HEAT = 25                 # wardens stop you in town above this
HUNTER_HEAT = 60                 # hunters come for you anywhere above this
WANTED_STANDING = -40            # Ledger standing at which you're "Wanted" on sight

# Flags that mean the player is working to break the System. The System
# reports each to the Ledger the first time it is set.
SEDITION_FLAGS = ("breakers_assembled", "system_override_attempted",
                  "destroy_system_path_flag", "joined_system_breakers")

# Typed [?] actions (world_action:VERB:…) that are crimes.
CRIME_VERBS = {"steal": "theft", "pickpocket": "theft", "rob": "theft", "pilfer": "theft",
               "burgle": "burglary", "smuggle": "smuggling", "bribe": "bribery",
               "trespass": "trespass", "sneak": "trespass"}


@dataclass
class CrimeResult:
    kind: str
    witnessed: bool
    heat: float
    bounty: int


def stealth(player: "Player") -> float:
    """0-0.45: how often a crime goes unseen thanks to AGI and LCK."""
    s = player.stats
    return min(0.45, (s.AGI + s.LCK) / 60.0)


def heat_label(heat: float) -> str:
    if heat >= HUNTER_HEAT:
        return "Hunted"
    if heat >= PATROL_HEAT:
        return "Wanted"
    if heat >= 10:
        return "Watched"
    return "Clean" if heat < 1 else "Noticed"


def commit_crime(state: "GameState", kind: str, rng: random.Random | None = None,
                 zone: str | None = None, seen: bool | None = None) -> CrimeResult | None:
    """Apply a crime the player just committed. Unknown kinds are ignored.
    `seen=True` skips the witness roll (the victims are the Ledger itself)."""
    crime = CRIMES.get(kind)
    if crime is None:
        return None
    rng = rng or random
    p = state.player
    zone = zone or state.current_scene_id
    if seen is not None:
        pass
    elif kind == "sedition":
        seen = True                                   # the System reports it
    else:
        chance = WITNESS.get(zone, 0.0) * (1.0 - stealth(p))
        if p.has_flag("criminal_record"):
            chance = min(1.0, chance + 0.1)           # the Ledger knows your face
        seen = rng.random() < chance
    if seen:
        p.heat = min(100.0, p.heat + crime.heat)
        p.bounty += crime.bounty
        p.crimes[kind] = p.crimes.get(kind, 0) + 1
        if state.world_db is not None:
            state.world_db.update_faction_standing(LEDGER, -crime.standing)
    else:
        p.heat = min(100.0, p.heat + SUSPICION)
    _log(state, "crime", {"kind": kind, "seen": seen, "zone": zone})
    return CrimeResult(kind, seen, p.heat, p.bounty)


def cool_down(player: "Player", turns: int = 1) -> None:
    """Heat fades with time; the bounty doesn't."""
    if player.heat > 0:
        player.heat = max(0.0, player.heat - HEAT_DECAY_PER_HOUR * turns / 6.0)


def check_sedition(state: "GameState") -> list[CrimeResult]:
    """Report Breaker milestones the player has reached (once each)."""
    out = []
    p = state.player
    for flag in SEDITION_FLAGS:
        mark = f"_ledger_saw_{flag}"
        if p.has_flag(flag) and not p.has_flag(mark):
            p.set_flag(mark)
            res = commit_crime(state, "sedition")
            if res:
                out.append(res)
    return out


def ledger_standing(state: "GameState") -> float:
    if state.world_db is None:
        return 0.0
    try:
        return state.world_db.get_faction_standing(LEDGER)[0]
    except Exception:
        return 0.0


def fine_for(player: "Player", standing: float = 0.0) -> int:
    """What it costs (copper) to settle with the Ledger now. Its own
    deputies and wardens pay half."""
    fine = player.bounty + int(player.heat * 20)
    if standing >= 40:
        fine //= 2
    return max(0, fine)


def jail_turns(player: "Player") -> int:
    """Time in the Ledger's cells: an hour per 5 heat, at least 2 hours."""
    return max(12, int(player.heat / 5) * 6)


def settle(player: "Player") -> None:
    player.heat = 0.0
    player.bounty = 0


def enforcement_due(state: "GameState", rng: random.Random | None = None) -> str:
    """'patrol', 'hunters' or '' for this turn."""
    rng = rng or random
    p = state.player
    zone = state.current_scene_id
    wanted = ledger_standing(state) <= WANTED_STANDING
    heat = p.heat + (20 if wanted else 0)
    if zone in LAWFUL_ZONES and heat >= PATROL_HEAT and rng.random() < heat / 400.0:
        return "patrol"
    if heat >= HUNTER_HEAT and not zone.startswith("gate:") and rng.random() < heat / 900.0:
        return "hunters"
    return ""


def crime_for_action(verb: str) -> str:
    """The crime a typed [?] action is, or ''."""
    return CRIME_VERBS.get((verb or "").lower(), "")


def _log(state: "GameState", kind: str, payload: dict) -> None:
    db = state.world_db
    if db is None:
        return
    try:
        db.log_event(state.turn_number, kind, payload)
    except Exception:
        pass
