from __future__ import annotations

import json
import os
from unittest.mock import patch

from maritime_risk.tools.weather import check_weather


def _mock_response(status_code: int = 200, data: dict[str, object] | None = None) -> object:
    class MockResp:
        def __init__(self) -> None:
            self.status_code = status_code
            self._data = data or {"results": []}

        def json(self) -> dict[str, object]:
            return self._data

    return MockResp()


def test_check_weather_no_token() -> None:
    with patch.dict(os.environ, {"NOAA_TOKEN": ""}, clear=False):
        raw = check_weather.__wrapped__(31.23, 121.47, "2024-07-15")

    result = json.loads(raw)
    assert "error" in result
    assert "not configured" in result["error"]


def test_check_weather_returns_json() -> None:
    mock_data = {
        "results": [
            {"datatype": "AWND", "value": 12.5},
            {"datatype": "PRCP", "value": 20.0},
        ],
    }

    with (
        patch.dict(os.environ, {"NOAA_TOKEN": "test-token"}, clear=False),
        patch("maritime_risk.tools.weather.httpx.get", return_value=_mock_response(data=mock_data)),
    ):
        raw = check_weather.__wrapped__(15.0, 42.0, "2024-08-01")

    result = json.loads(raw)
    assert result["lat"] == 15.0
    assert result["lon"] == 42.0
    assert result["date"] == "2024-08-01"
    assert result["observations"] == 2
    assert isinstance(result["alerts"], list)


def test_check_weather_high_wind_alert() -> None:
    mock_data = {
        "results": [
            {"datatype": "WSF5", "value": 25.0},
        ],
    }

    with (
        patch.dict(os.environ, {"NOAA_TOKEN": "test-token"}, clear=False),
        patch("maritime_risk.tools.weather.httpx.get", return_value=_mock_response(data=mock_data)),
    ):
        raw = check_weather.__wrapped__(15.0, 42.0, "2024-08-01")

    result = json.loads(raw)
    assert len(result["alerts"]) == 1
    assert "High wind" in result["alerts"][0]


def test_check_weather_api_error() -> None:
    with (
        patch.dict(os.environ, {"NOAA_TOKEN": "test-token"}, clear=False),
        patch("maritime_risk.tools.weather.httpx.get", return_value=_mock_response(status_code=503)),
    ):
        raw = check_weather.__wrapped__(15.0, 42.0, "2024-08-01")

    result = json.loads(raw)
    assert "error" in result
    assert "503" in result["error"]
