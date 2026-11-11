"""Polling, history, alarms and events for otmon-mine.

Findings/events use OTMon's `otmon.finding/v1` schema and rule-ID style, and
alarms alert on TRANSITIONS only (OTMon BUG-004 lesson): one event when a
condition goes bad, one info event when it returns to normal.

Rule IDs used here:
  R-PROC-HI / R-PROC-HIHI / R-PROC-LO / R-PROC-LOLO  analog process limits
  R-PROC-STATE      discrete status alarm (trip, interlock, breaker open)
  R-RNG-003         setpoint outside the approved baseline range (from OTMon)
  R-WR-001          watched setpoint/command changed with no HMI write (OTMon-style)
  R-POLL-ERR / R-POLL-OK   device communication lost / restored (from OTMon)
  R-HMI-WRITE       audit entry for every operator write from this HMI
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .modbus_io import FC_BY_TYPE, HmiWriter, ReadOnlyMaster

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def now_iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text())
    for d in cfg["devices"]:
        d["host"] = os.environ.get(f"OTMON_HOST_{d['id'].upper().replace('-', '_')}", d["host"])
        d["port"] = int(os.environ.get(f"OTMON_PORT_{d['id'].upper().replace('-', '_')}", d.get("port", 502)))
        d["points_by_id"] = {p["id"]: p for p in d["points"]}
        for p in d["points"]:
            p.setdefault("scale", 0)
        for a in d.get("alarms", []):
            if a["point"] not in d["points_by_id"]:
                raise ValueError(f"{d['id']}: alarm on unknown point {a['point']}")
        for c in d.get("controls", []) + d.get("faults", []):
            if c["point"] not in d["points_by_id"]:
                raise ValueError(f"{d['id']}: control on unknown point {c['point']}")
    return cfg


class Engine:
    def __init__(self, cfg, state_dir="/tmp/otmon-mine", hmi_writes=True):
        self.cfg = cfg
        self.devices = {d["id"]: d for d in cfg["devices"]}
        self.interval = max(1.0, float(cfg.get("poll_interval_s", 1)))
        self.hist_len = int(cfg.get("history_s", 900) / self.interval) + 5
        self.watch_window = float(cfg.get("write_watch_window_s", 6))
        self.hmi_writes = hmi_writes
        self.lock = threading.RLock()
        self.values = {d: {} for d in self.devices}          # device -> point -> value
        self.history = {d: {p["id"]: deque(maxlen=self.hist_len) for p in self.devices[d]["points"]
                            if p["type"] in ("input", "holding")} for d in self.devices}
        self.comm = {d: {"ok": False, "last_ok": None, "last_error": None, "latency_ms": None,
                         "polls": 0, "errors": 0} for d in self.devices}
        self.alarms = {}                                       # alarm key -> alarm dict
        self.events = deque(maxlen=2000)
        self.last_hmi_write = {}                               # (device, point) -> ts
        self.started = time.time()
        self.writer = HmiWriter()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.findings_path = self.state_dir / "findings.jsonl"
        self._stop = threading.Event()
        self._seq = 0

    # ------------------------------------------------------------------ events
    def emit(self, rule_id, severity, title, device=None, point=None, value=None, detail="", category="process"):
        with self.lock:
            self._seq += 1
            d = self.devices.get(device, {})
            ev = {"schema": "otmon.finding/v1", "id": f"M-{int(time.time())}-{self._seq}",
                  "observed_at": now_iso(), "detected_at": now_iso(), "rule_id": rule_id,
                  "category": category, "severity": severity, "title": title, "detail": detail,
                  "src_ip": "otmon-mine", "src_name": "otmon-mine", "dst_ip": d.get("host"),
                  "dst_name": device, "protocol": "modbus", "point": point, "value": value,
                  "evidence": {"source": "poller" if rule_id != "R-HMI-WRITE" else "hmi"},
                  "detector": "deterministic-rules"}
            self.events.append(ev)
            try:
                with open(self.findings_path, "a") as fh:
                    fh.write(json.dumps(ev) + "\n")
            except OSError:
                pass
            return ev

    # ------------------------------------------------------------------ alarms
    def _set_alarm(self, key, active, *, device, point, rule_id, severity, label, value, limit=None):
        a = self.alarms.get(key)
        units = self.devices[device]["points_by_id"][point].get("units", "")
        if active:
            if a and a["active"]:
                a["value"] = value
                return
            if not a:
                a = {"key": key, "device": device, "device_name": self.devices[device]["name"], "point": point,
                     "rule_id": rule_id, "severity": severity, "label": label, "limit": limit, "units": units,
                     "count": 0}
                self.alarms[key] = a
            a.update(active=True, acked=False, value=value, raised_at=now_iso(), cleared_at=None,
                     count=a["count"] + 1, severity=severity)
            lim = f" (limit {limit} {units})" if limit is not None else ""
            title = label if rule_id in ("R-PROC-STATE", "R-POLL-ERR") else f"{label}: {fmt(value)} {units}{lim}".strip()
            self.emit(rule_id, severity, title, device, point, value)
        elif a and a["active"]:
            a.update(active=False, cleared_at=now_iso(), value=value)
            self.emit(rule_id + "-CLEAR", "info", f"{label}: back to normal" + ("" if rule_id in ("R-PROC-STATE", "R-POLL-ERR") else f" ({f'{fmt(value)} {units}'.strip()})"),
                      device, point, value, category="return_to_normal")
            if a["acked"]:
                del self.alarms[key]

    def _eval_alarms(self, dev_id, vals):
        d = self.devices[dev_id]
        for rule in d.get("alarms", []):
            p = rule["point"]
            if p not in vals:
                continue
            v = vals[p]
            if "state" in rule:
                self._set_alarm(f"{dev_id}:{p}:state", int(v) == int(rule["state"]), device=dev_id, point=p,
                                rule_id="R-PROC-STATE", severity=rule.get("severity", "high"),
                                label=rule["label"], value=v)
                continue
            for lvl, cmp_hi, sev in (("hihi", True, "critical"), ("hi", True, "high"),
                                     ("lolo", False, "critical"), ("lo", False, "high")):
                if lvl not in rule:
                    continue
                lim = float(rule[lvl])
                key = f"{dev_id}:{p}:{lvl}"
                db = abs(lim) * 0.02  # 2 % deadband so values hovering at the limit do not chatter
                was = self.alarms.get(key, {}).get("active", False)
                if cmp_hi:
                    active = v >= lim if not was else v > lim - db
                else:
                    active = v <= lim if not was else v < lim + db
                self._set_alarm(key, active, device=dev_id, point=p, rule_id=f"R-PROC-{lvl.upper()}",
                                severity=rule.get(f"severity_{lvl}", sev),
                                label=f"{rule['label']} {lvl.upper()}", value=v, limit=lim)
        # OTMon R-RNG-003: setpoint outside approved baseline
        for p in d["points"]:
            if "baseline" in p and p["id"] in vals:
                lo, hi = p["baseline"]
                v = vals[p["id"]]
                self._set_alarm(f"{dev_id}:{p['id']}:baseline", not (lo <= v <= hi), device=dev_id, point=p["id"],
                                rule_id="R-RNG-003", severity="high",
                                label=f"{p['label']} outside approved baseline {lo}-{hi}", value=v)

    def _watch_writes(self, dev_id, old, new):
        d = self.devices[dev_id]
        for p in d["points"]:
            if not p.get("watch_writes") or p["id"] not in old or p["id"] not in new:
                continue
            if abs(old[p["id"]] - new[p["id"]]) < 1e-9:
                continue
            t = max(self.last_hmi_write.get((dev_id, p["id"]), 0), self.last_hmi_write.get((dev_id, "*"), 0))
            if time.time() - t > self.watch_window:
                self.emit("R-WR-001", "high",
                          f"{p['label']} changed {fmt(old[p['id']])} -> {fmt(new[p['id']])} with no write from "
                          f"this HMI (another Modbus master wrote to {d['name']})", dev_id, p["id"], new[p["id"]],
                          category="unexpected_write")

    def ack(self, key=None):
        n = 0
        with self.lock:
            for k in list(self.alarms):
                a = self.alarms[k]
                if key in (None, k) and not a["acked"]:
                    a["acked"] = True
                    a["acked_at"] = now_iso()
                    n += 1
                    if not a["active"]:
                        del self.alarms[k]
        return n

    # ------------------------------------------------------------------ polling
    def _poll_device(self, dev_id):
        d = self.devices[dev_id]
        m = None
        while not self._stop.is_set():
            t0 = time.time()
            try:
                if m is None:
                    m = ReadOnlyMaster(d["host"], d["port"])
                vals = {}
                for ptype in ("input", "discrete", "coil", "holding"):
                    pts = [p for p in d["points"] if p["type"] == ptype]
                    if not pts:
                        continue
                    start = min(p["addr"] for p in pts)
                    qty = max(p["addr"] for p in pts) - start + 1
                    raw = m.execute(d.get("unit", 1), FC_BY_TYPE[ptype], start, qty)
                    for p in pts:
                        r = raw[p["addr"] - start]
                        if ptype in ("input", "holding"):
                            r = r - 65536 if r > 32767 else r      # registers are int16
                            vals[p["id"]] = round(r / (10 ** p["scale"]), 4)
                        else:
                            vals[p["id"]] = int(bool(r))
                lat = round((time.time() - t0) * 1000, 1)
                with self.lock:
                    c = self.comm[dev_id]
                    if self.alarms.get(f"{dev_id}:comm", {}).get("active"):
                        self.emit("R-POLL-OK", "info", f"Communication with {d['name']} restored", dev_id,
                                  category="poll_recovered")
                        self._set_alarm(f"{dev_id}:comm", False, device=dev_id, point=d["points"][0]["id"],
                                        rule_id="R-POLL-ERR", severity="high", label=f"{d['name']} comms", value=1)
                    old = self.values[dev_id]
                    if old:
                        self._watch_writes(dev_id, old, vals)
                    self.values[dev_id] = vals
                    ts = time.time()
                    for pid, dq in self.history[dev_id].items():
                        if pid in vals:
                            dq.append((round(ts, 2), vals[pid]))
                    c.update(ok=True, last_ok=ts, latency_ms=lat, polls=c["polls"] + 1, last_error=None)
                    self._eval_alarms(dev_id, vals)
            except Exception as e:  # noqa: BLE001
                if m is not None:
                    m.close()
                    m = None
                with self.lock:
                    c = self.comm[dev_id]
                    first = c["ok"] or c["polls"] == 0 or c["last_error"] is None
                    c.update(ok=False, last_error=f"{e.__class__.__name__}: {e}", errors=c["errors"] + 1,
                             polls=c["polls"] + 1)
                    # devices take a few seconds to start; don't alarm during the first 20 s
                    if time.time() - self.started > 20 and first:
                        self._set_alarm(f"{dev_id}:comm", True, device=dev_id, point=d["points"][0]["id"],
                                        rule_id="R-POLL-ERR", severity="high",
                                        label=f"{d['name']} communication lost", value=0)
            self._stop.wait(max(0.05, self.interval - (time.time() - t0)))

    def start(self):
        for dev_id in self.devices:
            threading.Thread(target=self._poll_device, args=(dev_id,), daemon=True, name=f"poll-{dev_id}").start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------ HMI writes
    def allowed_writes(self, dev_id):
        d = self.devices[dev_id]
        out = {}
        for c in d.get("controls", []) + d.get("faults", []):
            out.setdefault(c["point"], []).append(c)
        for pid in ("auto_faults", "reset", "fault_reset"):
            if pid in d["points_by_id"]:
                out.setdefault(pid, []).append({"point": pid})
        return out

    def write(self, dev_id, point, value, user="operator", reason="hmi"):
        if not self.hmi_writes:
            raise PermissionError("HMI writes are disabled (OTMON_HMI_WRITES=0); this monitor is read-only")
        d = self.devices.get(dev_id)
        if not d:
            raise KeyError(f"unknown device {dev_id}")
        allowed = self.allowed_writes(dev_id)
        if point not in allowed:
            raise PermissionError(f"{point} on {dev_id} is not an HMI-writable point")
        p = d["points_by_id"][point]
        value = float(value)
        if p["type"] == "coil":
            if value not in (0.0, 1.0):
                raise ValueError("coil value must be 0 or 1")
            raw = int(value)
        else:
            lims = [c for c in allowed[point] if c.get("kind") == "setpoint"]
            if lims and not (lims[0]["min"] <= value <= lims[0]["max"]):
                raise ValueError(f"{p['label']} must be between {lims[0]['min']} and {lims[0]['max']}")
            raw = int(round(value * (10 ** p["scale"])))
        with self.lock:
            self.last_hmi_write[(dev_id, point)] = time.time()
            if point == "reset":
                self.last_hmi_write[(dev_id, "*")] = time.time()
        self.writer.write(d["host"], d["port"], d.get("unit", 1), p["type"], p["addr"], raw)
        self.emit("R-HMI-WRITE", "info", f"{user} wrote {p['label']} = {fmt(value)} {p.get('units', '')} "
                  f"on {d['name']} ({reason})".replace("  ", " "), dev_id, point, value, category="operator_action")

    def rogue_write(self):
        """ATTACK DEMO: write CV-101 speed setpoint = 150 % straight over Modbus, bypassing the HMI
        audit path, exactly like an unauthorized engineering laptop would. The monitor is not told,
        so its normal detection (R-WR-001 + R-RNG-003) has to catch it."""
        d = self.devices["plc-conveyor-01"]
        p = d["points_by_id"]["speed_sp"]
        HmiWriter().write(d["host"], d["port"], d.get("unit", 1), "holding", p["addr"], 150)

    def scenario(self, action, user="operator"):
        """auto_on / auto_off: cyclic fault scenarios on every device. reset: restart sim clocks + defaults.
        rogue: simulated unauthorized setpoint write (see rogue_write)."""
        if action == "rogue":
            if not self.hmi_writes:
                raise PermissionError("HMI writes are disabled (OTMON_HMI_WRITES=0)")
            self.rogue_write()
            return []
        point, value = {"auto_on": ("auto_faults", 1), "auto_off": ("auto_faults", 0), "reset": ("reset", 1)}[action]
        errs = []
        for dev_id, d in self.devices.items():
            if point in d["points_by_id"]:
                try:
                    self.write(dev_id, point, value, user=user, reason=f"scenario {action}")
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{dev_id}: {e}")
        return errs

    # ------------------------------------------------------------------ snapshots
    def health(self):
        with self.lock:
            now = time.time()
            devs = {k: (c["ok"] and c["last_ok"] is not None and now - c["last_ok"] < 5 * self.interval)
                    for k, c in self.comm.items()}
            return {"status": "ok" if all(devs.values()) else "degraded", "devices_online": sum(devs.values()),
                    "devices_total": len(devs), "devices": devs, "uptime_s": round(now - self.started, 1),
                    "active_alarms": sum(1 for a in self.alarms.values() if a["active"]),
                    "alarms_raised_total": sum(1 for e in self.events if e["rule_id"].startswith(("R-PROC", "R-RNG"))
                                               and not e["rule_id"].endswith("CLEAR")),
                    "hmi_writes_enabled": self.hmi_writes}

    def status(self):
        with self.lock:
            devs = []
            for dev_id, d in self.devices.items():
                vals = self.values[dev_id]
                c = self.comm[dev_id]
                st = d.get("status", {})
                if not c["ok"]:
                    state = "COMM LOSS" if c["polls"] else "CONNECTING"
                elif st.get("tripped") and vals.get(st["tripped"]):
                    state = "TRIPPED"
                elif st.get("running"):
                    state = "RUNNING" if vals.get(st["running"]) else "STOPPED"
                else:
                    state = "ONLINE"
                devs.append({
                    "id": dev_id, "name": d["name"], "kind": d["kind"], "role": d["role"], "area": d["area"],
                    "host": d["host"], "port": d["port"], "unit": d.get("unit", 1), "dnp3": d.get("dnp3"),
                    "state": state, "kpis": d.get("kpis", []), "values": vals,
                    "auto_faults": vals.get("auto_faults"),
                    "comm": dict(c, last_ok=now_iso(c["last_ok"]) if c["last_ok"] else None),
                    "points": [{k: p[k] for k in ("id", "type", "addr", "scale", "units", "label", "trend", "max",
                                                  "baseline") if k in p} for p in d["points"]],
                    "controls": d.get("controls", []), "faults": d.get("faults", []),
                    "alarm_rules": d.get("alarms", []),
                })
            alarms = sorted(self.alarms.values(), key=lambda a: (not a["active"], a["acked"],
                                                                  SEV_ORDER.get(a["severity"], 9), a["raised_at"]))
            return {"site": self.cfg.get("site_name", "mine"), "generated_at": now_iso(),
                    "health": self.health(), "devices": devs, "alarms": alarms,
                    "events": list(self.events)[-60:][::-1]}

    def get_history(self, keys, seconds=600):
        cutoff = time.time() - seconds
        out = {}
        with self.lock:
            for k in keys:
                dev, _, pid = k.partition(":")
                dq = self.history.get(dev, {}).get(pid)
                if dq is not None:
                    out[k] = [x for x in dq if x[0] >= cutoff]
        return out


def fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:.0f}"
    return str(v)
