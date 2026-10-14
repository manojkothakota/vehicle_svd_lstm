# SDV Platoon - Software-Defined Vehicle simulation

```
 MCU powertrain --plant--> MCU sensor --sensor--> HPC (ADAS + detector) --cmd--> MCU powertrain
                                   \                  ^  |alert/status
                                    +----> Gateway ECU +--+----> V2V broadcast (signed BSM)
                                             |  ^                 V2Cloud telemetry/alerts
                                             v  | verified OTA
                                           MQTT broker  <---->  cloud.py (+ dashboard :8000)
```
Each car = one process (`vehicle.py <id>`) with 4 ECUs. Cloud = `cloud.py`. Everything talks only via MQTT.

## Folder layout (put in your repo, NOT inside .venv)
```
sdv/
  config.py bus.py path_model.py mcu_powertrain.py mcu_sensor.py hpc.py gateway.py
  vehicle.py cloud.py attacker.py run_all.py dashboard.html requirements.txt
  data/waypoints.csv      <- from your convert_path.py (columns x_m,y_m). Auto-generated oval if missing.
```

## Setup
1. `pip install -r requirements.txt`
2. Install & start an MQTT broker (Windows): install Mosquitto from https://mosquitto.org/download/ then
   `net start mosquitto` (or run `mosquitto -v` in a terminal). Docker alternative:
   `docker run -p 1883:1883 -v mosq.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto`
   with `mosq.conf` containing `listener 1883` and `allow_anonymous true`.
3. `python run_all.py` -> opens http://localhost:8000

## Scenarios (use the dashboard buttons or the CLI)
| # | What | Expect |
|---|------|--------|
| 1 | Normal run | car0 CRUISE (blue), others FOLLOW (green), gaps ~ 6 + 1.0*v |
| 2 | Attack `teleport`, unsigned, `SDV_SECURE=1` | Gateway firewall drops it: "invalid V2V signature" alerts, `Drop` counter up, platoon unaffected |
| 3 | Attack `teleport`/`brake` with stolen key | Passes gateway, HPC detector rejects: victim goes DEGRADED (orange), `Rej` counter up, follows dead-reckoned estimate |
| 4 | Same as 3, but first OTA `detector_tol=9999` | Detector blinded -> victim reacts to fake data (gap jumps / hard braking) |
| 5 | OTA `target_speed` / `version` | Leader changes speed, followers adapt, version column updates |
| 6 | Attack `badota` | Gateway log: "rejected cloud command (bad signature / replay)", nothing applied |
| 7 | `SDV_SECURE=0 python run_all.py` + scenario 2 | No signatures; the HPC detector is the only defence |

CLI: `python attacker.py --victim 3 --mode teleport --signed`
Speed up: `set SDV_SCALE=3` (Windows) / `SDV_SCALE=3` (Linux) before `run_all.py`.

## Moving to real hardware
Only `bus.py` changes (MQTT -> CAN / SOME-IP). ECU classes stay the same.
`Detector.check()` in hpc.py is where your LSTM (`train_lstm_detector.py`) plugs in.
