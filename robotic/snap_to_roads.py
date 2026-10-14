"""
snap_to_roads.py
-----------------
Takes the raw path_data.json (straight-line points you manually clicked on
the map tool) and snaps it to real roads using OSRM's free public routing
service (no API key required). The output follows real streets between your
points instead of straight lines, in the same order you clicked them.

Install requirements first:
    pip install requests

Usage:
    python snap_to_roads.py path_data.json path_data_snapped.json
"""

import json
import argparse
import requests

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving/"


def load_points(json_file):
    with open(json_file, "r") as f:
        points = json.load(f)
    if len(points) < 2:
        raise ValueError("Need at least 2 points to snap to roads.")
    return points


def snap_to_roads(points, close_loop=True):
    pts = list(points)
    if close_loop:
        pts = pts + [pts[0]]  # close the loop back to the starting point

    coord_str = ";".join(f"{p['lng']},{p['lat']}" for p in pts)
    url = f"{OSRM_BASE_URL}{coord_str}"
    params = {"geometries": "geojson", "overview": "full"}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('message', data.get('code'))}")

    coords = data["routes"][0]["geometry"]["coordinates"]  # [ [lng, lat], ... ]
    snapped = [{"lat": lat, "lng": lng} for lng, lat in coords]
    return snapped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", help="raw path_data.json from the map tool")
    parser.add_argument("output_json", help="output snapped path json")
    parser.add_argument("--no-close-loop", action="store_true",
                         help="do not force closing the path back to the start point")
    args = parser.parse_args()

    points = load_points(args.input_json)
    print(f"Loaded {len(points)} manually drawn points.")

    snapped = snap_to_roads(points, close_loop=not args.no_close_loop)
    print(f"Snapped route has {len(snapped)} road-following points.")

    with open(args.output_json, "w") as f:
        json.dump(snapped, f, indent=2)

    print(f"Saved to: {args.output_json}")
    print("Next step: python convert_path.py " + args.output_json + " waypoints.csv --spacing 1.0")


if __name__ == "__main__":
    main()