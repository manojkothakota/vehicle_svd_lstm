"""
dynamics_control_stl.py

Newton-Euler rigid-body dynamics + platoon control + STL monitoring,
implementing every formula from the project documentation as executable
Python code. Exports results.json, consumed by dynamics_report.html.

Sections below map 1:1 to the documentation:
  3.1  General Newton-Euler equations
  3.2  Planar reduction (single-track vehicle model)
  3.3  Longitudinal force balance
  3.4  Yaw / heading dynamics (Euler's rotational equation)
  4.1  Longitudinal platoon control (constant time-gap spacing policy)
  4.2  Direction / heading control law (PD torque controller)
  4.3  Command-gated supervisory layer
  5.x  Signal Temporal Logic (STL) specifications phi1 - phi7
"""

import json
import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# =====================================================================
# CONFIG: physical constants and control gains
# =====================================================================
@dataclass
class Config:
    m: float = 1500.0          # vehicle mass (kg)
    I_z: float = 2500.0        # yaw moment of inertia (kg*m^2)
    rho: float = 1.225         # air density (kg/m^3)
    C_d: float = 0.32          # aerodynamic drag coefficient
    A: float = 2.2             # frontal area (m^2)
    C_r: float = 0.015         # rolling resistance coefficient
    g: float = 9.81            # gravity (m/s^2)
    theta: float = 0.0         # road grade angle (rad), flat road

    r0: float = 6.0            # standstill gap (m)
    h: float = 1.0             # time headway (s)
    k_p: float = 0.35          # spacing feedback gain
    k_v: float = 0.90          # speed feedback gain
    k_psi: float = 4.0         # heading proportional gain
    k_omega: float = 2.0       # yaw-rate damping gain

    a_min: float = -6.0        # min longitudinal acceleration (m/s^2)
    a_max: float = 3.0         # max longitudinal acceleration (m/s^2)
    tau_max: float = 4000.0    # max yaw torque magnitude (N*m)

    v_nom: float = 15.0        # nominal platoon speed (m/s)
    v_max: float = 40.0        # STL speed bound (m/s)
    d_min: float = 3.0         # STL minimum safe gap (m)

    eps_v: float = 0.5         # STL speed-tolerance (m/s)
    eps_psi: float = 0.05      # STL heading-tolerance (rad)
    tau: float = 3.0           # STL settle time for stop (s)
    tau_psi: float = 4.0       # STL settle time for heading (s)
    T1: float = 8.0            # STL speed-change reach-by time (s)
    T2: float = 3.0            # STL speed-change hold window (s)

    F_brake_max: float = 9000.0  # max braking force magnitude (N)


CFG = Config()


# =====================================================================
# 3.3  Longitudinal force balance
#      F_x,i = F_traction,i - F_aero,i - F_roll,i - F_grade,i
# =====================================================================
def aero_drag_force(v: float, cfg: Config = CFG) -> float:
    """F_aero = 1/2 * rho * C_d * A * v^2"""
    return 0.5 * cfg.rho * cfg.C_d * cfg.A * v * abs(v)


def rolling_resistance_force(cfg: Config = CFG) -> float:
    """F_roll = C_r * m * g * cos(theta)"""
    return cfg.C_r * cfg.m * cfg.g * math.cos(cfg.theta)


def grade_force(cfg: Config = CFG) -> float:
    """F_grade = m * g * sin(theta)"""
    return cfg.m * cfg.g * math.sin(cfg.theta)


def net_longitudinal_force(F_traction: float, v: float, cfg: Config = CFG) -> float:
    """F_x = F_traction - F_aero - F_roll - F_grade"""
    return F_traction - aero_drag_force(v, cfg) - rolling_resistance_force(cfg) - grade_force(cfg)


