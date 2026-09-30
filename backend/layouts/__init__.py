"""Named floor layouts. Each layout module draws a Layout; Warehouse(layout=
name) adopts it. `classic` is today's floor and the default everywhere."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import classic
from .base import Layout, Zone, rect

LayoutBuilder = Callable[[Optional[int], Optional[int]], Layout]

LAYOUTS: Dict[str, LayoutBuilder] = {
    classic.NAME: classic.build,
}


def build_layout(name: str = "classic", width: Optional[int] = None, height: Optional[int] = None) -> Layout:
    """Draw the layout called `name` (optionally at a custom grid size)."""
    builder = LAYOUTS.get(name)
    if builder is None:
        raise ValueError(f"Unknown layout {name!r} (known: {sorted(LAYOUTS)})")
    return builder(width, height)


__all__ = ["LAYOUTS", "Layout", "Zone", "build_layout", "rect"]
