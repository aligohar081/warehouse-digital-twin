"""REST routes for the floor's operations (multi-embodiment spec §11.5, §14):
the shift and its panel, the orders, the stock ledger, the people, the
conveyor and sorter, and faults injected on demand.

Every handler holds twin.lock while it reads or changes any of them: the
shift engine, the order book, the ledger, the equipment and the people
helpers take no lock of their own (the simulator tick holds it too). Errors
come back in the same JSON shape as the rest of the API: an unknown id or
fault kind → 404, a state conflict (the classic floor has no shift engine and
no conveyor) → 409, bad input → 400. A call that changes the shift publishes
the new state, so the dashboard sees it at once.
"""
from __future__ import annotations

import functools
from typing import Any, Dict, Optional, Sequence

from flask import jsonify, request

from .faults import FAULT_RISKS, fault_kind
from .operations.orders import DONE, FAILED, IN_PROGRESS, OPEN, ORDER_KINDS

ORDER_STATUSES = (OPEN, IN_PROGRESS, DONE, FAILED)
#: What POST /api/shift/config may change (ShiftEngine.configure's arguments).
SHIFT_CONFIG_FIELDS = ("pace", "seed", "rates")
#: How many orders GET /api/orders lists when no limit is given.
DEFAULT_ORDER_LIMIT = 100
#: How many of the latest hand-off records GET /api/equipment lists.
RECENT_HANDOFFS = 20


def register_operations_routes(app: Any, twin: Any, broadcaster: Any, api_error: Any) -> None:
    def guarded(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except api_error:
                raise
            except KeyError as exc:
                raise api_error(str(exc.args[0]) if exc.args else "Not found", status=404)
            except ValueError as exc:
                raise api_error(str(exc))
        return wrapper

    def body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        if data is None:
            data = request.form.to_dict() or {}
        if not isinstance(data, dict):
            raise api_error("Request body must be a JSON object")
        return data

    def needs_shift() -> None:
        """Only a floor with a shift engine has shift controls (409 on classic)."""
        if twin.layout_name == "classic":
            raise api_error("The classic floor has no shift engine", status=409)

    def needs_equipment() -> None:
        if twin.equipment is None:
            raise api_error(f"The {twin.layout_name} floor has no conveyor or sorter", status=409)

    def published(state: Dict[str, Any]):
        """Publish the state after a shift control, and answer with its panel."""
        broadcaster.publish("state", state)
        return jsonify({"ok": True, "shift": state["shift"]})

    def choice(key: str, allowed: Sequence[str]) -> Optional[str]:
        """An optional query filter that must be one of `allowed` (any case)."""
        value = (request.args.get(key) or "").strip().upper()
        if not value:
            return None
        if value not in allowed:
            raise api_error(f"{key} must be one of {', '.join(allowed)}", field=key)
        return value

    def limit(default: int) -> int:
        raw = (request.args.get("limit") or "").strip()
        if not raw:
            return default
        if not raw.isdigit() or int(raw) < 1:
            raise api_error("limit must be a whole number, at least 1", field="limit")
        return int(raw)

    def stock_row(location: Any) -> Dict[str, Any]:
        slot = twin.warehouse.slot(location.slot_id)
        return {**location.to_dict(), "discrepancy": location.discrepancy,
                "kind": slot.kind if slot is not None else None, "level": slot.level if slot is not None else None}

    # ---- the shift ------------------------------------------------------- #
    @app.get("/api/shift")
    @guarded
    def get_shift():
        with twin.lock:
            needs_shift()
            return jsonify({"ok": True, "shift": twin.shift.panel()})

    @app.post("/api/shift/start")
    @guarded
    def start_shift():
        with twin.lock:
            needs_shift()
            twin.shift.start()
            state = twin.snapshot()
        return published(state)

    @app.post("/api/shift/pause")
    @guarded
    def pause_shift():
        with twin.lock:
            needs_shift()
            twin.shift.pause()
            state = twin.snapshot()
        return published(state)

    @app.post("/api/shift/config")
    @guarded
    def configure_shift():
        data = body()
        with twin.lock:
            needs_shift()
            unknown = sorted(set(data) - set(SHIFT_CONFIG_FIELDS))
            if unknown:
                raise api_error(f"Unknown shift setting(s): {', '.join(unknown)} "
                                f"(known: {', '.join(SHIFT_CONFIG_FIELDS)})", field=unknown[0])
            if not data:
                raise api_error("Provide pace, seed or rates")
            twin.shift.configure(pace=data.get("pace"), seed=data.get("seed"), rates=data.get("rates"))
            state = twin.snapshot()
        return published(state)

    # ---- orders ---------------------------------------------------------- #
    @app.get("/api/orders")
    @guarded
    def list_orders():
        status, kind = choice("status", ORDER_STATUSES), choice("kind", ORDER_KINDS)
        count = limit(DEFAULT_ORDER_LIMIT)
        with twin.lock:
            book = twin.shift.orders
            return jsonify({"ok": True, "orders": book.list(status=status, kind=kind, limit=count),
                            "counts": book.counts()})

    @app.get("/api/orders/<order_id>")
    @guarded
    def get_order(order_id: str):
        with twin.lock:
            return jsonify({"ok": True, "order": twin.shift.orders.get(order_id)})

    # ---- stock, people, equipment ---------------------------------------- #
    @app.get("/api/stock")
    @guarded
    def get_stock():
        sku = (request.args.get("sku") or "").strip() or None
        with twin.lock:
            rows = [stock_row(location) for location in twin.stock.locations(sku)]
            skus = twin.stock.skus()
        totals = {"recorded_qty": sum(row["recorded_qty"] for row in rows),
                  "true_qty": sum(row["true_qty"] for row in rows)}
        return jsonify({"ok": True, "sku": sku, "locations": rows, "totals": totals, "skus": skus})

    @app.get("/api/people")
    @guarded
    def get_people():
        with twin.lock:
            rows = [{**operator.to_dict(), "in_transit": operator.in_transit} for operator in twin.operators.values()]
        return jsonify({"ok": True, "people": rows})

    @app.get("/api/equipment")
    @guarded
    def get_equipment():
        with twin.lock:
            needs_equipment()
            equipment = twin.equipment
            recent = list(equipment.handoffs)[-RECENT_HANDOFFS:]
            return jsonify({"ok": True, "equipment": equipment.view(),
                            "handoffs": [handoff.to_dict() for handoff in reversed(recent)]})

    # ---- faults ---------------------------------------------------------- #
    @app.post("/api/faults/<kind>")
    @guarded
    def inject_fault(kind: str):
        key = fault_kind(kind)
        if key is None:
            raise api_error(f"Unknown fault kind {kind!r} (known: {', '.join(sorted(FAULT_RISKS))})", status=404)
        count = body().get("count")
        with twin.lock:
            armed = twin.faults.arm(key, 1 if count is None else count)
        return jsonify({"ok": True, "kind": key, "armed": armed})
