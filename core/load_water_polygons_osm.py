import json
import ssl
from pathlib import Path
from urllib import request

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "external" / "volgograd_water_polygons.geojson"
STATIONS_PATH = DATA_DIR / "stations.geojson"

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _overpass_query(query: str) -> dict:
    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
    ]

    for url in urls:
        try:
            print(f"Trying Overpass server: {url}")
            req = request.Request(url, data=query.encode("utf-8"), method="POST")
            with request.urlopen(req, timeout=180, context=SSL_CONTEXT) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except Exception as exc:
            print(f"Failed: {exc}")
            continue

    raise RuntimeError("All Overpass servers failed")


def _volgograd_bbox():
    stations = gpd.read_file(STATIONS_PATH)
    row = stations[stations["db_station"] == "volgograd_hub"].iloc[0]
    lon = float(row.geometry.x)
    lat = float(row.geometry.y)
    return (lat - 0.18, lon - 0.28, lat + 0.18, lon + 0.28)


def _query_string():
    south, west, north, east = _volgograd_bbox()
    return f"""
[out:json][timeout:180];
(
  way["waterway"="riverbank"]({south},{west},{north},{east});
  way["natural"="water"]({south},{west},{north},{east});
  way["water"="river"]({south},{west},{north},{east});
  way["water"="reservoir"]({south},{west},{north},{east});
);
out geom;
"""


def _geometry_from_way(element):
    coords = [(point["lon"], point["lat"]) for point in element.get("geometry", [])]
    if len(coords) < 4:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        polygon = Polygon(coords)
    except Exception:
        return None
    if not polygon.is_valid or polygon.area == 0:
        return None
    return polygon


def download_volgograd_water_polygons():
    data = _overpass_query(_query_string())
    features = []

    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue

        geometry = _geometry_from_way(element)
        if geometry is None:
            continue

        tags = element.get("tags", {})
        features.append(
            {
                "osm_id": element.get("id"),
                "name": tags.get("name", ""),
                "name_en": tags.get("name:en", ""),
                "water": tags.get("water", ""),
                "natural": tags.get("natural", ""),
                "waterway": tags.get("waterway", ""),
                "geometry": geometry,
            }
        )

    if not features:
        raise RuntimeError("No water polygons were downloaded for the Volgograd area")

    gdf = gpd.GeoDataFrame(features, geometry="geometry", crs="EPSG:4326")

    # Keep only the largest polygons to avoid tiny ponds cluttering the map.
    gdf_3857 = gdf.to_crs(3857)
    gdf["area_m2"] = gdf_3857.area
    gdf = gdf[gdf["area_m2"] > 50_000].copy()
    gdf = gdf.sort_values("area_m2", ascending=False).reset_index(drop=True)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    download_volgograd_water_polygons()
