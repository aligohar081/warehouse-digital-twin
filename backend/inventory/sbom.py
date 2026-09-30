"""Deterministic synthetic CycloneDX 1.5 SBOMs for catalog releases.

The same release always produces a byte-identical SBOM, so its SHA-256 is a
stable identity a firmware baseline can pin to. Components are realistic
open-source packages plus the (fictional) vendor's own packages.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

_NAMESPACE = uuid.UUID("6f1c9a52-7c1e-4d0b-9a57-2f4b1f0e3c11")

#: Open-source stacks by robot-software release line (major.minor).
_ROBOT_STACKS: Dict[str, List[Tuple[str, str]]] = {
    "2.0": [("linux-rt-kernel", "5.15.148"), ("glibc", "2.35"), ("openssl", "3.0.2"),
            ("rclcpp", "16.0.5"), ("cyclonedds", "0.9.1"), ("protobuf", "3.12.4")],
    "2.1": [("linux-rt-kernel", "6.1.90"), ("glibc", "2.35"), ("openssl", "3.0.13"),
            ("rclcpp", "16.0.8"), ("cyclonedds", "0.10.3"), ("protobuf", "3.21.12")],
    "2.2": [("linux-rt-kernel", "6.6.21"), ("glibc", "2.39"), ("openssl", "3.0.14"),
            ("rclcpp", "28.1.3"), ("cyclonedds", "0.10.4"), ("protobuf", "3.21.12")],
}
_FIRMWARE_STACK: List[Tuple[str, str]] = [("rtos-kernel", "3.5.0"), ("mbedtls", "3.5.2"), ("lwip", "2.2.0")]
_AI_STACK: List[Tuple[str, str]] = [("onnxruntime", "1.17.3"), ("numpy", "1.26.4")]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _component(release_id: str, name: str, version: str, ctype: str = "library",
               supplier: Optional[str] = None) -> Dict[str, Any]:
    ref = f"{release_id}/{name}@{version}"
    component: Dict[str, Any] = {
        "type": ctype, "bom-ref": ref, "name": name, "version": version,
        "purl": f"pkg:generic/{name}@{version}",
        "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(ref.encode("utf-8")).hexdigest()}],
    }
    if supplier:
        component["supplier"] = {"name": supplier}
    return component


def build_sbom(release: Dict[str, Any], supplier_name: str) -> Dict[str, Any]:
    kind, version, release_id = release["kind"], release["version"], release["release_id"]
    vendor = _slug(supplier_name)
    if kind == "ROBOT_SOFTWARE":
        line = ".".join(version.split(".")[:2])
        components = [_component(release_id, n, v) for n, v in _ROBOT_STACKS.get(line, _ROBOT_STACKS["2.2"])]
        components.append(_component(release_id, f"{vendor}-nav-stack", version, supplier=supplier_name))
        components.append(_component(release_id, f"{vendor}-fleet-agent", version, supplier=supplier_name))
        root_type = "firmware"
    elif kind == "COMPONENT_FIRMWARE":
        components = [_component(release_id, n, v) for n, v in _FIRMWARE_STACK]
        components.append(_component(
            release_id, f"{vendor}-{_slug(release['target_code'])}-fw", version,
            ctype="firmware", supplier=supplier_name,
        ))
        root_type = "firmware"
    else:  # AI_POLICY_MODEL
        components = [_component(release_id, n, v) for n, v in _AI_STACK]
        components.append(_component(
            release_id, f"{_slug(release['target_code'])}-policy-weights", version,
            ctype="data", supplier=supplier_name,
        ))
        root_type = "machine-learning-model"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(_NAMESPACE, release_id)}",
        "version": 1,
        "metadata": {
            "timestamp": release["released_at"],
            "supplier": {"name": supplier_name},
            "component": {"type": root_type, "bom-ref": release_id,
                          "name": release["target_code"], "version": version},
        },
        "components": components,
        "dependencies": [{"ref": release_id, "dependsOn": [c["bom-ref"] for c in components]}],
    }


def serialize_sbom(sbom: Dict[str, Any]) -> Tuple[str, str]:
    text = json.dumps(sbom, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()
