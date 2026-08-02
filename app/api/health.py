from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", summary="Health check")
def health_check() -> dict[str, str]:
    """Return a simple health status payload."""
    return {"status": "ok"}
