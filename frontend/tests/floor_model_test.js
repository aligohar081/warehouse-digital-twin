/* Unit tests for frontend/floor_model.js (multi-embodiment spec §12 Floor,
   Robots, Air layer, Arms, People, Conveyor). backend/test_floor_model_js.py
   runs them:  jsc <fixtures.js> frontend/floor_model.js <this file>
   where FIXTURES = {classic, dc} holds real /api/state snapshots of both
   floors. Prints one line per failure, then "N passed, M failed"; any
   failure ends in an exception, so jsc exits non-zero. Plain ES5. */
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

function cells(list) {
  return list.map(function (c) { return c.x + "," + c.y; });
}

var FM = FloorModel;
var classic = FIXTURES.classic, dc = FIXTURES.dc;

/* The dashboard's colours, legend and cell fills before plan 1c (frontend/
   app.js COLORS, LEGEND and cellFill): the classic floor must look the same. */
var OLD_COLORS = {
  floor: "#0e1417", grid: "#182126", wall: "#2c3a41", shelf: "#243138", shelfLine: "#33454e",
  storage: "#1d2a30", charging: "#16302f", loading: "#2c2716", unloading: "#252c1a",
  packing: "#231d2e", parking: "#1b2429", restricted: "#2b1e14", amber: "#ffb300",
  cyan: "#31d1c4", ok: "#7ddf64", warn: "#ff9538", fault: "#ff5252", ink: "#e7eef1",
  muted: "#7b8d95", faint: "#4d5f67", box: "#c98b3a", boxCarried: "#ffb300", boxDelivered: "#5f8f4f"
};
var OLD_LEGEND = [
  ["Racking", "#243138"], ["Pick face", "#1d2a30"], ["Charging", "#16302f"], ["Loading", "#2c2716"],
  ["Unloading", "#252c1a"], ["Packing", "#231d2e"], ["Parking", "#1b2429"], ["Restricted", "#2b1e14"],
  ["Route", "#ffb300"]
];
function oldCellFill(type) {
  switch (type) {
    case "WALL": return OLD_COLORS.wall;
    case "SHELF": return OLD_COLORS.shelf;
    case "STORAGE": return OLD_COLORS.storage;
    case "CHARGING": return OLD_COLORS.charging;
    case "LOADING": return OLD_COLORS.loading;
    case "UNLOADING": return OLD_COLORS.unloading;
    case "PACKING": return OLD_COLORS.packing;
    case "PARKING": return OLD_COLORS.parking;
    case "RESTRICTED": return OLD_COLORS.restricted;
    default: return OLD_COLORS.floor;
  }
}

/* --------------------------------------------------------- the classic look */
test("the palette keeps every colour the dashboard has always drawn with", function () {
  Object.keys(OLD_COLORS).forEach(function (key) { eq(FM.PALETTE[key], OLD_COLORS[key], key); });
});

test("every classic cell fills exactly as before", function () {
  // Fails if a classic role's colour changes, or the table maps a classic type to another role.
  var fills = FM.cellFills(classic.cell_types, FM.PALETTE);
  classic.warehouse.cells.forEach(function (cell) {
    eq(fills[cell.type] || FM.PALETTE.floor, oldCellFill(cell.type), cell.type + " at " + cell.x + "," + cell.y);
  });
  eq(fills.EMPTY || FM.PALETTE.floor, OLD_COLORS.floor, "the open floor");
});

test("the classic legend is the one the dashboard has always shown", function () {
  eq(FM.legendItems(classic.cell_types, FM.PALETTE), OLD_LEGEND);
});

test("classic racking keeps its hatching and the restricted area its hazard stripes", function () {
  var roles = FM.cellRoles(classic.cell_types);
  eq(FM.cellPattern(roles.SHELF), "rack");
  eq(FM.cellPattern(roles.RESTRICTED), "hazard");
  ["STORAGE", "CHARGING", "LOADING", "UNLOADING", "PACKING", "PARKING", "WALL"].forEach(function (type) {
    eq(FM.cellPattern(roles[type]), null, type);
  });
});

test("every classic zone keeps its outline and label", function () {
  var frames = FM.zoneFrames(classic.warehouse.zones);
  eq(frames.map(function (f) { return f.key; }), classic.warehouse.zones.map(function (z) { return z.key; }));
  frames.forEach(function (f) {
    ok(f.outline && !f.vertical, f.key + " is outlined, label flat");
    eq(f.restricted, f.key === "restricted_area", f.key + " restricted");
  });
});

test("classic robots carry no letter, badge or arm view and all draw on the ground", function () {
  classic.robots.forEach(function (robot) {
    eq(FM.robotLetter(robot), null, robot.name);
    eq(FM.robotGlyph(robot), "square", robot.name);
    eq(FM.armView(robot, classic.equipment, classic.warehouse), null, robot.name);
    eq(FM.kindBadge(robot, { kind: "TOTE" }), null, robot.name);
  });
  var layers = FM.robotLayers(classic.robots, false);
  eq(layers.ground.length, classic.robots.length);
  eq(layers.air, []);
});

