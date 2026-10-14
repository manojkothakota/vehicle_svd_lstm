"""
platoon_simulation.py
----------------------
Simulates 10 cars, virtually connected in a linked list, following a fixed
closed road path (waypoints.csv from convert_path.py) at a uniform target
speed with realistic car-following control.

Each vehicle only "knows" its own state and receives a wireless message
from the vehicle directly ahead of it (v.prev.broadcast()) — this is the
exact communication channel a later cyberattack simulation will spoof.

Outputs:
    platoon_simulation.xlsx   - full per-second log + per-car comparison sheets
    platoon_speed_comparison.png
    platoon_acceleration_comparison.png
    platoon_distance_comparison.png
    platoon_gap_comparison.png
    platoon_snapshot.png      - car-shaped icons on the path (initial layout)
    platoon_animation.gif     - animated movement of all 10 cars on the path

Install requirements first:
    pip install numpy pandas matplotlib openpyxl pillow

Usage:
    python platoon_simulation.py waypoints.csv
    python platoon_simulation.py waypoints.csv --n_cars 10 --speed 12 --duration 200
"""

import argparse
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pyproj import Transformer


# ---------------------------------------------------------------------------
# 1. Fixed closed path (loaded from waypoints.csv, already road-shaped)
# ---------------------------------------------------------------------------
class ClosedPath:
    """Wraps the waypoints.csv reference line into a closed loop and lets
    any vehicle look up its (x, y, heading) from an arc-length position s,
    with s wrapping automatically once the loop is completed."""

    def __init__(self, csv_file):
        df = pd.read_csv(csv_file)
        self.x = df["x_m"].to_numpy(dtype=float)
        self.y = df["y_m"].to_numpy(dtype=float)
        self.n = len(self.x)
        if self.n < 2:
            raise ValueError("waypoints.csv must contain at least 2 points.")

        # distance from each point to the NEXT point, including the closing
        # segment from the last point back to the first (makes it a loop)
        x_next = np.roll(self.x, -1)
        y_next = np.roll(self.y, -1)
        seg_len = np.hypot(x_next - self.x, y_next - self.y)
        self.cum_dist = np.concatenate([[0.0], np.cumsum(seg_len)])[:-1]
        self.length = float(np.sum(seg_len))

        # heading of each segment (constant along the segment)
        self.seg_heading = np.arctan2(y_next - self.y, x_next - self.x)

    def position_at(self, s):
        """Return (x, y, heading) at arc-length s along the closed path."""
        s = s % self.length
        idx = int(np.searchsorted(self.cum_dist, s, side="right") - 1)
        idx = max(0, min(idx, self.n - 1))
        idx_next = (idx + 1) % self.n

        seg_start = self.cum_dist[idx]
        seg_len = (self.length - seg_start) if idx_next == 0 else (self.cum_dist[idx_next] - seg_start)
        frac = 0.0 if seg_len <= 1e-9 else (s - seg_start) / seg_len

        x = self.x[idx] + frac * (self.x[idx_next] - self.x[idx])
        y = self.y[idx] + frac * (self.y[idx_next] - self.y[idx])
        heading = self.seg_heading[idx]
        return x, y, heading


# ---------------------------------------------------------------------------
# 2. Vehicle = linked-list node
# ---------------------------------------------------------------------------
class Vehicle:
    def __init__(self, vid, s0, is_leader=False):
        self.id = vid
        self.is_leader = is_leader
        self.s = s0                 # arc-length position along the path (m)
        self.v = 0.0                # speed (m/s)
        self.a = 0.0                # acceleration (m/s^2), + or -
        self.distance_traveled = 0.0
        self.x = 0.0
        self.y = 0.0
        self.heading = 0.0
        self.prev = None            # linked-list pointer: vehicle ahead
        self.next = None            # linked-list pointer: vehicle behind

    def broadcast(self):
        """Wireless V2V message this vehicle sends to the vehicle behind it.
        This dict is exactly what a spoofing attack would later tamper with."""
        return {"id": self.id, "s": self.s, "v": self.v}


