# Fleet & Workforce Inventory — Design (Sub-project A)

**Date:** 2026-09-30
**Status:** Approved in brainstorming (approach, data model); remaining sections decided on the
approved defaults at the user's request ("continue and start working").
**Repo:** `physical_ai/warehouse-digital-twin`
**Branch:** `feature/fleet-inventory`

---

## 0. Program context

Goal: simulate a **real-world physical AI system** — multiple robot embodiments and operations,
multi-layered — with a robot + worker inventory carrying real-world detail, a unified database,
a passport pipeline (change robot info → passport updates), and realistic per-job robot logs
(sensor data + decisions).

| Layer | What | Owner |
|---|---|---|
| 1. Physical operations | Multi-embodiment robots, workers, tasks | twin |
| 2. Robot runtime logs | Sensor streams + decision logs per job | twin |
| 3. Inventory (source systems) | Fleet manager + workforce/qualification system | **twin — this spec** |
| 4. Unified DB + passports | Connectors → identity resolution → unified record → signed passports | `physical-work-assurance` (PWA) |
| 5. Trust | Pre-execution gate, eval, authorization | twin + PWA |

**Decided:** PWA's Postgres is the single unified system of record and passport engine. The
twin plays the *source systems* — a fleet manager and a workforce system with their own
database, their own IDs, and a pull change feed shaped for PWA's connector contract
(`discover` / `fetch_changes(checkpoint)` / `normalize` / `acknowledge`, PRD §13.1).

**Sub-projects (approved order A → B → C → D → E → F):**

