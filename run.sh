#!/usr/bin/env bash
# One-command bring-up for the mine OT simulation + otmon-mine dashboard.
# Non-interactive; safe to re-run. Exit code 0 = stack up and healthy.
#
#   ./run.sh            build (first run ~5-10 min) + start + wait for health
#   ./run.sh verify     run the end-to-end checks (scripts/verify.sh)
#   ./run.sh status     container + health status
#   ./run.sh logs       follow monitor logs
#   ./run.sh stop       stop and remove containers (keeps images)
#   ./run.sh clean      stop + remove containers, volume and built images
set -euo pipefail
cd "$(dirname "$0")"
PORT="${OTMON_HOST_PORT:-8088}"
URL="http://localhost:${PORT}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# docker may need sudo on Linux hosts where the user is not in the docker group
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo -n docker"
  else die "Docker is not running or not reachable. Start Docker Desktop / dockerd (and check 'docker info')."; fi
fi
if $DOCKER compose version >/dev/null 2>&1; then DC="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Docker Compose v2 not found (need 'docker compose'). Install the compose plugin."; fi

health() {
  if command -v curl >/dev/null 2>&1; then curl -fsS "${URL}/health" 2>/dev/null
  else $DC exec -T otmon-mine python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=3).read().decode())" 2>/dev/null; fi
}

case "${1:-up}" in
  up|start)
    say "Prerequisites: $($DOCKER --version | head -1); $($DC version | head -1)"
    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE "[:.]${PORT}\s" && ! $DC ps -q otmon-mine 2>/dev/null | grep -q .; then
      die "Port ${PORT} is already in use. Re-run with another port: OTMON_HOST_PORT=8090 ./run.sh"
    fi
    say "Building images and starting 5 ot-sim devices + otmon-mine (first build takes a few minutes)"
    $DC up -d --build
    say "Waiting for ${URL}/health (all devices polled)"
    for i in $(seq 1 90); do
      if out=$(health) && echo "$out" | grep -q '"status": "ok"'; then
        echo "$out"
        say "READY: open ${URL} in a browser"
        exit 0
      fi
      sleep 2
    done
    $DC ps
    die "Stack did not become healthy within 180 s. See: $DC logs --tail=50"
    ;;
  verify) exec ./scripts/verify.sh ;;
  status) $DC ps; echo; health || echo "health endpoint not reachable"; echo ;;
  logs)   exec $DC logs -f --tail=100 otmon-mine ;;
  stop|down) $DC down; say "Stopped." ;;
  clean)  $DC down -v --rmi local; say "Removed containers, volume and locally built images." ;;
  *) die "unknown command '$1' (use: up | verify | status | logs | stop | clean)" ;;
esac
