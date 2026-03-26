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

# Vessels on Panama Canal and US East Coast routes (operational disruptions)
TARGET_VESSELS = [
    {"name": "CMA CGM Libra", "vessel_id": "697ae2768-8a0f-b9d5-e4d1-68c22a7925f8"},
    {"name": "Maersk Hartford", "vessel_id": "00c73d546-661d-bbc5-0356-00ad430f42be"},
    {"name": "Maersk Seletar", "vessel_id": "8272e36d2-21d4-e258-7122-7aa7bc8cff84"},
    {"name": "Maersk Sentosa", "vessel_id": "fc20beb0a-ad6f-9004-0b87-d2b8df20b0a9"},
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
