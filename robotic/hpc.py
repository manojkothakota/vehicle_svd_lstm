"""High-Performance Compute (HPC / SoC) - AUTOSAR *Adaptive* platform application.
 * ADAS: platoon car-following + V2V plausibility detector + safety shield
 * Text commands (admin / car-to-car): authenticity verdict from the AI service (LSTM) over ara::com-like RPC.
   Only REAL commands change the control state.  A shadow controller (REAL-only reference) runs in parallel;
   a_dev = |a_live - a_shadow| feeds STL property 5 ("FAKE message => acceleration unaffected").
 * UCM validates OTA packages."""
import threading
import config as C
from autosar_adaptive import MANIFEST, MessageAuthProxy, UpdateManager
from autosar_classic import E2EProtect
from bus import Node, clip
from config import T
from lstm_detector import rule_classify

NAN = float("nan")


class ControlState:
    def __init__(self):
        self.mult, self.stopped, self.dir = 1.0, False, 1

    def apply(self, cmd):
        if cmd == "double":
            self.stopped, self.mult = False, min(4.0, self.mult * 2)
        elif cmd == "half":
            self.mult = max(0.15, self.mult * 0.5)
        elif cmd == "reset":
            self.mult, self.stopped = 1.0, False
        elif cmd == "increase":
            self.stopped, self.mult = False, min(4.0, self.mult + 0.4)
        elif cmd == "decrease":
            self.mult = max(0.15, self.mult - 0.4)
        elif cmd == "stop":
            self.stopped = True
        elif cmd == "resume":
            self.stopped = False
        elif cmd == "reverse":
            self.dir = -self.dir


class Detector:
    """Rule-based V2V plausibility check (replace check() with your LSTM later)."""

    def __init__(self, L):
        self.L, self.tol, self.a_lim = L, C.DETECTOR_TOL, C.DETECTOR_A_LIM
        self.last = None

    def check(self, m):
        last = self.last
        if last is None:
            self.last = m
            return True, "init"
        dt = m["t"] - last["t"]
        if dt <= 0:
            return False, "stale/replayed message"
        if abs(m["v"] - last["v"]) / dt > self.a_lim:
            return False, "impossible acceleration"
        d = last.get("d", 1)
        err = ((m["s"] - (last["s"] + d * last["v"] * dt) + self.L / 2) % self.L) - self.L / 2
        if abs(err) > self.tol + 0.5 * dt + 2 * last["v"] * dt * (1 if m.get("d", 1) != d else 0):
            return False, f"position jump {err:.1f} m"
        self.last = m
        return True, "ok"

    def estimate(self, now):
        last = self.last
        if last is None:
            return None
        d = last.get("d", 1)
        return {"s": (last["s"] + d * last["v"] * (now - last["t"])) % self.L, "v": d * last["v"]}