# =====================================================================
# 3.1 / 3.2  Newton-Euler rigid body, reduced to a planar single-track
#            vehicle:
#            m (dv_i/dt)        = F_x,i               (Newton, longitudinal)
#            I_z (domega_z,i/dt) = tau_z,i             (Euler, yaw)
#            dpsi_i/dt           = omega_z,i
# =====================================================================
@dataclass
class VehicleState:
    id: int
    is_leader: bool
    s: float = 0.0          # path position (m)
    v: float = 0.0          # longitudinal speed (m/s)
    a: float = 0.0          # longitudinal acceleration (m/s^2)
    psi: float = 0.0        # heading (rad)
    omega: float = 0.0      # yaw rate (rad/s)
    tau_z: float = 0.0      # applied yaw torque (N*m)

    # supervisory / command-gated state (Section 4.3)
    mu: float = 1.0         # commanded speed multiplier
    stopped: bool = False   # forced-halt flag
    direction: int = 1      # +1 forward, -1 reversed
    psi_des: Optional[float] = None  # commanded target heading


class NewtonEulerVehicle:
    """Integrates the reduced Newton-Euler equations for one vehicle
    (Sections 3.1-3.4), one explicit-Euler time step at a time."""

    def __init__(self, state: VehicleState, cfg: Config = CFG):
        self.state = state
        self.cfg = cfg

    def step(self, dt: float, F_traction: float, tau_z_cmd: float):
        st, cfg = self.state, self.cfg

        # ---- 3.3 longitudinal force balance -> 3.1/3.2 Newton eq ----
        if st.stopped:
            F_traction = -math.copysign(cfg.F_brake_max, st.v) if st.v != 0 else 0.0
        F_x = net_longitudinal_force(F_traction, st.v, cfg)
        st.a = F_x / cfg.m                                   # m dv/dt = F_x
        st.v = max(0.0, st.v + st.a * dt)
        st.s += st.direction * st.v * dt

        # ---- 3.4 Euler rotational equation (yaw) ----
        tau_z_cmd = max(-cfg.tau_max, min(cfg.tau_max, tau_z_cmd))
        st.tau_z = tau_z_cmd
        domega = tau_z_cmd / cfg.I_z                          # I_z domega/dt = tau_z
        st.omega += domega * dt
        st.psi += st.omega * dt                               # dpsi/dt = omega


# =====================================================================
# 4.1  Longitudinal platoon control - constant time-gap spacing policy
#      d_des,i   = r0 + h * v_i
#      e_gap,i   = (s_{i-1} - s_i) - d_des,i
#      e_v,i     = v_{i-1} - v_i
#      a_des,i   = k_p*e_gap,i + k_v*e_v,i
#      F_traction,i = m [ a_des,i + F_aero,i/m + F_roll,i/m + F_grade,i/m ]
#                    (feedback-linearizing inner loop)
# =====================================================================
class PlatoonSpacingController:
    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg

    def desired_gap(self, v_i: float) -> float:
        return self.cfg.r0 + self.cfg.h * v_i

    def desired_acceleration(self, gap: float, v_i: float, v_prev: float) -> float:
        d_des = self.desired_gap(v_i)
        e_gap = gap - d_des
        e_v = v_prev - v_i
        a_des = self.cfg.k_p * e_gap + self.cfg.k_v * e_v
        return max(self.cfg.a_min, min(self.cfg.a_max, a_des))

    def leader_desired_acceleration(self, v_i: float, v_target: float) -> float:
        # Leader tracks a commanded target speed directly (no vehicle ahead)
        a_des = 1.1 * (v_target - v_i)
        return max(self.cfg.a_min, min(self.cfg.a_max, a_des))

    def required_traction_force(self, a_des: float, v_i: float) -> float:
        """Feedback-linearizing inner loop: cancel resistance terms so the
        vehicle's actual acceleration tracks a_des (Section 4.1)."""
        cfg = self.cfg
        return cfg.m * a_des + aero_drag_force(v_i, cfg) + rolling_resistance_force(cfg) + grade_force(cfg)


