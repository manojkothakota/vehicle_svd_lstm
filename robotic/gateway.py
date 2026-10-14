"""Gateway ECU - the only node allowed to talk outside the vehicle.
  * V2V TX: signed BSM broadcast + signed text commands (car -> car / car -> all)
  * V2V RX: firewall (membership + schema + HMAC + replay counter) before the HPC sees anything
  * V2Cloud: telemetry/alerts/message-verdict uplink; verifies signed cloud commands (+ replay check)"""
import config as C
from bus import Node, signed, verify
from config import T


class GatewayECU(Node):
    def __init__(self, car_id):
        super().__init__(f"car{car_id}.gateway")
        self.id = car_id
        self.status, self.tick, self.drops, self.drop_t, self.last_seq = {}, 0, 0, -99.0, 0
        self.tx_n, self.rx_n = 0, {}
        self.sub(T.sensor(car_id), self.on_sensor)
        self.sub(T.status(car_id), lambda t, m: self.status.update(m))
        self.sub(T.alert(car_id), lambda t, m: self.pub(T.up_alert(self.id), m))
        self.sub(T.msglog(car_id), lambda t, m: self.pub(T.up_msg(self.id), m))
        self.sub("v2v/+/bsm", self.on_bsm)
        self.sub("v2v/+/cmd", self.on_cmd)
        self.sub(T.down("all"), self.on_cloud)
        self.sub(T.down(car_id), self.on_cloud)

    # ---- outbound ----
    def on_sensor(self, topic, m):
        bsm = {"id": self.id, "s": m["s"], "v": m["v"], "a": m["a"], "d": m.get("dir", 1), "t": m["t"]}
        self.pub(T.bsm(self.id), signed(bsm, C.V2V_KEY) if C.SECURE_V2V else bsm)
        self.tick += 1
        if self.tick % 2 == 0:
            self.pub(T.up_tel(self.id), {**m, **self.status, "drops": self.drops})

    def transmit_cmd(self, to, text):
        self.tx_n += 1
        frame = {"from": self.id, "to": to, "text": text, "n": self.tx_n, "t": C.sim_now()}
        self.pub(T.v2v_cmd(self.id), signed(frame, C.V2V_KEY) if C.SECURE_V2V else frame)

    # ---- inbound V2V BSM (firewall) ----
    def on_bsm(self, topic, m):
        if self.id == 0 or m.get("id") != self.id - 1:
            return
        if not all(k in m for k in ("s", "v", "t")):
            return self.drop("malformed V2V frame")
        if C.SECURE_V2V and not verify(m, C.V2V_KEY):
            return self.drop("invalid V2V signature")
        m.pop("sig", None)
        self.pub(T.v2v_in(self.id), m)

    # ---- inbound V2V text command (firewall) ----
    def on_cmd(self, topic, m):
        if m.get("from") == self.id or m.get("to") not in ("ALL", self.id):
            return
        if not all(k in m for k in ("from", "text", "n")):
            return self.drop("malformed V2V command")
        if C.SECURE_V2V and not verify(m, C.V2V_KEY):
            return self.drop("invalid V2V signature (command)")
        if m["n"] <= self.rx_n.get(m["from"], 0):
            return self.drop("replayed V2V command")
        self.rx_n[m["from"]] = m["n"]
        who = "Leader" if m["from"] == 0 else f"Car {m['from']}"
        self.pub(T.v2v_cmd_in(self.id), {"from": who, "text": m["text"]})

    def drop(self, reason):
        self.drops += 1
        now = C.sim_now()
        if now - self.drop_t > 1.0:
            self.drop_t = now
            self.pub(T.up_alert(self.id), {"car": self.id, "src": "gateway-firewall", "reason": reason, "t": now})
            self.log("DROPPED V2V frame:", reason)

    # ---- inbound cloud command ----
    def on_cloud(self, topic, m):
        if not verify(m, C.CLOUD_KEY) or m.get("seq", 0) <= self.last_seq:
            return self.log("rejected cloud command (bad signature / replay)")
        self.last_seq = m["seq"]
        if "tx" in m:                                            # cloud-relayed "driver HMI" request: send car->car/all
            return self.transmit_cmd(m["tx"].get("to", "ALL"), m["tx"].get("text", ""))
        self.pub(T.ota(self.id), {k: v for k, v in m.items() if k not in ("sig", "seq")})

    def start(self):
        pass