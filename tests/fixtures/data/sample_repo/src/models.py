"""Domain models. A flat wall model + a constructor-wall call site, plus a clean composed
model that must NOT fire. Edge: a @dataclass in a Pydantic project (PY-OOP-DATACLASS-IN-PYDANTIC)."""

from dataclasses import dataclass

from pydantic import BaseModel


class OpportunityRecord(BaseModel):
    # PY-OOP-FLAT-FIELD-MODEL (>=12 cohesive flat fields that should be composed)
    surface: str
    tool_name: str
    tool_namespace: str
    goal_family: str
    calls: int
    failures: int
    success_rate: float
    sample_args: str
    sample_error: str
    sample_task_id: str
    started_at: float
    ended_at: float


def make_wall() -> OpportunityRecord:
    # PY-OOP-CONSTRUCTOR-WALL (12 kwargs)
    return OpportunityRecord(
        surface="api",
        tool_name="search",
        tool_namespace="tools",
        goal_family="retrieval",
        calls=10,
        failures=2,
        success_rate=0.8,
        sample_args="{}",
        sample_error="",
        sample_task_id="t1",
        started_at=0.0,
        ended_at=1.0,
    )


@dataclass(frozen=True)
class CacheKey:
    # PY-OOP-DATACLASS-IN-PYDANTIC (record dataclass in a pydantic project)
    namespace: str
    digest: str


class Coordinates(BaseModel):
    lat: float
    lon: float


class Place(BaseModel):
    # negative: composed, small — must NOT trip flat-field
    name: str
    coords: Coordinates