# =====================================================================
# 4.2  Direction / heading control law - PD torque controller
#      psi_des,i = psi_i + pi                       (180 deg reversal)
#      tau_z,i   = I_z * psi_des_ddot
#                + k_psi (psi_des,i - psi_i)
#                + k_omega (omega_des,i - omega_z,i)
# =====================================================================
class HeadingController:
    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg

    def torque_command(self, psi_i: float, omega_i: float, psi_des: float,
                        omega_des: float = 0.0, psi_des_ddot: float = 0.0) -> float:
        cfg = self.cfg
        return (cfg.I_z * psi_des_ddot
                + cfg.k_psi * (psi_des - psi_i)
                + cfg.k_omega * (omega_des - omega_i))


# =====================================================================
# 4.3  Command-gated supervisory layer
#      v_des,i(t) = mu_i(t) * v_nom ,  mu_i updated only if Authentic(m)=TRUE
#      Stopped_i(t)=TRUE => F_traction,i <- F_brake,max
#
#      A lightweight rule-based Authentic(m) classifier is included here so
#      this file is self-contained; swap in the trained LSTM's
#      predict_message() (train_lstm_detector.py) for the full NLP model.
# =====================================================================
SUSPICIOUS_KEYWORDS = [
    "override", "bypass", "disable", "ignore safety", "hack", "inject",
    "malware", "virus", "unauthorized", "root access", "admin access",
    "shutdown", "kill switch", "force crash", "disable brakes", "spoof",
    "jam signal", "no limit", "unlimited speed", "crash", "collide",
]

COMMAND_PATTERNS = {
    "double": ["double the speed", "double speed"],
    "half": ["half the speed", "halve the speed"],
    "increase": ["increase speed", "speed up", "accelerate", "faster"],
    "decrease": ["decrease speed", "slow down", "decelerate", "slower"],
    "stop": ["stop", "halt", "brake"],
    "resume": ["resume", "continue"],
    "reverse": ["change direction", "reverse direction", "reverse", "turn around"],
}


def parse_command(text: str) -> Optional[str]:
    t = text.lower()
    for cmd, patterns in COMMAND_PATTERNS.items():
        if any(p in t for p in patterns):
            return cmd
    return None


def authentic(text: str) -> bool:
    """Authentic(m): TRUE (REAL) if the message matches a known command and
    contains no suspicious vocabulary; FALSE (FAKE) otherwise."""
    t = text.lower()
    if any(k in t for k in SUSPICIOUS_KEYWORDS):
        return False
    return parse_command(text) is not None


class SupervisoryLayer:
    """Applies verified V2V messages to a vehicle's reference state
    (Section 4.3). Fake messages are logged but never change mu/stopped/
    direction/psi_des."""

    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg
        self.event_log: List[Dict] = []

    def apply_message(self, vehicle: VehicleState, text: str, t: float):
        is_real = authentic(text)
        cmd = parse_command(text)
        applied = False

        if is_real and cmd:
            applied = True
            if cmd == "double":
                vehicle.mu = min(4.0, vehicle.mu * 2.0)
                vehicle.stopped = False
            elif cmd == "half":
                vehicle.mu = max(0.15, vehicle.mu * 0.5)
            elif cmd == "increase":
                vehicle.mu = min(4.0, vehicle.mu + 0.4)
                vehicle.stopped = False
            elif cmd == "decrease":
                vehicle.mu = max(0.15, vehicle.mu - 0.4)
            elif cmd == "stop":
                vehicle.stopped = True
            elif cmd == "resume":
                vehicle.stopped = False
            elif cmd == "reverse":
                vehicle.direction *= -1
                vehicle.psi_des = vehicle.psi + math.pi

        self.event_log.append({
            "t": t, "vehicle_id": vehicle.id, "text": text,
            "authentic": is_real, "command": cmd, "applied": applied
        })
        return is_real, cmd, applied


