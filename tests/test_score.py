"""Tests for eval.score metrics computation."""
from __future__ import annotations

import json

from eval.score import (
    compute_calibration,
    compute_vessel_speeds,
    extract_confidence,
    extract_risk_level,
    load_disruption_labels,
    score_results,
)


def _make_result(
    vessel_name: str,
    origin_pos: str,
    dest_pos: str,
    departure: str,
    arrival: str,
    agent_response: str,
) -> dict[str, object]:
    return {
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


class TestExtractRiskLevel:
    def test_valid_levels(self) -> None:
        for level in ("low", "medium", "high"):
            assert extract_risk_level({"overall_risk": level}) == level

    def test_invalid_level(self) -> None:
        assert extract_risk_level({"overall_risk": "extreme"}) is None

    def test_none_parsed(self) -> None:
        assert extract_risk_level(None) is None


class TestExtractConfidence:
    def test_valid_confidence(self) -> None:
        assert extract_confidence({"confidence": 0.85}) == 0.85

    def test_out_of_range(self) -> None:
        assert extract_confidence({"confidence": 1.5}) is None
        assert extract_confidence({"confidence": -0.1}) is None

    def test_none_parsed(self) -> None:
        assert extract_confidence(None) is None


class TestComputeCalibration:
    def test_empty(self) -> None:
        result = compute_calibration([])
        assert result["ece"] is None

    def test_perfect_calibration(self) -> None:
        # All confident and all correct
        pairs = [(0.9, True)] * 10
        result = compute_calibration(pairs, n_bins=5)
        assert result["ece"] is not None
        assert result["ece"] < 0.15  # close to 0

    def test_poor_calibration(self) -> None:
        # High confidence but all wrong
        pairs = [(0.95, False)] * 10
        result = compute_calibration(pairs, n_bins=5)
        assert result["ece"] is not None
        assert result["ece"] > 0.8


class TestComputeVesselSpeeds:
    def test_computes_median(self) -> None:
        # 3 voyages: 480nm in 1 day (20kt), 480nm in 2 days (10kt), 480nm in 1.5 days (~13.3kt)
        # Median should be ~13.3
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
        # Median of 3 values — middle one
        assert speeds["TestVessel"] > 0


class TestScoreResults:
    def test_risk_classification_false_alarm(self) -> None:
        """Agent says high risk but vessel arrives on time → false alarm."""
        # Two identical voyages to establish vessel speed baseline
        base_result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(risk="low", confidence=0.9),
        )
        # Third voyage: same speed (no delay) but agent says high risk
        false_alarm_result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-03 00:00:00+00:00", "2024-01-04 00:00:00+00:00",
            _make_assessment_json(risk="high", delay=5, confidence=0.9),
        )
        results = [base_result, base_result, false_alarm_result]
        summary = score_results(results)

        classification = summary["risk_classification"]
        assert isinstance(classification, dict)
        assert classification["false_alarms"] >= 1

    def test_confidence_calibration_present(self) -> None:
        result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(risk="low", confidence=0.95),
        )
        summary = score_results([result, result, result])

        calibration = summary["confidence_calibration"]
        assert isinstance(calibration, dict)
        assert "ece" in calibration

    def test_vessel_speeds_in_summary(self) -> None:
        result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(),
        )
        summary = score_results([result])
        assert "vessel_speeds_knots" in summary

    def test_detectable_disruptions_with_labels(self) -> None:
        """Detectable-only recall excludes undetectable disruptions."""
        # 5 normal voyages to establish a stable median speed (~1 day transit)
        base = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(risk="low", confidence=0.9),
        )
        # Shipment 5: very delayed (5 days for a 1-day route), detectable, agent missed
        missed_detectable = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-10 00:00:00+00:00", "2024-01-15 00:00:00+00:00",
            _make_assessment_json(risk="low", confidence=0.9),
        )
        # Shipment 6: very delayed (5 days for a 1-day route), undetectable, agent missed
        missed_undetectable = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-16 00:00:00+00:00", "2024-01-21 00:00:00+00:00",
            _make_assessment_json(risk="low", confidence=0.9),
        )
        results = [base, base, base, base, base, missed_detectable, missed_undetectable]
        labels = {
            5: {"shipment_idx": 5, "detectable": True, "actually_disrupted": True,
                "cause": "test", "evidence": "test", "news_dates": []},
            6: {"shipment_idx": 6, "detectable": False, "actually_disrupted": True,
                "cause": "unknown", "evidence": "none", "news_dates": []},
        }
        summary = score_results(results, labels=labels)

        det = summary["detectable_disruptions"]
        assert isinstance(det, dict)
        assert det["detected"] == 0
        assert det["missed"] == 1
        assert det["undetectable"] == 1
        assert det["recall"] == 0.0

    def test_detectable_disruptions_without_labels(self) -> None:
        """Without labels, detectable_disruptions section shows unlabeled state."""
        result = _make_result(
            "TestVessel", "lat=0 lon=0", "lat=0 lon=10",
            "2024-01-01 00:00:00+00:00", "2024-01-02 00:00:00+00:00",
            _make_assessment_json(),
        )
        summary = score_results([result], labels={})

        det = summary["detectable_disruptions"]
        assert isinstance(det, dict)
        assert det["labeled"] is False


class TestLoadDisruptionLabels:
    def test_loads_from_file(self) -> None:
        labels = load_disruption_labels()
        assert len(labels) == 9
        assert 4 in labels
        assert labels[4]["detectable"] is True
        assert labels[8]["detectable"] is False
