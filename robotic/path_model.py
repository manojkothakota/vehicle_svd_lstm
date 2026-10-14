"""path_model.py - ClosedPath from your platoon_simulation.py (no matplotlib needed).
Loads waypoints.csv (x_m, y_m) and returns (x, y, heading) at arc-length s.
If the CSV is missing, a default oval track is generated so the demo always runs."""
import os
import numpy as np
import pandas as pd


def _make_default_track(csv_file):
    t = np.linspace(0, 2 * np.pi, 240, endpoint=False)
    df = pd.DataFrame({"x_m": 250 * np.cos(t), "y_m": 120 * np.sin(t)})
    os.makedirs(os.path.dirname(csv_file) or ".", exist_ok=True)
    df.to_csv(csv_file, index=False)
    print(f"[path_model] {csv_file} not found -> generated default oval track", flush=True)


class ClosedPath:
    def __init__(self, csv_file):
        if not os.path.exists(csv_file):
            _make_default_track(csv_file)
        df = pd.read_csv(csv_file)
        self.x = df["x_m"].to_numpy(dtype=float)
        self.y = df["y_m"].to_numpy(dtype=float)
        self.n = len(self.x)
        if self.n < 2:
            raise ValueError("waypoints.csv must contain at least 2 points.")
        x_next, y_next = np.roll(self.x, -1), np.roll(self.y, -1)
        seg_len = np.hypot(x_next - self.x, y_next - self.y)
        self.cum_dist = np.concatenate([[0.0], np.cumsum(seg_len)])[:-1]
        self.length = float(np.sum(seg_len))
        self.seg_heading = np.arctan2(y_next - self.y, x_next - self.x)

    def position_at(self, s):
        s = s % self.length
        idx = int(np.searchsorted(self.cum_dist, s, side="right") - 1)
        idx = max(0, min(idx, self.n - 1))
        idx_next = (idx + 1) % self.n
        seg_start = self.cum_dist[idx]
        seg_len = (self.length - seg_start) if idx_next == 0 else (self.cum_dist[idx_next] - seg_start)
        frac = 0.0 if seg_len <= 1e-9 else (s - seg_start) / seg_len
        x = self.x[idx] + frac * (self.x[idx_next] - self.x[idx])
        y = self.y[idx] + frac * (self.y[idx_next] - self.y[idx])
        return float(x), float(y), float(self.seg_heading[idx])
