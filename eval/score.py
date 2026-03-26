"""Score backtest results against actual arrivals from GFW port visit data.

Usage:
    uv run python -m eval.score

Metrics:
    - Transit time prediction error (MAE) vs actual
    - Delay prediction accuracy (MAE) vs actual delay
    - Confidence calibration (ECE)
    - False alarm rate
    - Days saved vs "always continue" baseline
    - Disruption detection accuracy
    - Reroute validity
    - Security escalation rate
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = DATA_DIR / "results"


def load_results() -> list[dict[str, object]]:
    """Load all backtest result files."""
    if not RESULTS_DIR.exists():
        print("Error: Run eval.backtest first", file=sys.stderr)
        sys.exit(1)

    results = []
    for path in sorted(RESULTS_DIR.glob("shipment_*.json")):
        results.append(json.loads(path.read_text()))

    if not results:
        print("Error: No result files found", file=sys.stderr)
        sys.exit(1)

    return results


def load_disruption_labels() -> dict[int, dict[str, object]]:
    """Load ground-truth disruption labels keyed by shipment index."""
    labels_path = DATA_DIR / "disruption_labels.json"
    if not labels_path.exists():
        return {}
    raw = json.loads(labels_path.read_text())
    return {int(label["shipment_idx"]): label for label in raw}


def compute_actual_transit_days(shipment: dict[str, object]) -> float | None:
    """Compute actual transit days from GFW departure and arrival timestamps."""
    departure = str(shipment.get("origin_departure", ""))
    arrival = str(shipment.get("destination_arrival", ""))

    if not departure or not arrival:
        return None

    try:
        dep_dt = datetime.fromisoformat(departure)
        arr_dt = datetime.fromisoformat(arrival)
        delta = (arr_dt - dep_dt).total_seconds() / 86400.0
        return round(delta, 1) if delta > 0 else None
    except (ValueError, TypeError):
        return None


def compute_route_distance_nm(shipment: dict[str, object]) -> float | None:
    """Compute route distance in nautical miles from searoute-py."""
    try:
        import searoute as sr

        o_pos = str(shipment.get("origin_position", ""))
        d_pos = str(shipment.get("destination_position", ""))
        o_lat = float(o_pos.split("lat=")[1].split(" ")[0])
        o_lon = float(o_pos.split("lon=")[1])
        d_lat = float(d_pos.split("lat=")[1].split(" ")[0])
        d_lon = float(d_pos.split("lon=")[1])

        route = sr.searoute([o_lon, o_lat], [d_lon, d_lat], units="nm")
        distance_nm = route["properties"]["length"]
        return distance_nm if distance_nm > 0 else None
    except Exception:
        return None


def compute_vessel_speeds(results: list[dict[str, object]]) -> dict[str, float]:
    """Derive per-vessel median speed from actual transit data.

    Uses median rather than mean to be robust against outliers (disrupted voyages).
    """
    by_vessel: dict[str, list[float]] = {}
    for result in results:
        if "error" in result:
            continue
        shipment = result.get("shipment", {})
        if not isinstance(shipment, dict):
            continue

        vessel_name = str(shipment.get("vessel_name", ""))
        actual = compute_actual_transit_days(shipment)
        distance = compute_route_distance_nm(shipment)
        if actual and distance and actual > 0:
            speed = distance / (actual * 24.0)
            by_vessel.setdefault(vessel_name, []).append(speed)

    medians: dict[str, float] = {}
    for name, speeds in by_vessel.items():
        speeds.sort()
        medians[name] = speeds[len(speeds) // 2]
    return medians


def compute_baseline_transit_days(
    shipment: dict[str, object], vessel_speeds: dict[str, float],
) -> float | None:
    """Compute baseline transit days using per-vessel median speed.

    Independent of the LLM — uses searoute distance and vessel-specific speed.
    """
    distance = compute_route_distance_nm(shipment)
    if distance is None:
        return None

    vessel_name = str(shipment.get("vessel_name", ""))
    speed = vessel_speeds.get(vessel_name, 19.0)
    return round(distance / (speed * 24.0), 1)


def parse_json_response(response: str) -> dict[str, object] | None:
    """Try to extract JSON from agent response."""
    text = str(response)
    json_start = text.find("{")
    json_end = text.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        try:
            return json.loads(text[json_start:json_end])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass
    return None


def has_security_escalation(response: str, parsed: dict[str, object] | None) -> bool:
    """Check if the assessment includes a security escalation."""
    if parsed:
        leg_risks = parsed.get("leg_risks", [])
        if isinstance(leg_risks, list):
            for leg in leg_risks:
                if isinstance(leg, dict) and leg.get("risk_level") == "escalate":
                    return True
    return "escalate" in response.lower() and "security" in response.lower()


def extract_predicted_transit(response: str) -> float | None:
    """Extract predicted transit days from agent response."""
    patterns = [
        r"(?:transit|voyage).*?(\d+\.?\d*)\s*day",
        r"(\d+\.?\d*)\s*day.*?(?:transit|voyage)",
        r"ETA.*?(\d+\.?\d*)\s*day",
        r"(\d+\.?\d*)\s*days?\s*(?:from departure|total)",
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            if 1 < val < 100:
                return val
    return None


def extract_from_json(
    parsed: dict[str, object] | None, response: str,
) -> tuple[float | None, float | None]:
    """Extract delay and reroute delta from parsed JSON or regex fallback."""
    delay: float | None = None
    reroute_delta: float | None = None

    if parsed:
        # JSON extraction
        raw_delay = parsed.get("predicted_delay_days")
        if isinstance(raw_delay, (int, float)) and raw_delay >= 0:
            delay = float(raw_delay)

        reroute_options = parsed.get("reroute_options", [])
        if isinstance(reroute_options, list) and reroute_options:
            first = reroute_options[0]
            if isinstance(first, dict):
                raw_delta = first.get("delta_vs_planned")
                if isinstance(raw_delta, (int, float)):
                    reroute_delta = float(raw_delta)
    else:
        # Regex fallback
        for p in [
            r"predicted.*?delay.*?(\d+\.?\d*)\s*day",
            r"delay.*?(\d+\.?\d*)\s*day",
        ]:
            m = re.search(p, response, re.IGNORECASE)
            if m:
                delay = float(m.group(1))
                break

        for p in [
            r"delta.*?(\d+\.?\d*)\s*day",
            r"adds?\s*(\d+\.?\d*)\s*day",
        ]:
            m = re.search(p, response, re.IGNORECASE)
            if m:
                reroute_delta = float(m.group(1))
                break

    return delay, reroute_delta


def extract_risk_level(parsed: dict[str, object] | None) -> str | None:
    """Extract overall risk level from parsed JSON."""
    if parsed:
        level = parsed.get("overall_risk")
        if isinstance(level, str) and level in ("low", "medium", "high"):
            return level
    return None


def extract_confidence(parsed: dict[str, object] | None) -> float | None:
    """Extract overall confidence from parsed JSON."""
    if parsed:
        conf = parsed.get("confidence")
        if isinstance(conf, (int, float)) and 0 <= conf <= 1:
            return float(conf)
    return None


def compute_calibration(
    confidence_outcome_pairs: list[tuple[float, bool]], n_bins: int = 5,
) -> dict[str, object]:
    """Compute Expected Calibration Error and per-bin breakdown.

    Each pair is (confidence, was_prediction_correct). A prediction is "correct"
    if the risk level matched the actual outcome (low risk + no delay, or
    medium/high risk + delay materialized).
    """
    if not confidence_outcome_pairs:
        return {"ece": None, "bins": []}

    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for conf, outcome in confidence_outcome_pairs:
        idx = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, outcome))

    ece = 0.0
    n = len(confidence_outcome_pairs)
    bin_details: list[dict[str, object]] = []

    for i, b in enumerate(bins):
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        avg_acc = sum(1 for _, o in b if o) / len(b)
        ece += (len(b) / n) * abs(avg_acc - avg_conf)
        bin_details.append({
            "range": f"{i / n_bins:.1f}-{(i + 1) / n_bins:.1f}",
            "count": len(b),
            "avg_confidence": round(avg_conf, 3),
            "avg_accuracy": round(avg_acc, 3),
            "gap": round(abs(avg_acc - avg_conf), 3),
        })

    return {"ece": round(ece, 3), "bins": bin_details}


# Threshold in days above baseline to classify a voyage as actually disrupted.
DISRUPTION_THRESHOLD_DAYS = 1.0


def _safe_div(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 2) if denominator > 0 else None


def score_results(
    results: list[dict[str, object]],
    labels: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Compute all evaluation metrics."""
    if labels is None:
        labels = load_disruption_labels()

    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    successful = total - errors

    vessel_speeds = compute_vessel_speeds(results)

    transit_errors: list[float] = []
    prediction_errors: list[float] = []
    actual_delays: list[float] = []
    days_saved_list: list[float] = []
    confidence_outcome_pairs: list[tuple[float, bool]] = []
    disruptions_detected = 0
    security_escalations = 0
    reroutes_valid = 0
    reroutes_total = 0

    # Classification — all disruptions (includes undetectable)
    false_alarms = 0
    missed_disruptions = 0
    true_positives = 0
    true_negatives = 0

    # Classification — detectable disruptions only (excludes undetectable)
    det_tp = 0
    det_fn = 0
    undetectable_count = 0

    for i, result in enumerate(results):
        if "error" in result:
            continue

        response = str(result.get("agent_response", ""))
        shipment = result.get("shipment", {})
        if not isinstance(shipment, dict):
            continue

        parsed = parse_json_response(response)

        # Check for security escalation — exclude from operational metrics
        is_escalated = has_security_escalation(response, parsed)
        if is_escalated:
            security_escalations += 1

        # Transit times
        actual_transit = compute_actual_transit_days(shipment)
        baseline_transit = compute_baseline_transit_days(shipment, vessel_speeds)
        predicted_transit = extract_predicted_transit(response)

        # Transit prediction accuracy (agent vs actual)
        if actual_transit is not None and predicted_transit is not None:
            transit_errors.append(abs(predicted_transit - actual_transit))

        # Actual delay: actual transit - baseline (independent of LLM)
        actual_delay: float | None = None
        if actual_transit is not None and baseline_transit is not None:
            actual_delay = max(0, actual_transit - baseline_transit)

        # Prediction accuracy: predicted delay vs actual delay
        predicted_delay, reroute_delta = extract_from_json(parsed, response)
        if predicted_delay is not None and actual_delay is not None and not is_escalated:
            prediction_errors.append(abs(predicted_delay - actual_delay))

        if actual_delay is not None and not is_escalated:
            actual_delays.append(actual_delay)

        # Risk classification accuracy
        risk_level = extract_risk_level(parsed)
        actually_disrupted = (
            actual_delay is not None and actual_delay > DISRUPTION_THRESHOLD_DAYS
        )
        label = labels.get(i)
        detectable = bool(label and label.get("detectable", True)) if label else None

        if risk_level and actual_delay is not None and not is_escalated:
            agent_flagged = risk_level in ("medium", "high")

            # All-disruptions classification
            if agent_flagged and actually_disrupted:
                true_positives += 1
            elif agent_flagged and not actually_disrupted:
                false_alarms += 1
            elif not agent_flagged and actually_disrupted:
                missed_disruptions += 1
            else:
                true_negatives += 1

            # Detectable-only classification
            if actually_disrupted and detectable is not None:
                if not detectable:
                    undetectable_count += 1
                elif agent_flagged:
                    det_tp += 1
                else:
                    det_fn += 1

        # Confidence calibration
        confidence = extract_confidence(parsed)
        if confidence is not None and actual_delay is not None and not is_escalated:
            prediction_correct = (
                (risk_level == "low" and not actually_disrupted)
                or (risk_level in ("medium", "high") and actually_disrupted)
            ) if risk_level else None
            if prediction_correct is not None:
                confidence_outcome_pairs.append((confidence, prediction_correct))

        # Days saved — grounded in actual delay, not predicted delay
        if actual_delay is not None and reroute_delta is not None and not is_escalated:
            if reroute_delta > 0:
                days_saved = actual_delay - reroute_delta
                days_saved_list.append(days_saved)
                reroutes_total += 1
                reroutes_valid += 1
            elif reroute_delta == 0:
                reroutes_total += 1

        # Disruption detection (keyword-based)
        disruption_keywords = [
            "disruption", "delay", "storm", "closure", "strike",
            "congestion", "restriction", "escalate",
        ]
        if any(kw in response.lower() for kw in disruption_keywords):
            disruptions_detected += 1

    operational = successful - security_escalations
    calibration = compute_calibration(confidence_outcome_pairs)
    classified = true_positives + false_alarms + missed_disruptions + true_negatives

    summary: dict[str, object] = {
        "total_shipments": total,
        "successful_runs": successful,
        "errors": errors,
        "security_escalations": security_escalations,
        "operational_assessments": operational,
        "vessel_speeds_knots": {k: round(v, 1) for k, v in vessel_speeds.items()},
        "transit_predictions_matched": len(transit_errors),
        "transit_mae_days": (
            round(sum(transit_errors) / len(transit_errors), 2)
            if transit_errors
            else None
        ),
        "avg_actual_delay_days": (
            round(sum(actual_delays) / len(actual_delays), 2)
            if actual_delays
            else None
        ),
        "delay_prediction_mae_days": (
            round(sum(prediction_errors) / len(prediction_errors), 2)
            if prediction_errors
            else None
        ),
        "risk_classification": {
            "true_positives": true_positives,
            "false_alarms": false_alarms,
            "missed_disruptions": missed_disruptions,
            "true_negatives": true_negatives,
            "precision": _safe_div(true_positives, true_positives + false_alarms),
            "recall": _safe_div(true_positives, true_positives + missed_disruptions),
            "false_alarm_rate": _safe_div(false_alarms, false_alarms + true_negatives),
            "classified": classified,
            "disruption_threshold_days": DISRUPTION_THRESHOLD_DAYS,
        },
        "detectable_disruptions": {
            "detected": det_tp,
            "missed": det_fn,
            "undetectable": undetectable_count,
            "recall": _safe_div(det_tp, det_tp + det_fn),
            "labeled": bool(labels),
        },
        "confidence_calibration": calibration,
        "reroutes_suggested": reroutes_total,
        "reroutes_valid": reroutes_valid,
        "reroute_validity_rate": _safe_div(reroutes_valid, reroutes_total),
        "avg_days_saved": (
            round(sum(days_saved_list) / len(days_saved_list), 2)
            if days_saved_list
            else None
        ),
        "disruptions_detected_keyword": disruptions_detected,
        "disruption_detection_rate_keyword": (
            round(disruptions_detected / successful, 2) if successful > 0 else None
        ),
        "tool_usage": _score_tool_traces(results),
        "baseline": "always continue, never reroute (0 days saved)",
        "data_source": "Global Fishing Watch port visits API (free, non-commercial)",
    }

    return summary


