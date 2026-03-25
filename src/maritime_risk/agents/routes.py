from __future__ import annotations

import json

import searoute as sr
from strands import tool

VESSEL_SPEED_KNOTS = 14.0
HOURS_PER_DAY = 24.0


def _transit_days(distance_nm: float) -> float:
    return round(distance_nm / (VESSEL_SPEED_KNOTS * HOURS_PER_DAY), 1)


@tool
def calculate_route(origin_port: str, destination_port: str) -> str:
    """Calculate maritime route between two ports.

    Args:
        origin_port: Origin port name or code
        destination_port: Destination port name or code
    """
    route = sr.searoute(origin_port, destination_port, units="nm")
    distance_nm = route["properties"]["length"]
    transit_days = _transit_days(distance_nm)

    return json.dumps({
        "origin": origin_port,
        "destination": destination_port,
        "distance_nm": round(distance_nm, 1),
        "transit_days": transit_days,
        "route_type": "standard",
    })


@tool
def calculate_alternative_route(
    origin_port: str, destination_port: str, avoid: str,
) -> str:
    """Calculate alternative route avoiding specified passages.

    Args:
        origin_port: Origin port name or code
        destination_port: Destination port name or code
        avoid: Comma-separated passages to avoid (e.g., "suez", "panama")
    """
    restrictions = [r.strip().lower() for r in avoid.split(",")]

    standard = sr.searoute(origin_port, destination_port, units="nm")
    standard_nm = standard["properties"]["length"]

    alternative = sr.searoute(
        origin_port, destination_port, units="nm", restrictions=restrictions,
    )
    alt_nm = alternative["properties"]["length"]
    alt_days = _transit_days(alt_nm)
    delta_days = round(alt_days - _transit_days(standard_nm), 1)

    return json.dumps({
        "origin": origin_port,
        "destination": destination_port,
        "avoiding": restrictions,
        "distance_nm": round(alt_nm, 1),
        "transit_days": alt_days,
        "delta_vs_planned_days": delta_days,
        "route_type": "alternative",
    })
