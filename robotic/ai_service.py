"""AI service process = provider of the Adaptive-AUTOSAR service 'MessageAuthenticity'.
Loads the LSTM once and answers classify requests from every vehicle's HPC."""
import json
import time
from bus import Node
from lstm_detector import LSTMClassifier


class AIService(Node):
    def __init__(self):
        super().__init__("ai_service")
        self.clf = LSTMClassifier()
        self.sub("svc/msgauth/req", self.on_req)
        self.client.publish("sd/MessageAuthenticity/offer",
                            json.dumps({"instance": 1, "model": "lstm" if self.clf.ok else "rules"}), retain=True)
        self.log("service MessageAuthenticity offered (model:", "lstm" if self.clf.ok else "rules", ")")

    def on_req(self, topic, m):
        self.pub(f"svc/msgauth/res/{m['car']}", {"rid": m["rid"], **self.clf.classify(m["text"])})


if __name__ == "__main__":
    AIService()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    