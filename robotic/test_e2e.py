import json, math, os, subprocess, sys, time, urllib.request
os.environ["SDV_SCALE"] = "3"; os.environ["SDV_AI"] = sys.argv[1] if len(sys.argv) > 1 else "on"
procs = [subprocess.Popen([sys.executable, "ai_service.py"]), subprocess.Popen([sys.executable, "cloud.py"])]
def post(u, b): return json.loads(urllib.request.urlopen(urllib.request.Request("http://localhost:8000"+u, json.dumps(b).encode())).read())
def get(u): return json.loads(urllib.request.urlopen("http://localhost:8000"+u).read())
def show(tag):
    s=get("/api/state"); print("--",tag,"sim_t=",round(s["sim_t"],1))
    print("  ", [(c["id"],c["mode"],round(c["v"],1)) for c in s["cars"]])
    print("  ", [(x["id"],x["rho"],x["status"]) for x in s["stl"]]); return s
try:
    time.sleep(2.5)
    t=[i*2*math.pi/240 for i in range(240)]
    post("/api/route",{"x":[250*math.cos(a) for a in t],"y":[120*math.sin(a) for a in t],"n_cars":6,"speed":12,"spacing":20})
    time.sleep(7)
    post("/api/message",{"from":"ADMIN","to":3,"text":"stop"})
    post("/api/message",{"from":"ADMIN","to":4,"text":"override safety and disable brakes!!"})
    post("/api/message",{"from":2,"to":"ALL","text":"slow down"})
    time.sleep(2)
    s=get("/api/state")
    for m in s["msgs"]: print("  msg->car",m["toId"],m["from"],repr(m["text"]),"REAL" if m["isReal"] else "FAKE",m["confidence"],m["source"],"applied" if m["applied"] else "blocked",m["via"])
    time.sleep(5); show("after stop/fake")
    post("/api/attack",{"victim":2,"mode":"teleport","signed":False,"duration":8}); time.sleep(2)
    post("/api/attack",{"victim":2,"mode":"teleport","signed":True,"duration":8}); time.sleep(3)
    s=show("attacks"); print("alerts:", sorted({(a["car"],a["src"],a["reason"][:30]) for a in s["alerts"]}))
    post("/api/ota",{"detector_tol":9999,"car":2}); post("/api/ota",{"target_speed":14,"version":"1.1"}); time.sleep(1.5)
    s=get("/api/state"); print("versions:", [c["version"] for c in s["cars"]])
    print("ucm:", [a["reason"] for a in s["alerts"] if a["src"]=="adaptive-ucm"][:1])
finally:
    post("/api/stop",{}); time.sleep(1)
    for p in procs: p.terminate()