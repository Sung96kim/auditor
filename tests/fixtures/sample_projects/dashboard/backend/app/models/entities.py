"""Core domain models."""

from dataclasses import dataclass

from pydantic import BaseModel

# Rule metadata that really belongs on the ServiceAlert subclass as ClassVars.
SERVICE_ALERT_TITLE = "Service degraded"
SERVICE_ALERT_SEVERITY = "high"
SERVICE_ALERT_RUNBOOK = "https://runbooks.pulse/service-degraded"


@dataclass
class CachedMetric:
    name: str
    value: float
    captured_at: str


class Metric(BaseModel):
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


class Alert(BaseModel):
    id: str
    service: str
    severity: str


class ServiceAlert(Alert):
    runbook: str


def build_metric(
    service_name,
    service_region,
    service_tier,
    metric_name,
    metric_unit,
    metric_value,
    window_start,
    window_end,
    window_seconds,
    sample_count,
    sample_min,
    sample_max,
    sample_p99,
):
    return Metric(
        service_name=service_name,
        service_region=service_region,
        service_tier=service_tier,
        metric_name=metric_name,
        metric_unit=metric_unit,
        metric_value=metric_value,
        window_start=window_start,
        window_end=window_end,
        window_seconds=window_seconds,
        sample_count=sample_count,
        sample_min=sample_min,
        sample_max=sample_max,
        sample_p99=sample_p99,
    )


Alert.model_rebuild()
