"""Catalog reads and release management — the OEM reference data every
robot asset points at."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from .catalog_data import OS_VERSIONS
from .errors import Conflict
from .sbom import build_sbom, serialize_sbom

KIND_TAGS = {"ROBOT_SOFTWARE": "SW", "COMPONENT_FIRMWARE": "FW", "AI_POLICY_MODEL": "AI"}


def release_id_for(kind: str, target_code: str, version: str) -> str:
    return f"{target_code}:{KIND_TAGS[kind]}:{version}"


def make_release_row(kind: str, target_code: str, version: str, released_at: str, status: str,
                     min_hw_rev: Optional[str], supplier_name: str,
                     known_issues: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    row = {
        "release_id": release_id_for(kind, target_code, version),
        "kind": kind,
        "target_type": "PART" if kind == "COMPONENT_FIRMWARE" else "MODEL",
        "target_code": target_code,
        "version": version,
        "released_at": released_at,
        "min_hw_rev": min_hw_rev,
        "status": status,
    }
    row["sbom_json"], row["sbom_sha256"] = serialize_sbom(build_sbom(row, supplier_name))
    row["known_issues"] = list(known_issues or [])  # after the SBOM: it is not part of the bill of materials
    return row


def release_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k != "sbom_json"}


class CatalogMixin:
    # ---- reads ---------------------------------------------------------- #
    def list_manufacturers(self) -> List[Dict[str, Any]]:
        return self.store.select("manufacturer", order="manufacturer_id")

    def _manufacturer_names(self) -> Dict[str, str]:
        return {m["manufacturer_id"]: m["name"] for m in self.list_manufacturers()}

    def list_models(self) -> List[Dict[str, Any]]:
        names = self._manufacturer_names()
        return [{**m, "manufacturer_name": names[m["manufacturer_id"]]}
                for m in self.store.select("robot_model", order="model_code")]

    def get_model(self, model_code: str) -> Dict[str, Any]:
        model = self._require("robot_model", "model_code", model_code, "Robot model")
        model["manufacturer_name"] = self._manufacturer_names()[model["manufacturer_id"]]
        return model

    def has_model(self, model_code: str) -> bool:
        return bool(model_code) and self.store.exists("robot_model", "model_code", model_code)

    def model_class(self, model_code: str) -> str:
        return self.get_model(model_code)["embodiment_class"]

    def list_parts(self) -> List[Dict[str, Any]]:
        names = self._manufacturer_names()
        return [{**p, "manufacturer_name": names[p["manufacturer_id"]]}
                for p in self.store.select("part_model", order="part_number")]

    def get_part(self, part_number: str) -> Dict[str, Any]:
        return self._require("part_model", "part_number", part_number, "Part")

    def list_releases(self, target_code: Optional[str] = None, status: Optional[str] = None,
                      kind: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        for column, value in (("target_code", target_code), ("status", status), ("kind", kind)):
            if value:
                where.append(f"{column} = ?")
                params.append(str(value).upper() if column != "target_code" else value)
        rows = self.store.select("software_release", " AND ".join(where), params,
                                 order="kind, target_code, released_at")
        return [release_view(row) for row in rows]

    def get_release(self, release_id: str) -> Dict[str, Any]:
        return release_view(self._require("software_release", "release_id", release_id, "Release"))

    def get_sbom(self, release_id: str) -> Dict[str, Any]:
        row = self._require("software_release", "release_id", release_id, "Release")
        return json.loads(row["sbom_json"])

    def current_release(self, kind: str, target_code: str) -> Optional[Dict[str, Any]]:
        rows = self.store.select("software_release", "kind = ? AND target_code = ? AND status = 'CURRENT'",
                                 (kind, target_code), order="released_at DESC", limit=1)
        return release_view(rows[0]) if rows else None

    def release_by_version(self, kind: str, target_code: str, version: Optional[str]) -> Optional[Dict[str, Any]]:
        if not version:
            return None
        rows = self.store.select("software_release", "kind = ? AND target_code = ? AND version = ?",
                                 (kind, target_code, version), limit=1)
        return release_view(rows[0]) if rows else None

    def best_firmware(self, part_number: str, hw_revision: str) -> Optional[Dict[str, Any]]:
        """Newest non-recalled firmware this part revision can run."""
        rows = self.store.select("software_release",
                                 "kind = 'COMPONENT_FIRMWARE' AND target_code = ? AND status != 'RECALLED'",
                                 (part_number,), order="released_at DESC")
        for row in rows:
            if not row["min_hw_rev"] or row["min_hw_rev"] <= hw_revision:
                return release_view(row)
        return None

    @staticmethod
    def os_version_for(software_version: Optional[str]) -> Optional[str]:
        return OS_VERSIONS.get(software_version or "")

    # ---- release management ------------------------------------------- #
    def _catalog_revision(self, release_id: str) -> int:
        return self.store.count("change_log", "aggregate_id = ?", (release_id,)) + 1

    def publish_release(self, kind: str, target_code: str, version: str,
                        min_hw_rev: Optional[str] = None, actor: Any = None) -> Dict[str, Any]:
        kind = str(kind or "").upper()
        if kind not in KIND_TAGS:
            raise ValueError(f"Unknown release kind {kind!r} (known: {sorted(KIND_TAGS)})")
        version = str(version or "").strip()
        if not version:
            raise ValueError("version is required")
        with self._tx():
            if kind == "COMPONENT_FIRMWARE":
                target = self.get_part(target_code)
                if not target["firmware_capable"]:
                    raise ValueError(f"{target_code} does not take firmware updates")
            else:
                target = self.get_model(target_code)
            if min_hw_rev and min_hw_rev not in target["hw_revisions"]:
                raise ValueError(f"{target_code} has no hardware revision {min_hw_rev!r} (known: {target['hw_revisions']})")
            release_id = release_id_for(kind, target_code, version)
            if self.store.exists("software_release", "release_id", release_id):
                raise Conflict(f"Release {release_id} already exists")
            for prior in self.store.select("software_release",
                                           "kind = ? AND target_code = ? AND status = 'CURRENT'",
                                           (kind, target_code)):
                before = release_view(prior)
                self.store.update("software_release", "release_id", prior["release_id"], {"status": "SUPERSEDED"})
                self._record(aggregate_type="CATALOG", aggregate_id=prior["release_id"],
                             revision=self._catalog_revision(prior["release_id"]), action="RELEASE_SUPERSEDED",
                             before=before, after={**before, "status": "SUPERSEDED"}, actor=actor,
                             subject_id=prior["release_id"])
            supplier = self._manufacturer_names()[target["manufacturer_id"]]
            row = make_release_row(kind, target_code, version, self.now_iso(), "CURRENT", min_hw_rev, supplier)
            self.store.insert("software_release", row)
            return self._record(aggregate_type="CATALOG", aggregate_id=release_id,
                                revision=self._catalog_revision(release_id), action="RELEASE_PUBLISHED",
                                before=None, after=release_view(row), actor=actor, subject_id=release_id)

    def recall_release(self, release_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            row = self._require("software_release", "release_id", release_id, "Release")
            if row["status"] == "RECALLED":
                raise Conflict(f"Release {release_id} is already recalled")
            before = release_view(row)
            self.store.update("software_release", "release_id", release_id, {"status": "RECALLED"})
            change = self._record(aggregate_type="CATALOG", aggregate_id=release_id,
                                  revision=self._catalog_revision(release_id), action="RELEASE_RECALLED",
                                  before=before, after={**before, "status": "RECALLED"}, actor=actor,
                                  reason=reason, subject_id=release_id)
            if row["status"] == "CURRENT":
                candidates = self.store.select("software_release",
                                               "kind = ? AND target_code = ? AND status = 'SUPERSEDED'",
                                               (row["kind"], row["target_code"]), order="released_at DESC", limit=1)
                if candidates:
                    promoted = release_view(candidates[0])
                    self.store.update("software_release", "release_id", promoted["release_id"], {"status": "CURRENT"})
                    self._record(aggregate_type="CATALOG", aggregate_id=promoted["release_id"],
                                 revision=self._catalog_revision(promoted["release_id"]), action="RELEASE_PROMOTED",
                                 before=promoted, after={**promoted, "status": "CURRENT"}, actor=actor,
                                 reason=f"{release_id} recalled", subject_id=promoted["release_id"])
            return change
