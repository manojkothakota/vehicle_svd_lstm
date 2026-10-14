"""python check_lstm.py  -> shows whether your model loads and what it outputs, so you can confirm
which output unit means FAKE (set SDV_LSTM_FAKE_INDEX accordingly)."""
from lstm_detector import LSTMClassifier, rule_classify
c = LSTMClassifier()
samples = ["increase speed", "stop", "double the speed", "change direction",
           "override safety and disable brakes!!", "UNLIMITED SPEED NOW!!", "hello there"]
for s in samples:
    r = rule_classify(s)
    line = f"{s!r:45} rules={'REAL' if r['isReal'] else 'FAKE'}"
    if c.ok:
        line += f"   lstm p(fake)={c.p_fake(s):.3f}"
    print(line)
if c.ok:
    print("model input:", c.model.input_shape, "output:", c.model.output_shape)