/* ==========================================================================
   Warehouse Digital Twin — floor model
   Pure, DOM-free helpers the dashboard draws the floor from (multi-embodiment
   spec §12): cell fills and the legend from the snapshot's cell_types table,
   robot letters and glyphs, the air layer, arms, people and the conveyor.
   Nothing here touches the page, so frontend/tests/*.js run it under
   JavaScriptCore (where there is no window); app.js reads it as
   window.FloorModel. Like app.js it is plain ES5.
   ========================================================================== */
(function (root) {
  "use strict";

  /* The dashboard palette. The classic floor's colours are exactly the ones
     the dashboard has always drawn it with (frontend/tests pins them); the
     rest colour the distribution-centre floor's cell roles, named as
     backend/layouts/base.py CELL_TYPE_STYLES names them. */
  var PALETTE = {
    floor: "#0e1417",
    grid: "#182126",
    wall: "#2c3a41",
    shelf: "#243138",
    shelfLine: "#33454e",
    storage: "#1d2a30",
    charging: "#16302f",
    loading: "#2c2716",
    unloading: "#252c1a",
    packing: "#231d2e",
    parking: "#1b2429",
    restricted: "#2b1e14",
    amber: "#ffb300",
    cyan: "#31d1c4",
    ok: "#7ddf64",
    warn: "#ff9538",
    fault: "#ff5252",
    ink: "#e7eef1",
    muted: "#7b8d95",
    faint: "#4d5f67",
    box: "#c98b3a",
    boxCarried: "#ffb300",
    boxDelivered: "#5f8f4f",
    dock: "#26301a",
    dockDoor: "#5a4614",
    staging: "#1c2b27",
    palletRack: "#283640",
    toteShelf: "#21303a",
    walkway: "#2e2b12",
    station: "#251f33",
    conveyor: "#2b3236",
    sorter: "#2c2338",
    workshop: "#2a2118",
    dronePad: "#14293a",
    noFly: "#9b8cff"
  };

  /* One letter per robot type (spec §12). */
  var LETTERS = {
    AMR: "A", FORKLIFT: "F", HEAVY_HAULER: "H", PICKER: "K",
    SCOUT: "S", DRONE: "D", ARM: "R", HUMANOID: "U"
  };

  /* The chassis shape app.js draws for each robot type. */
  var GLYPHS = {
    AMR: "square", PICKER: "square", FORKLIFT: "forklift", HEAVY_HAULER: "wide",
    SCOUT: "diamond", DRONE: "rotor", ARM: "arm", HUMANOID: "round"
  };

  var KIND_BADGES = { PALLET: "P", TOTE: "T", ITEM: "I", CARTON: "C" };

  /* Cell roles drawn with the rack hatching, and with the hazard stripes. */
  var RACK_ROLES = { shelf: true, palletRack: true, toteShelf: true };
  var HAZARD_ROLES = { restricted: true };

  /* 1 cell = 1.5 m on every floor (spec §3); an arm's reach is in mm. */
  var CELL_SIZE_M = 1.5;

  /* --------------------------------------------------------------- cells */
  /* {x, y} from a cell as the backend sends it ({x, y}), as a pair [x, y]
     or as "x,y"; null for anything else. */
  function toCell(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") {
      var parts = value.split(",");
      if (parts.length !== 2) return null;
      value = [Number(parts[0]), Number(parts[1])];
    }
    if (Object.prototype.toString.call(value) === "[object Array]") {
      if (value.length !== 2) return null;
      value = { x: value[0], y: value[1] };
    }
    if (typeof value.x !== "number" || typeof value.y !== "number" ||
        isNaN(value.x) || isNaN(value.y)) return null;
    return { x: value.x, y: value.y };
  }

  function sameCell(a, b) {
    return !!a && !!b && a.x === b.x && a.y === b.y;
  }

  function manhattan(a, b) {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  }

  /* ---------------------------------------------------------- cell types */
  /* {type: role} from the snapshot's cell_types table. */
  function cellRoles(cellTypes) {
    var roles = {};
    (cellTypes || []).forEach(function (entry) { roles[entry.type] = entry.role; });
    return roles;
  }

  /* {type: colour}: each cell type's role looked up in `colors`; a role the
     palette doesn't know draws as plain floor. */
  function cellFills(cellTypes, colors) {
    var fills = {};
    (cellTypes || []).forEach(function (entry) {
      fills[entry.type] = colors[entry.role] || colors.floor;
    });
    return fills;
  }

  /* [[label, colour]] for the types the table shows in the legend, in its
     order, then the planned-route swatch the dashboard has always ended on. */
  function legendItems(cellTypes, colors) {
    var items = [];
    (cellTypes || []).forEach(function (entry) {
      if (entry.legend) items.push([entry.label, colors[entry.role] || colors.floor]);
    });
    items.push(["Route", colors.amber]);
    return items;
  }

  /* "rack" (hatched shelving), "hazard" (striped) or null, by cell role. */
  function cellPattern(role) {
    if (RACK_ROLES[role]) return "rack";
    if (HAZARD_ROLES[role]) return "hazard";
    return null;
  }

  /* The zone outlines and labels to draw: every zone but routes (a patrol
     loop is a path, not a place) and plain aisles (EMPTY zones only name
     cells). A zone whose cells don't fill its bounding box (the rack rows)
     gets a label but no outline; a one-cell-wide strip (the walkway) gets
     its label turned upright. */
  function zoneFrames(zones) {
    var frames = [];
    (zones || []).forEach(function (zone) {
      if (zone.type === "EMPTY" || (zone.attributes && zone.attributes.route)) return;
      if (!zone.cells || !zone.cells.length) return;
      var xs = zone.cells.map(function (c) { return c.x; });
      var ys = zone.cells.map(function (c) { return c.y; });
      var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      frames.push({
        key: zone.key, label: zone.label, x0: x0, y0: y0, x1: x1, y1: y1,
        outline: zone.cells.length === (x1 - x0 + 1) * (y1 - y0 + 1),
        restricted: zone.type === "RESTRICTED",
        vertical: x1 === x0 && y1 > y0 + 1
      });
    });
    return frames;
  }

  /* The no-fly cells the snapshot lists, as {x, y}. */
  function noFlyCells(value) {
    var cells = [];
    (value || []).forEach(function (item) {
      var cell = toCell(item);
      if (cell) cells.push(cell);
    });
    return cells;
  }

  /* -------------------------------------------------------------- robots */
  function embodiment(robot) {
    return robot && robot.mobility ? robot.mobility.embodiment_class : null;
  }

  /* The robot's type letter, or null for a robot with no floor profile (a
     classic robot draws exactly as it always has, Plan ruling 10). */
  function robotLetter(robot) {
    var kind = embodiment(robot);
    return kind ? (LETTERS[kind] || null) : null;
  }

  /* The chassis shape to draw: "square" (classic robots, AMRs, pickers),
     "forklift", "wide", "diamond", "rotor", "arm" or "round". */
  function robotGlyph(robot) {
    var kind = embodiment(robot);
    return (kind && GLYPHS[kind]) || "square";
  }

  /* A flying body (spec §5.1 movement AIR): the air layer's robots. */
  function isDrone(robot) {
    return !!(robot && robot.mobility && robot.mobility.movement === "AIR");
  }

  /* The robots to draw under the people and over them: drones go last, above
     everything, and only while the air layer is shown. */
  function robotLayers(robots, showAir) {
    var ground = [], air = [];
    (robots || []).forEach(function (robot) {
      if (isDrone(robot)) {
        if (showAir) air.push(robot);
      } else {
        ground.push(robot);
      }
    });
    return { ground: ground, air: air };
  }

  /* A box kind's letter: "P" pallet, "T" tote, "I" item, "C" carton. */
  function kindLetter(kind) {
    return KIND_BADGES[kind] || null;
  }

  /* The kind badge on the box a robot carries, or null: classic robots
     carry plain boxes, drawn as they always were. */
  function kindBadge(robot, box) {
    if (!robot || !robot.mobility || !box) return null;
    return kindLetter(box.kind);
  }

  /* ---------------------------------------------------------------- arms */
  /* What an arm shows (spec §12 Arms): its station (the pack-cell zone whose
     arm cell it stands on), the cells within its reach — any cell some point
     of which is within reach_mm of the arm's base, and always the conveyor
     cell it works — and whether it is paused (a safety wait: a person in its
     cell, or a jam upstream). Null for anything that isn't a fixed arm. */
  function armView(robot, equipment, layout) {
    if (!robot || !robot.mobility || robot.mobility.movement !== "FIXED") return null;
    var base = toCell(robot.position);
    if (!base) return null;
    var station = null;
    ((layout && layout.zones) || []).forEach(function (zone) {
      if (station === null && zone.attributes && sameCell(toCell(zone.attributes.arm_cell), base)) {
        station = zone.key;
      }
    });
    var reachM = robot.mobility.reach_mm ? robot.mobility.reach_mm / 1000 : 0;
    var span = Math.ceil(reachM / CELL_SIZE_M + 0.5);
    var width = layout && layout.width, height = layout && layout.height;
    var reach = [];
    for (var dy = -span; dy <= span; dy++) {
      for (var dx = -span; dx <= span; dx++) {
        if (!dx && !dy) continue;
        var x = base.x + dx, y = base.y + dy;
        if (x < 0 || y < 0 || (width && x >= width) || (height && y >= height)) continue;
        var gapX = Math.max(0, Math.abs(dx) * CELL_SIZE_M - CELL_SIZE_M / 2);
        var gapY = Math.max(0, Math.abs(dy) * CELL_SIZE_M - CELL_SIZE_M / 2);
        if (Math.sqrt(gapX * gapX + gapY * gapY) <= reachM) reach.push({ x: x, y: y });
      }
    }
    var work = station && equipment && equipment.arm_cells ? toCell(equipment.arm_cells[station]) : null;
    if (work && !reach.some(function (cell) { return sameCell(cell, work); })) reach.push(work);
    return {
      station: station,
      reach: reach,
      workCell: work,
      paused: !!robot.wait_reason,
      reason: robot.wait_reason || null
    };
  }

  /* -------------------------------------------------------------- people */
  /* Two letters for a person's dot: "Mateo Silva" → "MS", "Sam" → "Sa". */
  function initials(name) {
    var words = String(name || "").replace(/^\s+|\s+$/g, "").split(/\s+/);
    if (!words[0]) return "?";
    if (words.length === 1) {
      return words[0].charAt(0).toUpperCase() + words[0].charAt(1).toLowerCase();
    }
    return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
  }

  /* A zone's cells, nearest its centre first (then by row, then column), so
     the people standing in it spread out from the middle. */
  function cellsFromCentre(zone) {
    var centre = toCell(zone.center) || toCell(zone.cells[0]);
    return zone.cells.map(toCell).filter(Boolean).sort(function (a, b) {
      return manhattan(a, centre) - manhattan(b, centre) || a.y - b.y || a.x - b.x;
    });
  }

  /* Where each person on the floor is drawn (spec §12 People): a person in a
     zone stands on one of its cells, the k-th person in a zone on the k-th
     cell from its centre; a person walking between two zones (transit_to
     set) is on the walkway, at the walkway cell nearest the midpoint of the
     walk, one walker a cell. Off the floor (no zone) is not drawn. */
  function peopleDots(operators, zones, walkwayCells) {
    var byKey = {};
    (zones || []).forEach(function (zone) { byKey[zone.key] = zone; });
    var walkway = (walkwayCells || []).map(toCell).filter(Boolean);
    var inZone = {}, taken = {};
    var dots = [];
    (operators || []).forEach(function (person) {
      var from = person.zone ? byKey[person.zone] : null;
      if (!from || !from.cells || !from.cells.length) return;
      if (person.transit_to) {
        var to = byKey[person.transit_to] || from;
        var a = toCell(from.center), b = toCell(to.center);
        var middle = { x: Math.round((a.x + b.x) / 2), y: Math.round((a.y + b.y) / 2) };
        var spot = middle, best = null;
        walkway.forEach(function (cell) {
          var key = cell.x + "," + cell.y;
          var score = manhattan(cell, middle) + (taken[key] ? 1000 : 0);
          if (best === null || score < best) { best = score; spot = cell; }
        });
        taken[spot.x + "," + spot.y] = true;
        dots.push({ id: person.id, initials: initials(person.name), x: spot.x, y: spot.y,
          inTransit: true, zone: person.zone, to: person.transit_to });
        return;
      }
      var cells = cellsFromCentre(from);
      var k = inZone[from.key] || 0;
      inZone[from.key] = k + 1;
      var cell = cells[k % cells.length];
      dots.push({ id: person.id, initials: initials(person.name), x: cell.x, y: cell.y,
        inTransit: false, zone: person.zone, to: null });
    });
    return dots;
  }

  /* ------------------------------------------------------------ conveyor */
  /* The items riding the line and the jammed cells, from the snapshot's
     equipment (twin.equipment.to_dict(): conveyor.items[*].cell / box_id and
     conveyor.jams[*].cell). No equipment (classic) gives an empty line. */
  function conveyorView(equipment) {
    var line = equipment && equipment.conveyor;
    var view = { items: [], jammed: [] };
    if (!line) return view;
    (line.items || []).forEach(function (item) {
      var cell = toCell(item.cell);
      if (cell) view.items.push({ x: cell.x, y: cell.y, boxId: item.box_id });
    });
    (line.jams || []).forEach(function (jam) {
      var cell = toCell(jam.cell);
      if (cell) view.jammed.push(cell);
    });
    return view;
  }

  /* ------------------------------------------------------------- exports */
  root.FloorModel = {
    PALETTE: PALETTE,
    LETTERS: LETTERS,
    CELL_SIZE_M: CELL_SIZE_M,
    toCell: toCell,
    cellRoles: cellRoles,
    cellFills: cellFills,
    legendItems: legendItems,
    cellPattern: cellPattern,
    zoneFrames: zoneFrames,
    noFlyCells: noFlyCells,
    robotLetter: robotLetter,
    robotGlyph: robotGlyph,
    isDrone: isDrone,
    robotLayers: robotLayers,
    kindLetter: kindLetter,
    kindBadge: kindBadge,
    armView: armView,
    initials: initials,
    peopleDots: peopleDots,
    conveyorView: conveyorView
  };
})(typeof window !== "undefined" ? window : this);