EXPECTED_TOOLS = {"calculate_route", "search_maritime_news", "check_weather"}


def _score_tool_traces(results: list[dict[str, object]]) -> dict[str, object]:
    """Score tool usage patterns from backtest traces."""
    traced = 0
    total_calls = 0
    tool_counts: dict[str, int] = {}
    durations: list[float] = []
    errors = 0
    missing_required: list[int] = []

    for i, result in enumerate(results):
        if "error" in result:
            continue
        traces = result.get("tool_trace")
        if not isinstance(traces, list):
            continue

        traced += 1
        total_calls += len(traces)
        tools_used: set[str] = set()

        for trace in traces:
            if not isinstance(trace, dict):
                continue
            name = str(trace.get("tool", ""))
            tool_counts[name] = tool_counts.get(name, 0) + 1
            tools_used.add(name)
            if trace.get("status") == "error" or trace.get("error"):
                errors += 1
            dur = trace.get("duration_ms")
            if isinstance(dur, (int, float)):
                durations.append(float(dur))

        missing = EXPECTED_TOOLS - tools_used
        if missing:
            missing_required.append(i)

    if not traced:
        return {"traced_runs": 0, "note": "No tool traces found. Re-run backtest to capture."}

    durations.sort()

    return {
        "traced_runs": traced,
        "total_tool_calls": total_calls,
        "avg_calls_per_run": round(total_calls / traced, 1),
        "tool_call_counts": tool_counts,
        "tool_errors": errors,
        "error_rate": _safe_div(errors, total_calls),
        "runs_missing_required_tools": len(missing_required),
        "required_tool_coverage": _safe_div(
            traced - len(missing_required), traced,
        ),
        "latency_p50_ms": round(durations[len(durations) // 2]) if durations else None,
        "latency_p95_ms": (
            round(durations[int(len(durations) * 0.95)]) if durations else None
        ),
    }


def print_summary(summary: dict[str, object]) -> None:
    """Print evaluation summary as markdown table."""
    print("\n## Evaluation Results\n")
    print("| Metric | Value |")
    print("|---|---|")
    for key, value in summary.items():
        label = key.replace("_", " ").title()
        print(f"| {label} | {value} |")


def main() -> None:
    results = load_results()
    summary = score_results(results)

    summary_path = RESULTS_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print_summary(summary)
    print(f"\nFull summary saved to {summary_path}")


if __name__ == "__main__":
    main()
