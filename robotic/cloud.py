"""cloud.py - OEM cloud + backend for the browser UI.
  * launches the fleet (one process per car) when the browser posts its route
  * relays admin / car->car messages as signed commands, collects telemetry, alerts and AI verdicts
  * runs the STL monitor and serves everything on http://localhost:8000 (also serves index.html + js files)"""
import collections
import json
import math
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import pandas as pd
import config as C
import attacker
from bus import Node, signed
from config import T
from path_model import ClosedPath
from stl_monitor import STLMonitor
from netmon import NetMonitor

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
          ".json": "application/json"}


def clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


class Cloud(Node):
    def __init__(self):
        super().__init__("cloud")
        self.lock, self.fleet_lock = threading.RLock(), threading.Lock()
        self.cars, self.alerts, self.msgs = {}, collections.deque(maxlen=60), collections.deque(maxlen=300)
        self.msg_n, self.seq, self.procs, self.path = 0, int(time.time() * 1000), [], None
        self.mon = STLMonitor()
        self.sub("cloud/up/+/telemetry", self.on_tel)
        self.sub("cloud/up/+/alert", self.on_alert)
        self.sub("cloud/up/+/msg", self.on_msg)
        threading.Thread(target=self.sampler, daemon=True).start()

    # ---- uplink ----
    def on_tel(self, topic, m):
        with self.lock:
            self.cars[int(topic.split("/")[2])] = m

    def on_alert(self, topic, m):
        with self.lock:
            self.alerts.appendleft(m)

    def on_msg(self, topic, m):
        with self.lock:
            self.msg_n += 1
            e = {**m, "n": self.msg_n, "toId": int(topic.split("/")[2])}
            self.msgs.append(e)
        self.mon.on_msg(e)

    def sampler(self):
        while True:
            with self.lock:
                cars = list(self.cars.values())
            self.mon.update(cars)
            time.sleep(0.2)

    # ---- downlink ----
    def send_cmd(self, body, car=None):
        with self.lock:
            self.seq = max(self.seq + 1, int(time.time() * 1000))
            msg = signed({**body, "seq": self.seq}, C.CLOUD_KEY)
        self.pub(T.down("all" if car is None else car), msg)

    # ---- fleet management ----
    def stop_fleet(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(3)
            except Exception:
                p.kill()
        self.procs = []
        with self.lock:
            self.cars.clear(); self.alerts.clear(); self.msgs.clear()
        self.mon.reset(None)

    def start_fleet(self, xs, ys, n, speed, spacing):
        with self.fleet_lock:
            self.stop_fleet()
            os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
            csv = os.path.join(HERE, "data", "route_live.csv")
            pd.DataFrame({"x_m": xs, "y_m": ys}).to_csv(csv, index=False)
            C.WAYPOINTS_CSV = csv
            self.path = ClosedPath(csv)
            self.mon.reset(self.path.length)
            net.reset()
            env = {**os.environ, "SDV_EPOCH": str(time.time()), "SDV_NCARS": str(n), "SDV_SPEED": str(speed),
                   "SDV_SPACING": str(spacing), "SDV_WAYPOINTS": csv}
            for i in range(n):
                self.procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "vehicle.py"), str(i)],
                                                   env=env, cwd=HERE))
            print(f"[cloud] fleet started: {n} cars, route {self.path.length:.0f} m", flush=True)

    def snapshot(self, since=0):
        with self.lock:
            cars = [self.cars[k] for k in sorted(self.cars)]
            return clean({"running": bool(self.procs), "sim_t": max([c.get("t", 0) for c in cars], default=0),
                          "cars": cars, "alerts": list(self.alerts),
                          "msgs": [m for m in self.msgs if m["n"] > since],
                          "stl": self.mon.report(), "ai_mode": C.AI_MODE, "secure": C.SECURE_V2V})


cloud = Cloud()
net = NetMonitor()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/state":
            return self._send(200, cloud.snapshot(int(parse_qs(u.query).get("since", ["0"])[0])))
        if u.path == "/api/path":
            p = cloud.path
            return self._send(200, {"x": p.x.tolist() if p else [], "y": p.y.tolist() if p else [],
                                    "n_cars": C.N_CARS, "secure": C.SECURE_V2V})
        if u.path == "/api/net":
            q = parse_qs(u.query).get("car")
            return self._send(200, net.report(int(q[0]) if q and q[0] != "all" else None))
        name = "index.html" if u.path == "/" and os.path.exists(os.path.join(HERE, "index.html")) else \
               ("dashboard.html" if u.path == "/" else os.path.basename(u.path))
        fp = os.path.join(HERE, name)
        ext = os.path.splitext(name)[1]
        if ext in STATIC and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return self._send(200, f.read(), STATIC[ext])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path).path
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if u == "/api/route":
            threading.Thread(target=cloud.start_fleet, daemon=True, args=(
                req["x"], req["y"], int(req.get("n_cars", 10)), float(req.get("speed", 12)),
                float(req.get("spacing", 20)))).start()
            self._send(200, {"ok": True})
        elif u == "/api/stop":
            threading.Thread(target=lambda: (cloud.fleet_lock.acquire(), cloud.stop_fleet(), cloud.fleet_lock.release()),
                             daemon=True).start()
            self._send(200, {"ok": True})
        elif u == "/api/message":
            frm, to, text = req["from"], req["to"], str(req.get("text", ""))
            to = "ALL" if str(to).upper() == "ALL" else int(to)
            if frm == "ADMIN":
                cloud.send_cmd({"cmd": {"from": "Admin", "text": text}}, None if to == "ALL" else to)
            else:
                cloud.send_cmd({"tx": {"to": to, "text": text}}, int(frm))
            self._send(200, {"ok": True})
        elif u == "/api/ota":
            body = {}
            for k, f in (("target_speed", float), ("detector_tol", float), ("version", str)):
                if req.get(k) not in (None, ""):
                    body[k] = f(req[k])
            car = req.get("car")
            cloud.send_cmd(body, None if car in (None, "", "all") else int(car))
            self._send(200, {"ok": True, "sent": body})
        elif u == "/api/net_reset":
            net.reset()
            self._send(200, {"ok": True})
        elif u == "/api/attack":
            args = (int(req.get("victim", 3)), req.get("mode", "teleport"), bool(req.get("signed", False)),
                    float(req.get("duration", 10)))
            threading.Thread(target=attacker.run, args=args, daemon=True).start()
            self._send(200, {"ok": True, "attack": args})
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[cloud] backend + UI on http://localhost:{C.HTTP_PORT}  (open /index.html)", flush=True)
    try:
        ThreadingHTTPServer(("0.0.0.0", C.HTTP_PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        cloud.stop_fleet()