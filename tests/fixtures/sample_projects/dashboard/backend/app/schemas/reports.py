"""API response schemas for the reports endpoints."""

from pydantic import BaseModel


class ReportMetric(BaseModel):
    # Same field shape as models.entities.Metric — these have drifted into two models.
    service_name: str
    service_region: str
    service_tier: str
    metric_name: str
    metric_unit: str
    metric_value: float
    window_start: str
    window_end: str
    window_seconds: int
    sample_count: int
    sample_min: float
    sample_max: float
    sample_p99: float


class ReportPage(BaseModel):
    items: list[ReportMetric]
    total: int
    cursor: str | None
