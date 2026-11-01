from fastapi import APIRouter
from .health import router as health_router
from .auth import router as auth_router
from .users import router as users_router
from .shops import router as shops_router
from .customers import router as customers_router
from .transactions import router as transactions_router
from .payments import router as payments_router
from .voice import router as voice_router
from .reports import router as reports_router
from .dashboard import router as dashboard_router
from .notifications import router as notifications_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(shops_router)
api_v1_router.include_router(customers_router)
api_v1_router.include_router(transactions_router)
api_v1_router.include_router(payments_router)
api_v1_router.include_router(voice_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(notifications_router)
