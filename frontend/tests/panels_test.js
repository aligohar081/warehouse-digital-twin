/* Unit tests for the panel helpers of frontend/floor_model.js (multi-
   embodiment spec §12 Robot panel, Shift panel, Fleet page; the task form).
   backend/test_panels_js.py runs them:
     jsc <fixtures.js> frontend/floor_model.js <this file>
   where FIXTURES = {classic, dc, faultKinds, indexHtml}: real /api/state
   snapshots of both floors, backend/faults.py's fault kinds and
   frontend/index.html. Prints one line per failure, then "N passed, M
   failed"; any failure ends in an exception, so jsc exits non-zero. ES5. */
var passed = 0, failed = 0;

function test(name, body) {
  try {
    body();
    passed++;
  } catch (error) {
    failed++;
    print("FAIL " + name + ": " + error.message);
  }
}

function eq(actual, expected, what) {
  var a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error((what ? what + ": " : "") + "expected " + b + ", got " + a);
}

function ok(value, what) {
  if (!value) throw new Error(what || "expected a true value");
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function named(list, name) {
  for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
  throw new Error("nothing named " + name);
}

var FM = FloorModel;
var classic = FIXTURES.classic, dc = FIXTURES.dc;

/* ------------------------------------------------------------ robot panel */
test("the robot panel shows an AMR's model, asset, limits and current job", function () {
  var amr = named(dc.robots, "TR50-201");
  var view = FM.robotPanel(amr, dc.tasks);
  eq([view.name, view.letter, view.status], ["TR50-201", "A", "MOVING"]);
  eq(view.identity, [["Model", "AC-TR50"], ["Asset", "AST-000201"], ["Type", "AMR"], ["Layer", "ground"]]);
  eq(view.limits, [["Payload", "50 kg"], ["Reach", "shelf level 1 (lift 0.6 m)"], ["Clearance", "NARROW"]]);
  eq(view.job, [["Job", amr.current_task + " · Tote to station"], ["Step", "Navigate to slot TS-10-12-1"],
    ["Waiting for", "—"]]);
  eq(view.inventoryUrl, "/fleet.html?asset=AST-000201");
});

test("an arm's reach is its own, and it has no clearance class", function () {
  var view = FM.robotPanel(named(dc.robots, "CX10-210"), dc.tasks);
  eq(view.limits, [["Payload", "10 kg"], ["Reach", "1300 mm"], ["Clearance", "fixed station"]]);
  eq(view.job, [["Job", "none"], ["Step", "—"], ["Waiting for", "—"]]);
});

test("a drone in the air shows its layer and altitude", function () {
  var view = FM.robotPanel(named(dc.robots, "IX2-208"), dc.tasks);
  eq(view.identity[3], ["Layer", "air, 2.5 m up"]);
  eq(view.limits[2], ["Clearance", "flies (air layer)"]);
});

test("a robot holding a physical wait shows why", function () {
  var amr = clone(named(dc.robots, "TR50-201"));
  amr.wait_reason = "PERSON_IN_AISLE";
  var view = FM.robotPanel(amr, dc.tasks);
  eq(view.waitReason, "PERSON_IN_AISLE");
  eq(view.job[2], ["Waiting for", "Person in aisle"]);
});

test("a job the snapshot no longer lists still shows by id, and its step from the robot", function () {
  var amr = clone(named(dc.robots, "TR50-201"));
  eq(FM.robotPanel(amr, []).job.slice(0, 2), [["Job", amr.current_task], ["Step", "Navigate"]]);
});

test("a classic robot has no floor profile to show, and links to its record if it has one", function () {
  var robot = classic.robots[0];
  var view = FM.robotPanel(robot, classic.tasks);
  eq([view.letter, view.limits], [null, []]);
  eq(view.identity[2], ["Type", "AMR"]);
  eq(view.inventoryUrl, robot.asset_id ? "/fleet.html?asset=" + robot.asset_id : null);
  var unbound = clone(robot);
  unbound.asset_id = null;
  eq(FM.robotPanel(unbound, []).inventoryUrl, null);
});

/* ------------------------------------------------------------ shift panel */
test("the classic floor has no shift panel", function () {
  eq(classic.shift, null);
  eq(FM.shiftPanel(classic.shift), null);
});

test("the new floor's shift panel starts paused at 06:00", function () {
  // Plan ruling 6: the shift starts paused; Start (POST /api/shift/start) begins it.
  var view = FM.shiftPanel(dc.shift);
  eq([view.status, view.running, view.clock, view.pace, view.seed], ["PAUSED", false, "06:00", 1, 42]);
  eq([view.inFlight, view.backlog, view.done, view.throughput], [0, 0, 0, 0]);
  eq([view.failed, view.escalations], [[], []]);
});

test("the shift panel lists failed orders with reasons, and only safety escalations", function () {
  var shift = clone(dc.shift);
  shift.status = "RUNNING";
  shift.in_flight = 3;
  shift.backlog = 2;
  shift.throughput_per_hour = 12.5;
  shift.orders.CUSTOMER.DONE = 4;
  shift.orders.INBOUND.DONE = 1;
  shift.failed_orders = [{ order_id: "ORD-0007", kind: "CUSTOMER", reason: "TOTE-3 has no unit left" },
    { order_id: "ORD-0002", kind: "PALLET", reason: null }];
  shift.exceptions = [
    { type: "SAFETY_WAIT_ESCALATED", message: "H1-212 waited 120 s: SUPERVISOR_ABSENT", tick: 900 },
    { type: "ORDER_FAILED", message: "ORD-0007 failed", tick: 880 },
    { type: "STOCK_VARIANCE_DETECTED", message: "PR-10-05-0 is short 3", tick: 600 }];
  var view = FM.shiftPanel(shift);
  eq([view.running, view.inFlight, view.backlog, view.done, view.throughput], [true, 3, 2, 5, 12.5]);
  eq(view.failed, [{ id: "ORD-0007", kind: "CUSTOMER", reason: "TOTE-3 has no unit left" },
    { id: "ORD-0002", kind: "PALLET", reason: "no reason recorded" }]);
  eq(view.escalations, [{ message: "H1-212 waited 120 s: SUPERVISOR_ABSENT", tick: 900 }]);
  shift.in_flight = [{}, {}];
  eq(FM.shiftPanel(shift).inFlight, 2, "a list of orders counts as its length");
});

test("Inject fault offers exactly the backend's fault kinds", function () {
  // Fails if backend/faults.py FAULT_RISKS gains or renames a kind the select doesn't offer.
  eq(FM.FAULT_KINDS.map(function (kind) { return kind[0]; }).sort(), FIXTURES.faultKinds);
});

/* -------------------------------------------------------------- task form */
test("the task form shows each type's fields from options()", function () {
  eq(FM.taskFields(dc.options, "TOTE_TO_STATION"), ["box", "station", "priority"]);
  eq(FM.taskFields(dc.options, "MANUAL_PICK"), ["box", "quantity", "order_id", "pack_cell", "operator", "priority"]);
  eq(FM.taskFields(dc.options, "PATROL"), ["priority"]);
  eq(FM.taskFields(dc.options, "NOT_A_TYPE"), []);
});

test("every field either floor's options name is one the form can show", function () {
  // Fails if options()["task_types"][*].fields names a field index.html has no input for.
  [classic, dc].forEach(function (floor) {
    floor.options.task_types.forEach(function (entry) {
      FM.taskFields(floor.options, entry.id).forEach(function (field) {
        ok(FM.FORM_FIELDS.indexOf(field) !== -1, entry.id + " names an unknown field " + field);
      });
    });
  });
  FM.FORM_FIELDS.forEach(function (field) {
    ok(FIXTURES.indexHtml.indexOf('data-when="' + field + '"') !== -1, "index.html has no " + field + " field");
  });
});

test("the older task types keep the fields the dashboard always showed", function () {
  classic.options.task_types.forEach(function (entry) {
    eq(FM.taskFields(classic.options, entry.id), FM.taskFields(null, entry.id), entry.id);
  });
  eq(FM.taskFields(null, "PICK_AND_DELIVER"), ["box", "source", "destination", "priority"]);
});

test("a type's fields read payload names as form fields and never list the robot", function () {
  var options = { task_types: [{ id: "X", fields: ["box_id", "robot_id", "operator_id", "quantity", "box"] }] };
  eq(FM.taskFields(options, "X"), ["box", "operator", "quantity"]);
});

test("the form shows a type's guide", function () {
  ok(FM.taskGuide(dc.options, "TOTE_TO_STATION").indexOf("station") !== -1, "the tote job's guide");
  eq(FM.taskGuide(null, "TOTE_TO_STATION"), "");
});

test("an older task type is sent exactly as the form always sent it", function () {
  var values = { robot: "AUTO", box: "box_001", source: "", destination: "loading_zone", priority: "NORMAL",
    agent: "AUTO", operator: "AUTO", dual_signoff: false, second_operator: "AUTO", slot: "PR-10-05-0" };
  eq(FM.taskPayload("PICK_AND_DELIVER", FM.taskFields(classic.options, "PICK_AND_DELIVER"), values),
    { type: "PICK_AND_DELIVER", robot_id: "AUTO", box_id: "box_001", destination: "loading_zone", priority: "NORMAL" });
  values.dual_signoff = true;
  values.second_operator = "operator_002";
  eq(FM.taskPayload("HUMAN_INSPECTION", ["operator"], values),
    { type: "HUMAN_INSPECTION", robot_id: "AUTO", operator_id: "AUTO", dual_signoff: true,
      second_operator_id: "operator_002" });
});

test("a job's own fields go only when filled in, a whole quantity as a number", function () {
  var values = { robot: "AUTO", box: "box_009", priority: "HIGH", quantity: " 3 ", order_id: "  ",
    pack_cell: "pack_cell_2", station: "pick_station_1", operator: "AUTO" };
  eq(FM.taskPayload("PICK_ITEMS", FM.taskFields(dc.options, "PICK_ITEMS"), values),
    { type: "PICK_ITEMS", robot_id: "AUTO", box_id: "box_009", priority: "HIGH", quantity: 3, pack_cell: "pack_cell_2" });
  values.quantity = "2.5";   // the API answers "quantity must be a whole number of at least 1"
  eq(FM.taskPayload("PICK_ITEMS", ["quantity"], values).quantity, "2.5");
});

test("a job's station, pack cell, lane and dock are chosen from the floor's zones", function () {
  function keys(field, floor) {
    return FM.fieldChoices(floor.warehouse, field).map(function (c) { return c.value; });
  }
  eq(keys("station", dc), ["pick_station_1", "pick_station_2"]);
  eq(keys("pack_cell", dc), ["pack_cell_1", "pack_cell_2"]);
  eq(FM.fieldChoices(dc.warehouse, "lane"), [{ value: "dock_4", label: "Dock 4" }, { value: "dock_5", label: "Dock 5" }]);
  eq(keys("dock", dc), ["dock_3"]);
  eq(keys("slot", dc), []);
  ["station", "pack_cell", "lane", "dock"].forEach(function (field) { eq(keys(field, classic), [], field); });
});

/* ----------------------------------------------------------------- people */
test("a person walking back out of a job that ended early reads returning", function () {
  // Plan 1b T10 minor: Sam's CLEAR_JAM was cancelled once he got there; he is
  // still ON_TASK on the ended job until he is back.
  var sam = named(dc.operators, "Sam"), lee = named(dc.operators, "Lee");
  eq([sam.status, sam.transit_to], ["ON_TASK", "intake_staging"]);
  eq(FM.personStatus(sam, dc.tasks), "RETURNING");
  eq(FM.personStatus(lee, dc.tasks), "ON_TASK", "Lee is still walking to her jam");
  var tasks = clone(dc.tasks);
  tasks.forEach(function (task) { if (task.id === lee.current_task) task.params.phase = "RETURN"; });
  eq(FM.personStatus(lee, tasks), "RETURNING", "walking back after clearing it");
  classic.operators.forEach(function (o) { eq(FM.personStatus(o, classic.tasks), o.status, o.name); });
});

/* ------------------------------------------------------- links and clicks */
test("the dashboard reads the robot to select from its query string", function () {
  eq(FM.queryParam("?robot=robot_003", "robot"), "robot_003");
  eq(FM.queryParam("?x=1&robot=TR50%2D201", "robot"), "TR50-201");
  eq(FM.queryParam("?asset=AST-000201", "robot"), null);
  eq(FM.queryParam("", "robot"), null);
});

test("a click selects the robot drawn nearest it, within a cell", function () {
  var points = [{ id: "a", x: 3, y: 4 }, { id: "b", x: 3.3, y: 4 }, { id: "c", x: 10, y: 10 }];
  eq(FM.hitRobot(points, 3.2, 4), "b");
  eq(FM.hitRobot(points, 2.8, 4.1), "a");
  eq(FM.hitRobot(points, 6, 6), null);
});

test("the page has the shift panel, the robot panel and the guide line", function () {
  ["shiftPanel", "shiftStartBtn", "shiftPauseBtn", "shiftPace", "shiftPaceBtn", "shiftStats", "shiftFailed",
    "shiftEscalations", "faultKind", "faultBtn", "robotPanel", "robotPanelBody", "robotPanelClose", "taskGuide"]
    .forEach(function (id) { ok(FIXTURES.indexHtml.indexOf('id="' + id + '"') !== -1, "index.html has no #" + id); });
});

print(passed + " passed, " + failed + " failed");
if (failed) throw new Error(failed + " panel test(s) failed");
