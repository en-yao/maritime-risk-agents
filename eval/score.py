"""Score backtest results against actual arrivals from GFW port visit data.

Usage:
    uv run python -m eval.score

Metrics:
    - Transit time prediction error (MAE) vs actual
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


def score_results(results: list[dict[str, object]]) -> dict[str, object]:
    """Compute all evaluation metrics."""
    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    successful = total - errors

    transit_errors: list[float] = []
    prediction_errors: list[float] = []
    actual_delays: list[float] = []
    days_saved_list: list[float] = []
    disruptions_detected = 0
    security_escalations = 0
    reroutes_valid = 0
    reroutes_total = 0

    for result in results:
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

        # Transit time: predicted vs actual
        actual_transit = compute_actual_transit_days(shipment)
        predicted_transit = extract_predicted_transit(response)
        if actual_transit is not None and predicted_transit is not None:
            transit_errors.append(abs(predicted_transit - actual_transit))

        # Actual delay: actual transit - searoute baseline
        # Baseline transit is the predicted transit from the agent (which uses searoute)
        actual_delay: float | None = None
        if actual_transit is not None and predicted_transit is not None:
            actual_delay = max(0, actual_transit - predicted_transit)

        # Prediction accuracy: predicted delay vs actual delay
        predicted_delay, reroute_delta = extract_from_json(parsed, response)
        if predicted_delay is not None and actual_delay is not None and not is_escalated:
            prediction_errors.append(abs(predicted_delay - actual_delay))

        if actual_delay is not None and not is_escalated:
            actual_delays.append(actual_delay)

        # Days saved — grounded in actual delay, not predicted delay
        # Assumption: alternative route has no delay (stated limitation)
        # Only count when: reroute has positive delta AND is not a failed calculation
        if actual_delay is not None and reroute_delta is not None and not is_escalated:
            if reroute_delta > 0:  # valid reroute with positive transit cost
                days_saved = actual_delay - reroute_delta
                days_saved_list.append(days_saved)
                reroutes_total += 1
                reroutes_valid += 1
            elif reroute_delta == 0:
                # Invalid reroute (same route or tool failure) — skip
                reroutes_total += 1

        # Disruption detection
        disruption_keywords = [
            "disruption", "delay", "storm", "closure", "strike",
            "congestion", "restriction", "escalate",
        ]
        if any(kw in response.lower() for kw in disruption_keywords):
            disruptions_detected += 1

    operational = successful - security_escalations

    summary: dict[str, object] = {
        "total_shipments": total,
        "successful_runs": successful,
        "errors": errors,
        "security_escalations": security_escalations,
        "operational_assessments": operational,
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
        "reroutes_suggested": reroutes_total,
        "reroutes_valid": reroutes_valid,
        "reroute_validity_rate": (
            round(reroutes_valid / reroutes_total, 2)
            if reroutes_total > 0
            else None
        ),
        "avg_days_saved": (
            round(sum(days_saved_list) / len(days_saved_list), 2)
            if days_saved_list
            else None
        ),
        "disruptions_detected": disruptions_detected,
        "disruption_detection_rate": (
            round(disruptions_detected / successful, 2) if successful > 0 else None
        ),
        "baseline": "always continue, never reroute (0 days saved)",
        "days_saved_note": "actual_delay - reroute_delta. Assumes alternative route has no delay (stated limitation).",
        "data_source": "Global Fishing Watch port visits API (free, non-commercial)",
    }

    return summary


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
