from __future__ import annotations

import json
from typing import Any
from functools import lru_cache
from importlib import resources

import searoute as sr
from strands import tool

VESSEL_SPEED_KNOTS = 14.0
HOURS_PER_DAY = 24.0


@lru_cache(maxsize=1)
def _load_ports() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Load port data from searoute's bundled geojson."""
    port_file = resources.files("searoute") / "data" / "ports.geojson"
    data = json.loads(port_file.read_text())

    ports: dict[str, dict[str, Any]] = {}
    name_index: dict[str, str] = {}

    for feature in data["features"]:
        props = feature["properties"]
        coords = feature["geometry"]["coordinates"]
        code = props["port"]
        name = props["name"]

        ports[code] = {
            "code": code,
            "name": name,
            "country": props.get("cty", ""),
            "lon": coords[0],
            "lat": coords[1],
        }
        name_index[name.lower()] = code

    return ports, name_index


def _resolve_port(port: str) -> tuple[str, list[float]]:
    """Resolve port name or code to (code, [lon, lat])."""
    ports, name_index = _load_ports()

    # Exact code match
    upper = port.strip().upper()
    if upper in ports:
        p = ports[upper]
        return str(p["code"]), [float(p["lon"]), float(p["lat"])]

    # Exact name match
    lower = port.strip().lower()
    if lower in name_index:
        code = name_index[lower]
        p = ports[code]
        return str(p["code"]), [float(p["lon"]), float(p["lat"])]

    # Partial name match — prefer shortest matching name (most specific)
    matches: list[tuple[str, str]] = []
    for name, code in name_index.items():
        if lower in name or name in lower:
            matches.append((name, code))

    if matches:
        matches.sort(key=lambda x: len(x[0]))
        code = matches[0][1]
        p = ports[code]
        return str(p["code"]), [float(p["lon"]), float(p["lat"])]

    raise ValueError(f"Unknown port: {port}")


def _transit_days(distance_nm: float) -> float:
    return round(distance_nm / (VESSEL_SPEED_KNOTS * HOURS_PER_DAY), 1)


@tool
def calculate_route(origin_port: str, destination_port: str) -> str:
    """Calculate maritime route between two ports.

    Args:
        origin_port: Port name or UN/LOCODE (e.g., "Shanghai", "CNSHA", "Rotterdam")
        destination_port: Port name or UN/LOCODE (e.g., "Rotterdam", "NLRTM", "Houston")
    """
    try:
        origin_code, origin_coords = _resolve_port(origin_port)
        dest_code, dest_coords = _resolve_port(destination_port)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    route = sr.searoute(origin_coords, dest_coords, units="nm")
    distance_nm = route["properties"]["length"]

    if distance_nm <= 0:
        return json.dumps({"error": f"No viable route between {origin_port} and {destination_port}"})

    transit_days = _transit_days(distance_nm)

    return json.dumps({
        "origin": origin_port,
        "origin_code": origin_code,
        "destination": destination_port,
        "destination_code": dest_code,
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
        origin_port: Port name or UN/LOCODE (e.g., "Shanghai", "CNSHA", "Rotterdam")
        destination_port: Port name or UN/LOCODE (e.g., "Rotterdam", "NLRTM", "Houston")
        avoid: Comma-separated passages to avoid (e.g., "suez", "panama")
    """
    try:
        origin_code, origin_coords = _resolve_port(origin_port)
        dest_code, dest_coords = _resolve_port(destination_port)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    restrictions = [r.strip().lower() for r in avoid.split(",")]

    standard = sr.searoute(origin_coords, dest_coords, units="nm")
    standard_nm = standard["properties"]["length"]

    alternative = sr.searoute(
        origin_coords, dest_coords, units="nm", restrictions=restrictions,
    )
    alt_nm = alternative["properties"]["length"]

    if alt_nm <= 0:
        return json.dumps({
            "error": f"No viable alternative route found avoiding {restrictions}",
            "origin": origin_port,
            "destination": destination_port,
        })

    alt_days = _transit_days(alt_nm)
    delta_days = round(alt_days - _transit_days(standard_nm), 1)

    return json.dumps({
        "origin": origin_port,
        "origin_code": origin_code,
        "destination": destination_port,
        "destination_code": dest_code,
        "avoiding": restrictions,
        "distance_nm": round(alt_nm, 1),
        "transit_days": alt_days,
        "delta_vs_planned_days": delta_days,
        "route_type": "alternative",
    })