test("the classic floor has no people, conveyor or no-fly cells to draw", function () {
  eq(classic.equipment, null);
  eq(FM.conveyorView(classic.equipment), { items: [], jammed: [] });
  eq(FM.noFlyCells(classic.no_fly_cells), []);
  eq(FM.peopleDots(classic.operators, classic.warehouse.zones, []), []);
});

/* --------------------------------------------- the distribution-centre floor */
test("every distribution-centre cell type has a colour of its own", function () {
  var fills = FM.cellFills(dc.cell_types, FM.PALETTE);
  dc.cell_types.forEach(function (entry) {
    ok(FM.PALETTE[entry.role], "the palette has no colour for role " + entry.role);
  });
  dc.warehouse.cells.forEach(function (cell) {
    ok(fills[cell.type], "no fill for " + cell.type);
  });
  ok(fills.CONVEYOR !== fills.EMPTY && fills.WALKWAY !== fills.EMPTY, "the line and the walkway stand out");
});

test("the new floor's legend follows the cell-type table and ends on Route", function () {
  var expected = dc.cell_types.filter(function (e) { return e.legend; }).map(function (e) { return e.label; });
  expected.push("Route");
  eq(FM.legendItems(dc.cell_types, FM.PALETTE).map(function (item) { return item[0]; }), expected);
  ok(expected.indexOf("Racking") === -1, "classic-only types are not listed");
});

test("pallet racks and tote shelves are hatched like racking", function () {
  var roles = FM.cellRoles(dc.cell_types);
  eq(FM.cellPattern(roles.PALLET_RACK), "rack");
  eq(FM.cellPattern(roles.TOTE_SHELF), "rack");
  eq(FM.cellPattern(roles.RESTRICTED), "hazard");
  eq(FM.cellPattern(roles.CONVEYOR), null);
});

test("routes and plain aisles get no frame; the walkway's label stands upright", function () {
  var frames = {};
  FM.zoneFrames(dc.warehouse.zones).forEach(function (f) { frames[f.key] = f; });
  ["patrol_loop", "main_aisle", "cross_aisle", "top_aisle", "tote_aisle_1"].forEach(function (key) {
    ok(!frames[key], key + " has no frame");
  });
  ok(frames.walkway.vertical && frames.walkway.outline, "walkway");
  ok(!frames.pallet_racks.outline, "the rack rows are labelled but not boxed in");
  ok(frames.pack_cell_1.outline && !frames.pack_cell_1.vertical, "pack cell");
  ok(frames.restricted_area.restricted, "restricted");
});

test("each robot type has its letter and glyph", function () {
  var letters = {}, glyphs = {};
  dc.robots.forEach(function (robot) {
    letters[robot.name] = FM.robotLetter(robot);
    glyphs[robot.name] = FM.robotGlyph(robot);
  });
  eq(letters, {
    "TR50-201": "A", "PF1200-205": "F", "HH300-207": "H", "PK30-203": "K", "SC1-204": "S",
    "IX2-208": "D", "H1-212": "U", "CX10-210": "R"
  });
  eq(glyphs, {
    "TR50-201": "square", "PF1200-205": "forklift", "HH300-207": "wide", "PK30-203": "square",
    "SC1-204": "diamond", "IX2-208": "rotor", "H1-212": "round", "CX10-210": "arm"
  });
});

test("drones are drawn last, and not at all with the air layer hidden", function () {
  var shown = FM.robotLayers(dc.robots, true), hidden = FM.robotLayers(dc.robots, false);
  eq(shown.air.map(function (r) { return r.name; }), ["IX2-208"]);
  eq(named(dc.robots, "IX2-208").layer, "AIR");
  ok(shown.ground.every(function (r) { return !FM.isDrone(r); }), "no drone on the ground list");
  eq(shown.ground.length, dc.robots.length - 1);
  eq(hidden.air, []);
  eq(hidden.ground.length, dc.robots.length - 1);
});

test("a carried box shows its kind", function () {
  var amr = named(dc.robots, "TR50-201");
  var pallet = named(dc.boxes, "PAL-1"), carton = named(dc.boxes, "CARTON-1");
  eq(FM.kindBadge(amr, pallet), "P");
  eq(FM.kindBadge(amr, carton), "C");
  eq(FM.kindBadge(amr, { kind: "TOTE" }), "T");
  eq(FM.kindBadge(amr, { kind: "ITEM" }), "I");
  eq(FM.kindBadge(amr, null), null);
  eq([FM.kindLetter("CARTON"), FM.kindLetter("NOPE")], ["C", null]);
});

