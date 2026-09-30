/* Fleet & Workforce — the twin's fleet-manager and workforce source systems.
 *
 * Reads /api/fleet/* and /api/workforce/* (backend/inventory_api.py) and polls
 * every few seconds. Every change goes through a lifecycle-action endpoint, so
 * each one lands in the inventory's change feed exactly the way a passport
 * system ingests it. No framework; same styling as the control centre.
 */
(function () {
  "use strict";

  var POLL_MS = 3000;
  var FEED_MAX = 150;
  var LIFECYCLE = ["COMMISSIONING", "IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE", "DECOMMISSIONED"];
  var TONES = {
    VALID: "ok", IN_SERVICE: "ok", ONLINE: "ok", OK: "ok", ACTIVE: "ok", VERIFIED: "ok",
    DUE_SOON: "wait", EXPIRING_SOON: "wait", MAINTENANCE: "wait", DEGRADED: "wait", ON_LEAVE: "wait",
    INTERMITTENT: "wait", ROLLED_BACK: "wait",
    STAGED: "run", DOWNLOADING: "run", INSTALLING: "run", REPORTED: "run", OPEN: "run",
    COMMISSIONING: "info", NOT_YET_EFFECTIVE: "info",
    EXPIRED: "bad", MISSING: "bad", FAILED: "bad", REVOKED: "bad", FAULT: "bad", OFFLINE: "bad",
    OUT_OF_SERVICE: "bad",
    DECOMMISSIONED: "idle", TERMINATED: "idle", WORKER_INACTIVE: "idle", CLOSED: "idle", UNKNOWN: "idle"
  };

  var state = {
    view: "robots",
    robots: [], workers: [], models: [], releases: [], definitions: [],
    drawer: null,
    feed: [], cursors: { fleet: null, workforce: null }
  };

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(method, url, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) throw new Error(data.error || ("HTTP " + response.status));
        return data;
      });
    });
  }

  function toast(message, bad) {
    var node = $("toast");
    node.textContent = message;
    node.className = "toast" + (bad ? " bad" : "");
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { node.hidden = true; }, 3500);
  }

  function ago(iso) {
    if (!iso) return "never";
    var seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    if (seconds < 90) return seconds + "s ago";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    if (seconds < 172800) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  }

  function day(iso) { return iso ? String(iso).slice(0, 10) : "—"; }
  function words(text) { return String(text || "").replace(/_/g, " ").toLowerCase(); }
  function chip(text, tone) { return '<span class="chip ' + (tone || "idle") + '">' + esc(text) + "</span>"; }

  function statusChip(value) {
    if (!value) return '<span class="sub">—</span>';
    return chip(String(value).replace(/_/g, " "), TONES[value] || "info");
  }

  function shortHash(hash) { return hash ? String(hash).replace("sha256:", "").slice(0, 12) + "…" : "—"; }

  function kv(pairs) {
    return '<dl class="kv">' + pairs.map(function (pair) {
      var value = pair[1];
      var html = value && typeof value === "object" && "html" in value
        ? value.html : esc(value == null || value === "" ? "—" : value);
      return "<dt>" + esc(pair[0]) + "</dt><dd>" + html + "</dd>";
    }).join("") + "</dl>";
  }

  function section(title, body) {
    return '<section class="drawer-section"><h3>' + esc(title) + "</h3>" + body + "</section>";
  }

  function table(headers, rows, empty) {
    var head = headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("");
    var body = rows.length ? rows.join("")
      : '<tr><td colspan="' + headers.length + '" class="empty">' + esc(empty || "None.") + "</td></tr>";
    return '<div class="table-wrap"><table class="data-table"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  function options(values, selected) {
    return values.map(function (v) {
      var value = Array.isArray(v) ? v[0] : v;
      var label = Array.isArray(v) ? v[1] : v;
      return '<option value="' + esc(value) + '"' + (value === selected ? " selected" : "") + ">" + esc(label) + "</option>";
    }).join("");
  }

  function input(name, label, attrs) {
    return '<label class="field"><span>' + esc(label) + '</span><input name="' + esc(name) + '" aria-label="' + esc(label) + '" ' + (attrs || "") + " /></label>";
  }

  function select(name, label, opts) {
    return '<label class="field"><span>' + esc(label) + '</span><select name="' + esc(name) + '" aria-label="' + esc(label) + '">' + opts + "</select></label>";
  }

  function hidden(name, value) { return '<input type="hidden" name="' + esc(name) + '" value="' + esc(value) + '" />'; }

  /* A form the generic submit handler sends. `open` = "robot" | "worker" opens the result. */
  function form(method, url, label, fields, tone, open) {
    return '<form class="inline-form" data-method="' + method + '" data-url="' + esc(url) + '"' +
      (open ? ' data-open="' + open + '"' : "") + ">" + fields.join("") +
      '<button class="btn small' + (tone ? " " + tone : "") + '" type="submit">' + esc(label) + "</button></form>";
  }

  function describe(change) {
    return change ? change.aggregate_id + ": " + words(change.action) : "Saved";
  }

  function fmt(value) {
    if (value === null || value === undefined) return "∅";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function feedItem(change) {
    var keys = Object.keys(change.diff || {}).filter(function (k) {
      return !/(^|\.)(updated_at|revision|reported_at|observation_seq)$/.test(k);
    });
    var summary = keys.slice(0, 3).map(function (k) {
      return k + ": " + fmt(change.diff[k].from) + " → " + fmt(change.diff[k].to);
    }).join("; ") + (keys.length > 3 ? "  (+" + (keys.length - 3) + " more)" : "");
    var who = change.actor && change.actor.id ? ' <span class="sub">by ' + esc(change.actor.id) + "</span>" : "";
    var why = change.reason ? ' <span class="sub">— ' + esc(change.reason) + "</span>" : "";
    return '<li><span class="sub">' + esc(ago(change.occurred_at)) + '</span><span class="mono">' +
      esc(change.aggregate_id) + '</span><span><span class="what">' + esc(words(change.action)) + "</span>" +
      who + why + '<div class="diff">' + esc(summary) + "</div></span></li>";
  }

  function releaseLabel(release) {
    return release.version + (release.status === "CURRENT" ? " (current)" : " (" + words(release.status) + ")");
  }

  // ------------------------------------------------------------------ lists
  function matches(text, needle) { return !needle || String(text).toLowerCase().indexOf(needle) !== -1; }

  function renderRobots() {
    var sites = {};
    state.robots.forEach(function (r) { sites[r.site_code] = true; });
    var siteSelect = $("robotSite");
    var siteList = Object.keys(sites).sort();
    if (siteSelect.dataset.sites !== siteList.join(",")) {
      var current = siteSelect.value;
      siteSelect.innerHTML = '<option value="">All sites</option>' + options(siteList, current);
      siteSelect.dataset.sites = siteList.join(",");
    }
    var needle = $("robotSearch").value.trim().toLowerCase();
    var site = siteSelect.value;
    var status = $("robotStatus").value;
    var rows = state.robots.filter(function (r) {
      return (!site || r.site_code === site) && (!status || r.lifecycle_status === status) &&
        matches(r.asset_id + " " + r.serial_number + " " + r.model_name + " " + r.model_code, needle);
    }).map(function (r) {
      var f = r.flags;
      var software = '<span class="mono">' + esc(r.declared_software_version) + "</span>";
      if (f.software_mismatch) software += " " + chip("reports " + (r.reported_software_version || "?"), "bad");
      if (f.running_recalled_release) software += " " + chip("recalled", "bad");
      if (f.active_ota_job) software += " " + statusChip(f.active_ota_job.state);
      var report = r.lifecycle_status === "DECOMMISSIONED" ? '<span class="sub">retired</span>'
        : (f.report_stale ? chip("stale", "bad") : '<span class="sub">' + esc(ago(r.reported_at)) + "</span>") +
          " " + statusChip(r.connectivity);
      return '<tr class="clickable" data-asset="' + esc(r.asset_id) + '"><td class="mono">' + esc(r.asset_id) +
        "</td><td>" + esc(r.model_name) + '<div class="sub">' + esc(r.manufacturer_name) + " · " + esc(r.embodiment_class) +
        '</div></td><td class="mono">' + esc(r.serial_number) + "</td><td>" + esc(r.site_code) + "</td><td>" +
        statusChip(r.lifecycle_status) + "</td><td>" + software + "</td><td>" + statusChip(f.calibration_worst) +
        "</td><td>" + report + "</td></tr>";
    });
    $("robotTable").querySelector("tbody").innerHTML = rows.join("") ||
      '<tr><td colspan="8" class="empty">No robots match.</td></tr>';
    $("robotCount").textContent = state.robots.length;
  }

  function renderWorkers() {
    var needle = $("workerSearch").value.trim().toLowerCase();
    var status = $("workerStatus").value;
    var rows = state.workers.filter(function (w) {
      return (!status || w.employment_status === status) &&
        matches(w.worker_id + " " + w.display_name + " " + w.role_codes.join(" "), needle);
    }).map(function (w) {
      var credentials = w.credentials.map(function (c) { return chip(words(c.code), TONES[c.validity] || "info"); }).join(" ");
      return '<tr class="clickable" data-worker="' + esc(w.worker_id) + '"><td class="mono">' + esc(w.worker_id) +
        "</td><td>" + esc(w.display_name) + "</td><td>" + esc(words(w.worker_type)) + '<div class="sub">' +
        esc(w.organization) + "</div></td><td>" + esc(w.role_codes.join(", ")) + "</td><td>" + esc(w.site_codes.join(", ")) +
        "</td><td>" + statusChip(w.employment_status) + '</td><td><div class="chips">' + (credentials || '<span class="sub">none</span>') +
        "</div></td></tr>";
    });
    $("workerTable").querySelector("tbody").innerHTML = rows.join("") ||
      '<tr><td colspan="7" class="empty">No workers match.</td></tr>';
    $("workerCount").textContent = state.workers.length;
  }

  function renderFeed() {
    $("changeFeed").innerHTML = state.feed.map(feedItem).join("") || '<li class="sub">No changes yet.</li>';
  }

  // ----------------------------------------------------------- robot drawer
  function robotDrawer(robot, history) {
    var f = robot.flags;
    var reported = robot.reported || {};
    var base = "/api/fleet/robots/" + encodeURIComponent(robot.asset_id);
    var live = robot.lifecycle_status !== "DECOMMISSIONED";
    var floor = robot.floor_robot;

    var identity = kv([
      ["Serial number", robot.serial_number], ["Asset tag", robot.asset_tag],
      ["Model", robot.model.name + " · " + robot.model_code], ["Manufacturer", robot.manufacturer_name],
      ["Class", robot.model.embodiment_class], ["Hardware revision", robot.hw_revision],
      ["Fleet", robot.fleet_id], ["Site / home zone", robot.site_code + " / " + (robot.home_zone || "—")],
      ["Commissioned", day(robot.commissioned_at)], ["Lifecycle", { html: statusChip(robot.lifecycle_status) }],
      ["Record revision", robot.revision],
      ["On this floor as", floor ? floor.name + " — " + (floor.ota_installing ? "UPDATING" : floor.status) : "not on the simulated floor"]
    ]);
    var lifecycle = live ? form("POST", base + "/status", "Set status", [
      select("status", "Lifecycle", options(["IN_SERVICE", "MAINTENANCE", "OUT_OF_SERVICE"].filter(function (s) {
        return s !== robot.lifecycle_status;
      }))),
      input("reason", "Reason", 'required placeholder="why"')
    ]) + form("POST", base + "/decommission", "Decommission", [
      input("reason", "Reason", 'required placeholder="why it is retired"')
    ], "estop") : "";

    var spec = robot.model.spec || {};
    var specPairs = Object.keys(spec).map(function (key) {
      var value = spec[key];
      if (value && typeof value === "object" && !Array.isArray(value)) {
        value = Object.keys(value).map(function (k) { return words(k) + " " + value[k]; }).join(", ");
      } else if (Array.isArray(value)) {
        value = value.join(", ");
      }
      return [words(key), value];
    });
    specPairs.push(["safety standards", (robot.model.safety_standards || []).join(", ")]);

    var mismatch = function (bad) { return bad ? chip("mismatch", "bad") : chip("match", "ok"); };
    var softwareRows = [
      "<tr><td>Robot software</td><td class=\"mono\">" + esc(robot.declared_software_version) + '</td><td class="mono">' +
        esc(reported.software_version) + "</td><td>" + mismatch(f.software_mismatch) +
        (f.running_recalled_release ? " " + chip("recalled", "bad") : "") +
        (f.running_unknown_software ? " " + chip("unknown build", "bad") : "") + "</td></tr>"
    ];
    if (robot.declared_ai_policy_version || reported.ai_policy_version) {
      softwareRows.push("<tr><td>AI policy model</td><td class=\"mono\">" + esc(robot.declared_ai_policy_version) +
        '</td><td class="mono">' + esc(reported.ai_policy_version) + "</td><td>" + mismatch(f.ai_policy_mismatch) + "</td></tr>");
    }
    softwareRows.push('<tr><td>Operating system</td><td class="sub">—</td><td class="mono">' + esc(reported.os_version) + "</td><td></td></tr>");
    softwareRows.push('<tr><td>Config hash</td><td class="mono">' + esc(shortHash(robot.config_hash)) + '</td><td class="mono">' +
      esc(shortHash(reported.config_hash)) + "</td><td>" + mismatch(robot.config_hash !== reported.config_hash) + "</td></tr>");
    softwareRows.push('<tr><td>Safety policy hash</td><td class="mono">' + esc(shortHash(robot.safety_policy_hash)) + '</td><td class="mono">' +
      esc(shortHash(reported.safety_policy_hash)) + "</td><td>" + mismatch(robot.safety_policy_hash !== reported.safety_policy_hash) + "</td></tr>");

    var robotReleases = state.releases.filter(function (r) {
      return r.target_code === robot.model_code && r.status !== "RECALLED";
    }).map(function (r) {
      return [r.release_id, (r.kind === "AI_POLICY_MODEL" ? "AI policy " : "Software ") + releaseLabel(r)];
    });
    var otaForm = live && robotReleases.length ? form("POST", base + "/ota", "Start OTA", [
      select("release_id", "Release", options(robotReleases))
    ]) : "";
    var jobs = robot.ota_jobs.map(function (job) {
      var jobUrl = "/api/fleet/ota/" + encodeURIComponent(job.job_id);
      var actions = job.state !== "REPORTED" ? "" : '<div class="row-actions">' +
        form("POST", jobUrl + "/verify", "Verify", [input("verified_by", "Verified by", 'placeholder="E-10003"')]) +
        form("POST", jobUrl + "/rollback", "Roll back", [input("reason", "Reason", 'required placeholder="reason"')]) + "</div>";
      return '<tr><td class="mono">' + esc(job.job_id) + "</td><td>" + esc(job.slot ? words(job.slot) : words(job.kind)) +
        '</td><td class="mono">' + esc(job.from_version) + " → " + esc(job.version) + "</td><td>" + statusChip(job.state) +
        (job.failure_reason ? '<div class="sub">' + esc(job.failure_reason) + "</div>" : "") + "</td><td>" + actions + "</td></tr>";
    });

    var components = robot.components.map(function (c) {
      var firmware = c.declared_firmware_version
        ? '<span class="mono">' + esc(c.declared_firmware_version) + "</span>" +
          (c.reported_firmware_version !== c.declared_firmware_version ? " " + chip("reports " + (c.reported_firmware_version || "?"), "bad") : "")
        : '<span class="sub">n/a</span>';
      var latest = c.calibrations && c.calibrations[0];
      var calibration = c.calibration_status
        ? statusChip(c.calibration_status) + (latest && latest.valid_until ? '<div class="sub">until ' + esc(day(latest.valid_until)) + "</div>" : "")
        : '<span class="sub">n/a</span>';
      var actions = [];
      if (live && c.calibration_status) {
        actions.push(form("POST", "/api/fleet/components/" + encodeURIComponent(c.component_id) + "/calibrations", "Calibrate", [
          select("result", "Result", options(["PASS", "FAIL"])),
          input("performed_by", "By", 'placeholder="E-10003"')
        ]));
      }
      var partReleases = state.releases.filter(function (r) { return r.target_code === c.part_number && r.status !== "RECALLED"; });
      if (live && partReleases.length) {
        actions.push(form("POST", base + "/ota", "Flash", [
          select("release_id", "Firmware", options(partReleases.map(function (r) { return [r.release_id, releaseLabel(r)]; }))),
          hidden("component_id", c.component_id)
        ]));
      }
      return "<tr><td>" + esc(words(c.slot)) + "</td><td>" + esc(c.part_name) + '<div class="sub mono">' + esc(c.part_number) +
        " · rev " + esc(c.hw_revision) + '</div></td><td class="mono">' + esc(c.serial) + "</td><td>" + firmware +
        "</td><td>" + calibration + '</td><td><div class="row-actions">' + actions.join("") + "</div></td></tr>";
    });

    var slots = robot.components.map(function (c) { return [c.slot, words(c.slot)]; });
    var workOrders = robot.work_orders.map(function (wo) {
      var woUrl = "/api/fleet/work-orders/" + encodeURIComponent(wo.wo_id);
      var actions = wo.status !== "OPEN" ? "" : '<div class="row-actions">' +
        form("POST", woUrl + "/swap", "Swap part", [
          select("slot", "Slot", options(slots)), input("performed_by", "By", 'placeholder="E-10003"'),
          input("hw_revision", "HW rev", 'placeholder="latest"')
        ]) +
        form("POST", woUrl + "/close", "Close", [input("resolution", "Resolution", 'required placeholder="what was done"')]) + "</div>";
      return '<tr><td class="mono">' + esc(wo.wo_id) + "</td><td>" + esc(words(wo.type)) + "</td><td>" + esc(wo.description) +
        '<div class="sub">' + esc(day(wo.opened_at)) + (wo.technician_id ? " · " + esc(wo.technician_id) : "") +
        (wo.resolution ? " · " + esc(wo.resolution) : "") + "</div></td><td>" + statusChip(wo.status) + "</td><td>" + actions + "</td></tr>";
    });
    var openWorkOrder = live ? form("POST", base + "/work-orders", "Open work order", [
      select("type", "Type", options(["CORRECTIVE", "PREVENTIVE", "INSPECTION"])),
      input("description", "Description", 'required placeholder="what needs doing"'),
      input("technician_id", "Technician", 'placeholder="E-10003"')
    ]) : "";

    var battery = reported.battery;
    var reportedState = kv([
      ["Health", { html: statusChip(reported.health_state) }],
      ["Connectivity", { html: statusChip(reported.connectivity) }],
      ["Operational mode", reported.operational_mode], ["Zone", reported.zone],
      ["Battery", battery ? battery.soc_pct + "% charge · " + battery.soh_pct + "% health · " + battery.cycle_count + " cycles" : "mains powered"],
      ["Last report", ago(reported.reported_at) + (f.report_stale ? " (stale)" : "")]
    ]);

    var correction = live ? form("PATCH", base, "Save correction", [
      input("fleet_id", "Fleet", 'placeholder="' + esc(robot.fleet_id || "") + '"'),
      input("home_zone", "Home zone", 'placeholder="' + esc(robot.home_zone || "") + '"'),
      input("asset_tag", "Asset tag", 'placeholder="' + esc(robot.asset_tag) + '"'),
      input("reason", "Reason", 'required placeholder="why"')
    ]) : "";

    return section("Identity", identity + lifecycle) +
      section("Spec sheet", kv(specPairs)) +
      section("Software — declared vs reported", table(["", "Declared", "Reported", ""], softwareRows) + otaForm +
        table(["Job", "Target", "Version", "State", ""], jobs, "No updates yet.")) +
      section("Components", table(["Slot", "Part", "Serial", "Firmware", "Calibration", ""], components)) +
      section("Maintenance", table(["Work order", "Type", "Description", "Status", ""], workOrders, "No work orders.") + openWorkOrder) +
      section("Last report from the robot", reportedState) +
      section("Registry correction", '<p class="sub">Admin edits are logged with their reason.</p>' + correction) +
      section("History", '<ul class="change-feed">' + history.map(feedItem).join("") + "</ul>");
  }

  // ---------------------------------------------------------- worker drawer
  function workerDrawer(worker, history) {
    var base = "/api/workforce/workers/" + encodeURIComponent(worker.worker_id);
    var identity = kv([
      ["Type", words(worker.worker_type)], ["Organisation", worker.organization],
      ["Roles", worker.role_codes.join(", ")], ["Sites", worker.site_codes.join(", ")],
      ["Supervisor", worker.supervisor_id], ["Employment", { html: statusChip(worker.employment_status) }],
      ["Valid credentials", worker.valid_credential_codes.join(", ")], ["Record revision", worker.revision]
    ]);
    var statusForm = form("POST", base + "/employment-status", "Set status", [
      select("status", "Employment", options(["ACTIVE", "ON_LEAVE", "TERMINATED"].filter(function (s) {
        return s !== worker.employment_status;
      }))),
      input("reason", "Reason", 'required placeholder="why"')
    ]);
    var updateForm = form("PATCH", base, "Update", [
      input("role_codes", "Roles", 'data-list="1" placeholder="' + esc(worker.role_codes.join(", ")) + '"'),
      input("site_codes", "Sites", 'data-list="1" placeholder="' + esc(worker.site_codes.join(", ")) + '"'),
      input("supervisor_id", "Supervisor", 'placeholder="' + esc(worker.supervisor_id || "E-…") + '"'),
      input("reason", "Reason", 'required placeholder="why"')
    ]);
    var credentials = worker.credentials.map(function (c) {
      var url = "/api/workforce/credentials/" + encodeURIComponent(c.credential_id);
      var actions = c.verification_status === "REVOKED" ? "" : '<div class="row-actions">' +
        form("POST", url + "/renew", "Renew", [input("expires_at", "New expiry", 'type="date"')]) +
        form("POST", url + "/verify", "Verify", [select("verification_status", "Level", options(
          ["ISSUER_VERIFIED", "SOURCE_VERIFIED", "UNVERIFIED"].filter(function (s) { return s !== c.verification_status; })))]) +
        form("POST", url + "/revoke", "Revoke", [input("reason", "Reason", 'required placeholder="reason"')], "estop") + "</div>";
      var scope = [].concat(c.equipment_scope || [], c.site_scope || [], c.task_scope || []).join(", ");
      return "<tr><td>" + esc(c.name) + '<div class="sub mono">' + esc(c.credential_id) + "</div></td><td>" + esc(c.issuer) +
        '<div class="sub">' + esc(words(c.verification_status)) + "</div></td><td>" + statusChip(c.validity) +
        '<div class="sub">' + esc(day(c.effective_from)) + " → " + esc(day(c.expires_at)) + "</div></td><td>" +
        esc(scope || "any") + "</td><td>" + actions + "</td></tr>";
    });
    var issue = form("POST", base + "/credentials", "Issue credential", [
      select("code", "Credential", options(state.definitions.map(function (d) { return [d.code, d.name]; }))),
      input("issuer", "Issuer", 'placeholder="Site Training Office"'),
      input("expires_at", "Expires", 'type="date"'),
      input("equipment_scope", "Equipment", 'data-list="1" placeholder="NW-PF1200"'),
      input("site_scope", "Sites", 'data-list="1" placeholder="WH-01"')
    ]);
    var training = worker.training.map(function (t) {
      return "<tr><td>" + esc(t.course_code) + " v" + esc(t.course_version) + "</td><td>" + esc(day(t.completed_at)) +
        "</td><td>" + statusChip(t.validity) + '<div class="sub">until ' + esc(day(t.expires_at)) + "</div></td></tr>";
    });
    var trainingForm = form("POST", base + "/training", "Record training", [
      input("course_code", "Course", 'required placeholder="LOTO-101"'),
      input("course_version", "Version", 'placeholder="1"'),
      input("expires_at", "Expires", 'type="date"')
    ]);
    return section("Identity", identity + statusForm + updateForm) +
      section("Credentials", table(["Credential", "Issuer", "Validity", "Scope", ""], credentials, "No credentials.") + issue) +
      section("Training", table(["Course", "Completed", "Validity"], training, "No training records.") + trainingForm) +
      section("History", '<ul class="change-feed">' + history.map(feedItem).join("") + "</ul>");
  }

  function commissionDrawer() {
    return section("Commission a robot",
      '<p class="sub">Registers a new asset from a catalog model: its components, factory calibrations and first report are created the way a real commissioning would.</p>' +
      form("POST", "/api/fleet/robots", "Commission", [
        select("model_code", "Model", options(state.models.map(function (m) {
          return [m.model_code, m.manufacturer_name + " " + m.name + " (" + m.embodiment_class + ")"];
        }))),
        input("site_code", "Site", 'required value="WH-02"'),
        input("home_zone", "Home zone", 'placeholder="dock_1"'),
        input("fleet_id", "Fleet", 'placeholder="WH-02-FLEET"')
      ], "primary", "robot"));
  }

  function registerDrawer() {
    return section("Register a worker",
      '<p class="sub">Only qualification-relevant fields exist: anything else is refused by the API.</p>' +
      form("POST", "/api/workforce/workers", "Register", [
        input("display_name", "Name", "required"),
        select("worker_type", "Type", options(["EMPLOYEE", "CONTRACTOR", "PARTNER"])),
        input("organization", "Organisation", 'placeholder="Warehouse Operations"'),
        input("role_codes", "Roles", 'data-list="1" placeholder="WAREHOUSE_OPERATOR"'),
        input("site_codes", "Sites", 'data-list="1" placeholder="WH-01"')
      ], "primary", "worker"));
  }

  // ---------------------------------------------------------------- drawer
  function show(title, html) {
    var body = $("drawerBody");
    var scroll = body.scrollTop;
    $("drawerTitle").textContent = title;
    body.innerHTML = html;
    body.scrollTop = scroll;
    $("drawer").hidden = false;
  }

  function drawerDirty() {
    var drawer = $("drawer");
    if (drawer.hidden) return false;
    if (drawer.contains(document.activeElement) && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return true;
    return Array.prototype.some.call(drawer.querySelectorAll("input:not([type=hidden])"), function (el) {
      return el.value !== el.defaultValue;
    });
  }

  function renderDrawer() {
    var d = state.drawer;
    if (!d) return Promise.resolve();
    if (d.kind === "commission") { show("Commission robot", commissionDrawer()); return Promise.resolve(); }
    if (d.kind === "register") { show("Register worker", registerDrawer()); return Promise.resolve(); }
    var id = encodeURIComponent(d.id);
    var urls = d.kind === "robot"
      ? ["/api/fleet/robots/" + id, "/api/fleet/robots/" + id + "/history?limit=30"]
      : ["/api/workforce/workers/" + id, "/api/workforce/workers/" + id + "/history?limit=30"];
    return Promise.all(urls.map(function (url) { return api("GET", url); })).then(function (results) {
      if (!state.drawer || state.drawer.id !== d.id) return;
      if (d.kind === "robot") {
        var robot = results[0].robot;
        show(robot.asset_id + " · " + robot.model.name, robotDrawer(robot, results[1].history));
      } else {
        var worker = results[0].worker;
        show(worker.worker_id + " · " + worker.display_name, workerDrawer(worker, results[1].history));
      }
    });
  }

  function openDrawer(kind, id) {
    state.drawer = { kind: kind, id: id || null };
    $("drawerBody").scrollTop = 0;
    renderDrawer().catch(function (error) { toast(error.message, true); });
  }

  function closeDrawer() {
    state.drawer = null;
    $("drawer").hidden = true;
  }

  // --------------------------------------------------------------- actions
  function formPayload(formNode) {
    var payload = {};
    Array.prototype.forEach.call(formNode.elements, function (el) {
      if (!el.name) return;
      var value = String(el.value || "").trim();
      if (value === "") return;
      payload[el.name] = el.dataset.list
        ? value.split(",").map(function (part) { return part.trim(); }).filter(Boolean)
        : value;
    });
    return payload;
  }

  document.addEventListener("submit", function (event) {
    var formNode = event.target;
    if (!formNode.dataset || !formNode.dataset.url) return;
    event.preventDefault();
    var button = formNode.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    api(formNode.dataset.method || "POST", formNode.dataset.url, formPayload(formNode)).then(function (data) {
      toast(describe(data.change));
      if (formNode.dataset.open === "robot" && data.robot) state.drawer = { kind: "robot", id: data.robot.asset_id };
      if (formNode.dataset.open === "worker" && data.worker) state.drawer = { kind: "worker", id: data.worker.worker_id };
      return refresh(true);
    }).catch(function (error) {
      toast(error.message, true);
    }).then(function () {
      if (button) button.disabled = false;
    });
  });

  // ----------------------------------------------------------------- data
  function loadFeedPage(feed, pages) {
    var cursor = state.cursors[feed];
    var url = "/api/" + feed + "/changes?limit=200" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : "");
    return api("GET", url).then(function (page) {
      if (page.resync_required) state.feed = state.feed.filter(function (c) { return c.feed !== feed; });
      page.items.forEach(function (c) { c.feed = feed; state.feed.push(c); });
      state.cursors[feed] = page.next_cursor;
      if (page.has_more && pages < 20) return loadFeedPage(feed, pages + 1);
    });
  }

  function loadFeed() {
    return Promise.all([loadFeedPage("fleet", 0), loadFeedPage("workforce", 0)]).then(function () {
      state.feed.sort(function (a, b) {
        return String(b.occurred_at).localeCompare(String(a.occurred_at)) || b.seq - a.seq;
      });
      if (state.feed.length > FEED_MAX) state.feed.length = FEED_MAX;
      renderFeed();
    });
  }

  function loadList() {
    if (state.view === "workers") {
      return api("GET", "/api/workforce/workers").then(function (d) { state.workers = d.workers; renderWorkers(); });
    }
    return api("GET", "/api/fleet/robots").then(function (d) { state.robots = d.robots; renderRobots(); });
  }

  function refresh(force) {
    var jobs = [loadList(), loadFeed()];
    if (state.drawer && (force === true || !drawerDirty())) jobs.push(renderDrawer());
    return Promise.all(jobs).catch(function (error) { toast(error.message, true); });
  }

  function setView(view) {
    state.view = view;
    Array.prototype.forEach.call(document.querySelectorAll(".fleet-nav .tab"), function (tab) {
      tab.classList.toggle("active", tab.dataset.view === view);
    });
    ["robots", "workers", "changes"].forEach(function (name) { $("view-" + name).hidden = name !== view; });
    loadList().catch(function (error) { toast(error.message, true); });
  }

  // ----------------------------------------------------------------- init
  function init() {
    $("robotStatus").innerHTML = '<option value="">Any</option>' + options(LIFECYCLE);
    Array.prototype.forEach.call(document.querySelectorAll(".fleet-nav .tab"), function (tab) {
      tab.addEventListener("click", function () { setView(tab.dataset.view); });
    });
    $("robotTable").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-asset]");
      if (row) openDrawer("robot", row.dataset.asset);
    });
    $("workerTable").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-worker]");
      if (row) openDrawer("worker", row.dataset.worker);
    });
    ["robotSearch", "robotSite", "robotStatus"].forEach(function (id) { $(id).addEventListener("input", renderRobots); });
    ["workerSearch", "workerStatus"].forEach(function (id) { $(id).addEventListener("input", renderWorkers); });
    $("commissionBtn").addEventListener("click", function () { openDrawer("commission"); });
    $("registerBtn").addEventListener("click", function () { openDrawer("register"); });
    $("drawerClose").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeDrawer(); });

    Promise.all([
      api("GET", "/api/fleet/catalog/models"),
      api("GET", "/api/fleet/releases"),
      api("GET", "/api/workforce/credential-definitions")
    ]).then(function (results) {
      state.models = results[0].models;
      state.releases = results[1].releases;
      state.definitions = results[2].definitions;
      return refresh(true);
    }).catch(function (error) {
      toast(error.message, true);
    }).then(function () {
      setInterval(refresh, POLL_MS);
    });
  }

  init();
})();
