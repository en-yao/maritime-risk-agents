"""Export MarineTraffic port call data for a backtest period.

Usage:
    uv run python -m eval.export --start 2024-07-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

MARINETRAFFIC_BASE = "https://services.marinetraffic.com/api"
DATA_DIR = Path(__file__).parent / "data"


def export_port_calls(start: str, end: str, api_key: str) -> list[dict[str, object]]:
    """Fetch port call records from MarineTraffic for the given date range."""
    resp = httpx.get(
        f"{MARINETRAFFIC_BASE}/exportportcalls/v5/{api_key}",
        params={
            "fromdate": start,
            "todate": end,
            "msgtype": "simple",
            "protocol": "jsono",
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export MarineTraffic port call data")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    api_key = os.environ.get("MARINETRAFFIC_API_KEY", "")
    if not api_key:
        print("Error: MARINETRAFFIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    print(f"Exporting port calls from {args.start} to {args.end}...")
    records = export_port_calls(args.start, args.end, api_key)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "port_calls.json"
    output_path.write_text(json.dumps(records, indent=2))

    print(f"Exported {len(records)} port call records to {output_path}")


if __name__ == "__main__":
    main()