test("an arm shows its pack cell, the cells in its reach and its working cell", function () {
  var view = FM.armView(named(dc.robots, "CX10-210"), dc.equipment, dc.warehouse);
  eq(view.station, "pack_cell_1");
  eq(view.workCell, { x: 23, y: 15 });
  // 1300 mm reaches into every neighbouring cell (1.5 m cells), and no further.
  eq(cells(view.reach), ["22,13", "23,13", "24,13", "22,14", "24,14", "22,15", "23,15", "24,15"]);
  eq(view.paused, false);
});

test("an arm turns paused while it waits for a person to leave", function () {
  var arm = clone(named(dc.robots, "CX10-210"));
  arm.wait_reason = "PERSON_IN_CELL";
  var view = FM.armView(arm, dc.equipment, dc.warehouse);
  eq([view.paused, view.reason], [true, "PERSON_IN_CELL"]);
});

test("an arm with no reach on record still reaches the conveyor cell it works", function () {
  var arm = clone(named(dc.robots, "CX10-210"));
  arm.mobility.reach_mm = null;
  eq(FM.armView(arm, dc.equipment, dc.warehouse).reach, [{ x: 23, y: 15 }]);
});

test("only fixed arms have an arm view", function () {
  eq(FM.armView(named(dc.robots, "TR50-201"), dc.equipment, dc.warehouse), null);
  eq(FM.armView(named(dc.robots, "IX2-208"), dc.equipment, dc.warehouse), null);
});

test("the no-fly overlay covers the docks, the pack cells and the restricted area", function () {
  var noFly = cells(FM.noFlyCells(dc.no_fly_cells));
  eq(noFly.length, dc.no_fly_cells.length);
  ["2,3", "23,13", "28,7", "29,17"].forEach(function (key) { ok(noFly.indexOf(key) !== -1, key + " is no-fly"); });
  ["7,4", "20,2", "12,13"].forEach(function (key) { ok(noFly.indexOf(key) === -1, key + " may be flown"); });
  eq(FM.noFlyCells([[1, 2], "3,4", { x: 5, y: 6 }, null, "bad"]), [{ x: 1, y: 2 }, { x: 3, y: 4 }, { x: 5, y: 6 }]);
});

test("people stand in their zone, spread out from its centre", function () {
  var walkway = dc.warehouse.cells.filter(function (c) { return c.type === "WALKWAY"; });
  var dots = {};
  FM.peopleDots(dc.operators, dc.warehouse.zones, walkway).forEach(function (dot) { dots[dot.initials] = dot; });
  eq([dots.MS.x, dots.MS.y, dots.MS.inTransit], [23, 13, false], "Mateo, first in Pack 1, at its centre");
  eq([dots.RC.x, dots.RC.y, dots.RC.inTransit], [23, 12, false], "Riley, next to him");
  ok(!dots.SI, "Sasha is off the floor and not drawn");
});

test("a person on a walk is drawn on the walkway, at the cell nearest the middle of the walk", function () {
  var walkway = dc.warehouse.cells.filter(function (c) { return c.type === "WALKWAY"; });
  var jordan = FM.peopleDots(dc.operators, dc.warehouse.zones, walkway).filter(function (dot) {
    return dot.initials === "JB";
  })[0];
  // returns_qc (centre 22,7) to tote_aisle_1 (centre 12,13): the walk's middle is (17,10).
  eq([jordan.x, jordan.y, jordan.inTransit, jordan.zone, jordan.to], [17, 10, true, "returns_qc", "tote_aisle_1"]);
  var two = clone(dc.operators).filter(function (o) { return o.name === "Jordan Blake"; });
  two.push(clone(two[0]));
  two[1].id = "other";
  var both = FM.peopleDots(two, dc.warehouse.zones, walkway);
  ok(both[0].y !== both[1].y && both[1].x === 17, "two walkers take two walkway cells");
});

test("initials come from the first two names", function () {
  eq([FM.initials("Mateo Silva"), FM.initials("Sam"), FM.initials("  noor   haddad "), FM.initials("")],
    ["MS", "Sa", "NH", "?"]);
});

test("conveyor items and jammed cells come from the snapshot's equipment", function () {
  var view = FM.conveyorView(dc.equipment);
  eq(view.items, [
    { x: 20, y: 15, boxId: named(dc.boxes, "ITEM-1").id },
    { x: 22, y: 15, boxId: named(dc.boxes, "CARTON-1").id }
  ]);
  eq(view.jammed, [{ x: 23, y: 15 }]);
  eq(FM.conveyorView(null), { items: [], jammed: [] });
  eq(FM.conveyorView({}), { items: [], jammed: [] });
});

test("cells are read from every shape the backend uses", function () {
  eq([FM.toCell({ x: 1, y: 2 }), FM.toCell([3, 4]), FM.toCell("5,6"), FM.toCell(null), FM.toCell("x")],
    [{ x: 1, y: 2 }, { x: 3, y: 4 }, { x: 5, y: 6 }, null, null]);
});

print(passed + " passed, " + failed + " failed");
if (failed) throw new Error(failed + " floor model test(s) failed");
