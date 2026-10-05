"""C1 residual: a tote job cancelled while its AMR is on a walkway crossing. Where does the tote go,
can it be sent home, and what does a customer order for that SKU do afterwards?"""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus
from backend.simulator import Simulator
from backend.jobs import tote_is_home
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
for name, asset, cell in (("TR50-201", "AST-000201", (8, 13)), ("TR50-101", "AST-000101", (8, 15)),
                          ("PK30-203", "AST-000203", (20, 14)), ("H1-212", "AST-000212", (22, 7))):
    twin.add_robot(name=name, asset_id=asset, position=cell)
for n in range(1, 11): twin.add_operator(name=f"W{n:02d}", worker_id=f"E-{10000 + n}")
twin.add_robot(name="CX10-210", asset_id="AST-000210"); twin.add_robot(name="CX10-211", asset_id="AST-000211")
for n in range(6):
    twin.add_box(name=f"TOTE-{n}", kind="TOTE", sku=f"SKU-00{n + 1}", quantity=20, weight=21.5, slot=f"TS-{8 + n:02d}-12-0")
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
order = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
tote = twin.find_box("TOTE-1")
for i in range(3000):
    sim.tick()
    carrier = next((r for r in twin.robots.values() if r.carrying_box == tote.id), None)
    if carrier is not None and carrier.position in twin.warehouse.crossings:
        break
print("carrier", carrier.name if carrier else None, "at", carrier.position if carrier else None, "tick", i)
twin.shift.orders._fail(order, "probe fails the order on the crossing")
print("tote", tote.status.value, tote.position, "home?", tote_is_home(twin, tote))
rt = [t for t in twin.tasks.tasks.values() if t.type.value == "RETURN_TOTE"]
print("RETURN_TOTE:", [(t.id, t.status.value, t.error) for t in rt])
for _ in range(2000):
    sim.tick()
print("after 2000 ticks: tote", tote.status.value, tote.position, "home?", tote_is_home(twin, tote),
      [(t.id, t.status.value, t.error) for t in twin.tasks.tasks.values() if t.type.value == "RETURN_TOTE"])
again = twin.shift.orders.customer([{"sku": "SKU-002", "units": 1}], "dock_4")
for _ in range(3000):
    sim.tick()
print("a later SKU-002 order:", again.status, again.pick_station, again.failure_reason)
