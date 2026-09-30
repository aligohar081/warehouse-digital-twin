"""Workforce: workers, credentials and training — the HRIS / LMS /
qualification-system view PWA's worker passports are computed from.

Only PWA's §26.1 allowlist is stored: an identity reference, status, worker
type and organisation, role and site codes, credential and training claims,
and a supervisor only when supervision needs one. Any other field passed to
register_worker / update_worker is rejected before it can be stored or
logged. Credential numbers are kept only as sha256 hashes.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Mapping, Optional

from .core import sha256_text
from .documents import iso, parse_iso, to_datetime
from .errors import Conflict

WORKER_TYPES = ("EMPLOYEE", "CONTRACTOR", "PARTNER")
EMPLOYMENT_STATUSES = ("ACTIVE", "ON_LEAVE", "TERMINATED")
VERIFICATION_STATUSES = ("UNVERIFIED", "SOURCE_VERIFIED", "ISSUER_VERIFIED")
VALID_CREDENTIAL_STATES = ("VALID", "EXPIRING_SOON")
_UNSET = object()


def _codes(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def _hour(value: Any, label: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        hour = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an hour between 0 and 23") from None
    if not 0 <= hour <= 23:
        raise ValueError(f"{label} must be between 0 and 23")
    return hour


def _reject_unlisted(extra: Mapping[str, Any]) -> None:
    if extra:
        raise ValueError(f"Field(s) not allowed in a worker record: {sorted(extra)}")


class WorkforceMixin:
    # ---- lookups & documents ------------------------------------------ #
    def has_worker(self, worker_id: Optional[str]) -> bool:
        return bool(worker_id) and self.store.exists("worker", "worker_id", worker_id)

    def _worker(self, worker_id: str) -> Dict[str, Any]:
        return self._require("worker", "worker_id", worker_id, "Worker")

    def _credential(self, credential_id: str) -> Dict[str, Any]:
        return self._require("worker_credential", "credential_id", credential_id, "Credential")

    def _worker_document(self, worker_id: str) -> Dict[str, Any]:
        """The WORKER aggregate as stored in change_log `after` snapshots."""
        return {
            **self._worker(worker_id),
            "credentials": self.store.select("worker_credential", "worker_id = ?", (worker_id,)),
            "training": self.store.select("training_completion", "worker_id = ?", (worker_id,)),
        }

    def _commit_worker(self, worker_id: str, action: str, before: Optional[dict], actor: Any = None,
                       reason: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
        revision = self._worker(worker_id)["revision"] + 1
        self.store.update("worker", "worker_id", worker_id, {"revision": revision, "updated_at": self.now_iso()})
        return self._record(aggregate_type="WORKER", aggregate_id=worker_id, revision=revision, action=action,
                            before=before, after=self._worker_document(worker_id), actor=actor,
                            reason=reason, subject_id=subject_id)

    # ---- validity ------------------------------------------------------ #
    def _credential_validity(self, credential: Mapping[str, Any], worker: Mapping[str, Any], now) -> str:
        if credential["verification_status"] == "REVOKED":
            return "REVOKED"
        if worker["employment_status"] != "ACTIVE":
            return "WORKER_INACTIVE"
        start = parse_iso(credential["effective_from"])
        if start is not None and start > now:
            return "NOT_YET_EFFECTIVE"
        end = parse_iso(credential["expires_at"])
        if end is not None:
            if end <= now:
                return "EXPIRED"
            if end - now <= timedelta(days=self.setting("CREDENTIAL_EXPIRING_SOON_DAYS")):
                return "EXPIRING_SOON"
        return "VALID"

    def _training_validity(self, training: Mapping[str, Any], now) -> str:
        end = parse_iso(training["expires_at"])
        if end is None:
            return "VALID"
        if end <= now:
            return "EXPIRED"
        if end - now <= timedelta(days=self.setting("CREDENTIAL_EXPIRING_SOON_DAYS")):
            return "EXPIRING_SOON"
        return "VALID"

    # ---- workers ------------------------------------------------------- #
    def register_worker(self, display_name: str, worker_type: str = "EMPLOYEE",
                        organization: str = "Warehouse Operations", role_codes: Any = (), site_codes: Any = (), *,
                        worker_id: Optional[str] = None, supervisor_id: Optional[str] = None,
                        shift_start_hour: Any = None, shift_end_hour: Any = None,
                        employment_status: str = "ACTIVE", actor: Any = None, **extra: Any) -> Dict[str, Any]:
        _reject_unlisted(extra)
        name = str(display_name or "").strip()
        if not name:
            raise ValueError("display_name is required")
        worker_type = str(worker_type or "").upper()
        if worker_type not in WORKER_TYPES:
            raise ValueError(f"Unknown worker_type {worker_type!r} (known: {list(WORKER_TYPES)})")
        employment_status = str(employment_status or "").upper()
        if employment_status not in EMPLOYMENT_STATUSES:
            raise ValueError(f"Unknown employment_status {employment_status!r} (known: {list(EMPLOYMENT_STATUSES)})")
        organization = str(organization or "").strip() or "Warehouse Operations"
        start_hour = _hour(shift_start_hour, "shift_start_hour")
        end_hour = _hour(shift_end_hour, "shift_end_hour")
        with self._tx():
            if worker_id:
                if self.has_worker(worker_id):
                    raise Conflict(f"Worker '{worker_id}' already exists")
            else:
                worker_id = self.store.next_id("E-", "worker", "worker_id", 5, start=10001)
            if supervisor_id:
                self._worker(supervisor_id)
            self.store.insert("worker", {
                "worker_id": worker_id, "display_name": name, "worker_type": worker_type,
                "organization": organization, "role_codes": _codes(role_codes), "site_codes": _codes(site_codes),
                "employment_status": employment_status, "supervisor_id": supervisor_id or None,
                "shift_start_hour": start_hour, "shift_end_hour": end_hour,
                "revision": 0, "updated_at": self.now_iso(),
            })
            return self._commit_worker(worker_id, "WORKER_REGISTERED", None, actor=actor)

    def update_worker(self, worker_id: str, reason: str, *, role_codes: Any = None, site_codes: Any = None,
                      organization: Optional[str] = None, supervisor_id: Any = _UNSET, actor: Any = None,
                      **extra: Any) -> Dict[str, Any]:
        _reject_unlisted(extra)
        reason = self._reason(reason)
        with self._tx():
            worker = self._worker(worker_id)
            fields: Dict[str, Any] = {}
            if role_codes is not None:
                fields["role_codes"] = _codes(role_codes)
            if site_codes is not None:
                fields["site_codes"] = _codes(site_codes)
            if organization is not None:
                if not str(organization).strip():
                    raise ValueError("organization cannot be empty")
                fields["organization"] = str(organization).strip()
            if supervisor_id is not _UNSET:
                if supervisor_id:
                    if supervisor_id == worker_id:
                        raise ValueError("A worker cannot supervise themselves")
                    self._worker(supervisor_id)
                fields["supervisor_id"] = supervisor_id or None
            fields = {key: value for key, value in fields.items() if worker[key] != value}
            if not fields:
                raise ValueError("No changes to apply")
            before = self._worker_document(worker_id)
            self.store.update("worker", "worker_id", worker_id, fields)
            return self._commit_worker(worker_id, "WORKER_UPDATED", before, actor=actor, reason=reason)

    def set_employment_status(self, worker_id: str, status: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        status = str(status or "").upper()
        if status not in EMPLOYMENT_STATUSES:
            raise ValueError(f"Unknown employment_status {status!r} (known: {list(EMPLOYMENT_STATUSES)})")
        reason = self._reason(reason)
        with self._tx():
            worker = self._worker(worker_id)
            if worker["employment_status"] == status:
                raise Conflict(f"{worker_id} is already {status}")
            before = self._worker_document(worker_id)
            self.store.update("worker", "worker_id", worker_id, {"employment_status": status})
            return self._commit_worker(worker_id, "EMPLOYMENT_STATUS_CHANGED", before, actor=actor, reason=reason)

    # ---- credentials --------------------------------------------------- #
    def list_credential_definitions(self) -> List[Dict[str, Any]]:
        return self.store.select("credential_definition", order="code")

    def ensure_credential_definition(self, code: str, name: Optional[str] = None,
                                     renewal_period_days: Optional[int] = 365) -> Dict[str, Any]:
        code = str(code or "").strip()
        if not code:
            raise ValueError("A credential code is required")
        with self._tx():
            existing = self.store.get("credential_definition", "code", code)
            if existing is not None:
                return existing
            row = {
                "code": code, "name": name or code.replace("_", " ").title(), "issuer_types": ["INTERNAL"],
                "renewal_period_days": renewal_period_days, "scope_dimensions": ["site"],
            }
            self.store.insert("credential_definition", row)
            return row

    def issue_credential(self, worker_id: str, code: str, issuer: str = "Site Training Office", *,
                         effective_from: Any = None, expires_at: Any = None, equipment_scope: Any = (),
                         task_scope: Any = (), site_scope: Any = (), supervision_requirement: Optional[str] = None,
                         verification_status: str = "SOURCE_VERIFIED", credential_number: Optional[str] = None,
                         actor: Any = None) -> Dict[str, Any]:
        verification_status = str(verification_status or "").upper()
        if verification_status not in VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {list(VERIFICATION_STATUSES)} "
                             "(use revoke_credential to revoke)")
        issuer = str(issuer or "").strip()
        if not issuer:
            raise ValueError("issuer is required")
        with self._tx():
            worker = self._worker(worker_id)
            if worker["employment_status"] == "TERMINATED":
                raise Conflict(f"{worker_id} is terminated")
            definition = self.store.get("credential_definition", "code", code)
            if definition is None:
                raise ValueError(f"Unknown credential code {code!r}")
            start = to_datetime(effective_from) or self.now()
            end = to_datetime(expires_at)
            if end is None and definition["renewal_period_days"]:
                end = start + timedelta(days=definition["renewal_period_days"])
            if end is not None and end <= start:
                raise ValueError("expires_at must be after effective_from")
            before = self._worker_document(worker_id)
            credential_id = self.store.next_id("CRD-", "worker_credential", "credential_id", 6)
            number = credential_number or f"{code.upper()}-{worker_id}-{credential_id}"
            self.store.insert("worker_credential", {
                "credential_id": credential_id, "worker_id": worker_id, "code": code, "issuer": issuer,
                "credential_number_hash": sha256_text(str(number)), "verification_status": verification_status,
                "effective_from": iso(start), "expires_at": iso(end),
                "equipment_scope": _codes(equipment_scope), "task_scope": _codes(task_scope),
                "site_scope": _codes(site_scope), "supervision_requirement": supervision_requirement,
                "revoked_at": None, "revocation_reason": None,
            })
            return self._commit_worker(worker_id, "CREDENTIAL_ISSUED", before, actor=actor, subject_id=credential_id)

    def renew_credential(self, credential_id: str, expires_at: Any = None, actor: Any = None) -> Dict[str, Any]:
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is revoked — issue a new credential instead")
            definition = self.store.get("credential_definition", "code", credential["code"])
            new_end = to_datetime(expires_at)
            if new_end is None:
                if not definition or not definition["renewal_period_days"]:
                    raise ValueError("expires_at is required for this credential")
                new_end = self.now() + timedelta(days=definition["renewal_period_days"])
            current_end = parse_iso(credential["expires_at"])
            if current_end is not None and new_end <= current_end:
                raise ValueError("A renewal must extend the current expiry")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {"expires_at": iso(new_end)})
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_RENEWED", before, actor=actor,
                                       subject_id=credential_id)

    def verify_credential(self, credential_id: str, verification_status: str, actor: Any = None) -> Dict[str, Any]:
        status = str(verification_status or "").upper()
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"verification_status must be one of {list(VERIFICATION_STATUSES)}")
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is revoked")
            if credential["verification_status"] == status:
                raise Conflict(f"{credential_id} is already {status}")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {"verification_status": status})
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_VERIFIED", before, actor=actor,
                                       subject_id=credential_id)

    def revoke_credential(self, credential_id: str, reason: str, actor: Any = None) -> Dict[str, Any]:
        reason = self._reason(reason)
        with self._tx():
            credential = self._credential(credential_id)
            if credential["verification_status"] == "REVOKED":
                raise Conflict(f"{credential_id} is already revoked")
            before = self._worker_document(credential["worker_id"])
            self.store.update("worker_credential", "credential_id", credential_id, {
                "verification_status": "REVOKED", "revoked_at": self.now_iso(), "revocation_reason": reason,
            })
            return self._commit_worker(credential["worker_id"], "CREDENTIAL_REVOKED", before, actor=actor,
                                       reason=reason, subject_id=credential_id)

    # ---- training ------------------------------------------------------ #
    def record_training(self, worker_id: str, course_code: str, course_version: str = "1",
                        completed_at: Any = None, expires_at: Any = None, actor: Any = None) -> Dict[str, Any]:
        course_code = str(course_code or "").strip()
        if not course_code:
            raise ValueError("course_code is required")
        with self._tx():
            self._worker(worker_id)
            completed = to_datetime(completed_at) or self.now()
            end = to_datetime(expires_at)
            if end is not None and end <= completed:
                raise ValueError("expires_at must be after completed_at")
            before = self._worker_document(worker_id)
            training_id = self.store.next_id("TRN-", "training_completion", "training_id", 6)
            self.store.insert("training_completion", {
                "training_id": training_id, "worker_id": worker_id, "course_code": course_code,
                "course_version": str(course_version or "1"), "completed_at": iso(completed), "expires_at": iso(end),
            })
            return self._commit_worker(worker_id, "TRAINING_RECORDED", before, actor=actor, subject_id=training_id)

    # ---- reads --------------------------------------------------------- #
    def get_worker(self, worker_id: str) -> Dict[str, Any]:
        record = self._worker_document(worker_id)
        now = self.now()
        names = {d["code"]: d["name"] for d in self.list_credential_definitions()}
        record["credentials"] = [
            {**c, "name": names.get(c["code"], c["code"]), "validity": self._credential_validity(c, record, now)}
            for c in record["credentials"]
        ]
        record["training"] = [{**t, "validity": self._training_validity(t, now)} for t in record["training"]]
        record["valid_credential_codes"] = list(dict.fromkeys(
            c["code"] for c in record["credentials"] if c["validity"] in VALID_CREDENTIAL_STATES))
        return record

    @staticmethod
    def _worker_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "worker_id": record["worker_id"], "display_name": record["display_name"],
            "worker_type": record["worker_type"], "organization": record["organization"],
            "role_codes": record["role_codes"], "site_codes": record["site_codes"],
            "employment_status": record["employment_status"], "supervisor_id": record["supervisor_id"],
            "revision": record["revision"], "valid_credential_codes": record["valid_credential_codes"],
            "credentials": [{"credential_id": c["credential_id"], "code": c["code"], "validity": c["validity"]}
                            for c in record["credentials"]],
        }

    def list_workers(self, site: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            rows = self.store.select("worker", "employment_status = ?", (str(status).upper(),), order="worker_id")
        else:
            rows = self.store.select("worker", order="worker_id")
        if site:
            rows = [row for row in rows if site in row["site_codes"]]
        return [self._worker_summary(self.get_worker(row["worker_id"])) for row in rows]

    def worker_history(self, worker_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        self._worker(worker_id)
        return self.history([worker_id], limit)

    def valid_credential_codes(self, worker_id: str) -> List[str]:
        return self.get_worker(worker_id)["valid_credential_codes"]
