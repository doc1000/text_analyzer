"""Optional FastAPI router. NOT mounted anywhere. fastapi is imported lazily in the factory.

make_router(hierarchy_fn, capture_fn, find_fn):
  hierarchy_fn(days:int) -> packed_tree dict (harvest payload)
  capture_fn(payload:dict) -> item_id            (e.g. CaptureWriter.submit(...).result())
  find_fn(query:str, k:int, days:Optional[int]) -> list[dict]
"""
from __future__ import annotations

from typing import Callable, Optional

from .adapter import to_vb_hierarchy


def make_router(hierarchy_fn: Callable, capture_fn: Callable, find_fn: Callable):
    from fastapi import APIRouter, Body, HTTPException  # lazy: optional dependency

    router = APIRouter(prefix="/do", tags=["dataorchard"])

    @router.get("/topics/hierarchy")
    def topics_hierarchy(days: int = 30):
        return to_vb_hierarchy(hierarchy_fn(days), days)

    @router.post("/capture")
    def capture(payload: dict = Body(...)):
        try:
            return {"item_id": capture_fn(payload), "status": "saved"}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/find")
    def find(q: str, k: int = 10, days: Optional[int] = None):
        return {"results": find_fn(q, k, days)}

    return router