# =====================================================================
# SIMULATOR: wires Newton-Euler + control laws + supervisory layer
# together and logs every signal needed by the STL monitor
# =====================================================================
class PlatoonSimulator:
    def __init__(self, n_vehicles: int = 5, cfg: Config = CFG):
        self.cfg = cfg
        self.spacing_ctrl = PlatoonSpacingController(cfg)
        self.heading_ctrl = HeadingController(cfg)
        self.supervisor = SupervisoryLayer(cfg)

        self.vehicles: List[NewtonEulerVehicle] = []
        for i in range(n_vehicles):
            st = VehicleState(id=i, is_leader=(i == 0), s=-i * (cfg.r0 + cfg.h * cfg.v_nom))
            self.vehicles.append(NewtonEulerVehicle(st, cfg))

        self.t = 0.0
        self.log: Dict[int, List[Dict]] = {v.state.id: [] for v in self.vehicles}
        self.scheduled_messages: List[Dict] = []  # {"t":..,"vehicle_id":..,"text":..}

    def schedule_message(self, t: float, vehicle_id: int, text: str):
        self.scheduled_messages.append({"t": t, "vehicle_id": vehicle_id, "text": text})

    def _dispatch_due_messages(self, t_next: float):
        due = [m for m in self.scheduled_messages if self.t <= m["t"] < t_next]
        for m in due:
            vehicle = self.vehicles[m["vehicle_id"]].state
            self.supervisor.apply_message(vehicle, m["text"], m["t"])

    def step(self, dt: float):
        self._dispatch_due_messages(self.t + dt)
        cfg = self.cfg

        for idx, nev in enumerate(self.vehicles):
            st = nev.state

            # ---- 4.1 longitudinal control ----
            if st.is_leader:
                v_target = cfg.v_nom * st.mu
                a_des = self.spacing_ctrl.leader_desired_acceleration(st.v, v_target)
            else:
                prev = self.vehicles[idx - 1].state
                gap = prev.s - st.s
                if abs(st.mu - 1.0) > 1e-6:
                    # individually commanded -> track its own target directly
                    v_target = cfg.v_nom * st.mu
                    a_des = 1.1 * (v_target - st.v)
                    a_des = max(cfg.a_min, min(cfg.a_max, a_des))
                else:
                    a_des = self.spacing_ctrl.desired_acceleration(gap, st.v, prev.v)

            F_traction = self.spacing_ctrl.required_traction_force(a_des, st.v)

            # ---- 4.2 heading control ----
            tau_cmd = 0.0
            if st.psi_des is not None:
                tau_cmd = self.heading_ctrl.torque_command(st.psi, st.omega, st.psi_des)
                if abs(st.psi_des - st.psi) < CFG.eps_psi:
                    st.psi_des = None  # reached target, stop actuating

            # ---- 3.1-3.4 Newton-Euler integration ----
            nev.step(dt, F_traction, tau_cmd)

        self.t += dt
        self._record()

    def _record(self):
        for nev in self.vehicles:
            st = nev.state
            gap = None
            if not st.is_leader:
                prev = self.vehicles[st.id - 1].state
                gap = prev.s - st.s
            self.log[st.id].append({
                "t": round(self.t, 3), "s": st.s, "v": st.v, "a": st.a,
                "psi": st.psi, "omega": st.omega, "tau_z": st.tau_z,
                "gap": gap, "mu": st.mu, "stopped": st.stopped
            })

    def run(self, T: float, dt: float = 0.1):
        steps = int(T / dt)
        for _ in range(steps):
            self.step(dt)


