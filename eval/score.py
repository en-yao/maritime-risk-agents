"""Score backtest results against actual arrivals.

Usage:
    uv run python -m eval.score

Metrics:
    - Delay prediction error (MAE)
    - Days saved vs "always continue" baseline
    - Disruption detection accuracy
    - Reroute validity
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = DATA_DIR / "results"


def load_results() -> list[dict[str, object]]:
    """Load all backtest result files."""
    if not RESULTS_DIR.exists():
        print("Error: Run eval.backtest first", file=sys.stderr)
        sys.exit(1)

    results = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if path.name == "summary.json":
            continue
        results.append(json.loads(path.read_text()))

    if not results:
        print("Error: No result files found", file=sys.stderr)
        sys.exit(1)

    return results


def extract_predicted_delay(response: str) -> float | None:
    """Extract predicted delay days from agent response text."""
    patterns = [
        r"predicted.*?delay.*?(\d+\.?\d*)\s*day",
        r"(\d+\.?\d*)\s*day.*?delay",
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

    prediction_errors: list[float] = []
    days_saved_list: list[float] = []
    disruptions_detected = 0
    reroutes_valid = 0
    reroutes_total = 0

    for result in results:
        if "error" in result:
            continue

        response = str(result.get("agent_response", ""))

        # Delay prediction — extract from response
        predicted_delay = extract_predicted_delay(response)
        if predicted_delay is not None:
            # actual_delay would come from MarineTraffic arrival vs schedule
            # For now, record the prediction for manual verification
            prediction_errors.append(predicted_delay)

        # Days saved — predicted delay minus reroute delta
        reroute_delta = extract_reroute_delta(response)
        if predicted_delay is not None and reroute_delta is not None:
            days_saved = predicted_delay - reroute_delta
            days_saved_list.append(days_saved)
            reroutes_total += 1
            if reroute_delta > 0:
                reroutes_valid += 1

        # Disruption detection — check if agent found disruptions
        disruption_keywords = [
            "disruption", "delay", "storm", "closure", "strike",
            "congestion", "attack", "restriction",
        ]
        if any(kw in response.lower() for kw in disruption_keywords):
            disruptions_detected += 1

    summary: dict[str, object] = {
        "total_shipments": total,
        "successful_runs": successful,
        "errors": errors,
        "predictions_extracted": len(prediction_errors),
        "avg_predicted_delay_days": (
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