# ---------------------------------------------------------------------------
# 3. Platoon = linked list of vehicles + car-following control
# ---------------------------------------------------------------------------
class Platoon:
    def __init__(self, path, n_vehicles=10, spacing_m=20.0, target_speed=12.0):
        self.path = path
        self.n_vehicles = n_vehicles
        self.target_speed = target_speed
        self.time = 0.0

        # constant time-headway car-following control gains
        self.k_p = 0.35          # spacing-error gain
        self.k_v = 0.90          # relative-speed gain
        self.standstill_gap = 6.0
        self.time_gap = 1.0
        self.a_max = 2.0
        self.a_min = -3.0
        self.leader_accel = 1.0  # leader ramps up to target speed at this rate

        self.vehicles = []
        for i in range(n_vehicles):
            s0 = (-i * spacing_m) % path.length
            self.vehicles.append(Vehicle(i, s0, is_leader=(i == 0)))
        for i in range(1, n_vehicles):
            self.vehicles[i].prev = self.vehicles[i - 1]
            self.vehicles[i - 1].next = self.vehicles[i]

        self._update_xy()

    def _update_xy(self):
        for v in self.vehicles:
            v.x, v.y, v.heading = self.path.position_at(v.s)

    def step(self, dt):
        # ---- Leader: ramps up to and holds the uniform target speed ----
        leader = self.vehicles[0]
        leader.a = self.leader_accel if leader.v < self.target_speed else 0.0
        leader.v = float(np.clip(leader.v + leader.a * dt, 0.0, self.target_speed))
        leader.s = (leader.s + leader.v * dt) % self.path.length
        leader.distance_traveled += leader.v * dt

        # ---- Followers: react only to the wireless message from ahead ----
        for v in self.vehicles[1:]:
            msg = v.prev.broadcast()                       # <-- V2V message received
            gap = (msg["s"] - v.s) % self.path.length
            desired_gap = self.standstill_gap + self.time_gap * v.v
            spacing_error = gap - desired_gap
            speed_error = msg["v"] - v.v

            a = self.k_p * spacing_error + self.k_v * speed_error
            a = float(np.clip(a, self.a_min, self.a_max))

            v.a = a
            v.v = float(np.clip(v.v + a * dt, 0.0, self.target_speed * 1.3))
            v.s = (v.s + v.v * dt) % self.path.length
            v.distance_traveled += v.v * dt

        self._update_xy()
        self.time += dt

    def linked_list_state(self):
        """Traverse the platoon from the leader to the last follower using
        only the .next pointers, and collect every vehicle's live state.
        This is the linked-list data-retrieval pattern for the project."""
        state = []
        node = self.vehicles[0]
        while node is not None:
            state.append({"car_id": node.id, "s_m": node.s,
                           "speed_mps": node.v, "accel_mps2": node.a})
            node = node.next
        return state


# ---------------------------------------------------------------------------
# 4. Run the simulation and log per-second data for every car
# ---------------------------------------------------------------------------
def simulate(platoon, duration_s=200, dt=0.1, log_every=1.0):
    records = []
    n_steps = int(round(duration_s / dt))
    next_log = 0.0

    for step in range(n_steps + 1):
        t = step * dt
        if t + 1e-9 >= next_log:
            for v in platoon.vehicles:
                gap = np.nan
                if not v.is_leader:
                    gap = (v.prev.s - v.s) % platoon.path.length
                records.append({
                    "time_s": round(next_log, 2),
                    "car_id": v.id,
                    "role": "Leader" if v.is_leader else "Follower",
                    "x_m": round(v.x, 3),
                    "y_m": round(v.y, 3),
                    "heading_rad": round(v.heading, 5),
                    "distance_traveled_m": round(v.distance_traveled, 3),
                    "speed_mps": round(v.v, 4),
                    "acceleration_mps2": round(v.a, 4),
                    "gap_to_predecessor_m": round(gap, 3) if not np.isnan(gap) else np.nan,
                })
            next_log += log_every
        if step < n_steps:
            platoon.step(dt)

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 5. Excel export - full log + wide comparison sheets
# ---------------------------------------------------------------------------
def export_to_excel(df, filename="platoon_simulation.xlsx"):
    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Simulation_Log", index=False)

        for col, sheet in [("speed_mps", "Speed_by_Car"),
                            ("acceleration_mps2", "Acceleration_by_Car"),
                            ("distance_traveled_m", "Distance_by_Car"),
                            ("gap_to_predecessor_m", "Gap_by_Car")]:
            wide = df.pivot(index="time_s", columns="car_id", values=col)
            wide.columns = [f"Car_{c}" for c in wide.columns]
            wide.to_excel(writer, sheet_name=sheet)

    print(f"Saved: {filename}")


