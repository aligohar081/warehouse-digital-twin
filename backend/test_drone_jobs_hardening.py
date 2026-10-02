"""Drone count hardening (multi-embodiment spec §5.5, §10.1): a drone that is
already airborne when a count is assigned has no pad under it, so the count
ends on a free pad cell instead of "landing" where it hovers, and with no pad
cell free the count is refused at assignment, before any flight."""
import pytest

from backend import energy
from backend.digital_twin import DigitalTwin
from backend.jobs import JOB_SPECS
from backend.models import CONFIG, CellType, SimulationStatus, TaskStatus, TaskType
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def drone(twin):
    return twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))


COUNT = {"type": "CYCLE_COUNT", "face": "12,2"}


def stock_face(twin):
    for level, quantity in ((0, 40), (1, 30), (2, 20)):
        twin.add_box(name=f"PAL-{level}", kind="PALLET", sku=f"SKU-0{level}", quantity=quantity, weight=500.0,
                     slot=f"PR-12-02-{level}")


def left_in_the_air(twin, sim, drone):
    """Start a count, let the drone reach the rack face, then cancel the count:
    the drone is idle in the air over an aisle cell, with the next count not
    yet assigned."""
    task = twin.tasks.create_task(COUNT)
    assert task.status is TaskStatus.PLANNING, task.error
    for _ in range(1500):
        if drone.layer == "AIR" and drone.position == (12, 3):
            break
        sim.tick()
    assert drone.layer == "AIR" and drone.position == (12, 3), "the drone never reached the face"
    twin.tasks.cancel_task(task.id)
    assert task.status is TaskStatus.CANCELLED and drone.current_task is None


def hold(twin, cells):
    """Grounded drones parked on `cells`: no other body can land there."""
    for cell in cells:
        twin.add_robot(name=f"Pad-{cell[0]}-{cell[1]}", model_code="CT-IX2", position=cell)


def test_an_airborne_drone_given_a_count_finishes_it_on_a_free_pad(twin, sim, drone):
    stock_face(twin)
    left_in_the_air(twin, sim, drone)
    task = twin.tasks.create_task(COUNT)
    for _ in range(1500):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED, task.error
    steps = [e["data"]["step"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]
    assert steps == ["SCAN"] * 5 + ["NAVIGATE", "LAND"]          # already flying at the face: no take-off, no leg out
    assert drone.layer == "GROUND" and twin.warehouse.cell_type(*drone.position) is CellType.DRONE_PAD


def test_an_airborne_drone_with_every_pad_cell_taken_is_refused_the_count_before_flying(twin, sim, drone):
    stock_face(twin)
    left_in_the_air(twin, sim, drone)
    here = (drone.layer, drone.position)
    hold(twin, twin.warehouse.zones["drone_pad"].cells)                            # every pad cell taken
    task = twin.tasks.create_task({**COUNT, "robot_id": drone.id})
    for _ in range(50):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.FAILED
    assert "no free pad to land on after the count" in task.error.lower()
    assert (drone.layer, drone.position) == here                                  # it never flew the count
    for kind in ("ROBOT_STEP", "SCANNED"):
        assert twin.events.query(task_id=task.id, event_type=kind) == []


def test_the_energy_estimate_of_an_airborne_count_runs_on_to_the_nearest_free_pad(twin, drone):
    stock_face(twin)
    task = twin.tasks.create_task({**COUNT, "robot_id": drone.id})
    twin.set_robot_layer(drone.id, "AIR", altitude_m=1.0)
    drone.position = (12, 3)                                                       # hovering at the face already
    spec = JOB_SPECS[TaskType.CYCLE_COUNT]
    nearest = spec.mission_wh(twin.tasks, task, drone)
    hold(twin, [(19, 1), (19, 2), (19, 3)])                                        # the nearest pad column is taken
    farther = spec.mission_wh(twin.tasks, task, drone)
    profile = drone.mobility
    one_cell = energy.flight_wh_per_tick(profile) / (profile.speed_cells_s * CONFIG["TICK_DT"])
    assert farther - nearest == pytest.approx(one_cell)                            # one cell further to a free pad
