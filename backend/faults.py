"""Injected faults (multi-embodiment spec §10.6).

Each fault kind has a CONFIG risk (0.0 by default, settable through
policies.yaml like FALSE_SUCCESS_RISK): the chance the fault happens at each
opportunity. `FaultInjector.arm(kind)` also queues single occurrences on
demand for demos (plan 1c's POST /api/faults/<kind> calls it): an armed fault
fires at the next opportunity whatever the risk.

The code where a fault can happen calls `twin.faults.roll(kind)`; what the
fault then does lives there (the SCAN step, the conveyor, the sorter, ...).
"""
from __future__ import annotations

import random
from typing import Dict, Optional

from .models import CONFIG

#: Fault kind -> the CONFIG risk that sets how often it happens.
FAULT_RISKS: Dict[str, str] = {
    "scan_miscount": "SCAN_MISCOUNT_RISK",
    "wrong_level": "WRONG_LEVEL_RISK",
    "grasp_fail": "GRASP_FAIL_RISK",
    "conveyor_jam": "CONVEYOR_JAM_RISK",
    "handoff_loss": "HANDOFF_LOSS_RISK",
    "mis_sort": "MIS_SORT_RISK",
    "misdeclared_weight": "MISDECLARED_WEIGHT_RISK",
}


def _kind(kind: str) -> str:
    key = str(kind or "").strip().lower().replace("-", "_")
    if key not in FAULT_RISKS:
        raise ValueError(f"Unknown fault kind {kind!r} (known: {sorted(FAULT_RISKS)})")
    return key


class FaultInjector:
    """The twin's fault switchboard: armed one-shots plus the CONFIG risks."""

    def __init__(self) -> None:
        self._armed: Dict[str, int] = {}

    def arm(self, kind: str, count: int = 1) -> int:
        """Queue `count` occurrences of `kind` for its next opportunities.
        Returns how many are now armed."""
        key = _kind(kind)
        if int(count) < 1:
            raise ValueError("count must be at least 1")
        self._armed[key] = self._armed.get(key, 0) + int(count)
        return self._armed[key]

    def armed(self) -> Dict[str, int]:
        return {key: count for key, count in self._armed.items() if count}

    def roll(self, kind: str, rng: Optional[random.Random] = None) -> bool:
        """Should this opportunity fault? An armed occurrence is used up
        first; otherwise the CONFIG risk decides. A zero risk never draws a
        random number, so a fault-free run leaves every random stream alone."""
        key = _kind(kind)
        if self._armed.get(key):
            self._armed[key] -= 1
            return True
        risk = float(CONFIG.get(FAULT_RISKS[key], 0.0) or 0.0)
        if risk <= 0.0:
            return False
        return (rng or random).random() < risk

    def clear(self) -> None:
        self._armed.clear()
