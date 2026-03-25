"""Run backtest: agent assesses historical shipments with data cutoff enforced.

Usage:
    uv run python -m eval.backtest

MarineTraffic arrival times are NEVER fed to the agent — held out for scoring only.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from maritime_risk.orchestrator import create_orchestrator

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = DATA_DIR / "results"


def load_shipments() -> list[dict[str, object]]:
    """Load port call records and group into shipments."""
    port_calls_path = DATA_DIR / "port_calls.json"
    if not port_calls_path.exists():
        print("Error: Run eval.export first to generate port_calls.json", file=sys.stderr)
        sys.exit(1)

    records = json.loads(port_calls_path.read_text())

    shipments: dict[str, dict[str, object]] = {}
    for record in records:
        vessel_id = record.get("MMSI", record.get("IMO", "unknown"))
        if vessel_id not in shipments:
            shipments[vessel_id] = {
                "vessel_id": vessel_id,
                "port_calls": [],
                "departure_date": record.get("TIMESTAMP_UTC", ""),
            }
        calls = shipments[vessel_id]["port_calls"]
        if isinstance(calls, list):
            calls.append(record)

    return list(shipments.values())


def build_prompt(shipment: dict[str, object]) -> str:
    """Build agent prompt from shipment data. Excludes arrival times (held out)."""
    port_calls = shipment.get("port_calls", [])
    if not port_calls or not isinstance(port_calls, list):
        return ""

    ports = []
    for pc in port_calls:
        if isinstance(pc, dict):
            port_name = pc.get("PORT_NAME", pc.get("port_name", "Unknown"))
            ports.append(str(port_name))

    if len(ports) < 2:
        return ""

    origin = ports[0]
    destination = ports[-1]
    departure = str(shipment.get("departure_date", ""))
    date_str = departure[:10] if departure else "unknown date"

    return f"Assess delay risk for shipment from {origin} to {destination}, departing {date_str}"


def run_backtest() -> None:
    """Run the orchestrator against each historical shipment."""
    shipments = load_shipments()
    print(f"Loaded {len(shipments)} shipments for backtest")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    agent = create_orchestrator()

    for i, shipment in enumerate(shipments):
        prompt = build_prompt(shipment)
        if not prompt:
            print(f"  [{i + 1}/{len(shipments)}] Skipping — insufficient port call data")
            continue

        print(f"  [{i + 1}/{len(shipments)}] {prompt[:80]}...")

        try:
            result = agent(prompt)
            prediction = {
                "shipment": shipment,
                "prompt": prompt,
                "agent_response": result.message,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            prediction = {
                "shipment": shipment,
                "prompt": prompt,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }
            print(f"    Error: {e}")

        vessel_id = shipment.get("vessel_id", f"ship_{i}")
        result_path = RESULTS_DIR / f"{vessel_id}.json"
        result_path.write_text(json.dumps(prediction, indent=2, default=str))

    print(f"Backtest complete. Results in {RESULTS_DIR}")


def main() -> None:
    run_backtest()


if __name__ == "__main__":
    main()
