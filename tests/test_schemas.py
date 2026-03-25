from __future__ import annotations

import pytest
from pydantic import ValidationError

from predictor.schemas import (
    DelayRisk,
    Port,
    RerouteOption,
    RouteLeg,
    ShipmentAssessment,
)


def _port(code: str = "CNSHA", name: str = "Shanghai", country: str = "CN") -> Port:
    return Port(code=code, name=name, country=country, lat=31.23, lon=121.47)


def _leg() -> RouteLeg:
    return RouteLeg(
        origin=_port(),
        destination=_port("SGSIN", "Singapore", "SG"),
        distance_nm=2200.0,
        baseline_transit_days=6.5,
    )


def test_port_valid() -> None:
    p = _port()
    assert p.code == "CNSHA"
    assert p.lat == 31.23


def test_port_missing_field() -> None:
    with pytest.raises(ValidationError):
        Port(code="CNSHA", name="Shanghai", lat=31.23, lon=121.47)  # type: ignore[call-arg]


def test_route_leg_valid() -> None:
    leg = _leg()
    assert leg.distance_nm == 2200.0
    assert leg.origin.code == "CNSHA"


def test_delay_risk_valid() -> None:
    risk = DelayRisk(
        leg=_leg(),
        risk_level="high",
        delay_days_estimate=3.5,
        confidence=0.8,
        factors=["NOAA storm alert", "gCaptain: Suez delays"],
    )
    assert risk.risk_level == "high"
    assert len(risk.factors) == 2


def test_delay_risk_invalid_level() -> None:
    with pytest.raises(ValidationError):
        DelayRisk(
            leg=_leg(),
            risk_level="extreme",  # type: ignore[arg-type]
            delay_days_estimate=3.5,
            confidence=0.8,
            factors=[],
        )


def test_reroute_option_valid() -> None:
    opt = RerouteOption(
        route=[_leg()],
        total_transit_days=12.0,
        delta_vs_planned=6.0,
        residual_risk="low",
        rationale="Cape route avoids Suez disruption",
    )
    assert opt.delta_vs_planned == 6.0


def test_shipment_assessment_valid() -> None:
    assessment = ShipmentAssessment(
        shipment_id="SHP-001",
        overall_risk="high",
        predicted_delay_days=4.5,
        leg_risks=[
            DelayRisk(
                leg=_leg(),
                risk_level="high",
                delay_days_estimate=4.5,
                confidence=0.85,
                factors=["storm", "port congestion news"],
            ),
        ],
        reroute_options=[
            RerouteOption(
                route=[_leg()],
                total_transit_days=12.0,
                delta_vs_planned=6.0,
                residual_risk="low",
                rationale="Avoid Suez",
            ),
        ],
        recommendation="Divert via Cape of Good Hope",
        confidence=0.85,
    )
    assert assessment.shipment_id == "SHP-001"
    assert len(assessment.leg_risks) == 1
    assert len(assessment.reroute_options) == 1


def test_shipment_assessment_no_reroutes() -> None:
    assessment = ShipmentAssessment(
        shipment_id="SHP-002",
        overall_risk="low",
        predicted_delay_days=0.0,
        leg_risks=[],
        reroute_options=[],
        recommendation="Continue as planned",
        confidence=0.95,
    )
    assert assessment.overall_risk == "low"
    assert assessment.reroute_options == []
