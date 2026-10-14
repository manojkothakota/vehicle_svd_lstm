"""AUTOSAR *Classic* Platform concepts used on the MCUs (powertrain / sensor).

  Application SWCs (runnables)  -> PowertrainMCU.step / SensorMCU.on_plant
  RTE (sender-receiver ports)   -> typed signals on veh/<id>/can/*
  COM + E2E library             -> E2EProtect / E2ECheck below (alive counter + CRC8, like E2E Profile 1)
  WdgM (alive supervision)      -> WdgM below
  MCAL / CanIf                  -> bus.py (MQTT today, real CAN on hardware)
"""
import json
import config as C


def crc8(data: bytes, poly=0x1D, init=0xFF):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ 0xFF


def _crc(body):
    return crc8(json.dumps(body, sort_keys=True).encode())


class E2EProtect:
    def __init__(self):
        self.counter = 0

    def protect(self, signal: dict) -> dict:
        self.counter = (self.counter + 1) & 0x0F
        body = {**signal, "ctr": self.counter}
        return {**body, "crc": _crc(body)}


class E2ECheck:
    MAX_DELTA = 3

    def __init__(self):
        self.last, self.errors = None, 0

    def check(self, msg: dict):
        body = {k: v for k, v in msg.items() if k != "crc"}
        if msg.get("crc") != _crc(body):
            self.errors += 1
            return False, "E2E CRC error"
        ctr = msg.get("ctr", -1)
        if self.last is not None:
            if ctr == self.last:
                self.errors += 1
                return False, "E2E repeated counter"
            delta = (ctr - self.last) & 0x0F
            self.last = ctr
            if delta > self.MAX_DELTA:
                self.errors += 1
                return False, "E2E wrong sequence"
        self.last = ctr
        return True, "ok"


class WdgM:
    """Alive supervision: the supervised entity must report a checkpoint within `timeout` sim-seconds."""

    def __init__(self, timeout=1.0):
        self.timeout, self.t = timeout, C.sim_now()

    def checkpoint(self):
        self.t = C.sim_now()

    def failed(self):
        return C.sim_now() - self.t > self.timeout
    