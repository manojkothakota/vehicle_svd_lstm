"""config.py - every tunable, topic name and key lives here."""
import os
import time

# ---- network (MQTT broker = in-vehicle bus + V2V + V2Cloud transport) ----
BROKER_HOST = os.getenv("SDV_HOST", "localhost")
BROKER_PORT = int(os.getenv("SDV_PORT", "1883"))
CAFILE = os.getenv("SDV_CAFILE") or None
HTTP_PORT = 8000

WAYPOINTS_CSV = os.getenv("SDV_WAYPOINTS", "data/waypoints.csv")   # cloud overwrites this per run (route from the browser map)

# ---- simulation ----
N_CARS = int(os.getenv("SDV_NCARS", "10"))
SPACING = float(os.getenv("SDV_SPACING", "20"))
TARGET_SPEED = float(os.getenv("SDV_SPEED", "12"))
DT = 0.1
TIME_SCALE = float(os.getenv("SDV_SCALE", "1"))
EPOCH = float(os.getenv("SDV_EPOCH", str(time.time())))


def sim_now():
    """Shared simulation clock (all processes of one fleet get the same SDV_EPOCH)."""
    return (time.time() - EPOCH) * TIME_SCALE


# ---- car-following control ----
K_P, K_V = 0.35, 0.90
STANDSTILL, TIME_GAP = 6.0, 1.0
A_MAX, A_MIN, V_MAX = 2.0, -3.0, 20.0

# ---- security ----
SECURE_V2V = os.getenv("SDV_SECURE", "1") == "1"
V2V_KEY = b"v2v-fleet-key"
CLOUD_KEY = b"cloud-oem-key"
DETECTOR_TOL = 5.0
DETECTOR_A_LIM = 6.0

# ---- AI: message authenticity (your V2V-LSTM) ----
AI_MODE = os.getenv("SDV_AI", "on")        # on = HPC applies only REAL commands | off = naive HPC (fault-injection for STL spec 5)
LSTM_MODEL = os.getenv("SDV_LSTM_MODEL", "v2v_lstm_model (1).keras")
LSTM_TOKENIZER = os.getenv("SDV_LSTM_TOK", "v2v_tokenizer (1).pkl")
LSTM_FAKE_INDEX = int(os.getenv("SDV_LSTM_FAKE_INDEX", "1"))   # which output unit means FAKE (verify with check_lstm.py)
AI_TIMEOUT = 0.5                                                # real seconds before HPC falls back to local rules

# ---- STL specification parameters ----
D_MIN = 3.0          # m      safety distance
STL_TAU = 8.0        # s      stop must be effective within tau
STL_DELTA = 0.5      # m/s    "stopped" threshold
STL_T1, STL_T2 = 60.0, 20.0   # s  settle within T1, then stay for T2
STL_EPS = 1.5        # m/s    settling band
A_DEV_EPS = 0.3      # m/s^2  max accel deviation allowed while a FAKE message is active
FAKE_WINDOW = 15.0   # s      how long FAKE_i(t) stays active after a flagged message


class T:
    """MQTT topic map.  veh/<id>/can/*  = in-vehicle bus (simulated CAN)."""
    plant = staticmethod(lambda i: f"veh/{i}/can/plant")
    sensor = staticmethod(lambda i: f"veh/{i}/can/sensor")
    cmd = staticmethod(lambda i: f"veh/{i}/can/cmd")
    status = staticmethod(lambda i: f"veh/{i}/can/hpc_status")
    alert = staticmethod(lambda i: f"veh/{i}/can/alert")
    v2v_in = staticmethod(lambda i: f"veh/{i}/can/v2v_in")
    v2v_cmd_in = staticmethod(lambda i: f"veh/{i}/can/v2v_cmd")   # gateway -> HPC (verified text command)
    msglog = staticmethod(lambda i: f"veh/{i}/can/msglog")        # HPC -> gateway (verdicts)
    ota = staticmethod(lambda i: f"veh/{i}/can/ota")
    bsm = staticmethod(lambda i: f"v2v/{i}/bsm")
    v2v_cmd = staticmethod(lambda i: f"v2v/{i}/cmd")              # wireless car->car text command
    up_tel = staticmethod(lambda i: f"cloud/up/{i}/telemetry")
    up_alert = staticmethod(lambda i: f"cloud/up/{i}/alert")
    up_msg = staticmethod(lambda i: f"cloud/up/{i}/msg")
    down = staticmethod(lambda i: f"cloud/down/{i}")