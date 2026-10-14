"""lstm_detector.py - V2V message-authenticity classifier (REAL / FAKE).
 * rule_classify(): the same rules as messaging.js (works everywhere, no TensorFlow needed)
 * LSTMClassifier: loads YOUR trained model + tokenizer; ensemble = FAKE if LSTM or rules flag it.
Falls back to rules automatically if TensorFlow/Keras or the files are unavailable."""
import math
import re
import config as C

SAFE_COMMANDS = [
    ("double", ["double the speed", "double speed", "double it"]),
    ("half", ["half the speed", "halve the speed", "half speed", "reduce speed by half"]),
    ("reset", ["normal speed", "reset speed", "default speed"]),
    ("increase", ["increase speed", "speed up", "accelerate", "go faster", "faster"]),
    ("decrease", ["decrease speed", "slow down", "decelerate", "go slower", "slower"]),
    ("stop", ["emergency stop", "stop", "halt", "brake"]),
    ("resume", ["resume", "continue", "start moving", "carry on"]),
    ("reverse", ["change direction", "reverse direction", "turn around", "go back", "reverse"]),
]
SUSPICIOUS = ["override", "bypass", "disable", "ignore safety", "hack", "inject", "malware", "virus",
              "unauthorized", "root access", "admin access", "shutdown", "delete log", "kill switch",
              "force crash", "disable brakes", "spoof", "jam signal", "no limit", "unlimited speed",
              "crash", "collide"]


def parse_command(text):
    t = (text or "").lower()
    for cmd, pats in SAFE_COMMANDS:
        if any(p in t for p in pats):
            return cmd
    return None


def rule_classify(text):
    text = text or ""
    t = text.lower()
    reasons, susp = [], 0
    for k in SUSPICIOUS:
        if k in t:
            susp += 3
            reasons.append(f'suspicious term "{k}"')
    stop = re.search(r"\bstop\b|\bhalt\b", t)
    inc = re.search(r"increase|faster|speed up|accelerate", t)
    dec = re.search(r"decrease|slower|slow down|decelerate", t)
    if stop and (inc or dec):
        susp += 2
        reasons.append("contradictory instructions (stop + speed change)")
    if inc and dec:
        susp += 2
        reasons.append("conflicting speed instructions")
    m = re.search(r"(\d{2,})", t)
    if m and int(m.group(1)) > 60:
        susp += 2
        reasons.append(f"unrealistic value requested ({m.group(1)})")
    if text.count("!") >= 2:
        susp += 1
        reasons.append("excessive exclamation marks")
    if re.search(r"\b[A-Z]{4,}\b", text):
        susp += 1
        reasons.append("unusual all-caps wording")
    cmd = parse_command(t)
    if not cmd:
        susp += 1
        reasons.append("no recognized platoon command found")
    real = susp < 3
    if real and not reasons:
        reasons.append("matches known command, no anomalies detected")
    p_real = 1 / (1 + math.exp(-0.9 * (3 - susp)))
    return {"isReal": real, "command": cmd, "confidence": round((p_real if real else 1 - p_real) * 100),
            "reasons": reasons, "source": "rules"}


class LSTMClassifier:
    def __init__(self):
        self.ok = False
        try:
            import pickle
            import numpy as np
            import keras
            from keras.utils import pad_sequences
            self.model = keras.models.load_model(C.LSTM_MODEL)
            with open(C.LSTM_TOKENIZER, "rb") as f:
                self.tok = pickle.load(f)
            self.maxlen = int(self.model.input_shape[1] or 50)
            self._pad, self._np, self.ok = pad_sequences, np, True
            print(f"[lstm] loaded {C.LSTM_MODEL} (maxlen={self.maxlen}, fake_index={C.LSTM_FAKE_INDEX})", flush=True)
        except Exception as e:
            print(f"[lstm] model not loaded ({type(e).__name__}: {e}) -> rule-based classifier only", flush=True)

    def p_fake(self, text):
        x = self._pad(self.tok.texts_to_sequences([text]), maxlen=self.maxlen)
        p = self._np.asarray(self.model.predict(x, verbose=0))[0]
        if p.size == 1:
            pf = float(p[0])
            return pf if C.LSTM_FAKE_INDEX == 1 else 1 - pf
        return float(p[C.LSTM_FAKE_INDEX])

    def classify(self, text):
        r = rule_classify(text)
        if not self.ok:
            return r
        try:
            pf = self.p_fake(text)
        except Exception as e:
            print(f"[lstm] inference failed: {e}", flush=True)
            return r
        real = pf < 0.5 and r["isReal"]
        conf = round((min(1 - pf, r["confidence"] / 100) if real else max(pf, 1 - r["confidence"] / 100 if not r["isReal"] else 0)) * 100)
        return {"isReal": real, "command": r["command"], "confidence": conf,
                "reasons": r["reasons"] + [f"LSTM p(fake)={pf:.2f}"], "source": "lstm+rules"}