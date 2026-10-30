from fastapi import APIRouter

from . import admin, agents, graph, investigations, materials, reports, social

router = APIRouter(prefix="/api/v1")
for module in (investigations, graph, reports, agents, materials, social, admin):
    router.include_router(module.router)

__all__ = ["router"]
