"""
convert_path.py
----------------
Takes the path_data.json exported from map_path_selector.html
(a list of {"lat": .., "lng": ..} points you drew manually on the map)
and converts it into a clean, evenly-spaced set of waypoints in local
X/Y meters with heading — ready to be used as the reference trajectory
for the platoon leader vehicle in the Python simulation.

Install requirements first:
    pip install numpy pandas scipy pyproj

Usage:
    python convert_path.py path_data.json waypoints.csv --spacing 1.0
"""

import json
import argparse
import numpy as np
import pandas as pd
from scipy.interpolate import splprep, splev
from pyproj import Transformer


def load_path(json_file):
    with open(json_file, "r") as f:
        raw = json.load(f)
    lats = np.array([p["lat"] for p in raw])
    lngs = np.array([p["lng"] for p in raw])
    return lats, lngs


def latlng_to_utm(lats, lngs):
    """Convert WGS84 lat/lng to local UTM meters (auto-picks correct zone).
    Returns shifted x, y (origin at first point) plus the epsg code and the
    raw UTM origin (x0, y0) needed to convert back to lat/lng later."""
    zone = int((lngs[0] + 180) / 6) + 1
    hemisphere = "north" if lats[0] >= 0 else "south"
    epsg = 32600 + zone if hemisphere == "north" else 32700 + zone
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = transformer.transform(lngs, lats)
    x0, y0 = x[0], y[0]
    return x - x0, y - y0, epsg, x0, y0


def smooth_and_resample(x, y, spacing=1.0):
    """Fit a smooth spline through the manually drawn points and
    resample at fixed arc-length spacing (meters)."""
    # remove duplicate consecutive points (splprep needs strictly increasing param)
    pts = np.vstack([x, y]).T
    keep = np.insert(np.any(np.diff(pts, axis=0) != 0, axis=1), 0, True)
    x, y = x[keep], y[keep]

    k = 3 if len(x) > 3 else 1
    tck, _ = splprep([x, y], s=0, k=k)

    # estimate total path length by sampling densely first
    dense_u = np.linspace(0, 1, 2000)
    dense_x, dense_y = splev(dense_u, tck)
    seg_len = np.sqrt(np.diff(dense_x) ** 2 + np.diff(dense_y) ** 2)
    total_len = seg_len.sum()

    n_points = max(int(total_len / spacing), 2)
    u_fine = np.linspace(0, 1, n_points)
    xs, ys = splev(u_fine, tck)

    # cumulative distance
    dist = np.concatenate([[0], np.cumsum(np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2))])

    # heading (yaw) at each point, radians
    dx = np.gradient(xs)
    dy = np.gradient(ys)
    heading = np.arctan2(dy, dx)

    return xs, ys, dist, heading, total_len


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", help="path_data.json exported from the map tool")
    parser.add_argument("output_csv", help="output CSV of waypoints")
    parser.add_argument("--spacing", type=float, default=1.0,
                         help="distance in meters between resampled waypoints (default 1.0)")
    args = parser.parse_args()

    lats, lngs = load_path(args.input_json)
    x, y, epsg, x0, y0 = latlng_to_utm(lats, lngs)
    xs, ys, dist, heading, total_len = smooth_and_resample(x, y, args.spacing)

    # convert the resampled meter-based path back to lat/lon so it can be
    # drawn on a real map later (needed for the live map animation step)
    inv_transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon_out, lat_out = inv_transformer.transform(xs + x0, ys + y0)

    df = pd.DataFrame({
        "index": np.arange(len(xs)),
        "x_m": xs,
        "y_m": ys,
        "distance_m": dist,
        "heading_rad": heading,
        "lat": lat_out,
        "lon": lon_out,
    })
    df.to_csv(args.output_csv, index=False)

    meta = {"epsg": epsg, "origin_x": x0, "origin_y": y0}
    meta_file = args.output_csv.rsplit(".", 1)[0] + "_meta.json"
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"UTM zone used: EPSG:{epsg}")
    print(f"Raw drawn points: {len(lats)}")
    print(f"Resampled waypoints: {len(xs)} (spacing={args.spacing} m)")
    print(f"Total path length: {total_len:.1f} m")
    print(f"Saved to: {args.output_csv}")
    print(f"Saved metadata to: {meta_file}")


if __name__ == "__main__":
    main()