"""netmon.py - passive network analyser (like a Wireshark for the SDV network).
Subscribes to every MQTT topic, classifies each frame by network layer / link, and keeps
rates, byte counts and a log of notable frames with their security protection."""
import collections
import json
import threading
import time
from bus import Node

SIG = {"plant": ("Powertrain MCU", "Sensor MCU"), "sensor": ("Sensor MCU", "HPC + Gateway"),
       "cmd": ("HPC", "Powertrain MCU"), "hpc_status": ("HPC", "Gateway"), "alert": ("HPC", "Gateway"),
       "msglog": ("HPC", "Gateway"), "v2v_in": ("Gateway", "HPC"), "v2v_cmd": ("Gateway", "HPC"),
       "ota": ("Gateway", "HPC")}
L_CAN, L_V2V, L_UP, L_DOWN, L_SVC = ("In-vehicle bus (CAN)", "V2V wireless", "V2Cloud uplink",
                                      "V2Cloud downlink", "Adaptive services (SOME/IP-like)")
SECURITY = {L_CAN: "none (trusted zone); cmd frames: E2E CRC8 + alive counter", L_V2V: "HMAC-SHA256 + replay counter + gateway firewall",
            L_UP: "gateway-originated telemetry", L_DOWN: "HMAC (OEM key) + sequence anti-replay",
            L_SVC: "service discovery + timeout fallback"}
NOTABLE_CAN = {"v2v_cmd", "ota", "alert", "msglog"}


def classify(topic):
    p = topic.split("/")
    try:
        if p[0] == "veh":
            fr, to = SIG.get(p[3], ("?", "?"))
            return L_CAN, f"{fr} -> {to}", p[3], int(p[1])
        if p[0] == "v2v":
            return L_V2V, f"Car {p[1]} -> platoon", p[2], int(p[1])
        if p[0] == "cloud" and p[1] == "up":
            return L_UP, "Gateway -> Cloud", p[3], int(p[2])
        if p[0] == "cloud" and p[1] == "down":
            return L_DOWN, "Cloud -> Gateway", "command", None if p[2] == "all" else int(p[2])
        if p[0] in ("svc", "sd"):
            return L_SVC, "HPC <-> AI service", "/".join(p[1:]), None
    except Exception:
        pass
    return "other", topic, "", None


class NetMonitor(Node):
    def __init__(self):
        super().__init__("netmon")
        self.mu = threading.Lock()
        self.reset()
        self.client.message_callback_add("#", self.raw)
        self.client.subscribe("#")

    def reset(self):
        with getattr(self, "mu", threading.Lock()):
            self.stats, self.log, self.sample = {}, collections.deque(maxlen=200), collections.Counter()

    def raw(self, client, ud, msg):
        now, n = time.time(), len(msg.payload)
        layer, link, sig, car = classify(msg.topic)
        with self.mu:
            s = self.stats.setdefault((layer, link, sig, car), {"count": 0, "bytes": 0, "win": collections.deque()})
            s["count"] += 1
            s["bytes"] += n
            s["win"].append(now)
            while s["win"] and now - s["win"][0] > 2.0:
                s["win"].popleft()
            notable = (layer == L_CAN and sig in NOTABLE_CAN) or layer in (L_DOWN, L_SVC) or (layer == L_UP and sig in ("alert", "msg"))
            if not notable:                                       # sample the high-rate streams
                self.sample[(layer, sig, car)] += 1
                if self.sample[(layer, sig, car)] % (25 if sig == "bsm" else 150) != 1:
                    return
        text = msg.payload.decode("utf-8", "ignore")
        try:
            j = json.loads(text)
        except Exception:
            j = {}
        if '"sig"' in text:
            prot = "HMAC signed"
        elif '"crc"' in text:
            prot = "E2E CRC"
        else:
            prot = "unsigned" if layer in (L_V2V, L_DOWN) else "-"
        summ = j.get("text") or (j.get("reason") if sig == "alert" else None) or (("verdict " + ("REAL" if j.get("isReal") else "FAKE") + f" {j.get('confidence')}% ({j.get('source')})") if "isReal" in j else "")
        if not summ and "cmd" in j:
            summ = "admin cmd: " + str(j["cmd"].get("text"))
        if not summ and "tx" in j:
            summ = "tx request: " + str(j["tx"].get("text"))
        if not summ and layer == L_DOWN:
            summ = "update: " + ",".join(k for k in j if k not in ("sig", "seq"))
        if not summ and layer == L_V2V:
            summ = f"s={j.get('s', 0):.0f}m v={j.get('v', 0):.1f}" if "s" in j else ""
        with self.mu:
            self.log.appendleft({"t": now, "layer": layer, "link": link, "sig": sig, "car": car, "topic": msg.topic,
                                 "bytes": n, "protection": prot, "summary": str(summ)[:70]})

    def report(self, car=None):
        now = time.time()
        with self.mu:
            rows = {}
            for (layer, link, sig, c), s in self.stats.items():
                if car is not None and c != car and not (c is None and layer == L_SVC):
                    continue
                while s["win"] and now - s["win"][0] > 2.0:
                    s["win"].popleft()
                r = rows.setdefault((layer, link, sig), {"layer": layer, "link": link, "sig": sig, "rate": 0.0,
                                                          "count": 0, "bytes": 0, "security": SECURITY.get(layer, "")})
                r["rate"] += len(s["win"]) / 2.0
                r["count"] += s["count"]
                r["bytes"] += s["bytes"]
            log = [e for e in self.log if car is None or e["car"] in (car, None)][:80]
        return {"rows": sorted(rows.values(), key=lambda r: (r["layer"], r["link"])), "log": log}