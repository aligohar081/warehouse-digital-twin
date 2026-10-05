"""I1 beyond the put-away test: estimate (no reserve) vs measured drain for other new-floor job types."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus, CONFIG
from backend.simulator import Simulator


def case(label, robot_kw, boxes, payload, setup=None):
    tmp = tempfile.mkdtemp()
    twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                       layout="distribution_center")
    robot = twin.add_robot(**robot_kw)
    made = [twin.add_box(**b) for b in boxes]
    if setup:
        setup(twin, robot, made)
    sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
    before = robot.battery
    p = dict(payload); p["robot_id"] = robot.id
    if made: p.setdefault("box_id", made[0].id)
    task = twin.tasks.create_task(p)
    ticks = 0
    while not task.is_terminal and ticks < 6000:
        sim.tick(); ticks += 1
    used = before - robot.battery
    est = (task.battery_estimate or 0) - CONFIG["BATTERY_RESERVE"]
    print(f"{label:38s} {task.status.value:9s} est={est:6.3f}% actual={used:6.3f}% ratio={est / used if used else float('nan'):.3f} ticks={ticks} {task.error or ''}")


AMR = dict(name="TR50-201", asset_id="AST-000201", position=(8, 15))
FK = dict(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
HAUL = dict(name="HH300-207", asset_id="AST-000207", position=(2, 8))
H1 = dict(name="H1-212", asset_id="AST-000212", position=(18, 13))
case("TOTE_TO_STATION AMR", AMR, [dict(name="T", kind="TOTE", sku="S1", quantity=20, weight=21.5, slot="TS-11-12-0")],
     {"type": "TOTE_TO_STATION"})
case("RETRIEVE_PALLET forklift lvl4 1100kg", FK, [dict(name="P", kind="PALLET", sku="S", quantity=10, weight=1100.0,
                                                       slot="PR-12-05-4")], {"type": "RETRIEVE_PALLET"})
case("RETRIEVE_PALLET forklift lvl0 500kg", FK, [dict(name="P", kind="PALLET", sku="S", quantity=10, weight=500.0,
                                                      slot="PR-10-05-0")], {"type": "RETRIEVE_PALLET"})
case("UNLOAD_TRUCK hauler 800kg", HAUL, [dict(name="P", kind="PALLET", sku="S", quantity=10, weight=800.0,
                                              position=(2, 9))], {"type": "UNLOAD_TRUCK"})
case("MOVE_ROBOT AMR to parking", AMR, [], {"type": "MOVE_ROBOT", "destination": "parking_area"})
