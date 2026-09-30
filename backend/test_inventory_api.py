"""REST API over the fleet-manager and workforce source systems."""
import pytest

from backend.app import create_app
from backend.digital_twin import DigitalTwin


@pytest.fixture
def client(tmp_path):
    twin = DigitalTwin(log_dir=str(tmp_path / "logs"), data_dir=str(tmp_path / "data"),
                       persist_logs=False, demo=True, demo_tasks=False)
    app, _twin, _sim, _ci = create_app(twin=twin, autostart=False, run_thread=False)
    return app.test_client()


def test_catalog_and_release_endpoints(client):
    models = client.get("/api/fleet/catalog/models").get_json()["models"]
    assert any(m["model_code"] == "NW-PF1200" for m in models)
    assert client.get("/api/fleet/catalog/parts").get_json()["parts"]
    releases = client.get("/api/fleet/releases?target=AC-TR50&kind=ROBOT_SOFTWARE").get_json()["releases"]
    assert {"2.1.0", "2.2.0"} <= {r["version"] for r in releases}
    sbom = client.get("/api/fleet/releases/AC-TR50:SW:2.2.0/sbom")
    assert sbom.status_code == 200 and sbom.get_json()["bomFormat"] == "CycloneDX"
    body = {"kind": "ROBOT_SOFTWARE", "target_code": "AC-TR50", "version": "2.3.0"}
    created = client.post("/api/fleet/releases", json=body)
    assert created.status_code == 201 and created.get_json()["release"]["status"] == "CURRENT"
    assert client.post("/api/fleet/releases", json=body).status_code == 409
    assert client.post("/api/fleet/releases/AC-TR50:SW:2.3.0/recall", json={"reason": "bad"}).status_code == 200
    missing = client.get("/api/fleet/releases/NOPE/sbom")
    assert missing.status_code == 404 and missing.get_json()["ok"] is False


def test_robot_endpoints(client):
    robots = client.get("/api/fleet/robots?site=WH-01").get_json()["robots"]
    assert [r["asset_id"] for r in robots] == ["AST-000101", "AST-000102", "AST-000103"]
    detail = client.get("/api/fleet/robots/AST-000101").get_json()["robot"]
    assert detail["floor_robot"]["name"] == "Robo-01"
    assert client.get("/api/fleet/robots/AST-000201").get_json()["robot"]["floor_robot"] is None
    assert client.get("/api/fleet/robots/AST-404").status_code == 404
    made = client.post("/api/fleet/robots", json={"model_code": "CT-IX2", "site_code": "WH-02", "home_zone": "drone_pad"})
    assert made.status_code == 201
    asset = made.get_json()["robot"]["asset_id"]
    edited = client.patch(f"/api/fleet/robots/{asset}", json={"fleet_id": "AIR", "reason": "grouping"})
    assert edited.get_json()["robot"]["fleet_id"] == "AIR"
    assert client.patch(f"/api/fleet/robots/{asset}", json={"serial_number": "X", "reason": "no"}).status_code == 400
    assert client.post(f"/api/fleet/robots/{asset}/status", json={"status": "OUT_OF_SERVICE", "reason": "x"}).status_code == 200
    assert client.post(f"/api/fleet/robots/{asset}/status", json={"status": "COMMISSIONING", "reason": "x"}).status_code == 409
    history = client.get(f"/api/fleet/robots/{asset}/history").get_json()["history"]
    assert history[0]["action"] == "STATUS_CHANGED"
    assert client.post(f"/api/fleet/robots/{asset}/decommission", json={"reason": "gone"}).status_code == 200


