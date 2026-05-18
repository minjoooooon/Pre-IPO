from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness probe — always 200 if the process is up.

    Adapter readiness is reported separately at /api/status (P5.5)."""
    return {"status": "ok"}
