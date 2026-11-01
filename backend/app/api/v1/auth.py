from fastapi import APIRouter
router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/status")
def auth_status():
    return {"authenticated": True}
