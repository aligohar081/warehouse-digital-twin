"""C3(b) predicate gap: (18,14) is the only way to Pick 1's tote drop (19,14), but it is not in
cells_jobs_need. An idle AMR left there (its job ended mid-route) while a tote job heads for (19,14)."""
import sys, tempfile
sys.path.insert(0, "/Users/bakarmac/projects/physical_ai/warehouse-digital-twin")
from backend.digital_twin import DigitalTwin
from backend.models import SimulationStatus
from backend.simulator import Simulator
tmp = tempfile.mkdtemp()
twin = DigitalTwin(log_dir=tmp + "/l", data_dir=tmp + "/d", persist_logs=False, demo=True, demo_tasks=False,
                   layout="distribution_center")
need = twin.warehouse.cells_jobs_need()
print("in cells_jobs_need:", {c: c in need for c in [(18, y) for y in range(12, 19)]})
print("neighbours of (19,14):", list(twin.warehouse.neighbors((19, 14), twin.add_robot(name="TR50-101", asset_id="AST-000101", position=(18, 14)).mobility, "GROUND")))
squat = twin.find_robot("TR50-101")                      # idle on (18,14), as if its job had ended there
worker = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 15))
tote = twin.add_box(name="TOTE-1", kind="TOTE", sku="S1", quantity=20, weight=21.5, slot="TS-11-12-0")
sim = Simulator(twin); twin.simulation_status = SimulationStatus.RUNNING
job = twin.tasks.create_task({"type": "TOTE_TO_STATION", "box_id": tote.id, "robot_id": worker.id,
                              "station": "pick_station_1"})
for i in range(3000):
    if job.is_terminal:
        break
    sim.tick()
moves = [t for t in twin.tasks.tasks.values() if t.type.value == "MOVE_ROBOT"]
print("after", i, "ticks: job", job.status.value, job.error, "| worker at", worker.position, worker.status.value,
      "blocked_by", worker.blocked_by, "| squatter at", squat.position, squat.status.value,
      "| MOVE_ROBOTs:", [(t.robot_id, t.status.value) for t in moves])
