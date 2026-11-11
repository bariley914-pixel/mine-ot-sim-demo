"""End-to-end self-test, run INSIDE the otmon-mine container (no host tools needed):

    docker compose exec -T otmon-mine python -m otmon_mine.verify

Checks: /health ok (all 5 devices polled), telemetry is changing, operator write
round-trip over Modbus (FC6 then FC3 read-back), and that alarms really fire
(methane HI + pump trip injected through the HMI) and that a simulated rogue
setpoint write is detected (R-WR-001, R-RNG-003). Exits 0 only if all pass.
With OTMON_HMI_WRITES=0 it waits for the automatic fault scenarios instead.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("VERIFY_BASE", "http://127.0.0.1:8088")
H = {"Host": "localhost", "Content-Type": "application/json", "X-OTMon-HMI": "1"}
results = []


def get(path):
    req = urllib.request.Request(BASE + path, headers={"Host": "localhost"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return ok


def val(status, dev, point):
    return next(d for d in status["devices"] if d["id"] == dev)["values"].get(point)


def wait_for(fn, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = fn()
        if r:
            return r
        time.sleep(step)
    return None


def main():
    # 1. health
    h = wait_for(lambda: (lambda c, j: j if c == 200 else None)(*get("/health")), 90)
    check("health: all devices online", bool(h), json.dumps(h or get("/health")[1]))
    if not h:
        return 1
    # 2. telemetry changes
    _, s1 = get("/api/status")
    time.sleep(3)
    _, s2 = get("/api/status")
    changed = [f"{d1['id']}:{k}" for d1, d2 in zip(s1["devices"], s2["devices"])
               for k, v in d1["values"].items() if d2["values"].get(k) != v]
    check("telemetry: values change between polls", len(changed) >= 5, f"{len(changed)} points changed, e.g. {changed[:4]}")
    hmi = h.get("hmi_writes_enabled")
    if hmi:
        # 3. operator write round trip (fan speed setpoint 90 -> 80 -> 90)
        post("/api/write", {"device": "plc-vent-01", "point": "speed_sp", "value": 80, "user": "verify"})
        ok = wait_for(lambda: val(get("/api/status")[1], "plc-vent-01", "speed_sp") == 80, 10)
        post("/api/write", {"device": "plc-vent-01", "point": "speed_sp", "value": 90, "user": "verify"})
        check("HMI write: F-301 speed setpoint 80 % written (FC6) and read back (FC3)", bool(ok))
        # 4. inject faults and wait for alarms
        post("/api/write", {"device": "rtu-gas-01", "point": "gas_cmd", "value": 1, "user": "verify"})
        post("/api/write", {"device": "plc-dewater-01", "point": "trip_cmd", "value": 1, "user": "verify"})
        timeout = 60
    else:
        print("HMI writes disabled - waiting for the automatic fault scenarios (up to 4 min)")
        timeout = 260

    def alarm(key):
        return lambda: any(a["key"] == key and a["active"] for a in get("/api/status")[1]["alarms"])

    def event(rule, dev):
        return lambda: any(e["rule_id"] == rule and e["dst_name"] == dev for e in get("/api/events?limit=2000")[1])

    check("alarm fires: P-201 pump tripped (R-PROC-STATE)",
          bool(wait_for(lambda: alarm("plc-dewater-01:tripped:state")() or event("R-PROC-STATE", "plc-dewater-01")(), timeout)))
    check("alarm fires: GM-401 methane HI >= 1.0 % (R-PROC-HI)",
          bool(wait_for(lambda: alarm("rtu-gas-01:ch4:hi")() or event("R-PROC-HI", "rtu-gas-01")(), timeout)))
    if hmi:
        post("/api/scenario", {"action": "rogue", "user": "verify"})
        check("security: rogue setpoint write detected (R-WR-001)", bool(wait_for(event("R-WR-001", "plc-conveyor-01"), 15)))
        check("security: setpoint outside baseline (R-RNG-003)", bool(wait_for(event("R-RNG-003", "plc-conveyor-01"), 15)))
        post("/api/write", {"device": "plc-conveyor-01", "point": "speed_sp", "value": 100, "user": "verify"})
    _, st = get("/api/status")
    check("alarm list populated", len(st["alarms"]) > 0, f"{len(st['alarms'])} alarms: " +
          "; ".join(a["label"] for a in st["alarms"][:4]))
    fails = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed" + (f"; FAILED: {fails}" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
