"""stl_monitor.py - online Signal Temporal Logic monitor with quantitative robustness (rho).
rho >= 0  -> property satisfied (margin), rho < 0 -> violated.
 phi1  Safety             G (gap_i >= d_min)                                  rho = min_t,i (gap_i - d_min)
 phi2  Speed bound        G (0 <= v_i <= v_max)                               rho = min_t,i min(v_i, v_max - v_i)
 phi3  Cmd responsiveness G ((stop_i & REAL) -> F[0,tau] v_i <= delta)        rho = min_events max_{t in [tc,tc+tau]} (delta - v_i)
 phi4  Convergence        F[0,T1] G[0,T2] |v_i - v_des| <= eps                rho = min_i max_t0 min_t (eps - |v_i - v_des|)
 phi5  Attack robustness  G (FAKE_i -> a_i unaffected)                        rho = min_t,i (eps_a - |a_i - a_i_shadow|) while FAKE_i active"""
import math
import threading
import numpy as np
import config as C


class STLMonitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset(None)

    def reset(self, L):
        with self.lock:
            self.L = L
            self.rho1 = self.rho2 = self.rho5 = math.inf
            self.events, self.dev, self.t_ref, self.t_now = [], {}, 0.0, 0.0

    def on_msg(self, e):
        with self.lock:
            if e.get("isReal") and e.get("command"):
                self.t_ref, self.dev = e["t"], {}                 # restart settling window after an accepted command
                if e["command"] == "stop":
                    self.events.append({"t": e["t"], "car": e["toId"], "best": -math.inf, "done": False})

    def update(self, cars):
        with self.lock:
            cars = [c for c in cars if "t" in c and "s" in c]
            if not cars or not self.L:
                return
            t = max(c["t"] for c in cars)
            self.t_now = t
            by = {c["id"]: c for c in cars}
            S = {i: (c["s"] + c.get("dir", 1) * c["v"] * max(0.0, t - c["t"])) % self.L for i, c in by.items()}
            for i, c in by.items():
                if i > 0 and (i - 1) in by:                       # phi1
                    g = (S[i - 1] - S[i]) % self.L
                    g = g - self.L if g > self.L / 2 else g
                    self.rho1 = min(self.rho1, g - C.D_MIN)
                self.rho2 = min(self.rho2, c["v"], C.V_MAX - c["v"])           # phi2
                for e in self.events:                                           # phi3
                    if e["car"] == i and e["t"] <= t <= e["t"] + C.STL_TAU:
                        e["best"] = max(e["best"], C.STL_DELTA - c["v"])
                if t - c.get("fake_t", -99.0) <= C.FAKE_WINDOW:                # phi5
                    self.rho5 = min(self.rho5, C.A_DEV_EPS - c.get("a_dev", 0.0))
            for e in self.events:
                if t > e["t"] + C.STL_TAU:
                    e["done"] = True
            if 0 in by and t - self.t_ref <= C.STL_T1 + C.STL_T2:              # phi4 series
                l = by[0]
                vdes = 0.0 if l.get("stopped") else l.get("target", C.TARGET_SPEED) * l.get("mult", 1.0)
                for i, c in by.items():
                    self.dev.setdefault(i, []).append((t, abs(c["v"] - vdes)))

    def _phi4(self):
        if not self.dev:
            return None
        per_car = []
        for series in self.dev.values():
            a = np.array(series)
            if len(a) < 2:
                return None
            tt, g = a[:, 0], C.STL_EPS - a[:, 1]
            best = -math.inf
            for k in range(len(tt)):
                if tt[k] - self.t_ref > C.STL_T1:
                    break
                if tt[k] + C.STL_T2 > tt[-1]:
                    break
                hi = np.searchsorted(tt, tt[k] + C.STL_T2, side="right")
                best = max(best, float(g[k:hi].min()))
            if best == -math.inf:
                return None
            per_car.append(best)
        return min(per_car)

    def report(self):
        def fin(x):
            return None if x in (math.inf, -math.inf) else round(float(x), 3)

        def item(pid, name, formula, rho, pending=False):
            status = "pending" if pending else ("vacuous" if rho is None else ("sat" if rho >= 0 else "viol"))
            return {"id": pid, "name": name, "formula": formula, "rho": rho, "status": status}
        with self.lock:
            ev = self.events
            rho3 = min((e["best"] for e in ev), default=math.inf)
            pend3 = any((not e["done"]) and e["best"] < 0 for e in ev)
            r4 = self._phi4()
            return [
                item("phi1", "Safety (no collision)", "G (gap >= d_min)", fin(self.rho1)),
                item("phi2", "Speed bound", "G (0 <= v <= v_max)", fin(self.rho2)),
                item("phi3", "Command responsiveness", "G ((stop & REAL) -> F[0,tau] v <= delta)", fin(rho3), pend3),
                item("phi4", "Convergence / settling", "F[0,T1] G[0,T2] |v - v_des| <= eps",
                     None if r4 is None else round(r4, 3), r4 is None and bool(self.dev)),
                item("phi5", "Attack robustness", "G (FAKE -> a unaffected)", fin(self.rho5)),
            ]