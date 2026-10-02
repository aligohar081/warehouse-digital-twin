"""People's activities and duty (multi-embodiment spec §11.3, §11.4): the
shift engine moves people by role, sends them on staggered breaks, has them
step aside for a waiting forklift, and duty follows the shift clock and HR."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.models import OperatorStatus, SimulationStatus, TaskStatus
from backend.operations import move_people, on_break, refuge_zone, sync_duty
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


def crew(twin, *names):
    workers = {"Sam": "E-10001", "Noor": "E-10003", "Mateo": "E-10004", "Ana": "E-10005",
               "Jordan": "E-10006", "Riley": "E-10008", "Sasha": "E-10009", "Morgan": "E-10010"}
    return [twin.add_operator(name=name, worker_id=workers[name]) for name in names]


def settle(twin):
    """Finish every walk at once."""
    for operator in twin.operators.values():
        if operator.in_transit:
            people.place(twin, operator, operator.transit_to)


def test_people_clock_on_at_their_place_by_role(twin):
    sam, mateo, ana, riley = crew(twin, "Sam", "Mateo", "Ana", "Riley")
    move_people(twin.shift)
    assert (sam.zone, mateo.zone, ana.zone, riley.zone) == \
        ("intake_staging", "workshop", "outbound_staging", "pick_station_1")
    assert not any(o.in_transit for o in (sam, mateo, ana, riley))       # clocking on is instant


def test_pickers_go_where_the_work_is_and_dock_hands_meet_the_truck(twin):
    sam, ana = crew(twin, "Sam", "Ana")
    move_people(twin.shift)
    order = twin.shift.orders.customer([{"sku": "SKU-001", "units": 1}], "dock_4")
    order.status, order.pick_station = "IN_PROGRESS", "pick_station_2"
    pallet = twin.add_box(name="IN-1", kind="PALLET", sku="SKU-001", quantity=10, weight=300.0, position=(2, 8))
    twin.shift.orders.inbound("dock_2", [pallet.id])
    move_people(twin.shift)
    assert (sam.transit_to, ana.transit_to) == ("pick_station_2", "dock_2")
    settle(twin)
    order.status = "DONE"
    twin.shift.orders.orders.clear()
    move_people(twin.shift)
    assert (sam.transit_to, ana.transit_to) == ("intake_staging", "outbound_staging")


def test_jordan_follows_the_humanoid(twin):
    (jordan,) = crew(twin, "Jordan")
    humanoid = twin.add_robot(name="H1-212", asset_id="AST-000212", position=(22, 7))
    move_people(twin.shift)
    assert jordan.zone == "returns_qc"
    humanoid.position = (10, 13)
    move_people(twin.shift)
    assert jordan.transit_to == "tote_aisle_1"


def test_breaks_are_every_two_hours_staggered_and_start_after_two_hours():
    assert not on_break(0, 0) and not on_break(0, 3600)                   # nobody breaks in the first two hours
    assert on_break(0, 7200) and on_break(0, 7200 + 899) and not on_break(0, 7200 + 900)
    assert not on_break(1, 7200) and on_break(1, 7200 + 720)              # 12 minutes later for the next person
    assert on_break(0, 4 * 3600 + 60)


def test_a_person_on_break_goes_to_the_south_west_floor(twin):
    sam, = crew(twin, "Sam")
    move_people(twin.shift)
    twin.simulation_time = 7200.0                                          # Sam's slot is the first
    move_people(twin.shift)
    assert sam.transit_to == "sw_floor"
    settle(twin)
    twin.simulation_time = 7200.0 + 900
    move_people(twin.shift)
    assert sam.transit_to == "intake_staging"


def test_a_person_steps_aside_for_a_waiting_forklift(twin, sim):
    sam, = crew(twin, "Sam")
    people.place(twin, sam, "intake_staging")
    forklift = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(7, 4))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": forklift.id, "destination": "5,4"})
    for _ in range(20):
        sim.tick()
    assert forklift.wait_reason == "PERSON_IN_AISLE"
    refuge = refuge_zone(twin, "intake_staging")
    assert all(twin.warehouse.clearance(c) != "WIDE" for c in twin.warehouse.zones[refuge].cells)
    move_people(twin.shift)
    assert sam.transit_to == refuge
    for _ in range(400):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and forklift.wait_reason is None
    settle(twin)
    move_people(twin.shift)
    assert not sam.in_transit                                              # held clear for a while
    twin.simulation_time += 30.0
    move_people(twin.shift)
    assert sam.transit_to == "intake_staging"


def test_duty_follows_the_shift_clock_and_hr_state(twin):
    sam, sasha, morgan = crew(twin, "Sam", "Sasha", "Morgan")
    assert (sasha.employment_status, morgan.employment_status) == ("ON_LEAVE", "TERMINATED")
    twin.set_operator_shift(sam.id, 6, 14)
    sync_duty(twin)
    assert sam.status is OperatorStatus.AVAILABLE                         # 06:00
    assert sasha.status is OperatorStatus.OFF_DUTY and morgan.status is OperatorStatus.OFF_DUTY
    people.place(twin, sam, "intake_staging")
    twin.simulation_time = 8 * 3600.0                                       # 14:00
    sync_duty(twin)
    assert sam.status is OperatorStatus.OFF_DUTY and sam.zone is None
    changed = twin.events.query(event_type="OPERATOR_SHIFT_CHANGED")
    assert any("HR state ON_LEAVE" in e["message"] for e in changed)
    assert any("shift clock 14h" in e["message"] for e in changed)


def test_the_simulator_uses_the_shift_clock_on_the_new_floor(twin, sim):
    sam, = crew(twin, "Sam")
    twin.set_operator_shift(sam.id, 22, 23)                                 # not at 06:00
    for _ in range(20):
        sim.tick()
    assert sam.status is OperatorStatus.OFF_DUTY


def test_the_engine_moves_people_only_while_the_shift_runs(twin, sim):
    sam, = crew(twin, "Sam")
    for _ in range(40):
        sim.tick()
    assert sam.zone is None                                                 # paused: nobody is placed
    twin.shift.start()
    for _ in range(40):
        sim.tick()
    assert sam.zone == "intake_staging"
