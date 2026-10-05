"""A stopgap seed for the distribution-centre floor, so the app can boot it.

It places the fleet, goods and crew that backend/test_shift_soak.py runs a
fault-free 40 sim-minute shift on, through the public API. Spec §4.2's full
seed (every active asset at a free cell of its home zone, 60 pallets, 80
totes, 40 SKUs, Ada) is plan 1c's, and replaces this.
"""
from __future__ import annotations

from typing import Any

#: (twin robot name, inventory asset, cell). Arms (CX10) take their station.
FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))
ARMS = (("CX10-210", "AST-000210"), ("CX10-211", "AST-000211"))


def seed(twin: Any) -> None:
    """Populate an empty distribution-centre twin."""
    for name, asset, cell in FLEET:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    for name, asset in ARMS:
        twin.add_robot(name=name, asset_id=asset)
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")
    for number in range(1, 11):
        twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")
