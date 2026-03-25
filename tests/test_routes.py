from __future__ import annotations

import json
from unittest.mock import patch

from maritime_risk.agents.routes import calculate_alternative_route, calculate_route


def _mock_searoute(origin: str, destination: str, **kwargs: object) -> dict[str, object]:
    restrictions = kwargs.get("restrictions", [])
    if "suez" in restrictions:
        return {"properties": {"length": 12500.0}}
    return {"properties": {"length": 8400.0}}


def test_calculate_route_returns_valid_json() -> None:
    with patch("maritime_risk.agents.routes.sr.searoute", side_effect=_mock_searoute):
        raw = calculate_route.__wrapped__("Shanghai", "Rotterdam")

    result = json.loads(raw)
    assert result["origin"] == "Shanghai"
    assert result["destination"] == "Rotterdam"
    assert result["distance_nm"] == 8400.0
    assert result["transit_days"] > 0
    assert result["route_type"] == "standard"


def test_calculate_alternative_avoids_passage() -> None:
    with patch("maritime_risk.agents.routes.sr.searoute", side_effect=_mock_searoute):
        raw = calculate_alternative_route.__wrapped__("Shanghai", "Rotterdam", "suez")

    result = json.loads(raw)
    assert result["avoiding"] == ["suez"]
    assert result["distance_nm"] == 12500.0
    assert result["delta_vs_planned_days"] > 0
    assert result["route_type"] == "alternative"


def test_transit_days_calculation() -> None:
    with patch("maritime_risk.agents.routes.sr.searoute", side_effect=_mock_searoute):
        raw = calculate_route.__wrapped__("Shanghai", "Rotterdam")

    result = json.loads(raw)
    expected_days = round(8400.0 / (14.0 * 24.0), 1)
    assert result["transit_days"] == expected_days
