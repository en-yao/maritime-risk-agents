"""Score backtest results against actual arrivals from GFW port visit data.

Usage:
    uv run python -m eval.score

Metrics:
    - Delay prediction error (MAE) vs actual transit time
    - Days saved vs "always continue" baseline
    - Disruption detection accuracy
    - Reroute validity
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
            if 1 < val < 100:  # sanity check
                return val
    return None


def extract_predicted_delay(response: str) -> float | None:
    """Extract predicted delay days from agent response."""
    patterns = [
        r"predicted.*?delay.*?(\d+\.?\d*)\s*day",
        r"\+(\d+\.?\d*)\s*day.*?delay",
        r"delay.*?(\d+\.?\d*)\s*day",
        r"\+(\d+\.?\d*)\s*day",
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def extract_reroute_delta(response: str) -> float | None:
    """Extract reroute transit time delta from agent response."""
    patterns = [
        r"alternative.*?\+(\d+\.?\d*)\s*day",
        r"cape.*?\+(\d+\.?\d*)\s*day",
        r"adds?\s*(\d+\.?\d*)\s*day",
        r"reroute.*?\+(\d+\.?\d*)\s*day",
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def score_results(results: list[dict[str, object]]) -> dict[str, object]:
    """Compute all evaluation metrics."""
    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    successful = total - errors

    transit_errors: list[float] = []
    delay_predictions: list[float] = []
    days_saved_list: list[float] = []
    disruptions_detected = 0
    reroutes_valid = 0
    reroutes_total = 0

    for result in results:
        if "error" in result:
            continue

        response = str(result.get("agent_response", ""))
        shipment = result.get("shipment", {})
        if not isinstance(shipment, dict):
            continue

        # Transit time accuracy — predicted vs actual
        actual_transit = compute_actual_transit_days(shipment)
        predicted_transit = extract_predicted_transit(response)
        if actual_transit is not None and predicted_transit is not None:
            error = abs(predicted_transit - actual_transit)
            transit_errors.append(error)

        # Delay prediction
        predicted_delay = extract_predicted_delay(response)
        if predicted_delay is not None:
            delay_predictions.append(predicted_delay)

        # Days saved — predicted delay minus reroute delta
        reroute_delta = extract_reroute_delta(response)
        if predicted_delay is not None and reroute_delta is not None:
            days_saved = predicted_delay - reroute_delta
            days_saved_list.append(days_saved)
            reroutes_total += 1
            if reroute_delta > 0:
                reroutes_valid += 1

        # Disruption detection
        disruption_keywords = [
            "disruption", "delay", "storm", "closure", "strike",
            "congestion", "attack", "restriction", "reroute",
        ]
        if any(kw in response.lower() for kw in disruption_keywords):
            disruptions_detected += 1

    summary: dict[str, object] = {
        "total_shipments": total,
        "successful_runs": successful,
        "errors": errors,
        "transit_predictions_matched": len(transit_errors),
        "transit_mae_days": (
            round(sum(transit_errors) / len(transit_errors), 2)
            if transit_errors
            else None
        ),
        "delay_predictions_extracted": len(delay_predictions),
        "avg_predicted_delay_days": (
            round(sum(delay_predictions) / len(delay_predictions), 2)
            if delay_predictions
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