# =====================================================================
# 5.  SIGNAL TEMPORAL LOGIC (STL) MONITORS  --  phi1 .. phi7
#     Quantitative (robustness) semantics: value > 0 means satisfied,
#     value <= 0 means violated, |value| = margin of (dis)satisfaction.
# =====================================================================
class STLMonitor:
    def __init__(self, sim: PlatoonSimulator, cfg: Config = CFG):
        self.sim = sim
        self.cfg = cfg

    # 5.2  phi1: G[0,T]( gap_i(t) >= d_min )  for all i
    def phi1_collision_avoidance(self) -> float:
        worst = math.inf
        for vid, rows in self.sim.log.items():
            for r in rows:
                if r["gap"] is not None:
                    worst = min(worst, r["gap"] - self.cfg.d_min)
        return worst

    # 5.3  phi2: G[0,T]( 0 <= v_i(t) <= v_max )
    def phi2_speed_bound(self) -> float:
        worst = math.inf
        for rows in self.sim.log.values():
            for r in rows:
                worst = min(worst, r["v"] - 0.0, self.cfg.v_max - r["v"])
        return worst

    # 5.4  phi3: G[0,T]( (Stop_cmd,i ^ Authentic) -> F[0,tau]( v_i(t) <= eps_v ) )
    def phi3_stop_responsiveness(self) -> float:
        cfg = self.cfg
        worst = math.inf
        events = [e for e in self.sim.supervisor.event_log
                  if e["command"] == "stop" and e["authentic"]]
        if not events:
            return math.inf  # vacuously satisfied (no stop command issued)
        for e in events:
            vid, t0 = e["vehicle_id"], e["t"]
            window = [r for r in self.sim.log[vid] if t0 <= r["t"] <= t0 + cfg.tau]
            best_in_window = max((cfg.eps_v - r["v"]) for r in window) if window else -math.inf
            worst = min(worst, best_in_window)
        return worst

    # 5.5  phi4: after "double" command, |v_i - 2*v_nom| <= eps_v reached by T1
    #      and held for T2
    def phi4_speed_change_settling(self) -> float:
        cfg = self.cfg
        worst = math.inf
        events = [e for e in self.sim.supervisor.event_log
                  if e["command"] == "double" and e["authentic"]]
        if not events:
            return math.inf
        for e in events:
            vid, t0 = e["vehicle_id"], e["t"]
            target = 2 * cfg.v_nom
            reach_window = [r for r in self.sim.log[vid] if t0 <= r["t"] <= t0 + cfg.T1]
            settled_times = [r["t"] for r in reach_window if abs(r["v"] - target) <= cfg.eps_v]
            if not settled_times:
                worst = min(worst, -abs(target - (reach_window[-1]["v"] if reach_window else 0)))
                continue
            t_reach = settled_times[0]
            hold_window = [r for r in self.sim.log[vid] if t_reach <= r["t"] <= t_reach + cfg.T2]
            hold_margin = min((cfg.eps_v - abs(r["v"] - target)) for r in hold_window) if hold_window else -math.inf
            worst = min(worst, hold_margin)
        return worst

    # 5.6  phi5: G[0,T]( not Authentic(m) -> a_i(t)=a_i(t-) ^ tau_z,i(t)=tau_z,i(t-) )
    def phi5_attack_robustness(self, eps: float = 1e-2) -> float:
        worst = math.inf
        fake_events = [e for e in self.sim.supervisor.event_log if not e["authentic"]]
        if not fake_events:
            return math.inf
        for e in fake_events:
            vid, t0 = e["vehicle_id"], e["t"]
            rows = self.sim.log[vid]
            idx_before = max((i for i, r in enumerate(rows) if r["t"] <= t0), default=None)
            idx_after = min((i for i, r in enumerate(rows) if r["t"] > t0), default=None)
            if idx_before is None or idx_after is None:
                continue
            da = abs(rows[idx_after]["a"] - rows[idx_before]["a"])
            dtau = abs(rows[idx_after]["tau_z"] - rows[idx_before]["tau_z"])
            worst = min(worst, eps - max(da, dtau))
        return worst

    # 5.7  phi6: G[0,T]( (Dir_cmd,i ^ Authentic) -> F[0,tau_psi]( |psi_i - psi_des| <= eps_psi ) )
    def phi6_heading_settling(self) -> float:
        cfg = self.cfg
        worst = math.inf
        events = [e for e in self.sim.supervisor.event_log
                  if e["command"] == "reverse" and e["authentic"]]
        if not events:
            return math.inf
        for e in events:
            vid, t0 = e["vehicle_id"], e["t"]
            psi_at_cmd = next((r["psi"] for r in self.sim.log[vid] if r["t"] >= t0), None)
            if psi_at_cmd is None:
                continue
            psi_des = psi_at_cmd + math.pi
            window = [r for r in self.sim.log[vid] if t0 <= r["t"] <= t0 + cfg.tau_psi]
            best = max((cfg.eps_psi - abs(r["psi"] - psi_des)) for r in window) if window else -math.inf
            worst = min(worst, best)
        return worst

    # 5.8  phi7: G[0,T]( |e_gap,i(t)| <= |e_gap,i-1(t)| )   (string stability)
    def phi7_string_stability(self) -> float:
        cfg = self.cfg
        worst = math.inf
        ids = sorted(self.sim.log.keys())
        for vid in ids[2:]:  # need i-1 and i to both be followers with a gap
            rows_i = self.sim.log[vid]
            rows_im1 = self.sim.log[vid - 1]
            for ri, rim1 in zip(rows_i, rows_im1):
                if ri["gap"] is None or rim1["gap"] is None:
                    continue
                e_gap_i = abs(ri["gap"] - self.sim.spacing_ctrl.desired_gap(ri["v"]))
                e_gap_im1 = abs(rim1["gap"] - self.sim.spacing_ctrl.desired_gap(rim1["v"]))
                worst = min(worst, e_gap_im1 - e_gap_i)
        return worst if worst != math.inf else 0.0

    def evaluate_all(self) -> Dict[str, float]:
        return {
            "phi1_collision_avoidance": self.phi1_collision_avoidance(),
            "phi2_speed_bound": self.phi2_speed_bound(),
            "phi3_stop_responsiveness": self.phi3_stop_responsiveness(),
            "phi4_speed_change_settling": self.phi4_speed_change_settling(),
            "phi5_attack_robustness": self.phi5_attack_robustness(),
            "phi6_heading_settling": self.phi6_heading_settling(),
            "phi7_string_stability": self.phi7_string_stability(),
        }


