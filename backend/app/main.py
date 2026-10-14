from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.health import router as health_router
from backend.app.api.inspections import router as inspections_router
from backend.app.api.auth import router as auth_router
from backend.app.api.patterns import router as patterns_router
from backend.app.api.roles import router as roles_router
from backend.app.api.risk_targeting import router as risk_targeting_router
from backend.app.core.config import get_cors_origins, get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Inspection-assistance API. Results are not legally binding decisions.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(settings),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(self), geolocation=(self), microphone=()"
    return response


app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(inspections_router, prefix="/api")
app.include_router(patterns_router, prefix="/api")
app.include_router(roles_router, prefix="/api")
app.include_router(risk_targeting_router, prefix="/api")
