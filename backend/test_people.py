"""People as zone presence (multi-embodiment spec §6): zones, walks between
them, credential scopes, supervisor proximity and the PERSON_ON_CROSSING wait."""
import pytest

from backend import people
from backend.digital_twin import DigitalTwin
from backend.fleet_bridge import credential_scopes
from backend.models import OperatorStatus, RobotStatus, SimulationStatus, TaskStatus
from backend.operator import Operator
from backend.simulator import Simulator


def make_twin(tmp_path, layout="distribution_center"):
    return DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False, layout=layout)


@pytest.fixture
def twin(tmp_path):
    return make_twin(tmp_path)


@pytest.fixture
def sim(twin):
    simulator = Simulator(twin)
    twin.simulation_status = SimulationStatus.RUNNING
    return simulator


@pytest.fixture
def sam(twin):
    return twin.add_operator(name="Sam", worker_id="E-10001")


# --------------------------------------------------------------------------- #
# Operator fields
# --------------------------------------------------------------------------- #
def test_operator_zone_fields_round_trip_and_off_duty_leaves_the_floor():
    operator = Operator("operator_001", "Sam")
    assert (operator.zone, operator.transit_to, operator.transit_until_tick) == (None, None, None)
    assert operator.certification_scopes == {} and not operator.in_transit
    operator.zone, operator.transit_to, operator.transit_until_tick = "pick_station_2", "workshop", 40
    operator.certification_scopes = {"safety_inspection": {"equipment": [], "site": ["WH-01"]}}
    restored = Operator.from_dict(operator.to_dict())
    assert (restored.zone, restored.transit_to, restored.transit_until_tick) == ("pick_station_2", "workshop", 40)
    assert restored.certification_scopes == operator.certification_scopes and restored.in_transit
    operator.set_status(OperatorStatus.OFF_DUTY)
    assert (operator.zone, operator.transit_to, operator.transit_until_tick) == (None, None, None)
    legacy = {key: value for key, value in restored.to_dict().items()
              if key not in ("zone", "transit_to", "transit_until_tick", "certification_scopes")}
    assert Operator.from_dict(legacy).zone is None