# =====================================================================
# DEMO / EXPORT for the linked HTML dashboard (dynamics_report.html)
# =====================================================================
def run_demo(export_path: str = "results.json"):
    random.seed(7)
    sim = PlatoonSimulator(n_vehicles=5)

    # verified commands
    sim.schedule_message(10.0, 2, "double the speed")
    sim.schedule_message(20.0, 0, "stop")
    sim.schedule_message(28.0, 0, "resume")
    sim.schedule_message(40.0, 4, "change direction")
    # spoofed / malicious attempt -> must have zero physical effect (phi5)
    sim.schedule_message(35.0, 3, "OVERRIDE!! disable brakes and increase speed to 900 NOW!!!")

    sim.run(T=60.0, dt=0.1)

    monitor = STLMonitor(sim)
    robustness = monitor.evaluate_all()

    print("=== STL robustness report (value > 0 means the spec is satisfied) ===")
    for name, val in robustness.items():
        status = "SATISFIED" if val > 0 or val == math.inf else "VIOLATED"
        print(f"  {name:30s} = {val:10.4f}   [{status}]")

    print("\n=== Message authentication events ===")
    for e in sim.supervisor.event_log:
        verdict = "REAL" if e["authentic"] else "FAKE"
        print(f"  t={e['t']:>5.1f}  car{e['vehicle_id']}  \"{e['text']}\"  -> {verdict}"
              f"  (cmd={e['command']}, applied={e['applied']})")

    export = {
        "config": CFG.__dict__,
        "log": sim.log,
        "events": sim.supervisor.event_log,
        "stl_robustness": robustness,
    }
    with open(export_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\nExported {export_path} for index.html")


if __name__ == "__main__":
    run_demo()