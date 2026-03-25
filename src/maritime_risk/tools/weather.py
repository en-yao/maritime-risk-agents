from __future__ import annotations

import json
import os

import httpx
from strands import tool

NOAA_BASE_URL = "https://www.ncei.noaa.gov/cdo-web/api/v2"


@tool
def check_weather(lat: float, lon: float, date: str) -> str:
    """Check weather conditions at a location for a given date.

    Args:
        lat: Latitude of the location
        lon: Longitude of the location
        date: Date to check (YYYY-MM-DD)
    """
    token = os.environ.get("NOAA_TOKEN", "")
    if not token:
        return json.dumps({"error": "NOAA_TOKEN not configured"})

    extent = f"{lat - 2},{lon - 2},{lat + 2},{lon + 2}"

    resp = httpx.get(
        f"{NOAA_BASE_URL}/data",
        params={
            "datasetid": "GHCND",
            "startdate": date,
            "enddate": date,
            "extent": extent,
            "limit": 50,
            "units": "metric",
        },
        headers={"token": token},
        timeout=30.0,
    )

    if resp.status_code != 200:
        return json.dumps({
            "lat": lat,
            "lon": lon,
            "date": date,
            "error": f"NOAA API returned {resp.status_code}",
        })

    data = resp.json()
    results = data.get("results", [])

    wind_obs = [r for r in results if r.get("datatype") in ("AWND", "WSF2", "WSF5")]
    precip_obs = [r for r in results if r.get("datatype") in ("PRCP", "SNOW")]

    alerts: list[str] = []
    max_wind = max((r.get("value", 0) for r in wind_obs), default=0)
    total_precip = sum(r.get("value", 0) for r in precip_obs)

    if max_wind > 20:
        alerts.append(f"High wind: {max_wind} m/s")
    if total_precip > 50:
        alerts.append(f"Heavy precipitation: {total_precip} mm")

    return json.dumps({
        "lat": lat,
        "lon": lon,
        "date": date,
        "max_wind_ms": max_wind,
        "total_precip_mm": total_precip,
        "alerts": alerts,
        "observations": len(results),
    })
