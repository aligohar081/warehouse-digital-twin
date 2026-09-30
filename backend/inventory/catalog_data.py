"""Reference data for the inventory's seed catalog (spec §6).

Every company here is fictional (Microsoft-style sample names) so nothing
impersonates a real vendor. Specs, sensor suites, safety standards and
software stacks are modelled on real product categories and value ranges.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

MANUFACTURERS: List[Dict[str, Any]] = [
    {"manufacturer_id": "ACME", "name": "Acme Robotics", "serial_prefix": "ACM"},
    {"manufacturer_id": "NORTHWIND", "name": "Northwind Lift Systems", "serial_prefix": "NWL"},
    {"manufacturer_id": "CONTOSO", "name": "Contoso Aerial", "serial_prefix": "CTA"},
    {"manufacturer_id": "FABRIKAM", "name": "Fabrikam Automation", "serial_prefix": "FBA"},
    {"manufacturer_id": "TAILSPIN", "name": "Tailspin Humanoids", "serial_prefix": "TSH"},
    {"manufacturer_id": "WINGTIP", "name": "Wingtip Sensors", "serial_prefix": "WTS"},
    {"manufacturer_id": "LITWARE", "name": "Litware Compute", "serial_prefix": "LWC"},
]


def _part(part_number: str, manufacturer_id: str, name: str, component_type: str, hw_revisions: str,
          firmware_capable: bool, calibration_interval_days: Any, **spec: Any) -> Dict[str, Any]:
    return {
        "part_number": part_number, "manufacturer_id": manufacturer_id, "name": name,
        "component_type": component_type, "hw_revisions": list(hw_revisions),
        "firmware_capable": firmware_capable, "calibration_interval_days": calibration_interval_days,
        "spec": spec,
    }


PART_MODELS: List[Dict[str, Any]] = [
    _part("LW-LC8", "LITWARE", "LC-8 Edge Compute Module", "COMPUTE", "AB", True, None, cpu="8-core ARM", ram_gb=16, accelerator_tops=40),
    _part("LW-LC16", "LITWARE", "LC-16 AI Compute Module", "COMPUTE", "A", True, None, cpu="12-core ARM", ram_gb=32, accelerator_tops=275),
    _part("AC-SC2", "ACME", "SC-2 Safety Controller", "SAFETY_CONTROLLER", "ABC", True, None, sil="SIL 2", performance_level="PL d", channels=2),
    _part("NW-SC4", "NORTHWIND", "SC-4 Truck Safety Controller", "SAFETY_CONTROLLER", "AB", True, None, sil="SIL 2", performance_level="PL d"),
    _part("FB-SC6", "FABRIKAM", "SC-6 Cell Safety Controller", "SAFETY_CONTROLLER", "A", True, None, sil="SIL 3", performance_level="PL e"),
    _part("TS-SC1", "TAILSPIN", "SC-1 Humanoid Safety Controller", "SAFETY_CONTROLLER", "A", True, None, sil="SIL 2", performance_level="PL d"),
    _part("WT-L360", "WINGTIP", "WL-360 3D Lidar", "LIDAR", "ABC", True, 365, range_m=40, channels=32, rate_hz=10),
    _part("WT-SS2", "WINGTIP", "WS-2 Safety Laser Scanner", "SAFETY_SCANNER", "AB", True, 180, protective_field_m=5.5, performance_level="PL d"),
    _part("WT-D3", "WINGTIP", "WD-3 RGB-D Camera", "RGBD_CAMERA", "AB", True, 180, resolution="1280x720", depth_range_m=6),
    _part("WT-T1", "WINGTIP", "WT-1 Thermal Camera", "THERMAL_CAMERA", "A", True, 180, resolution="320x240", range_c=[-20, 400]),
    _part("WT-I9", "WINGTIP", "WI-9 9-Axis IMU", "IMU", "A", False, 365, axes=9, rate_hz=400),
    _part("WT-R1", "WINGTIP", "WR-1 RFID/Barcode Scanner", "SCANNER", "AB", True, 365, rfid="UHF", barcode="1D/2D", read_range_m=8),
    _part("AC-DU1", "ACME", "DU-1 Differential Drive Unit", "DRIVE_UNIT", "AB", True, None, motor_w=400),
    _part("NW-DU5", "NORTHWIND", "DU-5 Traction Drive", "DRIVE_UNIT", "A", True, None, motor_kw=3.5),
    _part("AC-B12", "ACME", "B-12 LFP Battery Pack 1.2 kWh", "BATTERY", "AB", True, None, chemistry="LFP", capacity_wh=1200, nominal_v=48),
    _part("NW-B24", "NORTHWIND", "B-24 Lead-Acid Traction Battery", "BATTERY", "A", False, None, chemistry="lead-acid", capacity_wh=14400, nominal_v=24),
    _part("CT-B150", "CONTOSO", "B-150 LiPo Flight Pack", "BATTERY", "AB", True, None, chemistry="LiPo", capacity_wh=150, nominal_v=22.2),
    _part("TS-B20", "TAILSPIN", "B-20 Li-ion Torso Pack 2 kWh", "BATTERY", "A", True, None, chemistry="NMC", capacity_wh=2000, nominal_v=48),
    _part("NW-LC2T", "NORTHWIND", "LC-2T Fork Load Cell", "LOAD_CELL", "AB", True, 180, max_kg=2000, accuracy_pct=0.5),
    _part("NW-ME5", "NORTHWIND", "ME-5 Mast Height Encoder", "MAST_ENCODER", "A", True, 365, resolution_mm=1),
    _part("NW-F1200", "NORTHWIND", "F-1200 Fork Carriage", "FORKS", "A", False, None, fork_length_mm=1150),
    _part("FB-FT6", "FABRIKAM", "FT-6 Force-Torque Sensor", "FORCE_TORQUE", "AB", True, 180, axes=6, max_force_n=500),
    _part("FB-LC4", "FABRIKAM", "LCU-4 Safety Light Curtain", "LIGHT_CURTAIN", "A", True, 180, resolution_mm=14, performance_level="PL e"),
    _part("FB-G2", "FABRIKAM", "G-2 Adaptive Gripper", "GRIPPER", "AB", True, None, stroke_mm=85, max_kg=10),
    _part("AC-G1", "ACME", "G-1 Suction Gripper", "GRIPPER", "A", True, None, max_kg=30),
    _part("FB-J6", "FABRIKAM", "J-6 Joint Actuator Set", "ACTUATOR", "A", True, None, joints=6),
    _part("TS-A28", "TAILSPIN", "A-28 Whole-Body Actuator Set", "ACTUATOR", "AB", True, None, joints=28),
    _part("CT-R4", "CONTOSO", "R-4 Rotor Set", "ROTOR", "A", False, None, rotors=4),
    _part("CT-FC3", "CONTOSO", "FC-3 Flight Controller", "COMPUTE", "AB", True, None, imu="triple-redundant"),
]


def _model(model_code: str, manufacturer_id: str, name: str, embodiment_class: str, hw_revisions: str,
           spec: Dict[str, Any], safety_standards: Sequence[str],
           layout: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    return {
        "model_code": model_code, "manufacturer_id": manufacturer_id, "name": name,
        "embodiment_class": embodiment_class, "hw_revisions": list(hw_revisions), "spec": spec,
        "safety_standards": list(safety_standards),
        "component_layout": [{"slot": slot, "part_number": part, "required": True} for slot, part in layout],
    }


def _battery(chemistry: str, capacity_wh: int, runtime_h: float, charge_time_h: float) -> Dict[str, Any]:
    return {"chemistry": chemistry, "capacity_wh": capacity_wh, "runtime_h": runtime_h, "charge_time_h": charge_time_h}


ROBOT_MODELS: List[Dict[str, Any]] = [
    _model("AC-TR50", "ACME", "TR-50 Tote Runner", "AMR", "CD", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 50, "max_lift_m": 0.6,
        "max_shelf_level": 1, "max_speed_mps": 1.5, "footprint_mm": [700, 500], "mass_kg": 95,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 8, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 2.0, "place_s": 1.5,
    }, ["ANSI/A3 R15.08-1", "ISO 3691-4"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("safety_scanner_front", "WT-SS2"), ("safety_scanner_rear", "WT-SS2"),
        ("camera", "WT-D3"), ("imu", "WT-I9"),
    ]),
    _model("AC-SC1", "ACME", "SC-1 Inspection Scout", "SCOUT", "A", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 0, "max_lift_m": 0,
        "max_shelf_level": 0, "max_speed_mps": 2.0, "footprint_mm": [600, 450], "mass_kg": 60,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 10, 1.5), "box_kinds": [],
        "supervision": None, "lift_speed_mps": 0.5,
    }, ["ANSI/A3 R15.08-1"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("camera", "WT-D3"), ("thermal_camera", "WT-T1"), ("imu", "WT-I9"),
    ]),
    _model("AC-PK30", "ACME", "PK-30 Piece Picker", "PICKER", "B", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 30, "max_lift_m": 1.2,
        "max_shelf_level": 1, "max_speed_mps": 1.8, "footprint_mm": [800, 600], "mass_kg": 120,
        "ip_rating": "IP54", "battery": _battery("LFP", 1200, 7, 1.5), "box_kinds": ["TOTE", "ITEM"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 2.5, "place_s": 1.5,
    }, ["ANSI/A3 R15.08-1", "ISO 10218-1"], [
        ("compute", "LW-LC8"), ("safety_controller", "AC-SC2"), ("drive_left", "AC-DU1"),
        ("drive_right", "AC-DU1"), ("battery", "AC-B12"), ("lidar", "WT-L360"),
        ("safety_scanner_front", "WT-SS2"), ("camera", "WT-D3"), ("gripper", "AC-G1"), ("imu", "WT-I9"),
    ]),
    _model("NW-PF1200", "NORTHWIND", "PF-1200 Autonomous Pallet Forklift", "FORKLIFT", "AB", {
        "movement": "GROUND", "clearance": "WIDE", "max_payload_kg": 1200, "max_lift_m": 4.5,
        "max_shelf_level": 4, "max_speed_mps": 1.2, "footprint_mm": [2100, 1100], "mass_kg": 1450,
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 7, 8), "box_kinds": ["PALLET"],
        "supervision": None, "lift_speed_mps": 0.3, "grasp_s": 3.0, "place_s": 3.0,
    }, ["ISO 3691-4", "ANSI/ITSDF B56.5"], [
        ("compute", "LW-LC8"), ("safety_controller", "NW-SC4"), ("traction_drive", "NW-DU5"),
        ("battery", "NW-B24"), ("lidar", "WT-L360"), ("safety_scanner_front", "WT-SS2"),
        ("safety_scanner_rear", "WT-SS2"), ("camera", "WT-D3"), ("load_cell", "NW-LC2T"),
        ("mast_encoder", "NW-ME5"), ("forks", "NW-F1200"), ("imu", "WT-I9"),
    ]),
    _model("NW-HH300", "NORTHWIND", "HH-300 Heavy Hauler", "HEAVY_HAULER", "A", {
        "movement": "GROUND", "clearance": "WIDE", "max_payload_kg": 300, "max_lift_m": 0.3,
        "max_shelf_level": 0, "max_speed_mps": 1.0, "footprint_mm": [1400, 900], "mass_kg": 380,
        "ip_rating": "IP54", "battery": _battery("lead-acid", 14400, 9, 8), "box_kinds": ["TOTE", "PALLET"],
        "supervision": None, "lift_speed_mps": 0.5, "grasp_s": 3.0, "place_s": 3.0,
    }, ["ISO 3691-4"], [
        ("compute", "LW-LC8"), ("safety_controller", "NW-SC4"), ("traction_drive", "NW-DU5"),
        ("battery", "NW-B24"), ("lidar", "WT-L360"), ("safety_scanner_front", "WT-SS2"),
        ("safety_scanner_rear", "WT-SS2"), ("imu", "WT-I9"),
    ]),
    _model("CT-IX2", "CONTOSO", "IX-2 Indoor Inventory Drone", "DRONE", "AB", {
        "movement": "AIR", "clearance": None, "max_payload_kg": 0, "max_lift_m": 12,
        "max_shelf_level": 4, "max_speed_mps": 3.0, "footprint_mm": [450, 450], "mass_kg": 2.1,
        "ip_rating": "IP43", "battery": _battery("LiPo", 150, 0.37, 1.0), "box_kinds": [],
        "flight_time_min": 22, "supervision": None, "lift_speed_mps": 0.5,
        "takeoff_s": 4.0, "land_s": 5.0, "scan_s": 3.0,
    }, ["IEC 62133-2"], [
        ("flight_controller", "CT-FC3"), ("compute", "LW-LC8"), ("battery", "CT-B150"),
        ("rotors", "CT-R4"), ("scanner", "WT-R1"), ("camera", "WT-D3"), ("imu", "WT-I9"),
    ]),
    _model("FB-CX10", "FABRIKAM", "CX-10 Collaborative Arm", "ARM", "AB", {
        "movement": "FIXED", "clearance": None, "max_payload_kg": 10, "reach_mm": 1300,
        "max_speed_mps": 1.0, "repeatability_mm": 0.05, "mass_kg": 33, "ip_rating": "IP54",
        "battery": None, "power": "mains 230 V", "box_kinds": ["ITEM"], "supervision": None,
        "lift_speed_mps": 0.5, "grasp_s": 2.5, "place_s": 1.5,
    }, ["ISO 10218-1", "ISO/TS 15066"], [
        ("compute", "LW-LC16"), ("safety_controller", "FB-SC6"), ("joints", "FB-J6"),
        ("force_torque", "FB-FT6"), ("wrist_camera", "WT-D3"), ("gripper", "FB-G2"),
        ("light_curtain", "FB-LC4"),
    ]),
    _model("TS-H1", "TAILSPIN", "H-1 General-Purpose Humanoid", "HUMANOID", "A", {
        "movement": "GROUND", "clearance": "NARROW", "max_payload_kg": 25, "max_lift_m": 1.8,
        "max_shelf_level": 2, "max_speed_mps": 1.2, "height_mm": 1750, "mass_kg": 70,
        "ip_rating": "IP44", "battery": _battery("NMC", 2000, 4, 2), "box_kinds": ["TOTE", "ITEM"],
        "supervision": "humanoid_supervision", "lift_speed_mps": 0.5, "grasp_s": 3.0, "place_s": 2.0,
    }, ["ISO 12100", "ISO 13849-1"], [
        ("compute", "LW-LC16"), ("safety_controller", "TS-SC1"), ("actuators", "TS-A28"),
        ("battery", "TS-B20"), ("stereo_camera_left", "WT-D3"), ("stereo_camera_right", "WT-D3"),
        ("lidar", "WT-L360"), ("imu", "WT-I9"),
    ]),
]

#: The catalog model a twin robot class is commissioned as when no model is given.
CLASS_DEFAULT_MODELS: Dict[str, str] = {
    "AMR": "AC-TR50", "SCOUT": "AC-SC1", "PICKER": "AC-PK30", "FORKLIFT": "NW-PF1200",
    "HEAVY_HAULER": "NW-HH300", "DRONE": "CT-IX2", "ARM": "FB-CX10", "HUMANOID": "TS-H1",
}

#: Robot-software releases every model ships: (version, days before seeding, status).
#: One version line across vendors keeps the twin's single
#: APPROVED_FIRMWARE_VERSIONS list meaningful (per-model baselines are PWA's job).
ROBOT_SOFTWARE_RELEASES: List[Tuple[str, int, str]] = [
    ("2.0.4", 420, "SUPERSEDED"), ("2.1.0", 300, "SUPERSEDED"),
    ("2.1.1", 200, "SUPERSEDED"), ("2.2.0", 90, "CURRENT"),
]
EXTRA_ROBOT_SOFTWARE_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
    "AC-TR50": [("2.2.1", 40, "RECALLED")],
}
ROBOT_SOFTWARE_MIN_HW: Dict[Tuple[str, str], str] = {("NW-PF1200", "2.2.0"): "B"}

#: The OS each robot-software version ships with (reported by robots as os_version).
OS_VERSIONS: Dict[str, str] = {
    "2.0.4": "linux-rt 5.15.148", "2.1.0": "linux-rt 6.1.90", "2.1.1": "linux-rt 6.1.90",
    "2.2.0": "linux-rt 6.6.21", "2.2.1": "linux-rt 6.6.21",
}

#: Component firmware per firmware-capable part: (previous, current).
COMPONENT_FIRMWARE_VERSIONS: Dict[str, Tuple[str, str]] = {
    "LW-LC8": ("5.10.2", "5.12.0"), "LW-LC16": ("6.1.0", "6.2.1"), "AC-SC2": ("3.0.8", "3.1.0"),
    "NW-SC4": ("4.2.0", "4.2.3"), "FB-SC6": ("2.7.1", "2.8.0"), "TS-SC1": ("1.2.0", "1.3.0"),
    "WT-L360": ("1.9.4", "2.0.1"), "WT-SS2": ("3.3.0", "3.4.0"), "WT-D3": ("0.18.2", "0.19.0"),
    "WT-T1": ("1.1.0", "1.2.0"), "WT-R1": ("2.2.0", "2.3.5"), "AC-DU1": ("1.0.6", "1.1.0"),
    "NW-DU5": ("7.0.1", "7.1.0"), "AC-B12": ("1.2.0", "1.3.1"), "CT-B150": ("0.9.0", "1.0.0"),
    "TS-B20": ("2.0.0", "2.1.0"), "NW-LC2T": ("1.4.0", "1.5.0"), "NW-ME5": ("1.0.0", "1.0.3"),
    "FB-FT6": ("3.2.0", "3.3.1"), "FB-LC4": ("1.6.0", "1.6.2"), "FB-G2": ("4.0.0", "4.1.0"),
    "AC-G1": ("1.0.0", "1.1.0"), "FB-J6": ("5.4.0", "5.5.0"), "TS-A28": ("0.9.3", "1.0.0"),
    "CT-FC3": ("4.4.0", "4.5.2"),
}
COMPONENT_FIRMWARE_MIN_HW: Dict[Tuple[str, str], str] = {("WT-L360", "2.0.1"): "B"}

#: Known issues shipped with a release, keyed by release_id. Each effect is
#: what that release does to the readings of one component type (the
#: sensor models apply them — multi-embodiment spec §13.2).
KNOWN_ISSUES: Dict[str, List[Dict[str, Any]]] = {
    "AC-TR50:SW:2.2.1": [{
        "code": "ODOMETRY_HEADING_DRIFT", "component_type": "DRIVE_UNIT",
        "summary": "Wheel-odometry heading drifts 0.5 degrees per metre travelled",
        "effect": {"heading_drift_deg_per_m": 0.5},
    }],
    "FB-FT6:FW:3.2.0": [{
        "code": "FORCE_TORQUE_Z_OFFSET", "component_type": "FORCE_TORQUE",
        "summary": "Force-torque firmware reports an 8 N offset on the z axis",
        "effect": {"force_z_offset_n": 8.0},
    }],
}

AI_POLICY_RELEASES: Dict[str, List[Tuple[str, int, str]]] = {
    "FB-CX10": [("grasp-3.1.0", 200, "SUPERSEDED"), ("grasp-3.2.0", 45, "CURRENT")],
    "TS-H1": [("loco-manip-1.3.0", 150, "SUPERSEDED"), ("loco-manip-1.4.0", 30, "CURRENT")],
}


def _credential(code: str, name: str, renewal_period_days: int, scope_dimensions: Sequence[str]) -> Dict[str, Any]:
    return {
        "code": code, "name": name, "issuer_types": ["INTERNAL", "THIRD_PARTY"],
        "renewal_period_days": renewal_period_days, "scope_dimensions": list(scope_dimensions),
    }


#: Codes match the twin's existing lowercase certification strings.
CREDENTIAL_DEFINITIONS: List[Dict[str, Any]] = [
    _credential("safety_inspection", "Safety Inspection", 365, ["site"]),
    _credential("electrical_safety", "Electrical Safety", 365, ["site"]),
    _credential("equipment_maintenance", "Equipment Maintenance", 730, ["equipment"]),
    _credential("heavy_equipment", "Heavy Equipment Operation", 1095, ["equipment", "site"]),
    _credential("hazmat_handling", "Hazardous Materials Handling", 365, ["site"]),
    _credential("quality_control", "Quality Control", 730, ["task"]),
    _credential("forklift_operator", "Forklift Operator", 1095, ["equipment", "site"]),
    _credential("humanoid_supervision", "Humanoid Robot Supervision", 365, ["equipment", "site", "supervision"]),
    _credential("robot_cell_access", "Robot Cell Access", 365, ["equipment", "site"]),
    _credential("drone_operations", "Indoor Drone Operations", 730, ["equipment", "site"]),
    _credential("robot_maintenance", "Robot Maintenance Technician", 730, ["equipment"]),
]
