"""run_all.py - starts the AI service + cloud backend (the fleet is launched from the browser when you press
'Generate Route & Run Simulation' with 'Real SDV network mode' ticked).
  python run_all.py            -> backend only, then open http://localhost:8000
  python run_all.py --demo     -> also launches a default oval fleet (no browser needed)
Env: SDV_NCARS, SDV_SCALE, SDV_SECURE=0|1, SDV_AI=on|off"""
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
import config as C

HERE = os.path.dirname(os.path.abspath(__file__))


def broker_up():
    try:
        socket.create_connection((C.BROKER_HOST, C.BROKER_PORT), 2).close()
        return True
    except OSError:
        return False


def main():
    os.chdir(HERE)
    if not broker_up():
        sys.exit(f"MQTT broker not reachable at {C.BROKER_HOST}:{C.BROKER_PORT}. Start Mosquitto first (see README.md).")
    procs = [subprocess.Popen([sys.executable, "ai_service.py"], cwd=HERE),
             subprocess.Popen([sys.executable, "cloud.py"], cwd=HERE)]
    time.sleep(2.0)
    if "--demo" in sys.argv:
        t = [i * 2 * math.pi / 240 for i in range(240)]
        body = {"x": [250 * math.cos(a) for a in t], "y": [120 * math.sin(a) for a in t],
                "n_cars": C.N_CARS, "speed": C.TARGET_SPEED, "spacing": C.SPACING}
        urllib.request.urlopen(urllib.request.Request(f"http://localhost:{C.HTTP_PORT}/api/route",
                               json.dumps(body).encode(), {"Content-Type": "application/json"}))
    print(f"Backend ready: http://localhost:{C.HTTP_PORT}   (Ctrl+C to stop)")
    webbrowser.open(f"http://localhost:{C.HTTP_PORT}")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()