from typing import Literal

from pydantic import BaseModel


class DependencyHealth(BaseModel):
    status: Literal["up", "down"]
    detail: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[str, DependencyHealth]