# ---------------------------------------------------------------------------
# 6. Comparison graphs across all 10 cars
# ---------------------------------------------------------------------------
def plot_comparisons(df, out_prefix="platoon"):
    car_ids = sorted(df["car_id"].unique())
    cmap = plt.get_cmap("tab10")

    def _plot(column, ylabel, title, filename):
        plt.figure(figsize=(10, 6))
        for cid in car_ids:
            sub = df[df["car_id"] == cid]
            label = "Car 0 (Leader)" if cid == 0 else f"Car {cid}"
            plt.plot(sub["time_s"], sub[column], label=label, color=cmap(cid % 10))
        plt.xlabel("Time (s)")
        plt.ylabel(ylabel)
        plt.title(title)
        plt.legend(loc="best", fontsize=8, ncol=2)
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(filename, dpi=150)
        plt.close()
        print(f"Saved: {filename}")

    _plot("speed_mps", "Speed (m/s)", "Speed Comparison - All Cars",
          f"{out_prefix}_speed_comparison.png")
    _plot("acceleration_mps2", "Acceleration (m/s^2)", "Acceleration Comparison - All Cars",
          f"{out_prefix}_acceleration_comparison.png")
    _plot("distance_traveled_m", "Cumulative Distance (m)", "Distance Traveled Comparison - All Cars",
          f"{out_prefix}_distance_comparison.png")

    plt.figure(figsize=(10, 6))
    for cid in car_ids:
        if cid == 0:
            continue
        sub = df[df["car_id"] == cid]
        plt.plot(sub["time_s"], sub["gap_to_predecessor_m"],
                  label=f"Car {cid} <- Car {cid - 1}", color=cmap(cid % 10))
    plt.xlabel("Time (s)")
    plt.ylabel("Gap to predecessor (m)")
    plt.title("Inter-Vehicle Spacing Over Time")
    plt.legend(loc="best", fontsize=8, ncol=2)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{out_prefix}_gap_comparison.png", dpi=150)
    plt.close()
    print(f"Saved: {out_prefix}_gap_comparison.png")


# ---------------------------------------------------------------------------
# 7. Car-shaped icons: static snapshot + animation
# ---------------------------------------------------------------------------
def _local_car_shapes(length=4.4, width=2.0):
    """Top-down car icon in the car's own local frame (forward = +x).
    Returns (body_polygon, windshield_polygon, [4 wheel_polygons])."""
    hl, hw = length / 2, width / 2

    body = np.array([
        [-hl,      -hw],
        [ hl * 0.6, -hw],
        [ hl,        0.0],   # pointed front, like a car nose
        [ hl * 0.6,  hw],
        [-hl,        hw],
    ])

    windshield = np.array([
        [ hl * 0.05, -hw * 0.55],
        [ hl * 0.55, -hw * 0.45],
        [ hl * 0.55,  hw * 0.45],
        [ hl * 0.05,  hw * 0.55],
    ])

    wheel_l, wheel_w = length * 0.22, width * 0.18
    wheel_centers = [
        (-hl * 0.55, -hw * 1.05), (-hl * 0.55, hw * 1.05),   # rear wheels
        ( hl * 0.35, -hw * 1.05), ( hl * 0.35, hw * 1.05),   # front wheels
    ]
    wheels = []
    for cx, cy in wheel_centers:
        wheels.append(np.array([
            [cx - wheel_l / 2, cy - wheel_w / 2],
            [cx + wheel_l / 2, cy - wheel_w / 2],
            [cx + wheel_l / 2, cy + wheel_w / 2],
            [cx - wheel_l / 2, cy + wheel_w / 2],
        ]))

    return body, windshield, wheels


def _rotate_translate(points, x, y, heading):
    c, s = np.cos(heading), np.sin(heading)
    R = np.array([[c, -s], [s, c]])
    out = points @ R.T
    out[:, 0] += x
    out[:, 1] += y
    return out