| # | Sub-project | Where |
|---|---|---|
| **A** | **Fleet & workforce inventory (this spec)** | twin |
| B | Twin → PWA ingestion; robot-change → passport re-mint (the triggers PWA's roadmap deferred in Phase 4b) | PWA (+ small twin API) |
| C | Multi-embodiment operations (see §9 for the decisions already made) | twin |
| D | Runtime telemetry & decision logs (sensor streams with noise/drift/faults; planner/behaviour/safety decisions) | twin (+ PWA observed state) |
| E | Conveyor & hand-off chains | twin |
| F | Closed trust loop (twin gate consults PWA; eval compares reported vs true) | both |

---

## 1. Scope of sub-project A

**In:** a robot *model catalog* (fictional OEM spec sheets), robot asset records with a
serialized component tree, calibration / maintenance / OTA history, robot-reported state kept
separate from declared state, a worker registry with credentials and training (PWA allowlist
only), lifecycle actions that each produce a revision + change event, a cursor-based change
feed, REST API, a Fleet & Workforce dashboard page, and the bridge binding live simulated
robots/operators to their inventory records.

**Out (deliberately):** spare-parts stock, suppliers, costs; PWA ingestion (B); physical
enforcement of catalog specs such as payload/reach/clearance (C); sensor streams (D);
automatic sim-generated lifecycle events (scheduled OTA campaigns, random faults); agent-chat
tools for the inventory.

**Behaviour neutrality:** A must not change how the existing simulation behaves. Robot speed and
allowed task types still come from `ROBOT_CLASS_PRESETS`; eligibility is unchanged. The only
runtime effects are the ones a lifecycle action explicitly causes (an OTA install changes the
running firmware; taking an asset out of service stops its robot; revoking a credential removes
it from the operator).

---

## 2. Architecture

```
backend/inventory/            ← the "source systems"; imports nothing from the twin
    schema.py                 DDL, connect(), schema version, meta (epoch)
    store.py                  InventoryStore: sqlite3 repository + change_log append
    documents.py              UTC time helpers, document flatten/diff
    core.py                   ServiceCore: clock, settings, transactions, change recording, feed
    catalog_data.py           fictional manufacturers, part models, robot models, releases
    sbom.py                   deterministic synthetic CycloneDX 1.5 JSON + sha256
    catalog.py / fleet.py / servicing.py / ota.py / workforce.py
                              InventoryService mixins, one per responsibility
    service.py                InventoryService composed from the mixins
    seed.py / demo_seed.py    seed the catalog; seed the demo fleet + workforce
    bootstrap.py              open_inventory(), reseed(), seeded-template cache
    errors.py                 NotFound(KeyError), Conflict(ValueError)
backend/fleet_bridge.py       FleetBridge: binds Robot↔asset, Operator↔worker; heartbeat;
                              OTA runtime effects; credential → operator.certifications sync
backend/inventory_api.py      register_inventory_routes(app, twin): /api/fleet/*, /api/workforce/*
frontend/fleet.html, fleet.js Fleet & Workforce page (linked from the main dashboard header)
```

- **Storage:** stdlib `sqlite3` (no new dependency). The app uses `data/inventory.sqlite3`;
  `DigitalTwin()` defaults to an in-memory database so tests stay isolated and fast. One
  connection per database (`check_same_thread=False`), all access serialized by the service's
  lock.
- **Transactions:** every lifecycle action is one SQLite transaction that writes the change, bumps
  the owning aggregate's `revision`, and appends a `change_log` row — the same
  transactional-outbox idea PWA uses.
- **Lock order (deadlock rule):** always `twin.lock` → inventory lock. API routes and the
  simulator call the inventory **only through `FleetBridge`**, which takes `twin.lock` first.
  Service callbacks to the twin run after the inventory transaction commits.
- **Isolation:** `backend/inventory/` never imports twin modules, so it can later be lifted out
  into a standalone enterprise-sim service unchanged.

---

## 3. Data model

All IDs are the source system's own (`AST-000123`, `E-10042`, …); PWA maps them via
`ExternalReference` in B.

### 3.1 Catalog (reference data — OEM spec sheets)

| Table | Key fields |
|---|---|
| `manufacturer` | `manufacturer_id`, `name` (fictional), `serial_prefix` |
| `robot_model` | `model_code`, `manufacturer_id`, `name`, `embodiment_class` (AMR, FORKLIFT, ARM, DRONE, HUMANOID, SCOUT, HEAVY_HAULER, PICKER), `hw_revisions[]`, `spec` JSON (movement mode, clearance class, max payload kg, max lift m / max shelf level, max speed m/s, footprint mm, mass kg, IP rating, battery chemistry + capacity Wh, runtime h, charge time h, supported box kinds, supervision requirement), `safety_standards[]`, `component_layout[]` (slot, part_number, required) |
| `part_model` | `part_number`, `manufacturer_id`, `name`, `component_type` (COMPUTE, SAFETY_CONTROLLER, DRIVE_UNIT, BATTERY, LIDAR, SAFETY_SCANNER, RGBD_CAMERA, IMU, LOAD_CELL, MAST_ENCODER, FORCE_TORQUE, SCANNER, LIGHT_CURTAIN, GRIPPER, FORKS, ROTOR, ACTUATOR), `hw_revisions[]`, `firmware_capable`, `calibration_interval_days` (null = no calibration), `spec` JSON |
| `software_release` | `release_id`, `kind` (ROBOT_SOFTWARE, COMPONENT_FIRMWARE, AI_POLICY_MODEL), `target_type` (MODEL, PART), `target_code`, `version`, `released_at`, `min_hw_rev`, `status` (CURRENT, SUPERSEDED, RECALLED), `sbom_json`, `sbom_sha256` |

The embodiment limits sketched earlier as per-class `EMBODIMENT_PROFILES` live in
`robot_model.spec`; sub-project C reads them from there.

### 3.2 Robot assets

| Table | Key fields |
|---|---|
| `robot_asset` | `asset_id`, `asset_tag`, `serial_number` (unique per manufacturer), `model_code`, `hw_revision`, `fleet_id`, `site_code`, `home_zone`, `lifecycle_status` (COMMISSIONING, IN_SERVICE, MAINTENANCE, OUT_OF_SERVICE, DECOMMISSIONED), `commissioned_at`, `decommissioned_at`, **declared** `software_release_id`, `config_hash`, `safety_policy_hash`, `ai_policy_release_id` (nullable), `revision`, `updated_at` |
| `component` | `component_id`, `asset_id` (kept after removal so part history stays linked; `status` says REMOVED), `slot`, `part_number`, `serial`, `hw_revision`, declared `firmware_release_id` (nullable), `installed_at`, `removed_at`, `status` (INSTALLED, REMOVED, FAULTY) |
| `calibration_record` | `calibration_id`, `component_id`, `performed_at`, `performed_by` (worker_id), `method`, `result` (PASS, FAIL), `valid_until` (null on FAIL), `certificate_ref` |
| `work_order` | `wo_id`, `asset_id`, `type` (PREVENTIVE, CORRECTIVE, INSPECTION), `description`, `technician_id`, `status` (OPEN, CLOSED), `opened_at`, `closed_at`, `resolution` |
| `ota_job` | `job_id`, `asset_id`, `component_id` (null = robot software / AI policy), `release_id`, `from_version`, `state`, `created_at`, `updated_at`, `created_by`, `failure_reason` (tick counts for the timed states are runtime-only, held by the bridge) |
| `reported_state` | `asset_id` (PK), `software_version`, `os_version`, `config_hash`, `safety_policy_hash`, `ai_policy_version`, `component_firmware` JSON (slot → version), `health_state` (OK, DEGRADED, FAULT, UNKNOWN), `connectivity` (ONLINE, OFFLINE, INTERMITTENT), `operational_mode`, `zone`, `battery` JSON (`soc_pct`, `soh_pct`, `cycle_count`), `reported_at`, `observation_seq` |

Refinement vs the brainstorm: battery `cycle_count` / `soh_pct` are *reported telemetry*, so
they live in `reported_state.battery`, not on the component row.

### 3.3 Workforce (PWA §26.1 allowlist only)

| Table | Key fields |
|---|---|
| `worker` | `worker_id`, `display_name` (synthetic), `worker_type` (EMPLOYEE, CONTRACTOR, PARTNER), `organization`, `role_codes[]`, `site_codes[]`, `employment_status` (ACTIVE, ON_LEAVE, TERMINATED), `supervisor_id` (nullable), `shift_start_hour` / `shift_end_hour` (twin-only; never exported), `revision`, `updated_at` |
| `credential_definition` | `code`, `name`, `issuer_types[]`, `renewal_period_days`, `scope_dimensions[]` |
| `worker_credential` | `credential_id`, `worker_id`, `code`, `issuer`, `credential_number_hash` (**never the raw number**), `verification_status` (UNVERIFIED, SOURCE_VERIFIED, ISSUER_VERIFIED, REVOKED), `effective_from`, `expires_at`, `equipment_scope[]`, `task_scope[]`, `site_scope[]`, `supervision_requirement`, `revoked_at`, `revocation_reason` |
| `training_completion` | `training_id`, `worker_id`, `course_code`, `course_version`, `completed_at`, `expires_at` |

Credential codes are the twin's existing lowercase certification strings
(`safety_inspection`, `electrical_safety`, `equipment_maintenance`, `heavy_equipment`,
`hazmat_handling`, `quality_control`) plus new ones: `forklift_operator`,
`humanoid_supervision`, `robot_cell_access`, `drone_operations`, `robot_maintenance`.

**Privacy:** there are no columns for compensation, performance, health, demographics or
discipline. `register_worker` / `admin_edit` reject any field not on the allowlist (a `ValueError`
naming the field), so a prohibited field can never be stored or logged.

### 3.4 Change tracking

`change_log`: `seq` (INTEGER PK AUTOINCREMENT — the feed cursor), `occurred_at`,
`aggregate_type` (ROBOT_ASSET, ROBOT_OBSERVATION, WORKER, CATALOG), `aggregate_id`, `revision`,
`action`, `subject_id` (the sub-record the action concerns: work order, OTA job, component,
calibration, credential, training or release id), `actor_type` (SYSTEM, WORKER, API),
`actor_id`, `reason`, `diff` JSON
(`{field: {"from": …, "to": …}}`), `after` JSON (full aggregate as it stands after the change).

`meta`: `schema_version`, `epoch` (random hex, regenerated whenever the database is re-seeded).

**Rules**
1. **Declared vs reported are separate** (`robot_asset` / `component` vs `reported_state`),
   mirroring PWA's `Robot` vs `RobotObservedState`. Mismatches arise naturally.
2. **One revision per aggregate.** Anything touching a robot's components, calibrations, work
   orders or OTA jobs bumps that robot's `revision`; credential/training changes bump the worker's.
   Observations use a separate per-asset `observation_seq`.
3. **Real wall-clock time**, seeded relative to "now" at seed time, so every demo shows some
   calibrations and credentials near expiry.
4. **The `after` snapshot** makes each change self-contained for PWA's `normalize()`.

---

## 4. Lifecycle actions & change feed

Every action: validate → mutate → bump revision → append `change_log` → commit → notify. The
`actor` is recorded (`WORKER`+id when a worker performed it, else `API` or `SYSTEM`).

### 4.1 Robot actions

| Action | Effect | change_log action |
|---|---|---|
| `commission_robot(model_code, site_code, home_zone, serial?, asset_tag?, hw_revision?, software_release_id?, asset_id?)` | Creates asset `IN_SERVICE` + components per the model's `component_layout` (generated serials, current firmware per part) + a PASS calibration at commissioning for each calibratable sensor | `COMMISSIONED` |
| `set_lifecycle_status(asset_id, status, reason)` | Legal: IN_SERVICE ↔ MAINTENANCE ↔ OUT_OF_SERVICE, COMMISSIONING → IN_SERVICE; DECOMMISSIONED only via `decommission` | `STATUS_CHANGED` |
| `decommission(asset_id, reason)` | Terminal; closes open OTA jobs as FAILED | `DECOMMISSIONED` |
| `start_ota(asset_id, release_id, component_id?)` | Job `STAGED`. Rejects: release not for this model/part, `RECALLED`, `min_hw_rev` above the asset's/component's revision, another active job for the same target, asset not IN_SERVICE/MAINTENANCE | `OTA_STAGED` |
| OTA progression (driven by simulator ticks — §5.3) | `STAGED → DOWNLOADING → INSTALLING → REPORTED`, or `FAILED` (`OTA_FAILURE_RISK`) | `OTA_DOWNLOADING`, `OTA_INSTALLING`, `OTA_REPORTED`, `OTA_FAILED` |
| `verify_ota(job_id, verified_by)` | `REPORTED → VERIFIED`; the declared release becomes the new one | `OTA_VERIFIED` |
| `rollback_ota(job_id, reason)` | `REPORTED → ROLLED_BACK`; the running version reverts to `from_version` | `OTA_ROLLED_BACK` |
| `record_calibration(component_id, performed_by, result, method?, certificate_ref?)` | Appends a record; `valid_until = performed_at + calibration_interval_days` on PASS | `CALIBRATION_RECORDED` |
| `open_work_order(asset_id, type, description, technician_id)` | PREVENTIVE/CORRECTIVE move the asset to MAINTENANCE | `WORK_ORDER_OPENED` |
| `swap_component(wo_id, slot, performed_by, hw_revision?)` | Needs an OPEN work order and the asset in MAINTENANCE; old component → REMOVED; new component with a new serial and the part's *current* firmware (may differ from the old one) and **no calibration** | `COMPONENT_SWAPPED` |
| `close_work_order(wo_id, resolution)` | Closes; the asset returns to IN_SERVICE when no other work order is open | `WORK_ORDER_CLOSED` |
| `report_state(asset_id, reported)` | Heartbeat. Always refreshes `reported_at`; writes a `ROBOT_OBSERVATION` change only when a reported field (other than `reported_at` and battery SoC) changed | `STATE_REPORTED` |
| `admin_edit(asset_id, fields, reason)` | Corrections to an allowlist: `asset_tag`, `fleet_id`, `site_code`, `home_zone`, `hw_revision`, `config_hash`, `safety_policy_hash`; `reason` is required | `ADMIN_EDIT` |

**OTA states:** `STAGED → DOWNLOADING → INSTALLING → REPORTED → VERIFIED | ROLLED_BACK`;
`FAILED` is reachable from DOWNLOADING/INSTALLING. Any other transition is a `Conflict`
(HTTP 409). Between `REPORTED` and `VERIFIED` the robot runs the new version while the
registry still declares the old one — the realistic "update landed but not verified" window
PWA will flag as `ROBOT_FIRMWARE_MISMATCH`.

### 4.2 Catalog actions

`publish_release(kind, target_type, target_code, version, min_hw_rev?)` (generates the SBOM;
the previous CURRENT release for that target becomes SUPERSEDED) and
`recall_release(release_id, reason)` → `CATALOG` changes (`RELEASE_PUBLISHED`,
`RELEASE_RECALLED`).

### 4.3 Worker actions

| Action | change_log action |
|---|---|
| `register_worker(worker_id?, display_name, worker_type, organization, role_codes, site_codes, supervisor_id?, shift?)` | `WORKER_REGISTERED` |
| `update_worker(worker_id, role_codes?, site_codes?, supervisor_id?, reason)` | `WORKER_UPDATED` |
| `set_employment_status(worker_id, status, reason)` | `EMPLOYMENT_STATUS_CHANGED` |
| `issue_credential(worker_id, code, issuer, effective_from?, expires_at?, scopes?, verification_status?)` | `CREDENTIAL_ISSUED` |
| `renew_credential(credential_id, expires_at)` | `CREDENTIAL_RENEWED` |
| `verify_credential(credential_id, verification_status)` | `CREDENTIAL_VERIFIED` |
| `revoke_credential(credential_id, reason)` | `CREDENTIAL_REVOKED` |
| `record_training(worker_id, course_code, course_version, completed_at?, expires_at?)` | `TRAINING_RECORDED` |

A credential is **valid** when it is not REVOKED, `effective_from ≤ now < expires_at` (or no
expiry), and the worker is ACTIVE.

### 4.4 Derived flags (computed on read, never stored)

Robot: `software_mismatch` (declared vs reported), `ai_policy_mismatch`,
`component_firmware_mismatches[]`, per-sensor `calibration_status` (VALID, DUE_SOON within
`CALIBRATION_DUE_SOON_DAYS`, EXPIRED, MISSING, FAILED) with `calibration_worst` and
`calibration_issues[]`, `report_stale` (older than `REPORT_STALE_SECONDS`),
`running_recalled_release`, `running_unknown_software` (a version with no catalog release),
`active_ota_job`. Worker: per-credential `validity` (VALID, EXPIRING_SOON within
`CREDENTIAL_EXPIRING_SOON_DAYS`, EXPIRED, REVOKED, NOT_YET_EFFECTIVE, WORKER_INACTIVE).

### 4.5 Change feed

`GET /api/fleet/changes?cursor=<epoch>:<seq>&limit=100` returns ROBOT_ASSET, ROBOT_OBSERVATION
and CATALOG changes; `GET /api/workforce/changes` returns WORKER changes — two feeds, modelling
two source systems (PWA's robot connector and HRIS connector).

```json
{ "epoch": "9f2c1a", "items": [ { "seq": 42, "occurred_at": "…", "aggregate_type": "ROBOT_ASSET",
  "aggregate_id": "AST-000101", "revision": 7, "action": "OTA_REPORTED",
  "actor": {"type": "SYSTEM", "id": null}, "reason": null, "diff": {…}, "after": {…} } ],
  "next_cursor": "9f2c1a:42", "has_more": false, "resync_required": false }
```

No cursor → from the start. A cursor from another epoch → `resync_required: true` and items
from the start of the current epoch (the source system was rebuilt). `limit` is capped at 500.

---

## 5. Integration with the simulation

### 5.1 Construction and persistence

- `DigitalTwin(..., inventory_path: str = ":memory:")`. `create_app()` passes
  `data/inventory.sqlite3`.
- Opening an existing file with the current `schema_version` reuses it (no re-seed); otherwise it
  is created and seeded. The **catalog is always seeded**; the demo fleet and workforce are seeded
  only when `demo=True`.
- `twin.reset()` wipes and re-seeds the inventory with a **new epoch**.
- Test speed: the seeded catalog + demo inventory is built once per process into a template
  in-memory database and copied into each new twin's database with `sqlite3.Connection.backup`.

### 5.2 Binding

- `Robot.asset_id` and `Operator.worker_id` (new, serialized in `to_dict` / `from_dict`).
- `add_robot(..., model_code=None, asset_id=None)`: an existing `asset_id` binds; otherwise an
  asset is commissioned from `model_code`, or from the class's default model
  (`CLASS_DEFAULT_MODELS`). A `model_code` whose class disagrees with `robot_class` is a
  `ValueError`. At bind, the runtime `Robot.firmware_version` and new `Robot.component_firmware`
  (slot → version) are set from the asset's reported state, falling back to declared.
- Demo robots `Robo-01` / `Robo-02` bind to fixed assets `AST-000101` / `AST-000102`
  (Acme TR-50, software `2.1.0` — the same firmware they run today, so behaviour is unchanged).
- `add_operator(name, certifications, …)`: registers a worker (or binds via `worker_id`) and
  issues one SOURCE_VERIFIED credential per certification (valid 365 days, in the given order).
  Demo `Sam` / `Lee` bind to `E-10001` / `E-10002`.
- `load_state` re-binds by id; if an asset or worker is missing (inventory reset), a new one is
  created and a WARNING is logged.
- `POST /api/robots` accepts an optional `model_code`; `POST /api/operators` is unchanged.

### 5.3 Runtime effects (all driven from `Simulator.tick`)

- **Heartbeat** every `FLEET_HEARTBEAT_EVERY_TICKS` (default 10): each floor robot reports
  `firmware_version`, `component_firmware`, health (`ERROR` → FAULT, a maintenance alert →
  DEGRADED, else OK), operational mode (its status), zone, battery (`soc_pct` = battery,
  `cycle_count` = charging sessions, `soh_pct` = seeded value minus 0.02 per cycle). Off-floor
  assets that are ONLINE only refresh `reported_at`; OFFLINE ones don't report at all (they go
  stale).
- **OTA progression** every tick: STAGED → DOWNLOADING immediately; DOWNLOADING lasts
  `OTA_DOWNLOAD_TICKS` (5); INSTALLING starts only when the floor robot is idle (no current
  task), lasts `OTA_INSTALL_TICKS` (8), and sets `Robot.ota_installing = True`
  (`is_available` → False, shown as UPDATING). On completion the running version changes on the
  `Robot` (software, AI policy or one component), or on `reported_state` directly for an
  off-floor asset. The next report moves the job to REPORTED. `OTA_FAILURE_RISK` (default 0.0,
  seedable like the other risk knobs) fails a job at install.
- **Lifecycle → dispatch:** a floor robot whose asset leaves IN_SERVICE is stopped through the
  existing `twin.stop_robot(reason="asset <status>")`, and resumed on return to IN_SERVICE.
- **Credentials → operator:** after any worker change, and on the existing
  `SHIFT_CHECK_EVERY_TICKS` cadence (to catch expiries), `operator.certifications` is recomputed
  as the codes of the worker's valid credentials in issuance order. The existing eligibility code
  is untouched and simply sees the result.
- New events: `INVENTORY_CHANGED` (INFO, carries `aggregate_id` + `action`),
  `OTA_JOB_UPDATED`, `OPERATOR_CERTIFICATIONS_CHANGED` (WARNING when a certification is lost).
  The dashboard's warning bell gets only failures (OTA_FAILED) and revocations / lost
  certifications. All inventory events use the new `FLEET` log category.

### 5.4 New CONFIG keys (policy-overridable)

`FLEET_HEARTBEAT_EVERY_TICKS: 10`, `OTA_DOWNLOAD_TICKS: 5`, `OTA_INSTALL_TICKS: 8`,
`OTA_FAILURE_RISK: 0.0`, `CALIBRATION_DUE_SOON_DAYS: 14`, `CREDENTIAL_EXPIRING_SOON_DAYS: 30`,
`REPORT_STALE_SECONDS: 300`.

---

## 6. Seed catalog and demo inventory

Fictional companies (Microsoft-style sample names, clearly not real vendors): **Acme Robotics**
(AMRs), **Northwind Lift Systems** (forklifts, haulers), **Contoso Aerial** (drones),
**Fabrikam Automation** (arms), **Tailspin Humanoids**, **Wingtip Sensors** (lidar, cameras,
scanners, IMUs), **Litware Compute** (onboard compute).

| Model | Class | Highlights |
|---|---|---|
| Acme TR-50 | AMR (default) | 50 kg totes, 2 shelf levels, lidar + 2 safety scanners + RGB-D + IMU, LFP 1.2 kWh, ANSI/A3 R15.08-1 |
| Acme SC-1 | SCOUT | inspection only, 360° camera + thermal |
| Acme PK-30 | PICKER | 30 kg, gripper + RGB-D |
| Northwind PF-1200 | FORKLIFT | 1,200 kg pallets, 4.5 m lift, load cell + mast encoder, lead-acid 24 V, ISO 3691-4 |
| Northwind HH-300 | HEAVY_HAULER | 300 kg, wide aisles |
| Contoso IX-2 | DRONE | no payload, RFID + barcode scanner, 22 min flight, LiPo 150 Wh |
| Fabrikam CX-10 | ARM | 10 kg, force-torque + wrist camera, light-curtain cell, ISO 10218-1 + ISO/TS 15066, AI grasp policy |
| Tailspin H-1 | HUMANOID | 25 kg totes, stereo cameras + IMU + joint actuators, AI locomotion/manipulation policy, supervision required |

**Releases:** each model has ROBOT_SOFTWARE releases with at least one SUPERSEDED, one CURRENT
and (for some) a RECALLED version. For the Acme models the versions are `2.0.4` (superseded,
unapproved), `2.1.0`, `2.1.1`, `2.2.0` (current) — consistent with the twin's existing
`APPROVED_FIRMWARE_VERSIONS`. Firmware-capable parts get COMPONENT_FIRMWARE releases; the arm
and the humanoid get AI_POLICY_MODEL releases. Each release carries a deterministic synthetic
CycloneDX 1.5 SBOM (OS kernel, libc, openssl, a ROS 2 client library, vendor packages).

**Demo inventory (`demo=True`):**
- Site **WH-01** (the simulated floor): `AST-000101` / `AST-000102` (Robo-01/02, TR-50,
  IN_SERVICE) and `AST-000103` (TR-50 in MAINTENANCE with an open CORRECTIVE work order: lidar
  swapped, calibration still missing).
- Site **WH-02** (inventory only, not on the floor): about 12 assets across every model,
  deliberately varied — one with reported ≠ declared software, one with an EXPIRED lidar
  calibration, one DUE_SOON, one running a RECALLED release, one OFFLINE with a stale report, one
  with an OTA job in REPORTED awaiting verification, one with battery SoH ~71%, one
  DECOMMISSIONED.
- Workers: `E-10001` Sam and `E-10002` Lee (the floor operators, their existing certifications
  as credentials) plus about 8 inventory-only workers — a forklift operator, a maintenance tech
  (who performs the seeded calibrations and work orders), a robotics technician, a humanoid
  supervisor, a contractor whose credential expires in 7 days, a worker with a REVOKED
  credential, one ON_LEAVE and one TERMINATED.
- Seeding is deterministic (fixed RNG seed); dates are relative to seed time.

---

## 7. API and dashboard

### 7.1 REST (in `backend/inventory_api.py`)

Errors: `NotFound` → 404, `Conflict` → 409, other `ValueError` → 400, same JSON shape as
`app.py`'s `ApiError`.

**Fleet**
- `GET /api/fleet/catalog/models`, `/api/fleet/catalog/parts`, `/api/fleet/releases`
  (`?target=`, `?status=`), `GET /api/fleet/releases/<id>/sbom`
- `GET /api/fleet/robots` (`?site=`, `?status=`), `GET /api/fleet/robots/<asset_id>` (asset +
  model spec + components + calibrations + work orders + OTA jobs + reported state + derived
  flags + bound floor robot), `GET /api/fleet/robots/<asset_id>/history`
- `POST /api/fleet/robots` (commission), `PATCH /api/fleet/robots/<asset_id>` (admin edit),
  `POST …/<asset_id>/status`, `POST …/<asset_id>/decommission`, `POST …/<asset_id>/ota`
- `POST /api/fleet/ota/<job_id>/verify`, `POST /api/fleet/ota/<job_id>/rollback`
- `POST /api/fleet/components/<component_id>/calibrations`
- `POST /api/fleet/robots/<asset_id>/work-orders`, `POST /api/fleet/work-orders/<wo_id>/swap`,
  `POST /api/fleet/work-orders/<wo_id>/close`
- `POST /api/fleet/releases`, `POST /api/fleet/releases/<id>/recall`
- `GET /api/fleet/changes`

**Workforce**
- `GET /api/workforce/workers`, `GET /api/workforce/workers/<id>`, `GET …/<id>/history`,
  `GET /api/workforce/credential-definitions`
- `POST /api/workforce/workers`, `PATCH /api/workforce/workers/<id>`,
  `POST …/<id>/employment-status`, `POST …/<id>/credentials`, `POST …/<id>/training`
- `POST /api/workforce/credentials/<id>/renew`, `…/verify`, `…/revoke`
- `GET /api/workforce/changes`

### 7.2 Dashboard page (`/fleet.html`)

Linked from the main dashboard header; reuses `style.css`; vanilla JS in `fleet.js`, polling
every 3 s (no SSE changes).
- **Robots tab:** table (asset, model, serial, site, lifecycle, software declared/reported with
  a mismatch badge, calibration worst-status chip, report freshness). Clicking a row opens a detail
  drawer: identity; model spec sheet; software and OTA (jobs with state, start / verify / rollback
  forms); component tree (slot, part, serial, hw rev, firmware declared/reported, calibration
  chip, record-calibration form); maintenance (work orders, open / swap / close forms);
  lifecycle status / decommission; revision history.
- **Workers tab:** table (id, name, type, roles, sites, status, credential chips). Drawer:
  identity, credentials (validity chips; issue / renew / verify / revoke), training, history.
- **Change feed tab:** the newest changes from both feeds, live.

---

## 8. Testing

- `backend/test_inventory_{store,core,catalog,fleet,servicing,ota,workforce,seed}.py` — the
  inventory package on its own, one file per module: schema + seed determinism;
  every action (revision bump, `diff` / `after` contents, actor); every illegal transition →
  `Conflict`; OTA compatibility checks (recalled, `min_hw_rev`, wrong target, concurrent job);
  calibration and credential validity derivation at boundary times; component swap semantics;
  the privacy allowlist; the change feed (cursor, `limit`, `has_more`, epoch change →
  `resync_required`).
- `backend/test_fleet_bridge.py` — binding (demo robots and operators bound to fixed ids;
  `add_robot` commissions; class/model mismatch rejected); heartbeat writes only on a real change;
  a test setting `robot.firmware_version` directly produces a ROBOT_OBSERVATION and a mismatch
  flag; OTA over ticks, including waiting for an idle robot, `is_available` false during
  install, and the failure knob; lifecycle → stop/resume; credential revoke → operator loses the
  certification → the existing `HUMAN_INSPECTION` gate rejects it; expiry picked up on the
  shift-check cadence; reset → new epoch; persistence reopen; a threaded test (API call during a
  running simulator thread) proving there is no deadlock.
- `backend/test_inventory_api.py` — Flask test client over every route: shapes and 400 / 404 /
  409 mapping.
- The existing suite must stay exactly as it is: 256 passing plus the 2 pre-existing failures
  (`test_real_task_006_box_conflict_is_caught`,
  `test_idle_robot_with_a_low_battery_charges_itself`, tracked separately), and the twin's
  construction time must not regress noticeably (template-database copy).
- Manual: run the app, open `/fleet.html`, run an OTA end to end, swap a part, revoke a credential,
  and watch the change feed.

---

## 9. Decisions carried forward for later sub-projects

**C — Multi-embodiment operations** (from the earlier brainstorm): roster = AMR, forklift,
arm, drone, humanoid + conveyor (E); keep the 2D grid with an air layer (drones over racking,
no-fly cells), fixed stations (arms act on adjacent cells), clearance classes (forklift =
wide aisles only — the footprint is modelled as clearance, not multi-cell occupancy), shelf levels
0–4 as an attribute; humans = zone presence (check-in/out, assignment); items = one `Box` +
`kind` (ITEM / TOTE / PALLET), real weight, `shelf_level`; layouts = keep `classic` 20×15
byte-for-byte (default for `DigitalTwin()` and the tests) + add `multi_embodiment` ~32×20 (the app
boots into it); new eligibility rules as primitive-argument functions in `eligibility.py`
(payload, reach, kind, clearance, no-fly, drone round-trip, arm interlock, humanoid
supervision) shared by gate and grade; `CHARGE_ROBOT` stays in every class; the drone becomes
AIR even in classic. Physical limits are read from `robot_model.spec` (this spec).

**B — PWA ingestion:** a fleet-manager connector and a workforce connector over §4.5's feeds;
map IDs via `ExternalReference`; extend PWA's robot schema for the real-world detail (ADR
wherever it deviates from the PRD); add robot-change → passport re-mint triggers (firmware,
hardware revision, calibration, maintenance, component changes); passport history per robot.

**D — Telemetry:** the sensors are this spec's `component` rows; readings carry
noise/drift/faults against ground truth; the trust sensors are the forklift load cell, lidar
calibration, drone scanner, arm light curtain, conveyor photo-eyes, and the firmware lifecycle.
