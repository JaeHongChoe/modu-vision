"""S1-10: job ledger events after a cursor, for the request's own workspace and project.

A client that was disconnected (a dropped WebSocket, a sleeping laptop, a restarted renderer) reads what the ledger
recorded after its cursor instead of trusting the live messages it happened to receive. A cursor names one project
stream of one ledger and the event it stopped at; a cursor that cannot be continued (no cursor yet, another project or
ledger, a ledger that was restored or no longer holds that event) is answered with `reset`, a fresh cursor and no
events: the client takes that cursor, then reloads its authoritative snapshot, so nothing recorded after it is missed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/job-events", tags=["job-events"])


def _digest(*parts: Any) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _cursor(stream: str, ledger_id: str, position: int, anchor: Optional[tuple]) -> str:
    # The anchor names the event at the position without revealing it (it may belong to another project).
    return f"v1.{stream}.{position}.{_digest(ledger_id, *anchor)[:16] if anchor else '0'}"


def _position(cursor: str, stream: str, ledger_id: str, store) -> Optional[int]:
    """The ledger position a cursor of this stream continues from, or None when it cannot be continued."""
    try:
        version, owner, position, anchor = cursor.split(".")
        value = int(position)
    except ValueError:
        return None
    if version != "v1" or owner != stream or not 0 <= value < 2 ** 63:  # SQLite row numbers are signed 64-bit
        return None
    if value == 0:
        return 0 if anchor == "0" else None
    held = store.event_at(value)
    return value if held is not None and _digest(ledger_id, *held)[:16] == anchor else None


def _event(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
    except ValueError:
        payload = None
    return {"id": f"{row['job_id']}:{row['seq']}", "job_id": row["job_id"], "kind": row["kind"], "seq": row["seq"],
            "event": row["event"], "from_state": row["from_state"], "to_state": row["to_state"],
            "at": row["at_ns"] / 1e9, "payload": payload}


@router.get("")
def job_events(request: Request, after: Optional[str] = Query(None, max_length=200),
               limit: int = Query(200, ge=1, le=500)) -> Dict[str, Any]:
    from backend.api.routes_training import _LEDGER_ERRORS, job_ledger
    from backend.contracts.context import get_project_context
    context = get_project_context(request)
    project_key = request.app.state.context_registry.project_key(context)
    try:
        store = job_ledger()
        ledger_id = store.ledger_id()
        stream = _digest(ledger_id, context.workspace_id, project_key)[:24]
        position = _position(after, stream, ledger_id, store) if after else None
        if position is None:
            # No events with a reset: everything up to the newest event is in the snapshot the client reloads next.
            newest = store.newest_event()
            return {"cursor": _cursor(stream, ledger_id, newest[0], newest[1:]) if newest else _cursor(stream, ledger_id, 0, None),
                    "events": [], "reset": True, "reason": "cursor_expired" if after else "initial", "more": False}
        rows, head, more = store.events_after(context.workspace_id, project_key, position, limit)
        anchor = store.event_at(head) if head else None
    except _LEDGER_ERRORS as exc:
        raise HTTPException(503, f"The job ledger could not be read: {exc}") from exc
    return {"cursor": _cursor(stream, ledger_id, head, anchor), "events": [_event(row) for row in rows],
            "reset": False, "reason": None, "more": more}