# --------------------------------------------------------------------------- #
# Credential scopes (synced alongside certifications)
# --------------------------------------------------------------------------- #
def test_classic_operators_carry_their_credential_scopes(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    sam = classic.find_operator("Sam")
    assert sam.certification_scopes == {
        "safety_inspection": {"equipment": [], "site": ["WH-01"]},
        "electrical_safety": {"equipment": [], "site": ["WH-01"]},
    }
    assert list(sam.certification_scopes) == sam.certifications


def test_scopes_follow_the_workforce_record(twin):
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    assert jordan.certification_scopes["humanoid_supervision"] == {"equipment": ["TS-H1"], "site": ["WH-01"]}
    riley = twin.add_operator(name="Riley", worker_id="E-10008")
    assert "robot_cell_access" not in riley.certification_scopes  # revoked
    assert "safety_inspection" in riley.certification_scopes
    credential = next(c for c in twin.inventory.get_worker("E-10006")["credentials"]
                      if c["code"] == "humanoid_supervision")
    twin.fleet.mutate(twin.inventory.revoke_credential, credential["credential_id"], "Audit")
    assert "humanoid_supervision" not in jordan.certification_scopes
    assert "humanoid_supervision" not in jordan.certifications


def test_two_credentials_with_one_code_merge_to_the_wider_scope():
    def credential(code, equipment, site, validity="VALID"):
        return {"code": code, "equipment_scope": equipment, "site_scope": site, "validity": validity}

    worker = {"credentials": [
        credential("forklift_operator", ["NW-PF1200"], ["WH-01"]),
        credential("forklift_operator", ["NW-HH300"], []),
        credential("robot_cell_access", ["FB-CX10"], ["WH-01"], validity="REVOKED"),
    ]}
    assert credential_scopes(worker) == {
        "forklift_operator": {"equipment": ["NW-PF1200", "NW-HH300"], "site": []},
    }


# --------------------------------------------------------------------------- #
# Zone presence and walking
# --------------------------------------------------------------------------- #
def test_placing_people_in_zones(twin, sam):
    people.place(twin, sam, "Pick 2")  # any alias resolves
    assert sam.zone == "pick_station_2" and people.people_in(twin, "pick_station_2") == [sam]
    assert people.people_at(twin, (20, 17)) == [sam] and people.people_at(twin, (10, 3)) == []
    with pytest.raises(ValueError, match="Unknown zone"):
        people.place(twin, sam, "the moon")
    people.place(twin, sam, None)
    assert sam.zone is None and people.people_in(twin, "pick_station_2") == []
    sam.set_status(OperatorStatus.OFF_DUTY)
    with pytest.raises(ValueError, match="off duty"):
        people.place(twin, sam, "workshop")


def test_a_walk_takes_its_distance_at_walking_pace(twin, sim, sam):
    # Pick 2's centre (20,17) to intake staging's (5,6): 26 cells x 1.5 m / 1.2 m/s = 32.5 s = 217 ticks.
    assert people.transit_ticks(twin.warehouse, "pick_station_2", "intake_staging") == 217
    people.place(twin, sam, "pick_station_2")
    arrival = people.start_transit(twin, sam, "intake_staging")
    assert arrival == twin.tick_count + 217
    assert sam.in_transit and people.in_transit(twin) == [sam] and people.anyone_in_transit(twin)
    assert people.people_in(twin, "pick_station_2") == [] and people.people_in(twin, "intake_staging") == []
    with pytest.raises(ValueError, match="already walking"):
        people.start_transit(twin, sam, "workshop")
    while twin.tick_count < arrival - 1:
        sim.tick()
    assert sam.in_transit
    sim.tick()
    assert not sam.in_transit and sam.zone == "intake_staging"
    moved = twin.events.query(event_type="PERSON_MOVED")
    assert len(moved) == 1 and moved[0]["data"] == {"operator_id": sam.id, "from": "pick_station_2",
                                                    "to": "intake_staging"}
    assert people.start_transit(twin, sam, "intake_staging") == twin.tick_count  # already there
    assert not sam.in_transit
    people.place(twin, sam, None)
    with pytest.raises(ValueError, match="not on the floor"):
        people.start_transit(twin, sam, "workshop")


# --------------------------------------------------------------------------- #
# Supervisor proximity
# --------------------------------------------------------------------------- #
def test_zones_touch_when_equal_overlapping_or_sharing_an_edge(twin):
    warehouse = twin.warehouse
    assert people.zones_touch(warehouse, "returns_qc", "returns_qc")
    assert people.zones_touch(warehouse, "cross_aisle", "patrol_loop")   # overlap
    assert people.zones_touch(warehouse, "returns_qc", "ne_floor")       # shared edge
    assert not people.zones_touch(warehouse, "returns_qc", "sw_floor")


def test_a_supervisor_is_nearby_in_the_same_or_an_adjacent_zone(twin):
    jordan = twin.add_operator(name="Jordan", worker_id="E-10006")
    humanoid_cell = (22, 7)  # returns_qc
    for zone, expected in (("returns_qc", True), ("ne_floor", True), ("pallet_lane", True),
                           ("sw_floor", False), ("workshop", False)):
        people.place(twin, jordan, zone)
        assert people.supervisor_nearby(twin, humanoid_cell, jordan) is expected, zone
    people.place(twin, jordan, "returns_qc")
    people.start_transit(twin, jordan, "sw_floor")
    assert not people.supervisor_nearby(twin, humanoid_cell, jordan)  # walking away
    people.place(twin, jordan, None)
    assert not people.supervisor_nearby(twin, humanoid_cell, jordan)


# --------------------------------------------------------------------------- #
# PERSON_ON_CROSSING (spec §5.3)
# --------------------------------------------------------------------------- #
def test_a_ground_robot_waits_at_a_crossing_while_someone_walks(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(16, 13))
    people.place(twin, sam, "pick_station_2")
    arrival = people.start_transit(twin, sam, "intake_staging")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "18,13"})
    for _ in range(20):
        sim.tick()
    assert amr.position == (16, 13) and amr.status is RobotStatus.WAITING
    assert amr.wait_reason == "PERSON_ON_CROSSING" and amr.to_dict()["wait_reason"] == "PERSON_ON_CROSSING"
    assert task.status is TaskStatus.BLOCKED
    waits = twin.events.query(event_type="ROBOT_SAFETY_WAIT")
    assert len(waits) == 1 and waits[0]["data"]["reason"] == "PERSON_ON_CROSSING"
    assert waits[0]["category"] == "SAFETY" and waits[0]["task_id"] == task.id
    while twin.tick_count < arrival:
        sim.tick()
        assert amr.position != (17, 13), "entered the crossing while a person was on the walkway"
    for _ in range(40):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and amr.position == (18, 13) and amr.wait_reason is None
    resumed = twin.events.query(event_type="ROBOT_SAFETY_RESUMED")
    assert len(resumed) == 1 and resumed[0]["data"]["waited_ticks"] > 200


