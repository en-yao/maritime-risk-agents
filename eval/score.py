"""Score backtest results — agent task-completion and tool-usage metrics.

Usage:
    uv run python -m eval.score

Metrics focus on agentic behaviour, not ML classification:
    - Task completion rate
    - Structured output rate (valid JSON)
    - Security escalation rate (scope awareness)
    - Tool usage patterns (coverage, error rate, latency)
"""
from __future__ import annotations

import json
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


def _safe_div(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 2) if denominator > 0 else None


EXPECTED_TOOLS = {"calculate_route", "search_maritime_news", "check_weather"}

REQUIRED_JSON_FIELDS = {
    "shipment_id", "overall_risk", "predicted_delay_days",
    "leg_risks", "recommendation",
}


def _score_structured_output(results: list[dict[str, object]]) -> dict[str, object]:
    """Score whether agent produced valid structured JSON output."""
    total = 0
    valid_json = 0
    has_required_fields = 0

    for result in results:
        if "error" in result:
            continue
        total += 1

        response = str(result.get("agent_response", ""))
        parsed = parse_json_response(response)
        if parsed is not None:
            valid_json += 1
            if REQUIRED_JSON_FIELDS.issubset(parsed.keys()):
                has_required_fields += 1

    return {
        "total_assessed": total,
        "valid_json": valid_json,
        "valid_json_rate": _safe_div(valid_json, total),
        "has_required_fields": has_required_fields,
        "schema_compliance_rate": _safe_div(has_required_fields, total),
    }


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


def score_results(results: list[dict[str, object]]) -> dict[str, object]:
    """Compute agent evaluation metrics."""
    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    successful = total - errors

    security_escalations = 0
    for result in results:
        if "error" in result:
            continue
        response = str(result.get("agent_response", ""))
        parsed = parse_json_response(response)
        if has_security_escalation(response, parsed):
            security_escalations += 1

    vessel_speeds = compute_vessel_speeds(results)

    return {
        "total_shipments": total,
        "successful_runs": successful,
        "task_completion_rate": _safe_div(successful, total),
        "errors": errors,
        "security_escalations": security_escalations,
        "security_escalation_rate": _safe_div(security_escalations, successful),
        "vessel_speeds_knots": {k: round(v, 1) for k, v in vessel_speeds.items()},
        "structured_output": _score_structured_output(results),
        "tool_usage": _score_tool_traces(results),
        "data_source": "Global Fishing Watch port visits API (free, non-commercial)",
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
