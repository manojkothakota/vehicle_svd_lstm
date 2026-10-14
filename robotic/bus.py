"""bus.py - communication layer shared by every ECU and the cloud.
To move to real hardware later, only this file changes (MQTT -> CAN / SOME/IP)."""
import json
import hmac
import hashlib
import threading
import time
import paho.mqtt.client as mqtt
import config as C


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def sign(body, key):
    body = {k: v for k, v in body.items() if k != "sig"}
    return hmac.new(key, json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()[:20]


def signed(body, key):
    return {**body, "sig": sign(body, key)}


def verify(msg, key):
    return hmac.compare_digest(str(msg.get("sig", "")), sign(msg, key))


class Node:
    """Base class of every ECU / cloud service: one MQTT client + periodic loop."""

    def __init__(self, name):
        self.name = name
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=name)
        if C.CAFILE:
            self.client.tls_set(ca_certs=C.CAFILE)
        self.client.connect(C.BROKER_HOST, C.BROKER_PORT)
        self.client.loop_start()

    def sub(self, topic, handler):
        def cb(client, userdata, msg):
            try:
                handler(msg.topic, json.loads(msg.payload))
            except Exception as e:                      # never crash an ECU on bad input
                print(f"[{self.name}] bad message on {msg.topic}: {e}", flush=True)
        self.client.message_callback_add(topic, cb)
        self.client.subscribe(topic)

    def pub(self, topic, payload):
        self.client.publish(topic, json.dumps(payload))

    def every(self, period_sim, fn):
        """Run fn every period_sim simulated seconds in a background thread."""
        def run():
            nxt = time.time()
            while True:
                fn()
                nxt += period_sim / C.TIME_SCALE
                time.sleep(max(0.0, nxt - time.time()))
        threading.Thread(target=run, daemon=True).start()

    def log(self, *a):
        print(f"[{self.name}]", *a, flush=True)