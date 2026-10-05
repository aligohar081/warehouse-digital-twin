"""Replicates backend/test_shift_soak.py's floor; prints counts every 2000 ticks plus end-state
invariants, so runs under different PYTHONHASHSEED values can be compared."""
import sys, tempfile, collections, time
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.faults import FAULT_RISKS
from backend.models import CONFIG, RobotStatus, SimulationStatus, TaskStatus, BoxKind
from backend.simulator import Simulator
from backend.jobs import tote_is_home
for risk in ("COLLISION_RISK", "FALSE_SUCCESS_RISK", *FAULT_RISKS.values()):
    CONFIG[risk] = 0.0
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 16000
PACE = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
FLEET = (("PF1200-205", "AST-000205", (7, 4)), ("PF1200-206", "AST-000206", (7, 8)),
         ("HH300-207", "AST-000207", (2, 8)), ("TR50-201", "AST-000201", (8, 13)),
         ("TR50-101", "AST-000101", (8, 15)), ("PK30-203", "AST-000203", (20, 14)),
         ("IX2-208", "AST-000208", (20, 2)), ("IX2-209", "AST-000209", (21, 2)),
         ("SC1-204", "AST-000204", (7, 6)), ("H1-212", "AST-000212", (22, 7)))
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/logs", data_dir=tmp + "/data", persist_logs=False,
                   demo=True, demo_tasks=False, layout="distribution_center")
for name, asset, cell in FLEET:
    twin.add_robot(name=name, asset_id=asset, position=cell)
twin.add_robot(name="CX10-210", asset_id="AST-000210")
twin.add_robot(name="CX10-211", asset_id="AST-000211")
for n in range(6):
    twin.add_box(name=f"TOTE-{n}", kind="TOTE", sku=f"SKU-00{n + 1}", quantity=20, weight=21.5, slot=f"TS-{8 + n:02d}-12-0")
for n in range(3):
    twin.add_box(name=f"PAL-{n}", kind="PALLET", sku=f"SKU-00{n + 1}", quantity=40, weight=500.0, slot=f"PR-{10 + n:02d}-05-0")
for n in range(1, 11):
    twin.add_operator(name=f"W{n:02d}", worker_id=f"E-{10000 + n}")
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
twin.shift.configure(pace=PACE); twin.shift.start()
t0 = time.time()
trace = []
for tick in range(1, TICKS + 1):
    sim.tick()
    if tick % 2000 == 0:
        trace.append(twin.shift.orders.counts()["CUSTOMER"]["DONE"])
print("wall", round(time.time() - t0, 1), "s; CUSTOMER DONE every 2000 ticks:", trace)
print("counts", {k: dict(v) for k, v in twin.shift.orders.counts().items()})
by = collections.Counter((t.type.value, t.status.value) for t in twin.tasks.tasks.values())
print("MOVE_ROBOT", {k[1]: v for k, v in by.items() if k[0] == "MOVE_ROBOT"},
      "RETURN_TOTE", {k[1]: v for k, v in by.items() if k[0] == "RETURN_TOTE"})
fails = collections.Counter((t.type.value, (t.error or "")[:70]) for t in twin.tasks.tasks.values()
                            if t.status is TaskStatus.FAILED)
print("failures", fails.most_common(8))
print("idle carrying", [r.name for r in twin.robots.values() if r.current_task is None and r.carrying_box])
print("ERROR", [r.name for r in twin.robots.values() if r.status is RobotStatus.ERROR])
print("VALIDATING", [t.id for t in twin.tasks.tasks.values() if t.status is TaskStatus.VALIDATING])
totes = [b for b in twin.boxes.values() if b.kind is BoxKind.TOTE]
print("totes not home", [(b.name, b.status.value, b.position, twin.tasks.active_task_for_box(b.id) is not None)
                         for b in totes if not tote_is_home(twin, b)])
print("stray ITEMs STORED", [(b.name, b.position) for b in twin.boxes.values() if b.kind is BoxKind.ITEM and b.status.value == "STORED"])
print("positions", sorted((r.name, r.position, r.status.value) for r in twin.robots.values()))
