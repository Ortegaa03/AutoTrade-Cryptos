from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .config import (
    OWNER_WALLET,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)


def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _headers(*, prefer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _base() -> str:
    return SUPABASE_URL.rstrip("/") + "/rest/v1"


def _parse_ts(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return str(value)


def op_to_row(op_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Serializa Operation.to_dict (sin flags runtime) → fila Supabase."""
    return {
        "id": op_dict["id"],
        "owner_wallet": op_dict.get("owner_wallet") or OWNER_WALLET,
        "token_address": op_dict["token_address"],
        "buy_price_usd": float(op_dict["buy_price_usd"]),
        "sell_price_usd": float(op_dict["sell_price_usd"]),
        "usdc_amount": float(op_dict["usdc_amount"]),
        "demo": bool(op_dict.get("demo", True)),
        "cycle_seconds": int(op_dict.get("cycle_seconds") or 86400),
        "status": op_dict.get("status") or "draft",
        "phase": op_dict.get("phase") or "waiting_buy",
        "token": op_dict.get("token") or {},
        "last_price_usd": op_dict.get("last_price_usd"),
        "bought_token_amount_raw": int(op_dict.get("bought_token_amount_raw") or 0),
        "buy_amount_out": op_dict.get("buy_amount_out"),
        "sell_amount_out": op_dict.get("sell_amount_out"),
        "buy_tx": op_dict.get("buy_tx"),
        "sell_tx": op_dict.get("sell_tx"),
        "demo_usdc_balance": float(op_dict.get("demo_usdc_balance") or 1000),
        "estimated": op_dict.get("estimated") or {},
        "realized_profit_usd": op_dict.get("realized_profit_usd"),
        "realized_total_usd": op_dict.get("realized_total_usd"),
        "error": op_dict.get("error"),
        "mode": op_dict.get("mode") or "classic",
        "grid_count": int(op_dict.get("grid_count") or 0),
        "grid_levels": op_dict.get("grid_levels") or [],
        "last_cycle_at": _parse_ts(op_dict.get("last_cycle_at")),
        "completed_at": _parse_ts(op_dict.get("completed_at")),
        "created_at": _parse_ts(op_dict.get("created_at"))
        or datetime.now(timezone.utc).isoformat(),
        "updated_at": _parse_ts(op_dict.get("updated_at"))
        or datetime.now(timezone.utc).isoformat(),
    }


def row_to_op_fields(row: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "token_address": row["token_address"],
        "buy_price_usd": float(row["buy_price_usd"]),
        "sell_price_usd": float(row["sell_price_usd"]),
        "usdc_amount": float(row["usdc_amount"]),
        "demo": bool(row.get("demo", True)),
        "cycle_seconds": int(row.get("cycle_seconds") or 86400),
        "status": row.get("status") or "draft",
        "phase": row.get("phase") or "waiting_buy",
        "token": row.get("token") or {},
        "last_price_usd": row.get("last_price_usd"),
        "bought_token_amount_raw": int(row.get("bought_token_amount_raw") or 0),
        "buy_amount_out": row.get("buy_amount_out"),
        "sell_amount_out": row.get("sell_amount_out"),
        "buy_tx": row.get("buy_tx"),
        "sell_tx": row.get("sell_tx"),
        "demo_usdc_balance": float(row.get("demo_usdc_balance") or 1000),
        "estimated": row.get("estimated") or {},
        "realized_profit_usd": row.get("realized_profit_usd"),
        "realized_total_usd": row.get("realized_total_usd"),
        "error": row.get("error"),
        "created_at": _parse_ts(row.get("created_at")) or "",
        "updated_at": _parse_ts(row.get("updated_at")) or "",
        "completed_at": _parse_ts(row.get("completed_at")),
        "last_cycle_at": _parse_ts(row.get("last_cycle_at")),
        "events": events,
        "mode": row.get("mode") or "classic",
        "grid_count": int(row.get("grid_count") or 0),
        "grid_levels": row.get("grid_levels") or [],
        "owner_wallet": row.get("owner_wallet") or OWNER_WALLET,
    }


def events_to_rows(operation_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ev in events or []:
        key = str(ev.get("id") or "").strip()
        if not key:
            continue
        rows.append(
            {
                "operation_id": operation_id,
                "event_key": key,
                "kind": str(ev.get("kind") or "event"),
                "title": str(ev.get("title") or ""),
                "detail": str(ev.get("detail") or ""),
                "data": ev.get("data") or {},
                "created_at": _parse_ts(ev.get("ts"))
                or datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


def rows_to_events(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row.get("event_key") or str(row.get("id") or "")[:8],
                "ts": _parse_ts(row.get("created_at")) or "",
                "kind": row.get("kind") or "event",
                "title": row.get("title") or "",
                "detail": row.get("detail") or "",
                "data": row.get("data") or {},
            }
        )
    out.sort(key=lambda e: e.get("ts") or "")
    return out


class SupabaseStore:
    def __init__(self) -> None:
        self.enabled = supabase_enabled()

    async def fetch_all_operations(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        async with httpx.AsyncClient(timeout=45.0) as client:
            ops_resp = await client.get(
                f"{_base()}/operations",
                headers=_headers(),
                params={"select": "*", "order": "updated_at.desc"},
            )
            ops_resp.raise_for_status()
            ops_rows = ops_resp.json()
            if not ops_rows:
                return []

            ev_resp = await client.get(
                f"{_base()}/operation_events",
                headers=_headers(),
                params={"select": "*", "order": "created_at.asc"},
            )
            ev_resp.raise_for_status()
            all_events = ev_resp.json()

        by_op: Dict[str, List[Dict[str, Any]]] = {}
        for ev in all_events:
            oid = str(ev.get("operation_id"))
            by_op.setdefault(oid, []).append(ev)

        result: List[Dict[str, Any]] = []
        for row in ops_rows:
            oid = str(row["id"])
            events = rows_to_events(by_op.get(oid, []))
            result.append(row_to_op_fields(row, events))
        return result

    async def fetch_running_operations(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        async with httpx.AsyncClient(timeout=45.0) as client:
            ops_resp = await client.get(
                f"{_base()}/operations",
                headers=_headers(),
                params={
                    "select": "*",
                    "status": "eq.running",
                    "order": "updated_at.asc",
                },
            )
            ops_resp.raise_for_status()
            ops_rows = ops_resp.json()
            if not ops_rows:
                return []
            ids = ",".join(str(r["id"]) for r in ops_rows)
            ev_resp = await client.get(
                f"{_base()}/operation_events",
                headers=_headers(),
                params={
                    "select": "*",
                    "operation_id": f"in.({ids})",
                    "order": "created_at.asc",
                },
            )
            ev_resp.raise_for_status()
            all_events = ev_resp.json()

        by_op: Dict[str, List[Dict[str, Any]]] = {}
        for ev in all_events:
            oid = str(ev.get("operation_id"))
            by_op.setdefault(oid, []).append(ev)

        return [
            row_to_op_fields(row, rows_to_events(by_op.get(str(row["id"]), [])))
            for row in ops_rows
        ]

    async def upsert_operation(self, op_dict: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        row = op_to_row(op_dict)
        events = events_to_rows(str(op_dict["id"]), op_dict.get("events") or [])
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{_base()}/operations",
                headers=_headers(prefer="resolution=merge-duplicates,return=minimal"),
                params={"on_conflict": "id"},
                content=json.dumps(row),
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Supabase upsert op: {resp.status_code} {resp.text}")

            if events:
                ev_resp = await client.post(
                    f"{_base()}/operation_events",
                    headers=_headers(prefer="resolution=merge-duplicates,return=minimal"),
                    params={"on_conflict": "operation_id,event_key"},
                    content=json.dumps(events),
                )
                if ev_resp.status_code >= 400:
                    raise RuntimeError(
                        f"Supabase upsert events: {ev_resp.status_code} {ev_resp.text}"
                    )

    async def delete_operation(self, op_id: str) -> None:
        if not self.enabled:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            # cascade borra events
            resp = await client.delete(
                f"{_base()}/operations",
                headers=_headers(prefer="return=minimal"),
                params={"id": f"eq.{op_id}"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Supabase delete op: {resp.status_code} {resp.text}")

    async def start_cron_run(self, job_name: str = "daily-cycles") -> str:
        if not self.enabled:
            return ""
        payload = {
            "job_name": job_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ok": None,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "details": {},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_base()}/cron_runs",
                headers=_headers(prefer="return=representation"),
                content=json.dumps(payload),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return str(data[0]["id"])
            if isinstance(data, dict):
                return str(data.get("id") or "")
            return ""

    async def finish_cron_run(
        self,
        run_id: str,
        *,
        ok: bool,
        processed: int,
        succeeded: int,
        failed: int,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        if not self.enabled or not run_id:
            return
        patch = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "details": details or {},
            "error": error,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.patch(
                f"{_base()}/cron_runs",
                headers=_headers(prefer="return=minimal"),
                params={"id": f"eq.{run_id}"},
                content=json.dumps(patch),
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Supabase cron finish: {resp.status_code} {resp.text}")


store = SupabaseStore()
