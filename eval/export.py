"""Export port visit data from Global Fishing Watch for a backtest period.

Usage:
    uv run python -m eval.export --start 2024-07-01 --end 2024-12-31

Requires GFW_API_TOKEN in environment. Free for non-commercial use.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from gfwapiclient import Client

DATA_DIR = Path(__file__).parent / "data"

# Container ships on Asia-Europe routes (Suez corridor)
TARGET_VESSELS = [
    {"name": "Ever Given", "vessel_id": "bfbe8607a-aaf2-a6fd-84b2-7a01de04cbce"},
    {"name": "MSC Oscar", "vessel_id": "f814ed5b4-418d-5437-d790-960428833d47"},
    {"name": "CMA CGM Marco Polo", "vessel_id": "73f5ac167-7fd7-a051-fdbe-6d3877900f08"},
    {"name": "OOCL Hong Kong", "vessel_id": "5e3fc3ec4-4111-1c65-2167-1e38427dc556"},
    {"name": "Maersk Eindhoven", "vessel_id": "75bd0212b-bd08-6db2-fa10-6861bb322c62"},
]


async def search_vessel(client: Client, query: str) -> str | None:
    """Search for a vessel by name and return its GFW vessel_id."""
    results = await client.vessels.search_vessels(
        query=query,
        datasets=["public-global-vessel-identity:latest"],
    )
    data = results._data if hasattr(results, "_data") else []
    if not data:
        return None

    vessel = data[0]
    info = vessel.self_reported_info
    if info:
        return str(info[0].id)
    return None


async def export_port_visits(
    client: Client, vessel_id: str, start: str, end: str,
) -> list[dict[str, object]]:
    """Fetch port visit events for a vessel in a date range."""
    events = await client.events.get_all_events(
        datasets=["public-global-port-visits-events:latest"],
        vessels=[vessel_id],
        start_date=start,
        end_date=end,
    )
    data = events._data if hasattr(events, "_data") else []

    visits = []
    for event in data:
        visits.append({
            "id": event.id,
            "type": event.type,
            "start": str(event.start),
            "end": str(event.end),
            "position": str(event.position),
            "vessel_id": vessel_id,
        })
    return visits


async def run_export(start: str, end: str) -> None:
    """Export port visits for all target vessels."""
    token = os.environ.get("GFW_API_TOKEN", "")
    if not token:
        print("Error: GFW_API_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    client = Client(access_token=token)
    all_visits: list[dict[str, object]] = []

    for vessel in TARGET_VESSELS:
        name = vessel["name"]
        vessel_id = vessel["vessel_id"]
        print(f"Exporting port visits for {name} ({vessel_id})...")

        visits = await export_port_visits(client, str(vessel_id), start, end)
        for v in visits:
            v["vessel_name"] = name
        all_visits.extend(visits)
        print(f"  Found {len(visits)} port visits")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "port_visits.json"
    output_path.write_text(json.dumps(all_visits, indent=2))
    print(f"\nExported {len(all_visits)} total port visits to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export GFW port visit data")
    parser.add_argument("--start", default="2024-07-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    asyncio.run(run_export(args.start, args.end))


if __name__ == "__main__":
    main()
