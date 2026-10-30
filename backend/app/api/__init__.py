from fastapi import APIRouter

from . import admin, graph, investigations, reports

router = APIRouter(prefix="/api/v1")
for module in (investigations, graph, reports, admin):
    router.include_router(module.router)

__all__ = ["router"]
