"""Run backtest: agent assesses historical shipments with data cutoff enforced.

Usage:
    1. Start news server: uv run python -m eval.news_server
    2. Run backtest:      uv run python -m eval.backtest

GFW arrival times are NEVER fed to the agent — held out for scoring only.
News feed is served via local RSS server with ?before= date cutoff.
Tool calls are traced via Strands hooks and saved alongside agent responses.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from maritime_risk.orchestrator import create_orchestrator

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = DATA_DIR / "results"


class ToolTracer(HookProvider):
    """Captures tool call traces during agent execution."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []
        self._pending_start: dict[str, float] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before)
        registry.add_callback(AfterToolCallEvent, self._on_after)

    def _on_before(self, event: BeforeToolCallEvent) -> None:
        tool_use_id = event.tool_use.get("toolUseId", "")
        self._pending_start[tool_use_id] = time.monotonic()

    def _on_after(self, event: AfterToolCallEvent) -> None:
        tool_use_id = event.tool_use.get("toolUseId", "")
        start = self._pending_start.pop(tool_use_id, None)
        duration_ms = round((time.monotonic() - start) * 1000) if start else None

        result_text: str | None = None
        if event.result:
            for block in event.result.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    result_text = block["text"]
                    break

        self.traces.append({
            "tool": event.tool_use.get("name", ""),
            "input": event.tool_use.get("input", {}),
            "status": event.result.get("status", "unknown") if event.result else "error",
            "result_preview": result_text[:500] if result_text else None,
            "duration_ms": duration_ms,
            "error": str(event.exception) if event.exception else None,
        })

    def reset(self) -> None:
        self.traces.clear()
        self._pending_start.clear()


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
    """Run the orchestrator against each historical shipment.

    Requires eval/news_server.py running on localhost:8765.
    Each shipment sets NEWS_RSS_FEEDS with ?before= to enforce data cutoff.
    """
    news_port = os.environ.get("NEWS_SERVER_PORT", "8765")
    news_base = f"http://localhost:{news_port}/feed"

    shipments = load_shipments()
    print(f"Loaded {len(shipments)} shipments for backtest")
    print(f"News server: {news_base}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tracer = ToolTracer()

    for i, shipment in enumerate(shipments):
        prompt = build_prompt(shipment)
        if not prompt:
            print(f"  [{i + 1}/{len(shipments)}] Skipping — insufficient data")
            continue

        # Set news feed URL with data cutoff for this shipment's departure date
        departure = str(shipment.get("origin_departure", ""))[:10]
        os.environ["NEWS_RSS_FEEDS"] = f"{news_base}?before={departure}"

        # Create fresh agent per shipment to pick up new feed URL
        tracer.reset()
        agent = create_orchestrator(hooks=[tracer])

        print(f"  [{i + 1}/{len(shipments)}] {prompt[:80]}...")

        try:
            result = agent(prompt)
            raw_message = result.message
            response_text: object = raw_message
            if isinstance(raw_message, dict):
                content = raw_message.get("content", [])
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        response_text = block["text"]
                        break

            prediction: dict[str, object] = {
                "shipment": shipment,
                "prompt": prompt,
                "agent_response": response_text,
                "tool_trace": tracer.traces.copy(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            prediction = {
                "shipment": shipment,
                "prompt": prompt,
                "error": str(e),
                "tool_trace": tracer.traces.copy(),
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
