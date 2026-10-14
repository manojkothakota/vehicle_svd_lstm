"""AUTOSAR *Adaptive* Platform concepts used on the HPC (service-oriented, dynamic).

  Execution Management  -> MANIFEST (app, version, required services)
  ara::com              -> MessageAuthProxy: service discovery (retained 'offer') + request/response method call,
                           transported over MQTT here (SOME/IP on real hardware)
  UCM (Update & Config) -> UpdateManager: validates + activates OTA packages (range checks, reject -> alert)
  Fail-safe             -> if the AI service is unavailable/slow, the proxy falls back to the local rule engine
"""
import json
import threading
import time
import uuid
import config as C
from lstm_detector import rule_classify

MANIFEST = {"app": "platoon_adas", "version": "1.0", "requires": ["MessageAuthenticity"],
            "provides": ["PlatoonControl"], "asil": "B"}

SERVICE = "MessageAuthenticity"


class MessageAuthProxy:
    def __init__(self, node, car_id):
        self.node, self.id = node, car_id
        self.available, self.pending, self.mu = False, {}, threading.Lock()
        node.sub(f"sd/{SERVICE}/offer", lambda t, m: setattr(self, "available", True))
        node.sub(f"svc/msgauth/res/{car_id}", self.on_res)

    def classify(self, text, callback):
        if not self.available:
            return callback({**rule_classify(text), "source": "rules(local-fallback)"})
        rid = uuid.uuid4().hex[:8]
        with self.mu:
            self.pending[rid] = (time.time(), text, callback)
        self.node.pub("svc/msgauth/req", {"rid": rid, "car": self.id, "text": text})

    def on_res(self, topic, m):
        with self.mu:
            item = self.pending.pop(m.get("rid"), None)
        if item:
            item[2](m)

    def poll(self):
        now, expired = time.time(), []
        with self.mu:
            for rid, (t0, text, cb) in list(self.pending.items()):
                if now - t0 > C.AI_TIMEOUT:
                    expired.append((text, cb))
                    del self.pending[rid]
        for text, cb in expired:
            cb({**rule_classify(text), "source": "rules(AI service timeout)"})


class UpdateManager:
    def __init__(self, hpc):
        self.hpc, self.state = hpc, "Idle"

    def process(self, pkg):
        errs = []
        if "target_speed" in pkg and not (0 <= pkg["target_speed"] <= C.V_MAX):
            errs.append("target_speed out of range")
        if "detector_tol" in pkg and not (0.5 <= pkg["detector_tol"] <= 50):
            errs.append("detector_tol out of range")
        if errs:
            self.state = "Rejected"
            self.hpc.alert("UCM rejected update: " + ", ".join(errs), "adaptive-ucm")
            return False
        self.hpc.det.tol = pkg.get("detector_tol", self.hpc.det.tol)
        self.hpc.target = pkg.get("target_speed", self.hpc.target)
        self.hpc.version = pkg.get("version", self.hpc.version)
        self.state = "Activated"
        self.hpc.log("UCM activated update:", pkg)
        return True