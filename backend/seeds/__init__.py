"""Twin seeds: what a floor holds when it boots or resets (multi-embodiment
spec §4.2). Each seed populates an empty twin through its public API."""
from __future__ import annotations

from typing import Any, Callable, Dict

from . import classic, distribution_center

#: Layout name -> its seed. DigitalTwin.seed_floor runs the twin's own.
SEEDS: Dict[str, Callable[[Any], None]] = {
    "classic": classic.seed,
    "distribution_center": distribution_center.seed,
}

__all__ = ["SEEDS", "classic", "distribution_center"]