def plot_platoon_snapshot(path, platoon, filename="platoon_snapshot.png"):
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.plot(path.x, path.y, color="lightgray", linewidth=2, zorder=1, label="Fixed Path")
    cmap = plt.get_cmap("tab10")
    body_l, windshield_l, wheels_l = _local_car_shapes()

    for v in platoon.vehicles:
        color = cmap(v.id % 10)
        body = _rotate_translate(body_l.copy(), v.x, v.y, v.heading)
        windshield = _rotate_translate(windshield_l.copy(), v.x, v.y, v.heading)

        ax.add_patch(plt.Polygon(body, closed=True, facecolor=color,
                                   edgecolor="black", zorder=5))
        ax.add_patch(plt.Polygon(windshield, closed=True, facecolor="#cfe8f5",
                                   edgecolor="black", linewidth=0.5, zorder=6))
        for wl in wheels_l:
            w = _rotate_translate(wl.copy(), v.x, v.y, v.heading)
            ax.add_patch(plt.Polygon(w, closed=True, facecolor="black", zorder=6))

        label = "Leader" if v.is_leader else f"Car {v.id}"
        ax.text(v.x, v.y + 6, label, fontsize=7, ha="center", zorder=7)

    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title("Platoon Initial Layout on Fixed Path")
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(filename, dpi=150)
    plt.close()
    print(f"Saved: {filename}")


def export_map_animation(df, waypoints_csv, filename="map_animation_data.json"):
    """Convert every logged car position (meters) back to lat/lon using the
    same UTM zone/origin convert_path.py used, and save a JSON the Leaflet
    map page (map_animation.html) can play back as real movement on the
    actual map. Requires waypoints_meta.json saved next to waypoints_csv."""
    meta_file = waypoints_csv.rsplit(".", 1)[0] + "_meta.json"
    with open(meta_file, "r") as f:
        meta = json.load(f)

    transformer = Transformer.from_crs(f"EPSG:{meta['epsg']}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(df["x_m"].to_numpy() + meta["origin_x"],
                                      df["y_m"].to_numpy() + meta["origin_y"])
    df = df.copy()
    df["lat"] = lat
    df["lon"] = lon

    # the fixed route itself, for drawing the path line on the map
    wp = pd.read_csv(waypoints_csv)
    path_latlon = [{"lat": r.lat, "lon": r.lon} for r in wp.itertuples()]

    frames = []
    for t, sub in df.groupby("time_s"):
        cars = [{"id": int(r.car_id), "role": r.role, "lat": r.lat, "lon": r.lon,
                 "heading_deg": float(np.degrees(r.heading_rad)),
                 "speed_mps": r.speed_mps, "accel_mps2": r.acceleration_mps2}
                for r in sub.itertuples()]
        frames.append({"time_s": float(t), "cars": cars})

    data = {"path": path_latlon, "frames": frames}
    with open(filename, "w") as f:
        json.dump(data, f)
    print(f"Saved: {filename}  (open map_animation.html in a browser served locally to view it)")


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("waypoints_csv", help="waypoints.csv from convert_path.py")
    parser.add_argument("--n_cars", type=int, default=10)
    parser.add_argument("--spacing", type=float, default=20.0, help="initial spacing between cars (m)")
    parser.add_argument("--speed", type=float, default=12.0, help="target uniform speed (m/s)")
    parser.add_argument("--duration", type=float, default=200.0, help="simulation duration (s)")
    parser.add_argument("--out_prefix", type=str, default="platoon")
    args = parser.parse_args()

    path = ClosedPath(args.waypoints_csv)
    print(f"Loaded path: {path.n} points, total loop length = {path.length:.1f} m")

    platoon = Platoon(path, n_vehicles=args.n_cars, spacing_m=args.spacing, target_speed=args.speed)
    plot_platoon_snapshot(path, platoon, filename=f"{args.out_prefix}_snapshot.png")

    print(f"Simulating {args.n_cars} cars for {args.duration:.0f}s ...")
    df = simulate(platoon, duration_s=args.duration, dt=0.1, log_every=1.0)

    export_to_excel(df, filename=f"{args.out_prefix}_simulation.xlsx")
    plot_comparisons(df, out_prefix=args.out_prefix)
    export_map_animation(df, args.waypoints_csv, filename="map_animation_data.json")

    print("\nFinal linked-list traversal (leader -> last follower):")
    for s in platoon.linked_list_state():
        print(f"  Car {s['car_id']}: s={s['s_m']:.1f} m, "
              f"v={s['speed_mps']:.2f} m/s, a={s['accel_mps2']:.2f} m/s^2")

    print("\nDone.")


if __name__ == "__main__":
    main()