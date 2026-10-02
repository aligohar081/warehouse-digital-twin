"""A deadlock sidestep is a move like any other (multi-embodiment spec §6): a
forklift or heavy hauler that steps aside never lands in a zone with a person
in it, announces the zone it does enter (ROBOT_STEP ENTER_ZONE), and every
other body — and the classic floor — sidesteps exactly as before."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.simulator import Simulator


@pytest.fixture
def twin(tmp_path):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout="distribution_center")


@pytest.fixture
def sim(twin):
    return Simulator(twin)


@pytest.fixture
def sam(twin):
    return twin.add_operator(name="Sam", worker_id="E-10001")


HEAVY = [("PF1200-205", "AST-000205"), ("HH300-207", "AST-000207")]   # a forklift and a hauler


def stuck_in_main_aisle(twin, name, asset_id, boxed_in=False):
    """A heavy body at (7,3) in `main_aisle`, blocked by an AMR at (6,3). Its way
    on into `pallet_aisle_1` is (8,3); with `boxed_in`, two more robots take
    (7,2) and (7,4), so (8,3) is the only cell it could step aside to."""
    body = twin.add_robot(name=name, asset_id=asset_id, position=(7, 3))
    blocker = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(6, 3))
    if boxed_in:
        twin.add_robot(name="TR50-202", asset_id="AST-000202", position=(7, 2))
        twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(7, 4))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": body.id, "destination": "10,3"})
    return body, blocker, task


def zone_keys(twin, cell):
    return {zone.key for zone in twin.warehouse.zones_of_cell(cell)}


def steps(twin, task):
    return [e["data"] for e in twin.events.query(task_id=task.id, event_type="ROBOT_STEP")]


@pytest.mark.parametrize("name, asset_id", HEAVY)
def test_a_heavy_body_does_not_sidestep_into_a_zone_with_a_person(twin, sim, sam, name, asset_id):
    body, blocker, task = stuck_in_main_aisle(twin, name, asset_id)
    people.place(twin, sam, "pallet_aisle_1")
    assert (8, 3) in twin.warehouse.neighbors(body.position, body.mobility, body.layer)
    sim._break_deadlock(body, task, blocker)
    assert body.position in {(7, 2), (7, 4)}, "it stepped aside, but not into the occupied aisle"
    assert "pallet_aisle_1" not in zone_keys(twin, body.position)
    assert len(twin.events.query(event_type="DEADLOCK_RESOLVED")) == 1
    assert body.wait_reason is None and not twin.events.query(event_type="ROBOT_SAFETY_WAIT")
    assert not steps(twin, task)                         # it entered no zone


@pytest.mark.parametrize("name, asset_id", HEAVY)
def test_a_boxed_in_heavy_body_stays_put_rather_than_enter_an_occupied_zone(twin, sim, sam, name, asset_id):
    body, blocker, task = stuck_in_main_aisle(twin, name, asset_id, boxed_in=True)
    people.place(twin, sam, "pallet_aisle_1")
    body.wait_ticks = 9
    sim._break_deadlock(body, task, blocker)
    assert body.position == (7, 3) and body.wait_ticks == 0   # no sidestep this tick, and no new wait
    assert body.wait_reason is None and not twin.events.query(event_type="ROBOT_SAFETY_WAIT")
    assert not twin.events.query(event_type="DEADLOCK_RESOLVED") and not steps(twin, task)


@pytest.mark.parametrize("name, asset_id", HEAVY)
def test_a_sidestep_into_an_empty_zone_still_happens_and_is_announced(twin, sim, sam, name, asset_id):
    body, blocker, task = stuck_in_main_aisle(twin, name, asset_id, boxed_in=True)
    people.place(twin, sam, "workshop")                  # nobody in pallet_aisle_1
    sim._break_deadlock(body, task, blocker)
    assert body.position == (8, 3)
    assert len(twin.events.query(event_type="DEADLOCK_RESOLVED")) == 1
    assert [(d["step"], d["zone"], d["people_present"]) for d in steps(twin, task)] == \
        [("ENTER_ZONE", "pallet_aisle_1", [])]


def test_only_entering_a_zone_counts_when_stepping_aside(twin, sim, sam):
    body = twin.add_robot(name="PF1200-205", asset_id="AST-000205", position=(9, 3))
    blocker = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 3))
    twin.add_robot(name="TR50-202", asset_id="AST-000202", position=(9, 4))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": body.id, "destination": "12,3"})
    people.place(twin, sam, "pallet_aisle_1")            # in the zone the forklift is already in
    sim._break_deadlock(body, task, blocker)
    assert body.position == (10, 3) and not steps(twin, task)
    assert len(twin.events.query(event_type="DEADLOCK_RESOLVED")) == 1


def test_a_body_without_the_rule_still_sidesteps_into_an_occupied_zone(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(7, 13))
    blocker = twin.add_robot(name="TR50-202", asset_id="AST-000202", position=(6, 13))
    twin.add_robot(name="PK30-203", asset_id="AST-000203", position=(7, 14))
    twin.add_robot(name="SC1-204", asset_id="AST-000204", position=(7, 12))
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "10,13"})
    people.place(twin, sam, "tote_aisle_1")
    sim._break_deadlock(amr, task, blocker)
    assert amr.position == (8, 13)                       # the rule is for forklifts and haulers only
    assert len(twin.events.query(event_type="DEADLOCK_RESOLVED")) == 1
    assert not steps(twin, task) and not twin.events.query(event_type="ROBOT_SAFETY_WAIT")


def test_a_classic_floor_robot_sidesteps_as_it_always_did(tmp_path):
    classic = DigitalTwin(log_dir=str(tmp_path / "c" / "logs"), data_dir=str(tmp_path / "c" / "data"),
                          persist_logs=False, demo=True, demo_tasks=False)
    robot, blocker = classic.find_robot("Robo-01"), classic.find_robot("Robo-02")
    assert robot.mobility is None
    blocker.position = (4, 8)
    robot.wait_ticks = 9
    task = classic.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": robot.id, "destination": "10,8"})
    Simulator(classic)._break_deadlock(robot, task, blocker)
    assert robot.position == (2, 8) and robot.wait_ticks == 0   # the first of the equally good free neighbours
    assert len(classic.events.query(event_type="DEADLOCK_RESOLVED")) == 1
    assert not classic.events.query(event_type="ROBOT_STEP")
