import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_v1_router
from main import app as legacy_app  # Inherits production routes, auth, voice & scheduler

app = legacy_app

# Mount v1 modular router alongside legacy API
if not any(route.path.startswith("/api/v1") for route in app.routes):
    app.include_router(api_v1_router)

# Mount frontend/static if directory exists
frontend_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "static")
if os.path.exists(frontend_static_dir):
    try:
        app.mount("/app", StaticFiles(directory=frontend_static_dir, html=True), name="frontend_app")
    except Exception:
        pass
