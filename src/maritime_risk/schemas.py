from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Port(BaseModel):
    code: str
    name: str
    country: str
    lat: float
    lon: float


class RouteLeg(BaseModel):
    origin: Port
    destination: Port
    distance_nm: float
    baseline_transit_days: float


class DelayRisk(BaseModel):
    leg: RouteLeg
    risk_level: Literal["low", "medium", "high"]
    delay_days_estimate: float
    confidence: float
    factors: list[str]


class RerouteOption(BaseModel):
    route: list[RouteLeg]
    total_transit_days: float
    delta_vs_planned: float
    residual_risk: Literal["low", "medium", "high"]
    rationale: str


class ShipmentAssessment(BaseModel):
    shipment_id: str
    overall_risk: Literal["low", "medium", "high"]
    predicted_delay_days: float
    leg_risks: list[DelayRisk]
    reroute_options: list[RerouteOption]
    recommendation: str
    confidence: float