def test_ota_and_maintenance_endpoints(client):
    staged = client.post("/api/fleet/robots/AST-000201/ota", json={"release_id": "AC-TR50:SW:2.1.1"})
    assert staged.status_code == 201
    job_id = staged.get_json()["change"]["subject_id"]
    assert client.post(f"/api/fleet/ota/{job_id}/verify", json={}).status_code == 409  # not REPORTED yet
    assert client.post("/api/fleet/robots/AST-000201/ota", json={"release_id": "AC-TR50:SW:2.2.1"}).status_code == 409
    opened = client.post("/api/fleet/robots/AST-000202/work-orders",
                         json={"type": "CORRECTIVE", "description": "Replace camera", "technician_id": "E-10004"})
    assert opened.status_code == 201
    wo_id = opened.get_json()["change"]["subject_id"]
    swapped = client.post(f"/api/fleet/work-orders/{wo_id}/swap", json={"slot": "camera", "performed_by": "E-10004"})
    assert swapped.status_code == 200
    camera = next(c for c in swapped.get_json()["robot"]["components"] if c["slot"] == "camera")
    assert camera["calibration_status"] == "MISSING"
    calibrated = client.post(f"/api/fleet/components/{camera['component_id']}/calibrations",
                             json={"result": "PASS", "performed_by": "E-10004"})
    assert calibrated.status_code == 201
    closed = client.post(f"/api/fleet/work-orders/{wo_id}/close", json={"resolution": "done"})
    assert closed.get_json()["robot"]["lifecycle_status"] == "IN_SERVICE"


def test_workforce_endpoints(client):
    workers = client.get("/api/workforce/workers?site=WH-01").get_json()["workers"]
    assert {"E-10001", "E-10002"} <= {w["worker_id"] for w in workers}
    made = client.post("/api/workforce/workers",
                       json={"display_name": "Robin", "role_codes": ["WAREHOUSE_OPERATOR"], "site_codes": ["WH-01"]})
    assert made.status_code == 201
    worker_id = made.get_json()["worker"]["worker_id"]
    refused = client.post("/api/workforce/workers", json={"display_name": "Pat", "salary": 1})
    assert refused.status_code == 400 and "salary" in refused.get_json()["error"]
    issued = client.post(f"/api/workforce/workers/{worker_id}/credentials",
                         json={"code": "forklift_operator", "equipment_scope": "NW-PF1200, NW-HH300"})
    assert issued.status_code == 201
    cred_id = issued.get_json()["change"]["subject_id"]
    cred = next(c for c in issued.get_json()["worker"]["credentials"] if c["credential_id"] == cred_id)
    assert cred["equipment_scope"] == ["NW-PF1200", "NW-HH300"]
    assert client.post(f"/api/workforce/credentials/{cred_id}/verify", json={"verification_status": "ISSUER_VERIFIED"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/renew", json={"expires_at": "2099-01-01"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/revoke", json={"reason": "audit"}).status_code == 200
    assert client.post(f"/api/workforce/credentials/{cred_id}/revoke", json={"reason": "audit"}).status_code == 409
    assert client.post(f"/api/workforce/workers/{worker_id}/training", json={"course_code": "LOTO-101"}).status_code == 201
    assert client.patch(f"/api/workforce/workers/{worker_id}", json={"reason": "move", "site_codes": ["WH-02"]}).status_code == 200
    assert client.post(f"/api/workforce/workers/{worker_id}/employment-status",
                       json={"status": "ON_LEAVE", "reason": "leave"}).status_code == 200
    history = client.get(f"/api/workforce/workers/{worker_id}/history").get_json()["history"]
    assert history[0]["action"] == "EMPLOYMENT_STATUS_CHANGED"
    assert client.get("/api/workforce/credential-definitions").get_json()["definitions"]
    assert client.get("/api/workforce/workers/E-404").status_code == 404


def test_change_feeds(client):
    first = client.get("/api/fleet/changes?limit=5").get_json()
    assert len(first["items"]) == 5 and first["has_more"]
    following = client.get(f"/api/fleet/changes?cursor={first['next_cursor']}&limit=5").get_json()
    assert following["items"][0]["seq"] > first["items"][-1]["seq"]
    assert client.get("/api/fleet/changes?cursor=bogus").status_code == 400
    workforce = client.get("/api/workforce/changes").get_json()
    assert workforce["items"] and all(i["aggregate_type"] == "WORKER" for i in workforce["items"])


def test_post_api_robots_accepts_model_code(client):
    made = client.post("/api/robots", json={"name": "Lifter-9", "model_code": "NW-PF1200"})
    assert made.status_code == 201
    robot = made.get_json()["robot"]
    assert robot["robot_class"] == "FORKLIFT" and robot["asset_id"]