class HPC(Node):
    def __init__(self, car_id, path):
        super().__init__(f"car{car_id}.hpc")
        self.id, self.L, self.leader = car_id, path.length, car_id == 0
        self.det = Detector(self.L)
        self.me, self.target, self.version = None, C.TARGET_SPEED, MANIFEST["version"]
        self.live, self.shadow, self.lock = ControlState(), ControlState(), threading.RLock()
        self.rejected, self.reason, self.reject_t, self.alert_t = 0, "ok", -99.0, -99.0
        self.gap, self.a_dev, self.fake_t = NAN, 0.0, -99.0
        self.e2e, self.ucm = E2EProtect(), UpdateManager(self)
        self.auth = MessageAuthProxy(self, car_id)
        self.sub(T.sensor(car_id), lambda t, m: setattr(self, "me", m))
        self.sub(T.v2v_in(car_id), self.on_v2v)
        self.sub(T.v2v_cmd_in(car_id), lambda t, m: self.on_command(m, "v2v"))
        self.sub(T.ota(car_id), self.on_ota)

    # ---------- helpers ----------
    def alert(self, reason, src="hpc"):
        self.pub(T.alert(self.id), {"car": self.id, "src": src, "reason": reason, "t": C.sim_now()})

    # ---------- V2V BSM plausibility ----------
    def on_v2v(self, topic, m):
        ok, why = self.det.check(m)
        if ok:
            return
        now = C.sim_now()
        self.rejected += 1
        self.reason, self.reject_t = why, now
        if now - self.alert_t > 1.0:
            self.alert_t = now
            self.alert(why, "hpc-detector")
            self.log("REJECTED V2V message:", why)

    # ---------- OTA / admin commands ----------
    def on_ota(self, topic, m):
        if "cmd" in m:
            return self.on_command(m["cmd"], "cloud")
        self.ucm.process(m)

    # ---------- text command pipeline ----------
    def on_command(self, c, via):
        text, frm = c.get("text", ""), c.get("from", "?")

        def done(res):
            with self.lock:
                now, cmd, real = C.sim_now(), res.get("command"), res["isReal"]
                reasons = list(res.get("reasons", []))
                if C.AI_MODE == "on":
                    apply_live = bool(real and cmd)
                else:                                   # naive HPC (fault injection): no authenticity check
                    apply_live = bool(cmd)
                    reasons.append("AI verification OFF: command applied without authenticity check")
                    res = {**res, "source": "none"}
                if apply_live:
                    self.live.apply(cmd)
                if real and cmd:
                    self.shadow.apply(cmd)              # REAL-only reference state
                if not real:
                    self.fake_t = now                   # FAKE_i(t) becomes true
                reasons.append("command applied by HPC" if apply_live else "blocked - control state unchanged")
                self.pub(T.msglog(self.id), {"car": self.id, "from": frm, "text": text, "via": via,
                                              "isReal": real, "command": cmd, "confidence": res["confidence"],
                                              "reasons": reasons, "source": res.get("source"),
                                              "applied": apply_live, "t": now})
        if C.AI_MODE == "on":
            self.auth.classify(text, done)
        else:
            done({**rule_classify(text)})

    # ---------- ADAS control law ----------
    def control(self, st, now, v, s):
        """Pure function of (control state, sensors, V2V estimate) -> (accel, mode, gap)."""
        desired = max(0.0, self.target * st.mult)
        if st.stopped:
            return C.A_MIN, "STOPPED", NAN
        if st.dir < 0:
            return clip(1.1 * (desired - v), C.A_MIN, C.A_MAX), "REVERSED", NAN
        if self.leader:
            a = min(1.0, 0.8 * (desired - v))
            return clip(a, C.A_MIN, C.A_MAX), ("CRUISE" if abs(st.mult - 1) < 1e-6 else "COMMANDED"), NAN
        ahead = self.det.estimate(now)
        if ahead is None:
            return 0.0, "WAIT", NAN
        gap = (ahead["s"] - s) % self.L
        if gap > self.L / 2:
            gap -= self.L
        if abs(st.mult - 1) > 1e-6:
            a, mode = 1.1 * (desired - v), "COMMANDED"
        else:
            a = C.K_P * (gap - (C.STANDSTILL + C.TIME_GAP * v)) + C.K_V * (ahead["v"] - v)
            mode = "FOLLOW"
        # safety shield (AEB-like): brake if the braking distance for the closing speed would eat the margin
        closing = max(0.0, v * v - max(0.0, ahead["v"]) ** 2) / (2 * abs(C.A_MIN))
        if gap < C.D_MIN + 2.0 + 1.3 * closing:
            a = C.A_MIN
        return clip(a, C.A_MIN, C.A_MAX), mode, gap

    def step(self):
        if self.me is None:
            return
        self.auth.poll()
        with self.lock:
            now, v, s = C.sim_now(), self.me["v"], self.me["s"]
            a, mode, gap = self.control(self.live, now, v, s)
            a_sh, _, _ = self.control(self.shadow, now, v, s)
            self.gap, self.a_dev = gap, abs(a - a_sh)
            if mode == "FOLLOW" and now - self.reject_t < 2.0:
                mode = "DEGRADED"
            frame = self.e2e.protect({"a": a, "dir": self.live.dir, "t": now})
            status = {"mode": mode, "gap": gap, "target": self.target, "tol": self.det.tol,
                      "version": self.version, "rejected": self.rejected, "reason": self.reason,
                      "mult": self.live.mult, "stopped": self.live.stopped, "dir_cmd": self.live.dir,
                      "a_dev": self.a_dev, "fake_t": self.fake_t,
                      "svc_ok": self.auth.available, "ucm": self.ucm.state}
        self.pub(T.cmd(self.id), frame)
        self.pub(T.status(self.id), status)

    def start(self):
        self.log("Adaptive manifest:", MANIFEST)
        self.every(C.DT, self.step)