from fastapi import APIRouter

from . import admin, agents, graph, investigations, reports

router = APIRouter(prefix="/api/v1")
for module in (investigations, graph, reports, agents, admin):
    router.include_router(module.router)

__all__ = ["router"]
