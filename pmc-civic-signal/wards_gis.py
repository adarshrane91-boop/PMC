"""Loads PMC ward polygons and assigns a lat/lon point to a ward.

IMPORTANT: data/pmc_wards.geojson shipped with this project is a placeholder
- eight illustrative rectangles, not the real PMC ward map. Point-in-polygon
assignment will work mechanically, but the wards themselves are wrong until
you replace that file. See README.md "Getting real ward boundaries".
"""
import json
from pathlib import Path
from typing import Optional

from shapely.geometry import Point, shape

BASE_DIR = Path(__file__).resolve().parent
WARDS_PATH = BASE_DIR / "data" / "pmc_wards.geojson"


class WardIndex:
    def __init__(self, geojson_path: Path = WARDS_PATH):
        data = json.loads(Path(geojson_path).read_text())
        self.wards = []
        for feat in data["features"]:
            self.wards.append({
                "id": feat["properties"]["ward_id"],
                "name": feat["properties"]["ward_name"],
                "geom": shape(feat["geometry"]),
                "geojson_geometry": feat["geometry"],
            })

    def ward_for_point(self, lat: float, lon: float) -> Optional[dict]:
        pt = Point(lon, lat)  # GeoJSON order is (lon, lat)
        for w in self.wards:
            if w["geom"].contains(pt):
                return w
        # Not inside any polygon (placeholder boundaries have gaps) -
        # fall back to nearest ward centroid so demo data still lands
        # somewhere sensible instead of being silently dropped.
        nearest = min(self.wards, key=lambda w: w["geom"].centroid.distance(pt))
        return nearest

    def as_feature_collection(self) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"ward_id": w["id"], "ward_name": w["name"]},
                 "geometry": w["geojson_geometry"]}
                for w in self.wards
            ],
        }


_index: Optional[WardIndex] = None


def get_index() -> WardIndex:
    global _index
    if _index is None:
        _index = WardIndex()
    return _index
