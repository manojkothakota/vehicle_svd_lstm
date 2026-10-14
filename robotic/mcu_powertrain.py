"""MCU #1 - Powertrain/brake controller (AUTOSAR Classic style).
Checks E2E protection (CRC + alive counter) on every HPC command, supervises the HPC with WdgM
(brakes if it goes silent) and integrates the vehicle motion (the 'plant') along the road path.
Supports driving direction (+1 forward / -1 reverse) for the 'change direction' command."""
import config as C
from autosar_classic import E2ECheck, WdgM
from bus import Node, clip
from config import T


class PowertrainMCU(Node):
    def __init__(self, car_id, path):
        super().__init__(f"car{car_id}.mcu_powertrain")
        self.id, self.L = car_id, path.length
        self.s = (-car_id * C.SPACING) % self.L
        self.v = self.a = self.dist = 0.0
        self.cmd_a, self.dir = 0.0, 1
        self.e2e, self.wdg, self.err_t = E2ECheck(), WdgM(1.0), -99.0
        self.sub(T.cmd(car_id), self.on_cmd)

    def on_cmd(self, topic, m):
        ok, why = self.e2e.check(m)
        if not ok:
            if C.sim_now() - self.err_t > 2.0:
                self.err_t = C.sim_now()
                self.log("command frame rejected:", why)
            return
        self.cmd_a, self.dir = m["a"], (-1 if m.get("dir", 1) < 0 else 1)
        self.wdg.checkpoint()

    def step(self):
        a = -1.0 if self.wdg.failed() else self.cmd_a            # WdgM fail-safe
        self.a = clip(a, C.A_MIN, C.A_MAX)
        self.v = clip(self.v + self.a * C.DT, 0.0, C.V_MAX)
        self.s = (self.s + self.dir * self.v * C.DT) % self.L
        self.dist += self.v * C.DT
        self.pub(T.plant(self.id), {"s": self.s, "v": self.v, "a": self.a, "dist": self.dist,
                                     "dir": self.dir, "e2e_err": self.e2e.errors, "wdg_ok": not self.wdg.failed(), "t": C.sim_now()})

    def start(self):
        self.every(C.DT, self.step)