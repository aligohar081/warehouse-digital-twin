"""Dump the distribution_center grid with cells_jobs_need marked, and check overlaps."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import CellType
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
w = twin.warehouse
need = w.cells_jobs_need()
print("size", w.width, w.height)
abbrev = {}
for y in range(w.height):
    row = ""
    for x in range(w.width):
        t = w.cell_type(x, y)
        ch = t.value[0] if t else "?"
        if (x, y) in need:
            ch = "*" if not w.is_walkable(x, y) else "#"
        elif w.is_walkable(x, y):
            ch = ch.lower()
        row += ch
    print(f"{y:2d} {row}")
types = {}
for y in range(w.height):
    for x in range(w.width):
        t = w.cell_type(x, y)
        types.setdefault(t.value if t else None, 0)
        types[t.value if t else None] += 1
print(types)
for key, zone in w.zones.items():
    inter = [c for c in zone.cells if c in need]
    if inter:
        print("zone", key, zone.cell_type, "cells in need:", len(inter), "/", len(zone.cells), sorted(inter)[:12])
print("parking", sorted(w.zones["parking_area"].cells))
print("crossings", sorted(w.crossings))
print("need on crossing", sorted(c for c in need if c in w.crossings))
