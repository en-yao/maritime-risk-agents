"""Run backtest: agent assesses historical shipments with data cutoff enforced.

Usage:
    uv run python -m eval.backtest

GFW arrival times are NEVER fed to the agent — held out for scoring only.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from maritime_risk.orchestrator import create_orchestrator

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = DATA_DIR / "results"


def load_shipments() -> list[dict[str, object]]:
    """Load port visit records and build shipment pairs (consecutive port visits).

    Each shipment = departure from port A → arrival at port B.
    """
    port_visits_path = DATA_DIR / "port_visits.json"
    if not port_visits_path.exists():
        print("Error: Run eval.export first to generate port_visits.json", file=sys.stderr)
        sys.exit(1)

    visits = json.loads(port_visits_path.read_text())

    # Group by vessel, sort by start time
    by_vessel: dict[str, list[dict[str, object]]] = {}
    for visit in visits:
        vid = str(visit.get("vessel_id", "unknown"))
        if vid not in by_vessel:
            by_vessel[vid] = []
        by_vessel[vid].append(visit)

    for vid in by_vessel:
        by_vessel[vid].sort(key=lambda v: str(v.get("start", "")))

    # Build shipments from consecutive port visits, filter short legs
    min_distance_nm = float(os.environ.get("BACKTEST_MIN_NM", "500"))
    shipments: list[dict[str, object]] = []
    for vid, vessel_visits in by_vessel.items():
        vessel_name = str(vessel_visits[0].get("vessel_name", "Unknown"))
        for i in range(len(vessel_visits) - 1):
            origin = vessel_visits[i]
            destination = vessel_visits[i + 1]

            # Compute rough distance to filter short legs
            o_pos = str(origin.get("position", ""))
            d_pos = str(destination.get("position", ""))
            try:
                o_lat = float(o_pos.split("lat=")[1].split(" ")[0])
                o_lon = float(o_pos.split("lon=")[1])
                d_lat = float(d_pos.split("lat=")[1].split(" ")[0])
                d_lon = float(d_pos.split("lon=")[1])
                rough_nm = math.sqrt((o_lat - d_lat) ** 2 + (o_lon - d_lon) ** 2) * 60
            except (ValueError, IndexError):
                rough_nm = 0

            if rough_nm < min_distance_nm:
                continue

            shipments.append({
                "vessel_id": vid,
                "vessel_name": vessel_name,
                "origin_position": o_pos,
                "origin_departure": str(origin.get("end", "")),
                "destination_position": d_pos,
                "destination_arrival": str(destination.get("start", "")),
                "rough_distance_nm": round(rough_nm),
            })

    return shipments


def build_prompt(shipment: dict[str, object]) -> str:
    """Build agent prompt from shipment data. Excludes arrival times (held out)."""
    origin_pos = str(shipment.get("origin_position", ""))
    dest_pos = str(shipment.get("destination_position", ""))
    departure = str(shipment.get("origin_departure", ""))
    vessel_name = str(shipment.get("vessel_name", ""))
    date_str = departure[:10] if departure else "unknown date"

    return (
        f"Assess delay risk for {vessel_name} departing {date_str}. "
        f"Origin coordinates: {origin_pos}. "
        f"Destination coordinates: {dest_pos}."
    )


def run_backtest() -> None:
    """Run the orchestrator against each historical shipment."""
    shipments = load_shipments()
    print(f"Loaded {len(shipments)} shipments for backtest")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    agent = create_orchestrator()

    for i, shipment in enumerate(shipments):
        prompt = build_prompt(shipment)
        if not prompt:
            print(f"  [{i + 1}/{len(shipments)}] Skipping — insufficient data")
            continue

        print(f"  [{i + 1}/{len(shipments)}] {prompt[:80]}...")

        try:
            result = agent(prompt)
            response = result.message
            if isinstance(response, dict):
                content = response.get("content", [])
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        response = block["text"]
                        break

            prediction = {
                "shipment": shipment,
                "prompt": prompt,
                "agent_response": response,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            prediction = {
                "shipment": shipment,
                "prompt": prompt,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            print(f"    Error: {e}")

        result_path = RESULTS_DIR / f"shipment_{i:03d}.json"
        result_path.write_text(json.dumps(prediction, indent=2, default=str))

    print(f"Backtest complete. Results in {RESULTS_DIR}")


def main() -> None:
    run_backtest()


if __name__ == "__main__":
    main()
