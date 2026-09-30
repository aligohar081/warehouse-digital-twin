"""REST routes for the fleet-manager and workforce source systems.

Reads go straight to the inventory service; every mutation goes through
twin.fleet.mutate() so it runs under twin.lock (see backend/fleet_bridge.py).
Errors: NotFound → 404, Conflict → 409, ValueError → 400,
in the same JSON shape as the rest of the API.
"""
from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from flask import jsonify, request

from .inventory import Conflict, NotFound


REGISTER_WORKER_FIELDS = ("worker_type", "organization", "role_codes", "site_codes",
                           "worker_id", "supervisor_id", "employment_status")
UPDATE_WORKER_FIELDS = ("role_codes", "site_codes", "organization", "supervisor_id")


def _list(value: Any) -> List[str]:
    """Accept a JSON list or a comma-separated string."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError("expected a list or a comma-separated string")


def _text(data: Dict[str, Any], key: str) -> Optional[str]:
    """A string-valued body field: missing, null or blank -> None; a string is
    stripped; a bare number is stringified; anything else (a list, an object,
    a bool) is a 400 instead of a TypeError deep in the service."""
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ValueError(f"{key} must be a string")


def register_inventory_routes(app, twin, api_error) -> None:
    service = twin.inventory
    fleet = twin.fleet

    def guarded(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except api_error:
                raise
            except NotFound as exc:
                raise api_error(str(exc), status=404)
            except Conflict as exc:
                raise api_error(str(exc), status=409)
            except ValueError as exc:
                raise api_error(str(exc))
        return wrapper

    def body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict() or {}
        if not isinstance(data, dict):
            raise api_error("Request body must be a JSON object")
        data.pop("actor", None)  # the actor is never client-supplied
        return data

    def limit(default: int = 200) -> int:
        try:
            return int(request.args.get("limit", default))
        except (TypeError, ValueError):
            raise api_error("limit must be an integer") from None

    def robot_payload(asset_id: str) -> Dict[str, Any]:
        record = service.get_robot(asset_id)
        floor = fleet.robot_for_asset(asset_id)
        record["floor_robot"] = ({"id": floor.id, "name": floor.name, "status": floor.status.value,
                                  "ota_installing": floor.ota_installing} if floor else None)
        return record

    def robot_response(change: Dict[str, Any], status: int = 200):
        return jsonify({"ok": True, "change": change, "robot": robot_payload(change["aggregate_id"])}), status

    def worker_response(change: Dict[str, Any], status: int = 200):
        return jsonify({"ok": True, "change": change, "worker": service.get_worker(change["aggregate_id"])}), status

    # ---- catalog -------------------------------------------------------- #
    @app.get("/api/fleet/catalog/models")
    @guarded
    def fleet_catalog_models():
        return jsonify({"ok": True, "models": service.list_models()})

    @app.get("/api/fleet/catalog/parts")
    @guarded
    def fleet_catalog_parts():
        return jsonify({"ok": True, "parts": service.list_parts(), "manufacturers": service.list_manufacturers()})

    @app.get("/api/fleet/releases")
    @guarded
    def fleet_releases():
        args = request.args
        return jsonify({"ok": True, "releases": service.list_releases(
            target_code=args.get("target") or None, status=args.get("status") or None,
            kind=args.get("kind") or None)})

    @app.get("/api/fleet/releases/<release_id>/sbom")
    @guarded
    def fleet_release_sbom(release_id: str):
        return jsonify(service.get_sbom(release_id))

    @app.post("/api/fleet/releases")
    @guarded
    def fleet_publish_release():
        data = body()
        change = fleet.mutate(service.publish_release, _text(data, "kind"), _text(data, "target_code"),
                              _text(data, "version"), min_hw_rev=_text(data, "min_hw_rev"))
        return jsonify({"ok": True, "change": change, "release": service.get_release(change["subject_id"])}), 201

    @app.post("/api/fleet/releases/<release_id>/recall")
    @guarded
    def fleet_recall_release(release_id: str):
        change = fleet.mutate(service.recall_release, release_id, _text(body(), "reason"))
        return jsonify({"ok": True, "change": change, "release": service.get_release(release_id)})

    # ---- robots --------------------------------------------------------- #
    @app.get("/api/fleet/robots")
    @guarded
    def fleet_robots():
        return jsonify({"ok": True, "robots": service.list_robots(
            site=request.args.get("site") or None, status=request.args.get("status") or None)})

    @app.post("/api/fleet/robots")
    @guarded
    def fleet_commission_robot():
        data = body()
        change = fleet.mutate(
            service.commission_robot, _text(data, "model_code"), _text(data, "site_code"), _text(data, "home_zone"),
            serial_number=_text(data, "serial_number"), asset_tag=_text(data, "asset_tag"),
            hw_revision=_text(data, "hw_revision"), software_release_id=_text(data, "software_release_id"),
            fleet_id=_text(data, "fleet_id"),
        )
        return robot_response(change, 201)

    @app.get("/api/fleet/robots/<asset_id>")
    @guarded
    def fleet_robot(asset_id: str):
        return jsonify({"ok": True, "robot": robot_payload(asset_id)})

    @app.patch("/api/fleet/robots/<asset_id>")
    @guarded
    def fleet_admin_edit(asset_id: str):
        data = body()
        reason = _text(data, "reason")
        data.pop("reason", None)
        fields = {key: _text(data, key) for key in data}
        return robot_response(fleet.mutate(service.admin_edit, asset_id, fields, reason))

    @app.get("/api/fleet/robots/<asset_id>/history")
    @guarded
    def fleet_robot_history(asset_id: str):
        return jsonify({"ok": True, "history": service.robot_history(asset_id, limit=limit())})

    @app.post("/api/fleet/robots/<asset_id>/status")
    @guarded
    def fleet_set_status(asset_id: str):
        data = body()
        return robot_response(fleet.mutate(service.set_lifecycle_status, asset_id, _text(data, "status"), _text(data, "reason")))

    @app.post("/api/fleet/robots/<asset_id>/decommission")
    @guarded
    def fleet_decommission(asset_id: str):
        return robot_response(fleet.mutate(service.decommission, asset_id, _text(body(), "reason")))

    @app.post("/api/fleet/robots/<asset_id>/ota")
    @guarded
    def fleet_start_ota(asset_id: str):
        data = body()
        change = fleet.mutate(service.start_ota, asset_id, _text(data, "release_id"),
                              component_id=_text(data, "component_id"))
        return robot_response(change, 201)

    @app.post("/api/fleet/ota/<job_id>/verify")
    @guarded
    def fleet_verify_ota(job_id: str):
        return robot_response(fleet.mutate(service.verify_ota, job_id, verified_by=_text(body(), "verified_by")))

    @app.post("/api/fleet/ota/<job_id>/rollback")
    @guarded
    def fleet_rollback_ota(job_id: str):
        return robot_response(fleet.mutate(service.rollback_ota, job_id, _text(body(), "reason")))

    @app.post("/api/fleet/components/<component_id>/calibrations")
    @guarded
    def fleet_record_calibration(component_id: str):
        data = body()
        change = fleet.mutate(service.record_calibration, component_id, _text(data, "result"),
                              performed_by=_text(data, "performed_by"), method=_text(data, "method") or "FIELD",
                              certificate_ref=_text(data, "certificate_ref"))
        return robot_response(change, 201)

    @app.post("/api/fleet/robots/<asset_id>/work-orders")
    @guarded
    def fleet_open_work_order(asset_id: str):
        data = body()
        change = fleet.mutate(service.open_work_order, asset_id, _text(data, "type"), _text(data, "description"),
                              technician_id=_text(data, "technician_id"))
        return robot_response(change, 201)

    @app.post("/api/fleet/work-orders/<wo_id>/swap")
    @guarded
    def fleet_swap_component(wo_id: str):
        data = body()
        return robot_response(fleet.mutate(
            service.swap_component, wo_id, _text(data, "slot"), performed_by=_text(data, "performed_by"),
            hw_revision=_text(data, "hw_revision"), part_number=_text(data, "part_number")))

    @app.post("/api/fleet/work-orders/<wo_id>/close")
    @guarded
    def fleet_close_work_order(wo_id: str):
        return robot_response(fleet.mutate(service.close_work_order, wo_id, _text(body(), "resolution")))

    @app.get("/api/fleet/changes")
    @guarded
    def fleet_changes():
        return jsonify({"ok": True, **service.changes(
            "fleet", cursor=request.args.get("cursor") or None, limit=request.args.get("limit", 100))})

    # ---- workforce ------------------------------------------------------ #
    @app.get("/api/workforce/workers")
    @guarded
    def workforce_workers():
        return jsonify({"ok": True, "workers": service.list_workers(
            site=request.args.get("site") or None, status=request.args.get("status") or None)})

    @app.post("/api/workforce/workers")
    @guarded
    def workforce_register_worker():
        data = body()
        display_name = _text(data, "display_name")
        data.pop("display_name", None)
        unknown = set(data.keys()) - set(REGISTER_WORKER_FIELDS)
        if unknown:
            raise ValueError(f"Field(s) not allowed in a worker record: {sorted(unknown)}")
        kwargs = {}
        for key in REGISTER_WORKER_FIELDS:
            if key not in data:
                continue
            kwargs[key] = _list(data[key]) if key in ("role_codes", "site_codes") else _text(data, key)
        return worker_response(fleet.mutate(service.register_worker, display_name, **kwargs), 201)

    @app.get("/api/workforce/workers/<worker_id>")
    @guarded
    def workforce_worker(worker_id: str):
        return jsonify({"ok": True, "worker": service.get_worker(worker_id)})

    @app.patch("/api/workforce/workers/<worker_id>")
    @guarded
    def workforce_update_worker(worker_id: str):
        data = body()
        reason = _text(data, "reason")
        data.pop("reason", None)
        unknown = set(data.keys()) - set(UPDATE_WORKER_FIELDS)
        if unknown:
            raise ValueError(f"Field(s) not allowed in a worker record: {sorted(unknown)}")
        kwargs = {}
        for key in UPDATE_WORKER_FIELDS:
            if key not in data:
                continue
            if key in ("role_codes", "site_codes"):
                kwargs[key] = _list(data[key])
            elif key == "organization":
                kwargs[key] = _text(data, key) or ""  # blank stays "given but empty" so the service rejects it
            else:
                kwargs[key] = _text(data, key)
        return worker_response(fleet.mutate(service.update_worker, worker_id, reason, **kwargs))

    @app.get("/api/workforce/workers/<worker_id>/history")
    @guarded
    def workforce_worker_history(worker_id: str):
        return jsonify({"ok": True, "history": service.worker_history(worker_id, limit=limit())})

    @app.post("/api/workforce/workers/<worker_id>/employment-status")
    @guarded
    def workforce_employment_status(worker_id: str):
        data = body()
        return worker_response(fleet.mutate(service.set_employment_status, worker_id, _text(data, "status"),
                                            _text(data, "reason")))

    @app.post("/api/workforce/workers/<worker_id>/credentials")
    @guarded
    def workforce_issue_credential(worker_id: str):
        data = body()
        change = fleet.mutate(
            service.issue_credential, worker_id, _text(data, "code"), _text(data, "issuer") or "Site Training Office",
            effective_from=_text(data, "effective_from"), expires_at=_text(data, "expires_at"),
            equipment_scope=_list(data.get("equipment_scope")), task_scope=_list(data.get("task_scope")),
            site_scope=_list(data.get("site_scope")), supervision_requirement=_text(data, "supervision_requirement"),
            verification_status=_text(data, "verification_status") or "SOURCE_VERIFIED",
            credential_number=_text(data, "credential_number"),
        )
        return worker_response(change, 201)

    @app.post("/api/workforce/workers/<worker_id>/training")
    @guarded
    def workforce_record_training(worker_id: str):
        data = body()
        change = fleet.mutate(service.record_training, worker_id, _text(data, "course_code"),
                              _text(data, "course_version") or "1", completed_at=_text(data, "completed_at"),
                              expires_at=_text(data, "expires_at"))
        return worker_response(change, 201)

    @app.get("/api/workforce/credential-definitions")
    @guarded
    def workforce_credential_definitions():
        return jsonify({"ok": True, "definitions": service.list_credential_definitions()})

    @app.post("/api/workforce/credentials/<credential_id>/renew")
    @guarded
    def workforce_renew_credential(credential_id: str):
        return worker_response(fleet.mutate(service.renew_credential, credential_id, _text(body(), "expires_at")))

    @app.post("/api/workforce/credentials/<credential_id>/verify")
    @guarded
    def workforce_verify_credential(credential_id: str):
        return worker_response(fleet.mutate(service.verify_credential, credential_id,
                                            _text(body(), "verification_status")))

    @app.post("/api/workforce/credentials/<credential_id>/revoke")
    @guarded
    def workforce_revoke_credential(credential_id: str):
        return worker_response(fleet.mutate(service.revoke_credential, credential_id, _text(body(), "reason")))

    @app.get("/api/workforce/changes")
    @guarded
    def workforce_changes():
        return jsonify({"ok": True, **service.changes(
            "workforce", cursor=request.args.get("cursor") or None, limit=request.args.get("limit", 100))})
