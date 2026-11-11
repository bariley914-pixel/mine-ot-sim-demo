# AGENTS.md - setup instructions for AI assistants

Goal: bring up the mine OT simulation (5 ot-sim devices) and the otmon-mine dashboard, prove it
works, and hand the human a browser URL. Every step below is **non-interactive** and
idempotent. Run commands from the repository root. Do not ask the human to use a terminal
after step 3; everything they need is in the web UI.

## 0. Rules

* Do **not** modify `devices/*.xml` or `monitor/config/mine.yaml` to make a check pass; they
  are the scenario. If a check fails, report the output.
* All published ports bind to `127.0.0.1`. Do not change that or expose the dashboard publicly
  (it has operator write controls and no authentication).
* Do not point any of this at a real OT network.

## 1. Prerequisites check

```bash
docker info >/dev/null && echo "docker OK"          # daemon running and reachable
docker compose version                               # need Compose v2 (2.x or later)
```

* If `docker info` fails with a permission error on Linux, use `sudo docker ...` (run.sh does
  this automatically when passwordless sudo is available) or add the user to the `docker`
  group.
* Needs ~2 GB disk and outbound internet for the first build (deb.debian.org, go.dev,
  github.com, proxy.golang.org, pypi.org).
* Free host ports: `8088` (dashboard), `15021-15025` (Modbus), `20000` (DNP3). If 8088 is
  taken, prefix commands with `OTMON_HOST_PORT=8090`.
* x86-64 host assumed. On ARM64 see README "Caveats".

## 2. Bring up (one command)

```bash
./run.sh
```

Equivalent without bash: `docker compose up -d --build`, then poll
`http://localhost:8088/health` until it returns HTTP 200.

Expected: the first build takes 5-10 minutes (it compiles opendnp3). The script ends with
`READY: open http://localhost:8088 in a browser` and exit code 0. Exit code 1 = not healthy
within 180 s (see troubleshooting).

## 3. Verify (scripted, exit 0 = pass)

```bash
./scripts/verify.sh          # same as ./run.sh verify
```

It checks: all 6 containers running; `GET /` returns 200 from the host; DNP3 class-0 read
from the substation outstation; `/health` ok with 5/5 devices; telemetry values change; an HMI
setpoint write round-trips over Modbus; injected faults raise alarms (pump trip, methane HI);
a simulated rogue write is detected (R-WR-001, R-RNG-003). Last line `VERIFY: ALL CHECKS PASSED`.
Takes about 30-60 s. Note: it injects faults, so the dashboard will show alarms afterwards;
that is expected (use *Reset simulation* and *Ack all* in the UI to tidy up).

Health only:

```bash
curl -s http://localhost:8088/health
# {"status": "ok", "devices_online": 5, "devices_total": 5, ...}   HTTP 200; 503 when degraded
```

## 4. Hand over to the human

Tell them: open **http://localhost:8088**. Then, all in the browser:

* *Overview*: live plant, click a device in the one-line diagram or a card.
* *Simulation scenario* panel: automatic faults on/off, **Inject ...** buttons, **Reset
  simulation**, **Simulate rogue setpoint write** (security demo).
* Alarm banner / *Alarms* page: acknowledge individually or *Ack all*.
* Device pages: start/stop, setpoints, breakers, trends, register map.
* Try: F-301 → *Stop fan* → watch methane rise on GM-401 until the interlock opens CB-2.

## 5. Stop / clean up

```bash
./run.sh stop        # docker compose down (keeps built images, fast restart)
./run.sh clean       # also removes the state volume and the two built images
```

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Start Docker Desktop / `sudo systemctl start docker`; re-run `./run.sh`. |
| `permission denied ... docker.sock` | `sudo ./run.sh`, or `sudo usermod -aG docker $USER` and log in again. |
| Build fails at `apt-get` (500 / not signed) | Transient mirror/proxy error; the Dockerfile retries 8×. Re-run `./run.sh`. |
| Build fails at `git clone` / `go build` | Needs github.com and proxy.golang.org access. Behind a proxy, set `HTTP(S)_PROXY` for Docker builds. |
| `port is already allocated` / port 8088 in use | `OTMON_HOST_PORT=8090 ./run.sh`, open `http://localhost:8090`. Ports 15021-15025/20000: stop whatever uses them, or edit `ports:` in `docker-compose.yml`. |
| `Pool overlaps with other one on this network` | Another Docker network uses 10.77.10.0/24. Change `subnet` and every `ipv4_address` in `docker-compose.yml` to a free /24. |
| `/health` stays 503, UI shows **COMM LOSS** for every device, monitor logs `TimeoutError` | Containers cannot reach each other on the bridge. Check `docker compose exec otmon-mine python -c "import socket;socket.create_connection(('plc-vent-01',502),2)"`. On some Linux hosts with `br_netfilter` loaded and a restrictive firewall, set `sudo sysctl -w net.bridge.bridge-nf-call-iptables=0` (seen on the build machine), or allow forwarding on the Docker bridge. |
| One device COMM LOSS | `docker compose logs --tail=50 <service>`; `docker compose restart <service>`. |
| DNP3 check fails right after start | The outstation needs a few seconds; re-run `./scripts/verify.sh`. |
| Dashboard 403 "Host header not allowed" | You used a host name other than localhost/127.0.0.1. Set `OTMON_ALLOWED_HOSTS=localhost,127.0.0.1,<name>` and `docker compose up -d`. |
| Buttons greyed out | Monitor started with `OTMON_HMI_WRITES=0` (read-only mode) or the device is offline. |
| ARM64 build error `exec format error` (Go) | See README Caveats (use linux/amd64 platform or the arm64 Go tarball). |

Useful: `./run.sh status`, `./run.sh logs`, `docker compose ps`,
`docker compose exec rtu-sub-01 mine-dnp3-poll 127.0.0.1` (DNP3 read).
