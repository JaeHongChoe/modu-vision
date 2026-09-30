from fastapi import APIRouter
from backend.engine.model_catalog import model_family_catalog

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/capabilities")
def capabilities():
    return model_family_catalog()