def test_a_drone_about_to_cross_the_walkway_waits_too(twin, sim, sam):
    drone = twin.add_robot(name="IX2-208", asset_id="AST-000208", position=(20, 2))
    twin.set_robot_layer(drone.id, "AIR")
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")
    twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": drone.id, "destination": "16,2"})
    for _ in range(60):
        sim.tick()
    assert drone.position == (18, 2) and drone.wait_reason == "PERSON_ON_CROSSING"


def test_robots_away_from_the_walkway_are_not_held_up(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(8, 13))
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "intake_staging")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "14,13"})
    for _ in range(80):
        if task.is_terminal:
            break
        sim.tick()
    assert task.status is TaskStatus.COMPLETED and sam.in_transit
    assert not twin.events.query(event_type="ROBOT_SAFETY_WAIT")


# --------------------------------------------------------------------------- #
# Only a walk that crosses the walkway strip holds the crossings (spec §6)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("origin, destination, crosses", [
    ("pick_station_2", "intake_staging", True),   # east to west across x=17
    ("intake_staging", "pick_station_2", True),
    ("pick_station_2", "pick_station_1", False),  # both east of the walkway
    ("walkway", "workshop", True),                # an end on the walkway itself
    ("pick_station_1", "walkway", True),
    ("intake_staging", "workshop", False),        # both west of it
])
def test_a_walk_crosses_the_walkway_only_between_its_sides_or_onto_it(twin, origin, destination, crosses):
    assert people.crosses_walkway(twin.warehouse, origin, destination) is crosses


def test_no_walk_crosses_the_walkway_on_the_classic_floor(tmp_path):
    classic = make_twin(tmp_path, layout="classic")
    zones = list(classic.warehouse.zones)
    assert all(not people.crosses_walkway(classic.warehouse, a, b) for a in zones for b in zones)
    sam = classic.find_operator("Sam")
    people.place(classic, sam, "shelf_a")
    people.start_transit(classic, sam, "loading_zone")
    assert people.anyone_in_transit(classic) and not people.anyone_on_walkway(classic)


def test_on_walkway_lists_only_the_walkers_who_cross_it(twin, sam):
    pat = twin.add_operator(name="Pat", worker_id="E-10002")
    people.place(twin, sam, "pick_station_2")
    people.place(twin, pat, "pick_station_2")
    people.start_transit(twin, sam, "pick_station_1")
    people.start_transit(twin, pat, "intake_staging")
    assert people.in_transit(twin) == [sam, pat]
    assert people.on_walkway(twin) == [pat] and people.anyone_on_walkway(twin)
    pat.zone, pat.transit_to, pat.transit_until_tick = None, None, None
    assert people.on_walkway(twin) == [] and not people.anyone_on_walkway(twin)
    assert people.anyone_in_transit(twin)  # Sam is still walking, just not across the strip


def test_a_walk_that_stays_on_one_side_does_not_hold_the_crossing(twin, sim, sam):
    amr = twin.add_robot(name="TR50-201", asset_id="AST-000201", position=(16, 13))
    people.place(twin, sam, "pick_station_2")
    people.start_transit(twin, sam, "pick_station_1")
    task = twin.tasks.create_task({"type": "MOVE_ROBOT", "robot_id": amr.id, "destination": "18,13"})
    walking_when_done = None
    for _ in range(40):
        sim.tick()
        if task.is_terminal:
            walking_when_done = sam.in_transit
            break
    assert task.status is TaskStatus.COMPLETED and amr.position == (18, 13)
    assert walking_when_done, "the robot only got across because the walk was over"
    assert not twin.events.query(event_type="ROBOT_SAFETY_WAIT")
