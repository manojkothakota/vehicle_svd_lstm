"""vehicle.py - one software-defined vehicle = one OS process.
Usage:  python vehicle.py <car_id>
Inside: Gateway ECU + Sensor MCU + HPC + Powertrain MCU, each with its own MQTT
client, talking only through the broker (the simulated CAN bus / V2V / V2Cloud)."""
import sys
import time
import config as C
from path_model import ClosedPath
from mcu_powertrain import PowertrainMCU
from mcu_sensor import SensorMCU
from hpc import HPC
from gateway import GatewayECU


def main():
    car_id = int(sys.argv[1])
    path = ClosedPath(C.WAYPOINTS_CSV)
    nodes = [GatewayECU(car_id), SensorMCU(car_id, path), HPC(car_id, path), PowertrainMCU(car_id, path)]
    time.sleep(0.5)                       # let all subscriptions register
    for n in nodes:
        n.start()
    print(f"[car{car_id}] online (secure_v2v={C.SECURE_V2V})", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
