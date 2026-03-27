"""Tests for eval.score metrics computation."""
from __future__ import annotations

import json

from eval.score import (
    _score_structured_output,
    _score_tool_traces,
    compute_vessel_speeds,
    has_security_escalation,
    parse_json_response,
    score_results,
)


def _make_result(
    vessel_name: str,
    origin_pos: str,
    dest_pos: str,
    departure: str,
    arrival: str,
    agent_response: str,
    tool_trace: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "shipment": {
            "vessel_id": "test-id",
            "vessel_name": vessel_name,
            "origin_position": origin_pos,
            "destination_position": dest_pos,
            "origin_departure": departure,
            "destination_arrival": arrival,
            "rough_distance_nm": 1000,
        },
        "prompt": "test prompt",
        "agent_response": agent_response,
    }
    if tool_trace is not None:
        result["tool_trace"] = tool_trace
    return result


def _make_assessment_json(
    risk: str = "low",
    delay: float = 0,
    confidence: float = 0.9,
) -> str:
    assessment = {
        "shipment_id": "test_001",
        "overall_risk": risk,
        "predicted_delay_days": delay,
        "leg_risks": [],
        "reroute_options": [],
        "recommendation": "proceed",
        "confidence": confidence,
    }
    return json.dumps(assessment)


class TestParseJsonResponse:
    def test_valid_json(self) -> None:
        response = 'Here is the assessment: {"overall_risk": "low"} done.'
        parsed = parse_json_response(response)
        assert parsed is not None
        assert parsed["overall_risk"] == "low"

    def test_no_json(self) -> None:
        assert parse_json_response("No JSON here") is None

    def test_invalid_json(self) -> None:
        assert parse_json_response("Some text {broken json} more") is None


class TestHasSecurityEscalation:
    def test_escalation_in_leg_risks(self) -> None:
        parsed: dict[str, object] = {"leg_risks": [{"risk_level": "escalate"}]}
        assert has_security_escalation("", parsed) is True

    def test_escalation_in_text(self) -> None:
        assert has_security_escalation("escalate to security team", None) is True

    def test_no_escalation(self) -> None:
        parsed: dict[str, object] = {"leg_risks": [{"risk_level": "low"}]}
        assert has_security_escalation("all clear", parsed) is False


class TestComputeVesselSpeeds:
    def test_computes_median(self) -> None:
        results = [
            _make_result(
                "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(),
            ),
            _make_result(
                "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-03 00:00:00+00:00", "2024-01-05 00:00:00+00:00",
                _make_assessment_json(),
            ),
            _make_result(
                "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-06 00:00:00+00:00", "2024-01-07 12:00:00+00:00",
                _make_assessment_json(),
            ),
        ]
        speeds = compute_vessel_speeds(results)
        assert "TestVessel" in speeds
        assert speeds["TestVessel"] > 0


class TestScoreStructuredOutput:
    def test_valid_json_with_fields(self) -> None:
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(),
            ),
        ]
        output = _score_structured_output(results)
        assert output["valid_json"] == 1
        assert output["has_required_fields"] == 1
        assert output["schema_compliance_rate"] == 1.0

    def test_no_json(self) -> None:
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                "No structured output here, just text.",
            ),
        ]
        output = _score_structured_output(results)
        assert output["valid_json"] == 0
        assert output["schema_compliance_rate"] == 0.0

    def test_json_missing_fields(self) -> None:
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                '{"overall_risk": "low"}',
            ),
        ]
        output = _score_structured_output(results)
        assert output["valid_json"] == 1
        assert output["has_required_fields"] == 0


class TestScoreToolTraces:
    def test_complete_traces(self) -> None:
        traces = [
            {"tool": "calculate_route", "input": {}, "status": "success",
             "duration_ms": 100, "error": None},
            {"tool": "search_maritime_news", "input": {}, "status": "success",
             "duration_ms": 200, "error": None},
            {"tool": "check_weather", "input": {}, "status": "success",
             "duration_ms": 150, "error": None},
        ]
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(), tool_trace=traces,
            ),
        ]
        usage = _score_tool_traces(results)
        assert usage["traced_runs"] == 1
        assert usage["total_tool_calls"] == 3
        assert usage["tool_errors"] == 0
        assert usage["required_tool_coverage"] == 1.0

    def test_missing_required_tool(self) -> None:
        traces = [
            {"tool": "calculate_route", "input": {}, "status": "success",
             "duration_ms": 100, "error": None},
        ]
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(), tool_trace=traces,
            ),
        ]
        usage = _score_tool_traces(results)
        assert usage["runs_missing_required_tools"] == 1
        assert usage["required_tool_coverage"] == 0.0

    def test_tool_error(self) -> None:
        traces = [
            {"tool": "check_weather", "input": {}, "status": "error",
             "duration_ms": 50, "error": "timeout"},
        ]
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(), tool_trace=traces,
            ),
        ]
        usage = _score_tool_traces(results)
        assert usage["tool_errors"] == 1

    def test_no_traces(self) -> None:
        results = [
            _make_result(
                "V", "lat=0 lon=0", "lat=0 lon=10",
                "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
                _make_assessment_json(),
            ),
        ]
        usage = _score_tool_traces(results)
        assert usage["traced_runs"] == 0


class TestScoreResults:
    def test_basic_summary(self) -> None:
        result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(),
        )
        summary = score_results([result])
        assert summary["total_shipments"] == 1
        assert summary["successful_runs"] == 1
        assert summary["task_completion_rate"] == 1.0
        assert "vessel_speeds_knots" in summary
        assert "structured_output" in summary
        assert "tool_usage" in summary

    def test_security_escalation_counted(self) -> None:
        escalated = _make_result(
            "V", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            '{"leg_risks": [{"risk_level": "escalate"}]}',
        )
        normal = _make_result(
            "V", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-03 00:00:00+00:00", "2024-01-04 00:00:00+00:00",
            _make_assessment_json(),
        )
        summary = score_results([escalated, normal])
        assert summary["security_escalations"] == 1

    def test_error_result(self) -> None:
        error_result: dict[str, object] = {
            "shipment": {},
            "prompt": "test",
            "error": "something failed",
            "tool_trace": [],
        }
        summary = score_results([error_result])
        assert summary["errors"] == 1
        assert summary["successful_runs"] == 0
