"""Liveness probe used by the hosting platform and CI smoke checks."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")
