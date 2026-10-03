"""A fault-free shift doesn't wedge (multi-embodiment spec §11, §16).

The populated distribution-centre floor — the fleet and crew the integration
soak probe uses, built through the public API — runs a shift with seed 42 and
every injected-fault risk at 0.0 for SOAK_TICKS ticks at pace SOAK_PACE (40
sim-minutes). Customer orders must keep completing to the end, and at the end
no robot is idle holding a box, none is in ERROR, and no task is stuck in
VALIDATING. The run is deterministic: the shift engine has its own seeded RNG
and a zero risk never draws a random number.
"""
import pytest

from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, RobotStatus, SimulationStatus, TaskStatus
from backend.simulator import Simulator

SOAK_PACE = 2.0
SOAK_TICKS = 16000                    # 16000 × TICK_DT 0.15 s = 40 sim-minutes
#: Customer orders DONE by the end. The green run finished 18 (13 by 30
#: sim-minutes) where the wedged floor finished 1; two thirds of that leaves
#: room for small, legitimate changes in timing.
MIN_CUSTOMER_DONE = 12

FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))


@pytest.fixture
def fault_free(monkeypatch):
    for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
        monkeypatch.setitem(CONFIG, risk, 0.0)


def populated_floor(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"), persist_logs=False,
                       demo=True, demo_tasks=False, layout="distribution_center")
    for name, asset, cell in FLEET:
        twin.add_robot(name=name, asset_id=asset, position=cell)
    twin.add_robot(name="CX10-210", asset_id="AST-000210")
    twin.add_robot(name="CX10-211", asset_id="AST-000211")
    for number in range(6):
        twin.add_box(name=f"TOTE-{number}", kind="TOTE", sku=f"SKU-00{number + 1}", quantity=20, weight=21.5,
                     slot=f"TS-{8 + number:02d}-12-0")
    for number in range(3):
        twin.add_box(name=f"PAL-{number}", kind="PALLET", sku=f"SKU-00{number + 1}", quantity=40, weight=500.0,
                     slot=f"PR-{10 + number:02d}-05-0")
    for number in range(1, 11):
        twin.add_operator(name=f"W{number:02d}", worker_id=f"E-{10000 + number}")
    return twin


def customer_done(twin):
    return twin.shift.orders.counts()["CUSTOMER"]["DONE"]


def test_a_fault_free_shift_keeps_completing_customer_orders(tmp_path, fault_free):
    twin = populated_floor(tmp_path)
    sim = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    twin.shift.configure(pace=SOAK_PACE)
    twin.shift.start()
    done_at_two_thirds = None
    for tick in range(1, SOAK_TICKS + 1):
        sim.tick()
        if tick == SOAK_TICKS * 2 // 3:
            done_at_two_thirds = customer_done(twin)
    done = customer_done(twin)
    assert twin.simulation_time >= 40 * 60
    assert done > done_at_two_thirds, f"no customer order completed in the last third ({done} done)"
    assert done >= MIN_CUSTOMER_DONE, twin.shift.orders.counts()
    idle_carrying = [r.name for r in twin.robots.values() if r.current_task is None and r.carrying_box]
    assert not idle_carrying
    assert not [(r.name, r.last_error) for r in twin.robots.values() if r.status is RobotStatus.ERROR]
    assert not [t.id for t in twin.tasks.tasks.values() if t.status is TaskStatus.VALIDATING]
