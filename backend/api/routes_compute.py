"""Desktop-token-protected compute profile management and connection probe."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.remote.profiles import ComputeProfile, get_profile_store
from backend.remote.ssh_transport import SSHTransport


router = APIRouter(prefix="/api/compute", tags=["compute"])


class ProfileInput(ComputeProfile):
    id: str = Field(default_factory=lambda: uuid4().hex)


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compute_profile_id: str | None


@router.get("/profiles")
def list_profiles() -> dict:
    return {"profiles": [profile.model_dump() for profile in get_profile_store().list()]}


@router.post("/profiles", status_code=201)
def save_profile(profile: ProfileInput) -> dict:
    saved = get_profile_store().save(ComputeProfile.model_validate(profile.model_dump()))
    return {"profile": saved.model_dump()}


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str) -> Response:
    if not get_profile_store().delete(profile_id):
        raise HTTPException(status_code=404, detail="Compute profile not found")
    return Response(status_code=204)


@router.get("/selection")
def get_selection() -> dict:
    return {"compute_profile_id": get_profile_store().get_selected()}


@router.put("/selection")
def set_selection(selection: SelectionInput) -> dict:
    try:
        selected = get_profile_store().set_selected(selection.compute_profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Compute profile not found") from None
    return {"compute_profile_id": selected}


@router.post("/profiles/{profile_id}/probe")
def probe_profile(profile_id: str) -> dict:
    profile = get_profile_store().get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Compute profile not found")
    return SSHTransport().probe(profile)


@router.get("/reservations")
def reservations():
    from backend.engine.shared_scheduler import shared_leases
    import time
    rows = shared_leases().list()
    return {"reservations": [{**row, "expired": row["expires"] < time.time(), "requires_reconciliation": bool(row["remote"] and (row["uncertain"] or row["expires"] < time.time()))} for row in rows]}
